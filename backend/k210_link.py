#!/usr/bin/env python3
"""慧签 K210 + AS608 联动服务 (Pi5 侧)
流程: K210 TRIGGER -> 指纹优先(AS608 直连 USB-TTL) -> 人脸+活体确认 -> 打卡 -> 回 OK/FAIL/CLOSE
AS608 未接入时自动回退到纯人脸打卡。
接线: AS608 TX->USB-TTL RX, RX->USB-TTL TX, VCC->5V, GND->GND; USB-TTL -> Pi5 USB (57600)
"""
import glob, os, sys, time, logging, threading, datetime, signal, random
import cv2
import numpy as np
import serial
import face_engine, attendance
import as608
import voice

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('link')

BASE = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.path.join(BASE, 'static', 'photos')
BAUD = 115200
FP_BAUD = 57600
BURST_FRAMES = 16
BURST_DELAY = 0.10
LIVENESS_PROMPT_DELAY = 0.65
LIVENESS_ACTIONS = {
    'lr': {'voice': 'liveness_lr', 'label': '左右摇头'},
    'ud': {'voice': 'liveness_ud', 'label': '上下点头'},
    'mouth': {'voice': 'liveness_mouth', 'label': '张嘴'},
}
FP_TIMEOUT = 10.0            # 指纹等待秒数(等手指按下)
FP_STRICT = True             # True=指纹失败直接拒; False=回退纯人脸
MIN_TRIGGER_INTERVAL = 2.0


def find_serial_ports():
    ports = []
    for pat in ('/dev/ttyUSB*', '/dev/ttyACM*'):
        ports += sorted(glob.glob(pat))
    return ports


def open_serial(port):
    return serial.Serial(port, BAUD, timeout=0.2)


def save_photo(bgr):
    os.makedirs(PHOTO_DIR, exist_ok=True)
    fn = time.strftime('%Y%m%d_%H%M%S_') + str(int(time.time() * 1000) % 1000).zfill(3) + '.jpg'
    cv2.imwrite(os.path.join(PHOTO_DIR, fn), bgr)
    return fn


def capture_burst():
    from picamera2 import Picamera2
    picam2 = Picamera2()
    picam2.configure(picam2.create_still_configuration(main={'size': (1280, 720)}))
    picam2.start()
    time.sleep(0.5)
    frames = []
    try:
        for _ in range(BURST_FRAMES):
            arr = picam2.capture_array()
            frames.append(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
            time.sleep(BURST_DELAY)
    finally:
        picam2.stop()
        picam2.close()
    return frames


def process_burst(frames, confirm_user_id=None, method='face', action='lr'):
    """识别 + 活体; confirm_user_id 指定时只和该用户比对"""
    best_face = None
    nose_xs = []
    nose_ys = []
    mouth_ratios = []
    truncated = False
    for f in frames:
        lms = face_engine.face_landmarks(f)
        if lms:
            nose_xs.append(face_engine.nose_x(lms[0]))
            nose_ys.append(face_engine.nose_y(lms[0]))
            mouth_ratios.append(face_engine.mouth_open_ratio(lms[0]))
        boxes = face_engine.detect_faces(f)
        if not boxes:
            continue
        hh, ww = f.shape[:2]
        x0, y0, bw, bh = boxes[0]
        if x0 + bw >= ww - 5 or y0 + bh >= hh - 5 or x0 <= 5 or y0 <= 5:
            truncated = True
        size = bw * bh
        if best_face is None or size > best_face[0]:
            best_face = (size, f, face_engine.crop_face(f, boxes[0]))
    if best_face is None:
        if frames:
            photo = save_photo(frames[-1])
            attendance.log_action('陌生人', '人脸验证失败',
                                  '原因=未检测到人脸;动作=%s;抓拍=%s' %
                                  (LIVENESS_ACTIONS.get(action, LIVENESS_ACTIONS['lr'])['label'], photo))
        return None, 'CLOSE' if truncated else '未检测到人脸'
    emb = face_engine.get_embedding(best_face[2])
    if emb is None:
        log.info('HOG 未识别, CNN 兜底一次')
        emb = face_engine.get_embedding_cnn(best_face[2])
    if emb is None:
        photo = save_photo(best_face[1])
        attendance.log_action('陌生人', '人脸验证失败',
                              '原因=未能提取人脸特征;动作=%s;抓拍=%s' %
                              (LIVENESS_ACTIONS.get(action, LIVENESS_ACTIONS['lr'])['label'], photo))
        return None, '未能提取人脸特征'
    user, dist = attendance.match_face(emb, user_id=confirm_user_id)
    if user is None:
        # 未识别/异常: 抓拍留证(比赛展示点: 陌生人+指纹不符都有日志和照片)
        stranger_photo = save_photo(best_face[1])
        actor = '指纹用户#%d' % confirm_user_id if confirm_user_id is not None else '陌生人'
        attendance.log_action(actor, '人脸验证失败',
                              '原因=人脸不匹配;dist=%.3f;抓拍=%s' % (dist, stranger_photo))
        return None, '未识别(dist=%.2f)' % dist
    action_info = LIVENESS_ACTIONS.get(action, LIVENESS_ACTIONS['lr'])
    if action == 'ud':
        live_ok = face_engine.head_motion_detected(nose_ys)
    elif action == 'mouth':
        live_ok = face_engine.mouth_open_detected(mouth_ratios)
    else:
        live_ok = face_engine.head_motion_detected(nose_xs)
    if not live_ok:
        photo = save_photo(best_face[1])
        detail = '动作=%s;原因=未完成动作;抓拍=%s' % (action_info['label'], photo)
        attendance.log_action(user['name'], '活体验证失败', detail)
        log.info('活体验证失败: %s (%s)', user['name'], detail)
        return None, '活体验证失败: %s' % action_info['label']
    kind = attendance.next_kind(user['id'])
    if kind is None:
        return None, '当前不在可打卡时间'
    photo = save_photo(best_face[1])
    ok, info = attendance.punch(user['id'], kind, photo, method=method)
    if not ok:
        return None, info
    return {'ok': True, 'name': user['name'], 'kind': kind, 'dist': round(dist, 3),
            'liveness_action': action_info['label'], 'photo': photo, 'record_id': info}, None


def send_jpeg(k210, bgr, menu=None):
    """BGR -> 320x240 JPEG -> 串口 IMG:<len>+bytes 发给 K210; menu=底部中文菜单列表"""
    h, w = bgr.shape[:2]
    if (w, h) != (320, 240):
        sc = min(320.0 / w, 240.0 / h)
        if sc < 1:
            bgr = cv2.resize(bgr, (max(1, int(w * sc)), max(1, int(h * sc))))
        h2, w2 = bgr.shape[:2]
        canvas = np.zeros((240, 320, 3), dtype=np.uint8)
        x0, y0 = (320 - w2) // 2, (240 - h2) // 2
        canvas[y0:y0 + h2, x0:x0 + w2] = bgr
        bgr = canvas
    if menu:
        # 用 PIL 把中文菜单叠到底部 26px
        from PIL import Image, ImageDraw, ImageFont
        pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        fp = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
        try:
            f = ImageFont.truetype(fp, 16, index=2)
        except Exception:
            f = ImageFont.truetype(fp, 16)
        n = len(menu)
        wseg = 320 // n
        for i, lab in enumerate(menu):
            d.rectangle([i * wseg, 214, (i + 1) * wseg, 239], fill=(16, 24, 40))
            tw = d.textbbox((0, 0), lab, font=f)[2]
            d.text((i * wseg + (wseg - tw) // 2, 218), lab, font=f, fill=(255, 255, 255))
        bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    ok, jpg = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 60])
    data = jpg.tobytes()
    k210.write(('IMG:%d\n' % len(data)).encode())
    for i in range(0, len(data), 512):
        k210.write(data[i:i + 512])
        time.sleep(0.008)
    return data


def render_rank_page(page=1, per_page=9):
    """生成本周排行榜分页图 320x240 BGR(每页 per_page 人, 底部显示 第x/y页)"""
    from PIL import Image, ImageDraw, ImageFont
    stats = attendance.weekly_summary()
    users = stats.get('users', [])
    total = max(1, (len(users) + per_page - 1) // per_page)
    page = ((page - 1) % total) + 1
    rows = users[(page - 1) * per_page: page * per_page]
    font_path = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
    img = Image.new('RGB', (320, 240), (18, 26, 40))
    d = ImageDraw.Draw(img)
    try:
        f_title = ImageFont.truetype(font_path, 22, index=2)
        f_row = ImageFont.truetype(font_path, 16, index=2)
        f_small = ImageFont.truetype(font_path, 12, index=2)
    except Exception:
        f_title = ImageFont.truetype(font_path, 22)
        f_row = ImageFont.truetype(font_path, 16)
        f_small = ImageFont.truetype(font_path, 12)
    d.rectangle([0, 0, 319, 239], outline=(60, 90, 140), width=2)
    d.text((10, 8), '本周排行榜', font=f_title, fill=(255, 255, 255))
    d.text((205, 16), 'HuiQian', font=f_row, fill=(120, 160, 210))
    y = 52
    if not rows:
        d.text((10, y), '(暂无打卡数据)', font=f_row, fill=(200, 200, 200))
    for i, u in enumerate(rows, 1):
        color = (255, 215, 80) if u['rank'] == 1 else (235, 235, 235)
        d.text((10, y), '%d. %s' % (u['rank'], u['name']), font=f_row, fill=color)
        d.text((150, y), '%.2fh' % u['hours'], font=f_row, fill=(150, 230, 150))
        d.text((235, y), '%d次' % u['punches'], font=f_row, fill=(150, 190, 255))
        y += 18
    d.text((10, 220), '第 %d/%d 页  短按翻页 长按退出' % (page, total), font=f_small, fill=(160, 180, 210))
    return np.array(img)[:, :, ::-1].copy()


def safe_folder(name):
    import re
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip() or 'user'


def run_enroll_session(k210, fp=None):
    """统一录入: 自动编号 -> 人脸3张(K210确认) -> (有AS608则)指纹2次(语音+屏幕提示)
    协议: K210发 ENROLL_START -> Pi发 TXT:编号 -> IMG:<len>+字节 -> K210回 CONFIRM/RETRY/ABORT
          -> 人脸完成后 Pi发 TXT:指纹状态 -> 最后 DONE
    """
    from picamera2 import Picamera2
    name = attendance.next_number_name()
    log.info('录入会话开始, 自动编号=%s', name)

    def txt(msg):
        try:
            k210.write(('TXT:%s\n' % msg).encode())
        except Exception:
            pass

    user = None
    sample = 1
    picam2 = Picamera2()
    picam2.configure(picam2.create_still_configuration(main={'size': (1280, 720)}))
    picam2.start()
    time.sleep(0.5)

    def capture_now():
        arr = picam2.capture_array()
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    def send_photo(bgr):
        send_jpeg(k210, bgr, menu=['重拍', '确认', '取消'])
        log.info('已发图 sample%d', sample)
        return bgr

    txt('编号 %s' % name)
    txt('人脸录入 %d/3' % sample)
    try:
        frame = capture_now()
        send_photo(frame)
        deadline = time.time() + 300
        last_act = time.time()
        buf = b''
        while time.time() < deadline:
            data = k210.read(64)
            if not data:
                if time.time() - last_act > 90:
                    log.warning('录入会话空闲超时(90s), 结束')
                    break
                time.sleep(0.05)
                continue
            last_act = time.time()
            buf += data
            while b'\n' in buf:
                line, _, buf = buf.partition(b'\n')
                cmd = line.strip().decode('utf-8', 'ignore')
                if cmd.startswith('DBG:'):
                    log.info('K210-DBG %s', cmd[4:])
                    continue
                if cmd == 'CONFIRM':
                    if user is None:
                        user = attendance.add_user(name)
                    n = len(attendance.get_face_samples(user['id'])) + 1
                    folder = os.path.join(attendance.FACE_LIB, safe_folder(name))
                    os.makedirs(folder, exist_ok=True)
                    rel = os.path.join('face_library', safe_folder(name), 'sample_%02d.jpg' % n).replace(os.sep, '/')
                    cv2.imwrite(os.path.join(BASE, rel), frame)
                    emb = face_engine.get_embedding_multi(frame)
                    if emb is None:
                        k210.write(b'ERR_NOEMB\n')
                        log.warning('sample%d 提特征失败', sample)
                    else:
                        attendance.add_face_sample(user['id'], emb.tolist(), rel)
                        attendance.log_action(name, 'K210录入', '样本 %d' % n)
                        log.info('编号%s 已存样本%d', name, n)
                    sample += 1
                    if sample > 3:
                        _finish_fp(k210, fp, user, name, txt)
                        k210.write(b'DONE\n')
                        log.info('录入完成: %s', name)
                        voice.play('enroll_ok')
                        return
                    txt('人脸录入 %d/3' % sample)
                    frame = capture_now()
                    send_photo(frame)
                elif cmd == 'RETRY':
                    log.info('收到 RETRY, 重拍')
                    frame = capture_now()
                    send_photo(frame)
                elif cmd == 'SKIP':
                    sample += 1
                    if sample > 3:
                        _finish_fp(k210, fp, user, name, txt)
                        k210.write(b'DONE\n')
                        if user is not None and len(attendance.get_face_samples(user['id'])) == 0:
                            attendance.delete_user(user['id'])
                        return
                    txt('人脸录入 %d/3' % sample)
                    frame = capture_now()
                    send_photo(frame)
                elif cmd == 'ABORT':
                    k210.write(b'DONE\n')
                    if user is not None and len(attendance.get_face_samples(user['id'])) == 0:
                        attendance.delete_user(user['id'])
                    voice.play('enroll_abort')
                    log.info('录入已取消')
                    return
    finally:
        try:
            picam2.stop()
            picam2.close()
        except Exception:
            pass


def _finish_fp(k210, fp, user, name, txt):
    """人脸录入完成后, 有 AS608 则录指纹(语音+屏幕提示)"""
    if fp is None:
        txt('无指纹模块')
        log.info('无 AS608, 跳过指纹')
        return
    if user is None:
        log.warning('无人脸样本, 跳过指纹')
        return
    slot = attendance.next_fp_slot()
    txt('请按指纹 槽位%d' % slot)
    voice.play('fp_press')

    def on_first():
        voice.play('fp_again')
        txt('再按一次')

    def on_again():
        voice.play('fp_again')

    try:
        code, sid = fp.enroll(slot, timeout=15, on_first=on_first, on_again=on_again)
    except Exception as e:
        log.warning('指纹录入异常: %s', e)
        code, sid = -99, None
    if code == 0 and sid is not None:
        attendance.add_fingerprint(user['id'], sid)
        attendance.log_action(name, '录入指纹', '槽位%d' % sid)
        voice.play('fp_enroll_ok')
        txt('指纹OK 槽位%d' % sid)
        log.info('指纹录入成功: %s 槽位%d', name, sid)
    else:
        voice.play('fp_enroll_fail')
        txt('指纹失败 code=%s' % code)
        log.warning('指纹录入失败: %s code=%s', name, code)



def detect_ports():
    """返回 (k210_ser, fp608_or_None): 只有一个串口时=纯K210, 绝不探测AS608(避免给K210发垃圾)"""
    ports = find_serial_ports()
    if not ports:
        return None, None
    if len(ports) < 2:
        # 只有K210, 不探测
        try:
            return open_serial(ports[0]), None
        except Exception:
            return None, None
    fp = None
    rest = []
    for p in ports:
        f = None
        try:
            f = as608.AS608(p, baud=FP_BAUD)
            if f.verify_password():
                fp = f
                log.info('检测到 AS608 指纹: %s', p)
                continue
            f.close()
        except Exception:
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass
        rest.append(p)
    k210 = None
    for p in rest:
        try:
            k210 = open_serial(p)
            break
        except Exception:
            pass
    return k210, fp



def get_local_ip():
    """取本机非回环 IPv4; 优先热点地址/无线网卡(手机连热点后填这个IP)"""
    import socket, subprocess
    ips = []
    try:
        out = subprocess.check_output(['ip', '-4', '-o', 'addr', 'show'], text=True)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1] != 'lo':
                ips.append((parts[1], parts[3].split('/')[0]))
    except Exception:
        pass
    for iface, ip in ips:          # 热点模式 10.42.x.x 最优先
        if ip.startswith('10.42.'):
            return ip
    for iface, ip in ips:          # 其次无线(手机热点)
        if iface.startswith('wlan'):
            return ip
    for iface, ip in ips:          # 最后有线
        if iface.startswith('eth'):
            return ip
    return None



def reminder_loop():
    """日常(window)模式: 每段打卡结束前1分钟, 放大音量提醒'请及时签退'(有人在场才播)"""
    reminded = {}
    while True:
        try:
            mode = attendance.get_punch_mode()
            if mode == 'window':
                windows, dl = attendance.get_windows_config()
                now = datetime.datetime.now()
                m = now.hour * 60 + now.minute
                today = now.date().isoformat()
                for i, (st, en) in enumerate(windows):
                    if 0 < en - m <= 1 and reminded.get(('w', i)) != today:
                        if attendance.current_presence():
                            log.info('时段%d即将结束, 语音提醒', i + 1)
                            voice.play_loud('remind_end')
                            attendance.log_action('系统', '语音提醒', '时段%d结束前1分钟' % (i + 1))
                        reminded[('w', i)] = today
                if 0 < dl - m <= 1 and reminded.get(('d', 0)) != today:
                    if attendance.current_presence():
                        log.info('最晚签退截止临近, 语音提醒')
                        voice.play_loud('remind_end')
                        attendance.log_action('系统', '语音提醒', '最晚签退前1分钟')
                    reminded[('d', 0)] = today
        except Exception as e:
            log.warning('提醒任务异常: %s', e)
        time.sleep(20)


def main():
    attendance.init_db()
    log.info('慧签 联动服务启动')
    voice.init()
    voice.play_seq(['jingle_start', 'welcome'])
    threading.Thread(target=reminder_loop, daemon=True).start()
    k210 = None
    fp = None
    while k210 is None:
        k210, fp = detect_ports()
        if k210 is None:
            log.info('等待串口设备...')
            time.sleep(3)
    log.info('K210 串口: %s', k210.port)
    try:
        with open('/tmp/k210_port', 'w') as _f:
            _f.write(k210.port)
    except Exception:
        pass
    # 开机把本机 IP 发给 K210 显示(现场无电脑时看 K210 LCD 就知道访问地址)
    last_ip_sent = None
    last_ip_check = 0.0
    try:
        ip = get_local_ip()
        if ip:
            k210.write(('IP:%s\n' % ip).encode())
            last_ip_sent = ip
            log.info('已发送本机 IP 到 K210: %s', ip)
    except Exception as e:
        log.warning('发送 IP 失败: %s', e)
    if fp is not None:
        log.info('AS608 指纹就绪, 指纹模式开启')
    else:
        log.info('未检测到 AS608, 使用纯人脸打卡(指纹模块接入后自动切换)')
    last_trigger = 0.0
    buf = b''
    while True:
        # 每 10 秒检查, IP 变化则重发到 K210 LCD
        if time.time() - last_ip_check >= 10:
            last_ip_check = time.time()
            try:
                ip = get_local_ip()
                if ip and ip != last_ip_sent:
                    k210.write(('IP:%s\n' % ip).encode())
                    last_ip_sent = ip
                    log.info('IP 变化, 已更新 K210: %s', ip)
            except Exception:
                pass
        try:
            data = k210.read(64)
        except Exception:
            time.sleep(1)
            continue
        if not data:
            continue
        buf += data
        while b'\n' in buf:
            line, _, buf = buf.partition(b'\n')
            cmd = line.strip()
            if cmd == b'WINDOWS':
                mode = attendance.get_punch_mode()
                windows, dl = attendance.get_windows_config()
                def fm(m):
                    return '%02d:%02d' % (m // 60, m % 60)
                k210.write(('TXT:MODE %s\n' % ('UNLIMITED' if mode == 'unlimited' else 'LIMITED')).encode())
                for i, (a, b) in enumerate(windows, 1):
                    k210.write(('TXT:%d %s-%s\n' % (i, fm(a), fm(b))).encode())
                k210.write(('TXT:OUT BY %s\n' % fm(dl)).encode())
                k210.write(b'TXT_END\n')
                continue
            if cmd == b'RANK' or cmd.startswith(b'RANK:'):
                page = 1
                if b':' in cmd:
                    try:
                        page = max(1, int(cmd.split(b':')[1]))
                    except Exception:
                        page = 1
                log.info('收到 RANK page=%d', page)
                try:
                    arr = render_rank_page(page)
                    send_jpeg(k210, arr)
                    log.info('排行榜第%d页已发送到 K210', page)
                except Exception as e:
                    log.warning('排行榜生成失败: %s', e)
                continue
            if cmd == b'ENROLL_START':
                log.info('收到 ENROLL_START, 进入录入模式')
                run_enroll_session(k210, fp)
                log.info('录入模式结束, 回到打卡模式')
                continue
            if cmd.startswith(b'DBG:'):
                log.info('K210-DBG %s', cmd[4:].decode('utf-8', 'ignore'))
                continue
            if cmd != b'TRIGGER':
                continue
            now = time.time()
            if now - last_trigger < MIN_TRIGGER_INTERVAL:
                continue
            last_trigger = now
            log.info('收到 TRIGGER')
            user = None
            if fp is not None:
                voice.play('fp_please')
                code, fp_id = fp.verify(timeout=FP_TIMEOUT)
                if code == 0 and fp_id is not None:
                    user = attendance.get_user_by_fp_id(fp_id)
                    if user is None:
                        log.warning('指纹ID %d 未绑定用户', fp_id)
                        voice.play('fp_fail')
                        k210.write(b'FAIL\n')
                        continue
                    log.info('指纹通过: %s (fp_id=%d)', user['name'], fp_id)
                else:
                    log.info('指纹未通过(code=%s)%s', code, ', 回退纯人脸' if not FP_STRICT else '')
                    if FP_STRICT:
                        voice.play('fp_fail')
                        k210.write(b'FAIL\n')
                        continue
            action = random.choice(tuple(LIVENESS_ACTIONS))
            action_info = LIVENESS_ACTIONS[action]
            voice.play(action_info['voice'])
            # 给用户听清随机指令并开始动作，再进入连续抓拍窗口。
            time.sleep(LIVENESS_PROMPT_DELAY)
            frames = capture_burst()
            if user is not None:
                result, err2 = process_burst(frames, confirm_user_id=user['id'], method='fp+face', action=action)
            else:
                result, err2 = process_burst(frames, method='face', action=action)
            if result and result['ok']:
                log.info('打卡成功: %s %s dist=%.2f', result['name'], result['kind'], result['dist'])
                if result['kind'] == 'in':
                    if attendance.is_first_in_today():
                        log.info('当天第一位签到, 播放问候')
                        voice.play_seq(['jingle_in', 'first_in', 'checkin_ok'])
                    else:
                        voice.play_seq(['jingle_in', 'checkin_ok'])
                else:
                    if datetime.datetime.now().hour >= 21:
                        voice.play_seq(['jingle_out', 'checkout_ok', 'late_out'])
                    else:
                        voice.play_seq(['jingle_out', 'checkout_ok'])
                k210.write(('OK:%s\n' % result['kind']).encode())
            else:
                if err2 == 'CLOSE':
                    log.warning('打卡失败: 脸被截断, 提示退后')
                    voice.play('step_back')
                    k210.write(b'CLOSE\n')
                else:
                    log.warning('打卡失败: %s', err2)
                    if '不在可打卡' in str(err2):
                        voice.play('not_in_window')
                    elif '活体验证失败' in str(err2):
                        voice.play(action_info['voice'])
                    elif '未能提取' in str(err2) or '未检测到人脸' in str(err2):
                        voice.play('face_please')
                    else:
                        voice.play('fail_retry')
                    k210.write(b'FAIL\n')


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda s, f: os._exit(0))
    try:
        main()
    except KeyboardInterrupt:
        log.info('退出')
