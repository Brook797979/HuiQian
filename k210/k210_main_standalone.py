# 慧签 K210 系统面板 v9 (KEY导航 + 触摸确认)
# 纯按键操作: 短按切换/长按进入执行 (触摸已全局移除)
import sensor, image, lcd, time, gc
try:
    import KPU as kpu
except ImportError:
    try:
        import kpu
    except ImportError:
        from maix import KPU as kpu
from machine import UART
from Maix import GPIO
from fpioa_manager import fm

BUZZ_PIN = 9
KEY_PIN = 16
UART_TX_PIN = 6
UART_RX_PIN = 7
LONG_PRESS_MS = 700
MENU_POS = [74, 110, 146, 182]

fm.register(BUZZ_PIN, fm.fpioa.GPIO0)
buzzer = GPIO(GPIO.GPIO0, GPIO.OUT)
try:
    fm.register(KEY_PIN, fm.fpioa.GPIO1, force=True)
    key = GPIO(GPIO.GPIO1, GPIO.IN, GPIO.PULL_UP)
except Exception:
    key = None
fm.register(UART_TX_PIN, fm.fpioa.UART1_TX, force=True)
fm.register(UART_RX_PIN, fm.fpioa.UART1_RX, force=True)
uart = UART(UART.UART1, 115200, 8, None, 0, read_buf_len=4096)

lcd.init()
sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)
sensor.set_vflip(1)

task = kpu.load(0x300000)
anchor = (1.889, 2.5245, 2.9465, 3.94056, 3.99987, 5.3658, 5.155437, 6.92275, 6.718375, 9.01025)
kpu.init_yolo2(task, 0.5, 0.3, 5, anchor)


def beep(times):
    # 无源蜂鸣器: 5kHz 方波驱动(实测最响)
    for _ in range(times):
        end = time.ticks_ms() + 200
        while time.ticks_ms() < end:
            buzzer.value(1)
            time.sleep_us(100)
            buzzer.value(0)
            time.sleep_us(100)
        time.sleep_ms(150)


def beep_long(ms=400):
    end = time.ticks_ms() + ms
    while time.ticks_ms() < end:
        buzzer.value(1)
        time.sleep_us(100)
        buzzer.value(0)
        time.sleep_us(100)


def show(text, y=0, color=lcd.WHITE, scale=2):
    try:
        lcd.draw_string(0, y, text, color, scale)
    except Exception:
        pass


def show_jpg(path):
    try:
        img = image.Image(path)
        lcd.display(img)
        del img
        gc.collect()
        return True
    except Exception:
        return False


# ---------- 按键 ----------
def wait_key_release():
    if key is not None:
        while key.value() == 0:
            time.sleep_ms(10)

def poll_key_event():
    if key is None:
        return None
    if key.value() == 0:
        time.sleep_ms(20)
        if key.value() == 0:
            p0 = time.ticks_ms()
            while key.value() == 0:
                if time.ticks_ms() - p0 > LONG_PRESS_MS:
                    wait_key_release()
                    return 'long'
                time.sleep_ms(5)
            return 'short'
    return None

def poll_key_press():
    if key is None:
        return False
    if key.value() == 0:
        time.sleep_ms(20)
        if key.value() == 0:
            t0 = time.ticks_ms()
            while key.value() == 0:
                if time.ticks_ms() - t0 > 2000:
                    wait_key_release()
                    return False
                time.sleep_ms(10)
            return True
    return False


# ---------- 串口 ----------
_rx = b''
def uart_pump():
    global _rx
    d = uart.read()
    if d:
        _rx += d

def recv_line(timeout_ms):
    global _rx
    t0 = time.ticks_ms()
    while time.ticks_ms() - t0 < timeout_ms:
        uart_pump()
        if b'\n' in _rx:
            line, _, _rx = _rx.partition(b'\n')
            return line.strip()
        time.sleep_ms(10)
    return None

def recv_bytes(n, timeout_ms):
    global _rx
    t0 = time.ticks_ms()
    while len(_rx) < n and time.ticks_ms() - t0 < timeout_ms:
        uart_pump()
        if time.ticks_ms() % 30 == 0:
            gc.collect()
        time.sleep_ms(5)
    if len(_rx) < n:
        _rx = b''
        return None
    data = _rx[:n]
    _rx = _rx[n:]
    return data

def uart_flush():
    global _rx
    t0 = time.ticks_ms()
    while time.ticks_ms() - t0 < 500:
        if not uart.read():
            break
    _rx = b''


# ---------- 面板 ----------
_panel_img = None

def show_panel(idx):
    global _panel_img
    if _panel_img is None:
        try:
            _panel_img = image.Image('/flash/panel.jpg')
        except Exception:
            _panel_img = None
    if _panel_img is not None:
        lcd.display(_panel_img)
    else:
        lcd.clear(lcd.BLACK)
        show('HuiQian')
    y = MENU_POS[idx] + 6
    lcd.draw_string(12, y, '>', lcd.GREEN, 2)

def enter_mode(zone_or_idx):
    global _panel_img
    _panel_img = None       # 进模式释放面板缓存, 腾出内存
    m = zone_or_idx
    if m == 0:
        face_mode()
    elif m == 1:
        enroll_mode()
    elif m == 2:
        rank_mode()
    else:
        windows_mode()


# ---------- 打卡模式 ----------
def face_mode():
    gc.collect()
    lcd.clear(lcd.BLACK)
    show('PUNCH MODE')
    show('KEY=EXIT', 200, lcd.WHITE, 1)
    try:
        sensor.run(1)
        time.sleep_ms(300)
        for _ in range(5):
            sensor.snapshot()
    except Exception:
        pass
    cooldown_until = 0
    last_detect = 0
    while True:
        if poll_key_press():
            try:
                sensor.run(0)
            except Exception:
                pass
            return
        if time.ticks_ms() < cooldown_until:
            time.sleep_ms(50)
            continue
        # 检测节流 200ms: 降低CPU占用, 让按键更跟手
        if time.ticks_ms() - last_detect < 200:
            time.sleep_ms(30)
            continue
        last_detect = time.ticks_ms()
        img = sensor.snapshot()
        objs = kpu.run_yolo2(task, img)
        if not objs:
            continue
        show('FACE! SHAKE')
        beep(2)
        uart.write(b'TRIGGER\n')
        deadline = time.ticks_ms() + 60000
        result = None
        kind = None
        while time.ticks_ms() < deadline:
            d = uart.read()
            if d:
                try:
                    s = d.decode('utf-8')
                except Exception:
                    s = str(d)
                if 'OK' in s:
                    result = 'OK'
                    kind = 'in' if ':in' in s else 'out'
                    break
                if 'FAIL' in s:
                    result = 'FAIL'
                    break
                if 'CLOSE' in s:
                    result = 'CLOSE'
                    break
            time.sleep_ms(50)
        if result == 'OK':
            if kind == 'in':
                show_jpg('/flash/in.jpg'); beep(2)
            else:
                show_jpg('/flash/out.jpg'); beep(2); time.sleep_ms(250); beep_long(400)
            time.sleep_ms(1800)
            lcd.clear(lcd.BLACK)
            show('PUNCH MODE')
            show('KEY=EXIT', 200, lcd.WHITE, 1)
            cooldown_until = time.ticks_ms() + 1500
        elif result == 'CLOSE':
            show('退后一点'); beep_long()
            cooldown_until = time.ticks_ms() + 1500
        else:
            show('FAIL 滴3声 等5秒'); beep(3)
            cooldown_until = time.ticks_ms() + 5000
        while uart.read():
            pass


# ---------- 录入模式 ----------
MENU3 = ['RETRY', 'CONFIRM', 'ABORT']

def draw_menu3(idx, sample):
    show('SHOT %d/3' % sample, 0, lcd.WHITE, 2)
    show('KEY 短按切换 长按执行', 192, lcd.WHITE, 1)
    x = 20
    for i in range(3):
        color = lcd.GREEN if i == idx else lcd.WHITE
        try:
            lcd.draw_string(x, 214, MENU3[i], color, 1)
        except Exception:
            pass
        x += 100

def enroll_menu_tap(sample, idx):
    # 纯按键: 短按切换 重拍/确认/取消, 长按执行高亮项
    t0 = time.ticks_ms()
    c = 0
    while time.ticks_ms() - t0 < 60000:
        c += 1
        if c >= 50:
            c = 0
            gc.collect()
        ev = poll_key_event()
        if ev == 'short':
            idx = (idx + 1) % 3
            draw_menu3(idx, sample)
            beep(1)
        elif ev == 'long':
            beep(1)
            return MENU3[idx]
        time.sleep_ms(20)
    return 'CONFIRM'

_enroll_num = ''

def enroll_mode():
    global _enroll_num
    gc.collect()
    _enroll_num = ''
    uart_flush()
    uart.write(b'ENROLL_START\n')
    sample = 1
    idx = 0
    _t0 = time.ticks_ms()
    while True:
        if time.ticks_ms() - _t0 > 120000:   # 看门狗: 120s 强制退出
            show('录入超时'); time.sleep_ms(800)
            return
        line = recv_line(15000)
        if line is None:
            return
        if line == b'DONE':
            show('DONE! 完成'); beep(2); time.sleep_ms(1200)
            return
        if line.startswith(b'TXT:'):
            try:
                s = line[4:].decode('utf-8', 'ignore')
                if s.startswith('编号'):
                    _enroll_num = s
                    show(s, 0, lcd.YELLOW, 2)
                else:
                    show(s, 26, lcd.WHITE, 1)
            except Exception:
                pass
            continue
        if not line.startswith(b'IMG:'):
            continue
        try:
            n = int(line[4:])
        except Exception:
            continue
        data = recv_bytes(n, 20000)
        if data is None:
            uart.write(b'RETRY\n'); time.sleep_ms(300)
            continue
        try:
            f = open('/flash/tmp.jpg', 'wb')
            f.write(data)
            f.close()
            del data
            img = image.Image('/flash/tmp.jpg')
            lcd.display(img)
            del img
            gc.collect()
        except Exception:
            uart.write(b'RETRY\n'); time.sleep_ms(300)
            continue
        if _enroll_num:
            show(_enroll_num, 0, lcd.YELLOW, 1)
        draw_menu3(idx, sample)
        choice = enroll_menu_tap(sample, idx)
        uart.write((choice + '\n').encode())
        beep(1)
        _rx = b''          # 只清Python侧缓存, 不读串口(避免吞掉Pi的回复图)
        gc.collect()
        if choice == 'ABORT':
            return
        if choice == 'CONFIRM':
            sample += 1


# ---------- 打卡时段模式 ----------
def windows_mode():
    gc.collect()
    uart_flush()
    uart.write(b'WINDOWS\n')
    lines = []
    t0 = time.ticks_ms()
    while time.ticks_ms() - t0 < 8000:
        line = recv_line(3000)
        if line is None:
            break
        if line == b'TXT_END':
            break
        if line.startswith(b'TXT:'):
            lines.append(line[4:].decode('utf-8', 'ignore'))
    lcd.clear(lcd.BLACK)
    show('PUNCH WINDOWS', 0, lcd.WHITE, 2)
    y = 46
    if not lines:
        lines = ['(no data)']
    for i, ln in enumerate(lines[:9]):
        if i == 0 and ln.startswith('MODE'):
            color = lcd.GREEN if 'UNLIMITED' in ln else lcd.YELLOW
            show(ln, y, color, 2)
            y += 30
        else:
            show(ln, y, lcd.WHITE, 1)
            y += 18
    show('KEY=EXIT', 214, lcd.WHITE, 1)
    t1 = time.ticks_ms()
    while time.ticks_ms() - t1 < 60000:
        if poll_key_event() is not None:
            return
        time.sleep_ms(30)


# ---------- 排行榜模式 ----------
def rank_mode():
    gc.collect()
    uart_flush()
    page = 1
    while True:
        uart.write(('RANK:%d\n' % page).encode())
        line = recv_line(10000)
        if line is None or not line.startswith(b'IMG:'):
            show('RANK TIMEOUT'); time.sleep_ms(800)
            return
        try:
            n = int(line[4:])
        except Exception:
            return
        data = recv_bytes(n, 20000)
        if data is None:
            show('IMG TIMEOUT'); time.sleep_ms(800)
            return
        try:
            f = open('/flash/rank.jpg', 'wb')
            f.write(data)
            f.close()
            del data
            img = image.Image('/flash/rank.jpg')
            lcd.display(img)
            del img
            gc.collect()
        except Exception:
            show('IMG ERR'); time.sleep_ms(800)
            return
        t0 = time.ticks_ms()
        while time.ticks_ms() - t0 < 60000:
            ev = poll_key_event()
            if ev == 'short':
                page += 1
                break
            if ev == 'long':
                return
            time.sleep_ms(30)


# ===== 启动 =====
beep(3)      # 开机自检: 滴3声 = 蜂鸣器正常
gc.collect()
idx = 0
last_ip = ''
ip_timer = 0
_gc_cnt = 0
_prev_idx = -1
while True:
    _gc_cnt += 1
    if _gc_cnt >= 40:
        _gc_cnt = 0
        gc.collect()
    if idx != _prev_idx:
        show_panel(idx)
        _prev_idx = idx
    if time.ticks_ms() - ip_timer > 5000:
        ip_timer = time.ticks_ms()
        d = uart.read()
        if d:
            try:
                s = d.decode('utf-8','ignore').strip()
                if s.startswith('IP:'):
                    last_ip = s[3:]
                    lcd.draw_string(200, 8, 'IP ' + last_ip, lcd.WHITE, 1)
            except Exception:
                pass
    if last_ip:
        lcd.draw_string(200, 8, 'IP ' + last_ip, lcd.WHITE, 1)
    # 主界面纯按键: 短按切换 / 长按进入 (触摸易误触, 已关闭)
    ev = poll_key_event()
    if ev == 'short':
        idx = (idx + 1) % 4
    elif ev == 'long':
        enter_mode(idx)
        lcd.clear(lcd.BLACK)
        _prev_idx = -1          # 从模式返回后强制重绘面板
        gc.collect()
    time.sleep_ms(30)
