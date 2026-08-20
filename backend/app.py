"""慧签 后端 Flask API (开发骨架)"""
import os, io, re, shutil, time, datetime, sqlite3
import cv2, numpy as np
import threading
from functools import wraps
from flask import Flask, g, request, jsonify, send_from_directory
import face_engine, attendance
from auth_middleware import require_admin, require_super_admin

BASE = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.path.join(BASE, 'static', 'photos')

app = Flask(__name__)
attendance.init_db()


_cam_lock = threading.Lock()


def capture_now():
    """用 Pi5 CSI 摄像头现拍一张, 返回 BGR (带锁+重试, 防止摄像头被占用)"""
    with _cam_lock:
        for attempt in range(3):
            try:
                from picamera2 import Picamera2
                picam2 = Picamera2()
                cfg = picam2.create_still_configuration(main={'size': (1280, 720)})
                picam2.configure(cfg)
                picam2.start()
                time.sleep(1.2)
                arr = picam2.capture_array()          # RGB
                picam2.stop()
                picam2.close()
                return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.0)


def save_photo(bgr):
    os.makedirs(PHOTO_DIR, exist_ok=True)
    fn = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.jpg'
    cv2.imwrite(os.path.join(PHOTO_DIR, fn), bgr)
    return fn


def parse_image(req):
    """从 multipart 读图, 返回 BGR 或 None"""
    f = req.files.get('image')
    if f is None:
        return None
    data = f.read()
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


@app.post('/api/enroll')
@require_admin
def enroll():
    """录入: multipart name + image; 照片存 face_library/<姓名>/, 特征存 face_samples(可多张)"""
    name = (request.form.get('name') or '').strip()
    img = parse_image(request)
    if not name or img is None:
        return jsonify(ok=False, msg='缺少 name 或 image'), 400
    boxes = face_engine.detect_faces(img)
    if not boxes:
        return jsonify(ok=False, msg='未检测到人脸'), 400
    emb = face_engine.get_embedding_multi(img, boxes[0])
    if emb is None:
        return jsonify(ok=False, msg='未能提取人脸特征'), 400
    user = attendance.add_user(name)
    folder = os.path.join(attendance.FACE_LIB, safe_folder(name))
    os.makedirs(folder, exist_ok=True)
    n = len(attendance.get_face_samples(user['id'])) + 1
    rel = os.path.join('face_library', safe_folder(name), 'sample_%02d.jpg' % n).replace(os.sep, '/')
    cv2.imwrite(os.path.join(BASE, rel), img)
    attendance.add_face_sample(user['id'], emb.tolist(), rel)
    attendance.log_action(name, '录入人脸', '样本 %d %s' % (n, rel))
    return jsonify(ok=True, name=name, user_id=user['id'], samples=n, photo=rel)


@app.post('/api/punch')
def punch():
    """打卡: 可选传 image, 否则现拍(最多重试5帧); 返回 结果/照片/是否眨眼"""
    uploaded = parse_image(request)
    attempts = 1 if uploaded is not None else 5
    img, emb, box = None, None, None
    for _ in range(attempts):
        img = uploaded if uploaded is not None else capture_now()
        boxes = face_engine.detect_faces(img)
        if not boxes:
            if uploaded is not None:
                return jsonify(ok=False, msg='未检测到人脸')
            continue
        box = boxes[0]
        emb = face_engine.get_embedding(face_engine.crop_face(img, box))
        if emb is not None:
            break
    if emb is None:
        return jsonify(ok=False, msg='未能提取人脸特征')
    lms = face_engine.face_landmarks(img)
    blink = face_engine.is_blinking(lms[0]) if lms else False
    if lms and blink:
        return jsonify(ok=False, msg='请睁开眼睛(活体检测)')
    user, dist = attendance.match_face(emb)
    if user is None:
        return jsonify(ok=False, msg='未识别', dist=round(dist, 3))
    kind = attendance.next_kind(user['id'])
    if kind is None:
        return jsonify(ok=False, msg='当前不在可打卡时间', dist=round(dist, 3))
    photo = save_photo(img)
    ok, info = attendance.punch(user['id'], kind, photo)
    if not ok:
        return jsonify(ok=False, msg=info, dist=round(dist, 3))
    return jsonify(ok=ok, name=user['name'], kind=kind, dist=round(dist, 3),
                   blink=blink, record_id=info, photo=photo, time=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@app.get('/api/users')
@require_admin
def users():
    conn = attendance.get_db()
    rows = conn.execute('''
        SELECT u.id, u.name, u.role, u.created_at,
               (SELECT COUNT(*) FROM face_samples s WHERE s.user_id = u.id) AS sample_count,
               (SELECT COUNT(*) FROM fingerprints f WHERE f.user_id = u.id) AS fp_count
        FROM users u ORDER BY u.id''').fetchall()
    conn.close()
    return jsonify(ok=True, users=[dict(r) for r in rows])


@app.get('/api/records')
@require_admin
def records():
    uid = request.args.get('user_id', type=int)
    day = request.args.get('date')
    conn = attendance.get_db()
    sql = 'SELECT r.id, r.user_id, u.name, r.punch_time, r.kind, r.photo FROM records r LEFT JOIN users u ON u.id=r.user_id'
    args = []
    if uid:
        sql += ' WHERE r.user_id=?'
        args.append(uid)
        if day:
            sql += ' AND date(r.punch_time)=?'
            args.append(day)
    elif day:
        sql += ' WHERE date(r.punch_time)=?'
        args.append(day)
    sql += ' ORDER BY r.punch_time DESC LIMIT 200'
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return jsonify(ok=True, records=[dict(r) for r in rows])


@app.get('/api/weekly')
@require_admin
def weekly():
    uid = request.args.get('user_id', type=int)
    ws = request.args.get('week_start')          # YYYY-MM-DD 周一起始
    if uid is None:
        return jsonify(ok=False, msg='需要 user_id'), 400
    import datetime as dt
    week = dt.date.fromisoformat(ws) if ws else None
    return jsonify(ok=True, stats=attendance.weekly_stats(uid, week))


@app.get('/static/photos/<path:fn>')
def photo(fn):
    return send_from_directory(PHOTO_DIR, fn)


@app.get('/api/health')
def health():
    return jsonify(ok=True, msg='慧签后端运行中')


# ==================== 小程序学生端接口 ====================
# 学生端只使用登录后绑定的 openid，不复用管理员 Bearer Token。
def require_student(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        openid = (request.args.get('openid') or request.headers.get('X-MiniProgram-OpenID') or '').strip()
        if not openid:
            return jsonify(ok=False, code='STUDENT_OPENID_REQUIRED', msg='缺少小程序 openid'), 401
        user = attendance.get_user_by_openid(openid)
        if user is None:
            return jsonify(ok=False, code='STUDENT_NOT_BOUND', msg='小程序尚未绑定学生账号'), 401
        g.student_openid = openid
        g.student_user = user
        return view(*args, **kwargs)
    return wrapped


def _student_public_user(user):
    data = dict(user)
    data.pop('password', None)
    return data


@app.get('/api/student/me')
@require_student
def student_me():
    return jsonify(ok=True, user=_student_public_user(g.student_user))


@app.get('/api/student/records')
@require_student
def student_records():
    """返回当前绑定学生自己的打卡记录，不能通过参数读取其他用户。"""
    day = request.args.get('date')
    conn = attendance.get_db()
    sql = ('SELECT r.id, r.user_id, u.name, r.punch_time, r.kind, r.photo, r.duration '
           'FROM records r LEFT JOIN users u ON u.id=r.user_id WHERE r.user_id=?')
    args = [g.student_user['id']]
    if day:
        sql += ' AND date(r.punch_time)=?'
        args.append(day)
    sql += ' ORDER BY r.punch_time DESC LIMIT 200'
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return jsonify(ok=True, records=[dict(row) for row in rows])


@app.get('/api/student/weekly')
@require_student
def student_weekly():
    """返回当前绑定学生自己的周统计。"""
    ws = request.args.get('week_start')
    try:
        week = datetime.date.fromisoformat(ws) if ws else None
    except ValueError:
        return jsonify(ok=False, code='WEEK_START_INVALID', msg='week_start 日期格式错误'), 400
    return jsonify(ok=True, stats=attendance.weekly_stats(g.student_user['id'], week))


@app.get('/api/student/rank')
@require_student
def student_rank():
    """返回学生排行榜；不接受 user_id，当前用户由 openid 决定。"""
    ws = request.args.get('week_start')
    try:
        week = datetime.date.fromisoformat(ws) if ws else None
    except ValueError:
        return jsonify(ok=False, code='WEEK_START_INVALID', msg='week_start 日期格式错误'), 400
    summary = attendance.weekly_summary(week)
    for item in summary['users']:
        item['days'] = len(attendance.weekly_stats(item['user_id'], week).get('days', []))
    current_id = int(g.student_user['id'])
    current = next((item for item in summary['users'] if int(item['user_id']) == current_id), None)
    return jsonify(ok=True, stats=summary, current_user=current)


@app.get('/api/student/presence')
@require_student
def student_presence():
    """只返回当前学生是否在场，避免学生端获得全员实时信息。"""
    users = attendance.current_presence()
    current_id = int(g.student_user['id'])
    current = next((item for item in users if int(item['user_id']) == current_id), None)
    return jsonify(ok=True, present=current is not None, user=current)




def _admin_actor():
    return 'admin:' + g.admin['username']


@app.post('/api/admin/auth/login')
def admin_login():
    data = request.get_json(silent=True) or {}
    account = attendance.authenticate_admin(data.get('username'), data.get('password'))
    if account is None:
        return jsonify(ok=False, code='ADMIN_CREDENTIALS_INVALID', msg='administrator credentials are invalid'), 401
    token, session = attendance.issue_admin_session(account['id'])
    attendance.log_action('admin:' + account['username'], 'administrator login', 'session issued')
    return jsonify(
        ok=True,
        token=token,
        expires_at=session['expires_at'],
        admin=attendance.public_admin(account),
    )


@app.post('/api/admin/auth/logout')
@require_admin
def admin_logout():
    attendance.revoke_admin_session(g.admin_session['session_id'])
    attendance.log_action(_admin_actor(), 'administrator logout', 'session revoked')
    return jsonify(ok=True)


@app.get('/api/admin/auth/me')
@require_admin
def admin_me():
    return jsonify(ok=True, admin=attendance.public_admin(g.admin))


@app.get('/api/admin/accounts')
@require_super_admin
def admin_accounts():
    return jsonify(ok=True, admins=[attendance.public_admin(row) for row in attendance.list_admins()])


@app.post('/api/admin/accounts')
@require_super_admin
def create_admin_account():
    data = request.get_json(silent=True) or {}
    account, error = attendance.create_admin(
        data.get('username'), data.get('password'), attendance.ADMIN_TYPE_REGULAR
    )
    if error == 'USERNAME_INVALID':
        return jsonify(ok=False, code=error, msg='username must use 3 to 32 letters, digits, underscores, or hyphens'), 400
    if error == 'PASSWORD_INVALID':
        return jsonify(ok=False, code=error, msg='password does not meet administrator password requirements'), 400
    if error == 'ADMIN_ACCOUNT_EXISTS':
        return jsonify(ok=False, code=error, msg='administrator username already exists'), 409
    attendance.log_action(_admin_actor(), 'create administrator', 'username=' + account['username'])
    return jsonify(ok=True, admin=attendance.public_admin(account)), 201


@app.post('/api/admin/accounts/<int:admin_id>/status')
@require_super_admin
def update_admin_status(admin_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('enabled'), bool):
        return jsonify(ok=False, code='ENABLED_REQUIRED', msg='enabled must be a boolean'), 400
    target = attendance.get_admin_by_id(admin_id)
    if target is None:
        return jsonify(ok=False, code='ADMIN_NOT_FOUND', msg='administrator account was not found'), 404
    if attendance.is_super_admin(target):
        return jsonify(ok=False, code='SUPER_ADMIN_PROTECTED', msg='super administrator accounts can only be changed on the Raspberry Pi'), 403
    account, error = attendance.set_admin_enabled(admin_id, data['enabled'])
    if error == 'ADMIN_NOT_FOUND':
        return jsonify(ok=False, code=error, msg='administrator account was not found'), 404
    if error == 'LAST_SUPER_ADMIN_PROTECTED':
        return jsonify(ok=False, code=error, msg='the last enabled super administrator cannot be disabled'), 409
    attendance.log_action(_admin_actor(), 'update administrator status', 'id=%d enabled=%d' % (admin_id, int(account['enabled'])))
    return jsonify(ok=True, admin=attendance.public_admin(account))


@app.post('/api/admin/accounts/<int:admin_id>/password')
@require_super_admin
def update_admin_password(admin_id):
    data = request.get_json(silent=True) or {}
    target = attendance.get_admin_by_id(admin_id)
    if target is None:
        return jsonify(ok=False, code='ADMIN_NOT_FOUND', msg='administrator account was not found'), 404
    if attendance.is_super_admin(target):
        return jsonify(ok=False, code='SUPER_ADMIN_PROTECTED', msg='super administrator passwords can only be changed by that account'), 403
    account, error = attendance.reset_admin_password(admin_id, data.get('password'))
    if error == 'ADMIN_NOT_FOUND':
        return jsonify(ok=False, code=error, msg='administrator account was not found'), 404
    if error == 'PASSWORD_INVALID':
        return jsonify(ok=False, code=error, msg='password does not meet administrator password requirements'), 400
    attendance.log_action(_admin_actor(), 'reset administrator password', 'id=%d' % admin_id)
    return jsonify(ok=True, admin=attendance.public_admin(account))


@app.post('/api/admin/auth/password')
@require_admin
def change_own_admin_password():
    data = request.get_json(silent=True) or {}
    account, error = attendance.change_own_admin_password(
        g.admin['id'], data.get('current_password'), data.get('new_password')
    )
    if error == 'CURRENT_PASSWORD_INVALID':
        return jsonify(ok=False, code=error, msg='current password is invalid'), 401
    if error == 'PASSWORD_INVALID':
        return jsonify(ok=False, code=error, msg='password does not meet administrator password requirements'), 400
    if error == 'ADMIN_NOT_FOUND':
        return jsonify(ok=False, code=error, msg='administrator account was not found'), 404
    attendance.log_action(_admin_actor(), 'change own administrator password', 'session revoked')
    return jsonify(ok=True, admin=attendance.public_admin(account))


@app.post('/api/bind')
def bind():
    """登录绑定: json {openid, name, password} 或 {openid, user_id, password}(需密码验证)"""
    data = request.get_json(silent=True) or {}
    openid = (data.get('openid') or '').strip()
    if not openid:
        return jsonify(ok=False, msg='缺少 openid'), 400
    row = attendance.get_user_by_openid(openid)
    if row is not None:
        d = dict(row); d.pop('password', None)
        return jsonify(ok=True, user=d, already=True)
    name = (data.get('name') or '').strip()
    uid = _int(data.get('user_id'))
    password = data.get('password') or ''
    user = None
    if name and password:
        user = attendance.verify_login(name, password)
    elif uid and password:
        u = attendance.get_user_by_id(uid)
        if u and attendance._check_pw(password, u['password']):
            user = u
    if user is None:
        return jsonify(ok=False, msg='姓名或密码错误'), 401
    attendance.bind_wechat(openid, user_id=user['id'])
    row = attendance.get_user_by_id(user['id'])
    d = dict(row); d.pop('password', None)
    return jsonify(ok=True, user=d, already=False)


@app.get('/api/me')
def me():
    """按 openid 查询我的信息(含角色)"""
    openid = request.args.get('openid', '').strip()
    if not openid:
        return jsonify(ok=False, msg='缺少 openid'), 400
    row = attendance.get_user_by_openid(openid)
    if row is None:
        return jsonify(ok=False, msg='未绑定'), 404
    d = dict(row)
    d.pop('password', None)
    return jsonify(ok=True, user=d)


@app.post('/api/set_role')
@require_admin
def set_role():
    """设置角色: json {user_id, role} (0=学生 1=管理员)"""
    data = request.get_json(silent=True) or {}
    uid = _int(data.get('user_id'))
    role = _int(data.get('role'))
    if uid is None or role is None:
        return jsonify(ok=False, msg='需要 user_id 和 role'), 400
    attendance.set_role(uid, role)
    return jsonify(ok=True)

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def safe_folder(name):
    """文件夹名安全化(防路径穿越, 允许中文)"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip()
    return s or 'user'

@app.get('/api/face_samples')
@require_admin
def face_samples_api():
    """查看人脸样本列表 (可选 user_id)"""
    uid = request.args.get('user_id', type=int)
    rows = attendance.get_face_samples(uid)
    return jsonify(ok=True, samples=[dict(r) for r in rows])


@app.post('/api/fingerprint/bind')
@require_admin
def fp_bind():
    """绑定指纹: json {user_id, fp_id} (fp_id=AS608 模块槽位号)"""
    data = request.get_json(silent=True) or {}
    uid = _int(data.get('user_id'))
    fpid = _int(data.get('fp_id'))
    if uid is None or fpid is None:
        return jsonify(ok=False, msg='需要 user_id 和 fp_id'), 400
    attendance.add_fingerprint(uid, fpid)
    return jsonify(ok=True)


@app.get('/api/fingerprint/list')
@require_admin
def fp_list():
    """指纹映射列表"""
    return jsonify(ok=True, fingerprints=[dict(r) for r in attendance.list_fingerprints()])


@app.post('/api/rename')
@require_admin
def rename():
    """改名: json {user_id, name}; 同步改数据库 + face_library 文件夹"""
    data = request.get_json(silent=True) or {}
    uid = _int(data.get('user_id'))
    name = (data.get('name') or '').strip()
    if uid is None or not name:
        return jsonify(ok=False, msg='需要 user_id 和 name'), 400
    user = attendance.get_user_by_id(uid)
    if user is None:
        return jsonify(ok=False, msg='用户不存在'), 404
    old_dir = os.path.join(attendance.FACE_LIB, safe_folder(user['name']))
    new_dir = os.path.join(attendance.FACE_LIB, safe_folder(name))
    if os.path.isdir(old_dir) and old_dir != new_dir and not os.path.isdir(new_dir):
        os.rename(old_dir, new_dir)
    conn = attendance.get_db()
    try:
        conn.execute('UPDATE users SET name=? WHERE id=?', (name, uid))
        conn.execute("UPDATE face_samples SET photo=replace(photo, ?, ?) WHERE user_id=?",
                     ('face_library/' + safe_folder(user['name']) + '/',
                      'face_library/' + safe_folder(name) + '/', uid))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify(ok=False, msg='该名字已存在'), 409
    conn.close()
    return jsonify(ok=True)

@app.get('/api/stats')
@require_admin
def stats_api():
    """本周每人统计(打卡次数/总时长/排名)"""
    import datetime as dt
    ws = request.args.get('week_start')
    week = dt.date.fromisoformat(ws) if ws else None
    return jsonify(ok=True, stats=attendance.weekly_summary(week))


@app.get('/api/activity')
@require_admin
def activity_api():
    """操作日志(谁/什么时间/做了什么)"""
    return jsonify(ok=True, logs=[dict(r) for r in attendance.list_activity()])


@app.get('/api/presence')
@require_admin
def presence_api():
    """实时在场人数(当天最后一条打卡为 in 且未超时)"""
    users = attendance.current_presence()
    return jsonify(ok=True, count=len(users), users=users)



@app.post('/api/delete_user')
@require_admin
def delete_user_api():
    """删除用户: 级联删数据库(人脸/指纹/记录) + 删 face_library 文件夹"""
    data = request.get_json(silent=True) or {}
    uid = _int(data.get('user_id'))
    if uid is None:
        return jsonify(ok=False, msg='需要 user_id'), 400
    user = attendance.get_user_by_id(uid)
    if user is None:
        return jsonify(ok=False, msg='用户不存在'), 404
    attendance.delete_user(uid)   # 级联删 face_samples/fingerprints/records
    folder = os.path.join(attendance.FACE_LIB, safe_folder(user['name']))
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    attendance.log_action(user['name'], '删除用户', 'id=%d' % uid)
    return jsonify(ok=True)



def _fmt_min(m):
    return '%02d:%02d' % (m // 60, m % 60)


def _parse_min(s):
    parts = str(s).split(':')
    h = int(parts[0])
    mm = int(parts[1]) if len(parts) > 1 else 0
    return h * 60 + mm


@app.post('/api/delete_face')
@require_admin
def delete_face_api():
    """只删人脸(保留用户/指纹/记录)"""
    data = request.get_json(silent=True) or {}
    uid = _int(data.get('user_id'))
    if uid is None:
        return jsonify(ok=False, msg='需要 user_id'), 400
    if attendance.get_user_by_id(uid) is None:
        return jsonify(ok=False, msg='用户不存在'), 404
    n = attendance.delete_face(uid)
    return jsonify(ok=True, removed=n)


@app.post('/api/delete_fp')
@require_admin
def delete_fp_api():
    """只删指纹绑定"""
    data = request.get_json(silent=True) or {}
    uid = _int(data.get('user_id'))
    if uid is None:
        return jsonify(ok=False, msg='需要 user_id'), 400
    removed = attendance.delete_fp(uid)
    return jsonify(ok=True, removed=removed)


@app.post('/api/fingerprint/enroll')
@require_admin
def fp_enroll_api():
    """录入指纹(服务器操AS608+语音提示): 用户按2次手指; 绑定到 user_id"""
    data = request.get_json(silent=True) or {}
    uid = _int(data.get('user_id'))
    if uid is None:
        return jsonify(ok=False, msg='需要 user_id'), 400
    user = attendance.get_user_by_id(uid)
    if user is None:
        return jsonify(ok=False, msg='用户不存在'), 404
    import as608, glob
    import voice as voice_mod
    ports = []
    for pat in ('/dev/ttyUSB*', '/dev/ttyACM*'):
        ports += sorted(glob.glob(pat))
    # 排除 K210 的端口(绝不给 K210 发探测垃圾)
    try:
        _kp = open('/tmp/k210_port').read().strip()
        if _kp in ports:
            ports.remove(_kp)
    except Exception:
        pass
    if len(ports) < 1:
        return jsonify(ok=False, msg='未检测到指纹模块, 请检查接线/供电(需第二块USB-TTL)')
    fp = None
    for p in ports:
        try:
            f = as608.AS608(p, baud=57600)
            if f.verify_password():
                fp = f
                break
            f.close()
        except Exception:
            pass
    if fp is None:
        return jsonify(ok=False, msg='未检测到指纹模块, 请检查接线/供电')
    voice_mod.init()   # 确认有模块后再初始化语音
    slot = attendance.next_fp_slot()
    voice_mod.play('fp_press')

    def on_first():
        voice_mod.play('fp_again')

    def on_again():
        voice_mod.play('fp_again')

    try:
        code, sid = fp.enroll(slot, timeout=15, on_first=on_first, on_again=on_again)
    finally:
        fp.close()
    if code == 0 and sid is not None:
        attendance.add_fingerprint(uid, sid)
        attendance.log_action(user['name'], '录入指纹', '槽位%d' % sid)
        voice_mod.play('fp_enroll_ok')
        return jsonify(ok=True, name=user['name'], fp_id=sid)
    voice_mod.play('fp_enroll_fail')
    return jsonify(ok=False, msg='指纹录入失败(code=%s), 请重试' % code)


@app.post('/api/login')
def login_api():
    """登录: name + password; 通过则绑定 openid(可选) 并返回用户"""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    password = data.get('password') or ''
    openid = (data.get('openid') or '').strip()
    if not name or not password:
        return jsonify(ok=False, msg='需要 name 和 password'), 400
    user = attendance.verify_login(name, password)
    if user is None:
        return jsonify(ok=False, msg='姓名或密码错误'), 401
    if openid:
        attendance.bind_wechat(openid, user_id=user['id'])
        user = attendance.get_user_by_id(user['id'])
    d = dict(user)
    d.pop('password', None)
    return jsonify(ok=True, user=d)


@app.post('/api/set_password')
@require_admin
def set_password_api():
    """管理员改密码: user_id + new_password (至少8位, 含两种字符)"""
    data = request.get_json(silent=True) or {}
    uid = _int(data.get('user_id'))
    if uid is None:
        return jsonify(ok=False, msg='需要 user_id'), 400
    if attendance.get_user_by_id(uid) is None:
        return jsonify(ok=False, msg='用户不存在'), 404
    new_pw = data.get('new_password') or ''
    ok, msg = attendance.set_password(uid, new_pw)
    if not ok:
        return jsonify(ok=False, msg=msg), 400
    user = attendance.get_user_by_id(uid)
    attendance.log_action(user['name'], '修改密码', 'id=%d' % uid)
    return jsonify(ok=True)




@app.get('/api/settings')
@require_admin
def settings_get():
    """打卡设置: 模式(unlimited/window) + 时间区间 + 签退截止"""
    mode = attendance.get_punch_mode()
    windows, dl = attendance.get_windows_config()
    return jsonify(ok=True, punch_mode=mode,
                   windows=[[_fmt_min(a), _fmt_min(b)] for a, b in windows],
                   out_deadline=_fmt_min(dl))


@app.post('/api/settings')
@require_admin
def settings_set():
    """保存打卡设置: json {punch_mode?, windows?:[['07:30','10:00'],...], out_deadline?:'23:00'}"""
    data = request.get_json(silent=True) or {}
    try:
        if 'punch_mode' in data:
            attendance.set_punch_mode(str(data['punch_mode']))
        if 'windows' in data and 'out_deadline' in data:
            windows = [[_parse_min(p[0]), _parse_min(p[1])] for p in data['windows']]
            attendance.set_windows_config(windows, _parse_min(str(data['out_deadline'])))
    except Exception as e:
        return jsonify(ok=False, msg=str(e)), 400
    return jsonify(ok=True)

if __name__ == '__main__':
    import signal
    signal.signal(signal.SIGTERM, lambda s, f: os._exit(0))
    app.run(host='0.0.0.0', port=8000)
