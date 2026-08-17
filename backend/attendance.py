"""慧签 数据层: 用户 / 人脸样本 / 指纹映射 / 打卡记录 / 周统计

存储设计(保持数据库清晰整洁):
- 人脸照片:  face_library/<姓名>/sample_XX.jpg   (展示/留证, 不入库)
- 人脸特征:  face_samples 表, 每人多行, 打卡时快速比对
- 指纹模板:  AS608 模块内部 flash (模块自管), 数据库只存 fp_id -> user 映射
- 打卡记录:  records 表
- 外键级联删除 + 索引, 无冗余字段
"""
import os, re, json, sqlite3, datetime, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'huiqian.db')
DEFAULT_PASSWORD = '123456qmx'
FACE_LIB = os.path.join(BASE, 'face_library')
PHOTO_DIR = os.path.join(BASE, 'static', 'photos')


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db():
    """建表(幂等) + 兼容旧库"""
    conn = get_db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      role INTEGER NOT NULL DEFAULT 0,
      wechat_openid TEXT UNIQUE,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS face_samples(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      embedding TEXT NOT NULL,
      photo TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_face_user ON face_samples(user_id);
    CREATE TABLE IF NOT EXISTS fingerprints(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      fp_id INTEGER NOT NULL UNIQUE,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_fp_user ON fingerprints(user_id);
    CREATE TABLE IF NOT EXISTS records(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      punch_time TEXT NOT NULL,
      kind TEXT NOT NULL,
      photo TEXT,
      method TEXT NOT NULL DEFAULT 'face',
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_rec_user_time ON records(user_id, punch_time);
    CREATE TABLE IF NOT EXISTS activity_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      log_time TEXT NOT NULL,
      user TEXT,
      action TEXT NOT NULL,
      detail TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_log_time ON activity_log(log_time);
    CREATE TABLE IF NOT EXISTS settings(
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    ''')
    # 兼容旧库: 移除 users.face_embedding 遗留列
    cols = [r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()]
    if 'face_embedding' in cols:
        conn.execute('ALTER TABLE users DROP COLUMN face_embedding')
    # records.duration 列(窗口模式存有效时长)
    rcols = [r[1] for r in conn.execute('PRAGMA table_info(records)').fetchall()]
    if 'duration' not in rcols:
        conn.execute('ALTER TABLE records ADD COLUMN duration INTEGER')
    # users.password 列(登录用) + 旧用户回填默认密码
    ucols = [r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()]
    if 'password' not in ucols:
        conn.execute('ALTER TABLE users ADD COLUMN password TEXT')
    conn.execute("UPDATE users SET password=? WHERE password IS NULL",
                 (_hash_pw(DEFAULT_PASSWORD),))
    conn.commit()
    conn.close()


def now_str():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ---------- 密码 ----------
def _hash_pw(pw, salt=None):
    """加盐哈希: 返回 'salt$hash'"""
    if salt is None:
        import os as _os
        salt = _os.urandom(8).hex()
    h = hashlib.sha256((salt + ':' + str(pw)).encode('utf-8')).hexdigest()
    return '%s$%s' % (salt, h)


def _check_pw(pw, stored):
    if not stored or '$' not in stored:
        return False
    salt, h = stored.split('$', 1)
    return h == hashlib.sha256((salt + ':' + str(pw)).encode('utf-8')).hexdigest()


def validate_password(pw):
    """密码规则: 至少8位, 至少包含两种字符(小写/大写/数字/特殊)"""
    if not pw or len(pw) < 8:
        return False, '密码至少 8 位'
    types = 0
    if any('a' <= c <= 'z' for c in pw): types += 1
    if any('A' <= c <= 'Z' for c in pw): types += 1
    if any('0' <= c <= '9' for c in pw): types += 1
    if any(not c.isalnum() for c in pw): types += 1
    if types < 2:
        return False, '密码需至少包含两种字符(字母/数字/符号)'
    return True, ''


def verify_login(name, password):
    """姓名+密码验证; 通过返回用户行, 否则 None"""
    conn = get_db()
    row = conn.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
    conn.close()
    if row is None:
        return None
    if _check_pw(password, row['password']):
        return row
    return None


def set_password(user_id, new_password):
    """设置密码(校验规则); 返回 (ok, msg)"""
    ok, msg = validate_password(new_password)
    if not ok:
        return False, msg
    conn = get_db()
    conn.execute('UPDATE users SET password=? WHERE id=?', (_hash_pw(new_password), int(user_id)))
    conn.commit()
    conn.close()
    return True, ''




# ---------- 用户 ----------
def add_user(name, role=0):
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO users(name, role, created_at, password) VALUES (?,?,?,?)',
                 (name, int(role), now_str(), _hash_pw(DEFAULT_PASSWORD)))
    conn.commit()
    row = conn.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
    conn.close()
    return row


def get_user_by_id(uid):
    conn = get_db()
    row = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return row


def get_user_by_name(name):
    conn = get_db()
    row = conn.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
    conn.close()
    return row


def get_user_by_openid(openid):
    conn = get_db()
    row = conn.execute('SELECT * FROM users WHERE wechat_openid=?', (openid,)).fetchone()
    conn.close()
    return row


def bind_wechat(openid, user_id=None, name=None):
    conn = get_db()
    if user_id is not None:
        row = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    elif name:
        row = conn.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
    else:
        row = None
    if row is None:
        conn.close()
        return None, '用户不存在, 请先由管理员录入'
    conn.execute('UPDATE users SET wechat_openid=? WHERE id=?', (openid, row['id']))
    conn.commit()
    row = conn.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
    conn.close()
    return row, None


def set_role(user_id, role):
    conn = get_db()
    conn.execute('UPDATE users SET role=? WHERE id=?', (int(role), int(user_id)))
    conn.commit()
    conn.close()


def delete_user(user_id):
    conn = get_db()
    conn.execute('DELETE FROM users WHERE id=?', (user_id,))  # 级联删 face_samples/fingerprints/records
    conn.commit()
    conn.close()


# ---------- 人脸样本 ----------
def add_face_sample(user_id, embedding, photo):
    conn = get_db()
    cur = conn.execute('INSERT INTO face_samples(user_id, embedding, photo, created_at) VALUES (?,?,?,?)',
                       (user_id, json.dumps(embedding), photo, now_str()))
    conn.commit()
    conn.close()
    return cur.lastrowid


def get_face_samples(user_id=None):
    conn = get_db()
    if user_id is not None:
        rows = conn.execute('SELECT * FROM face_samples WHERE user_id=? ORDER BY id', (user_id,)).fetchall()
    else:
        rows = conn.execute('SELECT * FROM face_samples ORDER BY id').fetchall()
    conn.close()
    return rows


def match_face(embedding, user_id=None, tolerance=0.6):
    """在所有样本(或仅指定用户)里找最近距离; 返回 (user_row, dist) 或 (None, best_dist)"""
    import numpy as np
    import face_recognition
    samples = get_face_samples(user_id)
    if not samples:
        return None, 99.0
    best_user, best_d, cache = None, 99.0, {}
    for s in samples:
        ref = np.array(json.loads(s['embedding']))
        d = float(face_recognition.face_distance([ref], embedding)[0])
        if d < best_d:
            best_d = d
            uid = s['user_id']
            if uid not in cache:
                cache[uid] = get_user_by_id(uid)
            best_user = cache[uid]
    if best_user is None or best_d > tolerance:
        return None, best_d
    return best_user, best_d


# ---------- 指纹映射 ----------
def add_fingerprint(user_id, fp_id):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO fingerprints(user_id, fp_id, created_at) VALUES (?,?,?)',
                 (user_id, int(fp_id), now_str()))
    conn.commit()
    conn.close()


def get_user_by_fp_id(fp_id):
    conn = get_db()
    row = conn.execute('SELECT u.* FROM fingerprints f JOIN users u ON u.id=f.user_id WHERE f.fp_id=?',
                       (int(fp_id),)).fetchone()
    conn.close()
    return row


def list_fingerprints():
    conn = get_db()
    rows = conn.execute('SELECT f.id, f.fp_id, f.created_at, u.name FROM fingerprints f JOIN users u ON u.id=f.user_id ORDER BY f.fp_id').fetchall()
    conn.close()
    return rows


# ---------- 设置(打卡模式/时间区间) ----------
DEFAULT_WINDOWS = [[450, 600], [615, 810], [840, 1080], [1110, 1350]]  # 07:30-10:00 10:15-13:30 14:00-18:00 18:30-22:30
DEFAULT_OUT_DEADLINE = 1380  # 23:00


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key, value))
    conn.commit()
    conn.close()


def get_punch_mode():
    return get_setting('punch_mode', 'unlimited')


def set_punch_mode(mode):
    if mode not in ('unlimited', 'window'):
        raise ValueError('mode must be unlimited or window')
    set_setting('punch_mode', mode)


def get_windows_config():
    import json
    w = get_setting('windows')
    if not w:
        return list(DEFAULT_WINDOWS), DEFAULT_OUT_DEADLINE
    try:
        data = json.loads(w)
        return [list(x) for x in data['windows']], int(data.get('deadline', DEFAULT_OUT_DEADLINE))
    except Exception:
        return list(DEFAULT_WINDOWS), DEFAULT_OUT_DEADLINE


def set_windows_config(windows, deadline):
    import json
    set_setting('windows', json.dumps({'windows': windows, 'deadline': int(deadline)}))


def _to_minutes(dt):
    return dt.hour * 60 + dt.minute


def _in_window(windows, m):
    """返回 (index,start,end,next_start_or_deadline) 若 m 在某个窗口内, 否则 None"""
    _, dl = get_windows_config()
    for i, (st, en) in enumerate(windows):
        if st <= m <= en:
            nx = windows[i + 1][0] if i + 1 < len(windows) else dl
            return i, st, en, nx
    return None


def _window_of(windows, m):
    """返回 m 所在窗口 index; 不在则 None"""
    for i, (st, en) in enumerate(windows):
        if st <= m <= en:
            return i
    return None


# ---------- 打卡记录 ----------
def next_kind(user_id, now=None):
    """模式感知: unlimited=当前行为; window=按时间区间, 无法打卡返回 None"""
    now = now or datetime.datetime.now()
    today = now.strftime('%Y-%m-%d')
    conn = get_db()
    row = conn.execute("SELECT kind, punch_time FROM records WHERE user_id=? AND date(punch_time)=? ORDER BY punch_time DESC LIMIT 1",
                       (user_id, today)).fetchone()
    conn.close()
    if get_punch_mode() == 'unlimited':
        return 'out' if row and row['kind'] == 'in' else 'in'
    # window 模式
    windows, _ = get_windows_config()
    m = _to_minutes(now)
    if row and row['kind'] == 'in':
        in_dt = datetime.datetime.strptime(row['punch_time'], '%Y-%m-%d %H:%M:%S')
        wi = _window_of(windows, _to_minutes(in_dt))
        if wi is not None:
            nx = windows[wi + 1][0] if wi + 1 < len(windows) else get_windows_config()[1]
            if m <= nx:
                return 'out'
        # 旧签到已超过可签退期: 也记作"作废签退"(duration=0), 下一次才签到
        return 'out'
    # 能否签到
    if _in_window(windows, m) is not None:
        return 'in'
    return None


def punch(user_id, kind, photo, method='face', now=None):
    """打卡: unlimited=不限次数; window=按时间区间校验并算有效时长
    返回 (ok, info): ok=True info=record_id; ok=False info=错误消息"""
    now = now or datetime.datetime.now()
    duration = None
    mode = get_punch_mode()
    if mode == 'window':
        windows, dl = get_windows_config()
        m = _to_minutes(now)
        if kind == 'in':
            if _in_window(windows, m) is None:
                return False, '当前不在签到时段'
        else:
            today = now.strftime('%Y-%m-%d')
            conn = get_db()
            open_in = conn.execute(
                "SELECT punch_time FROM records WHERE user_id=? AND date(punch_time)=? AND kind='in' ORDER BY punch_time DESC LIMIT 1",
                (user_id, today)).fetchone()
            conn.close()
            if open_in is None:
                return False, '没有签到记录'
            in_dt = datetime.datetime.strptime(open_in['punch_time'], '%Y-%m-%d %H:%M:%S')
            in_m = _to_minutes(in_dt)
            wi = _window_of(windows, in_m)
            if wi is None:
                duration = 0
            else:
                st, en = windows[wi]
                nx = windows[wi + 1][0] if wi + 1 < len(windows) else dl
                if m <= en:
                    duration = int((now - in_dt).total_seconds())
                elif m <= nx:
                    end_dt = datetime.datetime.combine(now.date(), datetime.time(en // 60, en % 60))
                    duration = int((end_dt - in_dt).total_seconds())
                else:
                    duration = 0
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO records(user_id, punch_time, kind, photo, method, duration) VALUES (?,?,?,?,?,?)',
        (user_id, now.strftime('%Y-%m-%d %H:%M:%S'), kind, photo, method, duration))
    u = get_user_by_id(user_id)
    conn.commit()
    conn.close()
    log_action(u['name'] if u else str(user_id), '打卡', '%s %s' % (kind, photo))
    return True, cur.lastrowid


def weekly_stats(user_id, week_start=None):
    """周一起始; 多段 in-out 累加; 返回 dict"""
    if week_start is None:
        today = datetime.date.today()
        week_start = today - datetime.timedelta(days=today.weekday())
    start = week_start.isoformat()
    end = (week_start + datetime.timedelta(days=7)).isoformat()
    conn = get_db()
    rows = conn.execute("SELECT * FROM records WHERE user_id=? AND date(punch_time)>=? AND date(punch_time)<? ORDER BY punch_time",
                        (user_id, start, end)).fetchall()
    conn.close()
    by_day = {}
    for r in rows:
        by_day.setdefault(r['punch_time'][:10], []).append(dict(r))
    daily, total, fmt = [], 0, '%Y-%m-%d %H:%M:%S'
    for day in sorted(by_day):
        recs = sorted(by_day[day], key=lambda r: r['punch_time'])
        in_t = next((r['punch_time'] for r in recs if r['kind'] == 'in'), None)
        out_t = next((r['punch_time'] for r in reversed(recs) if r['kind'] == 'out'), None)
        secs, open_t = 0, None
        for r in recs:
            if r['kind'] == 'in':
                open_t = r['punch_time']
            elif r['kind'] == 'out':
                if r.get('duration') is not None:
                    secs += int(r['duration'])
                elif open_t:
                    secs += max(0, int((datetime.datetime.strptime(r['punch_time'], fmt) - datetime.datetime.strptime(open_t, fmt)).total_seconds()))
                open_t = None
        total += secs
        daily.append({'date': day, 'in': in_t, 'out': out_t, 'seconds': secs})
    return {'week_start': start, 'total_seconds': total, 'total_hours': round(total / 3600, 2),
            'days': daily, 'records': [dict(r) for r in rows]}

# ---------- 操作日志 ----------
def log_action(user, action, detail=''):
    conn = get_db()
    conn.execute('INSERT INTO activity_log(log_time, user, action, detail) VALUES (?,?,?,?)',
                 (now_str(), user, action, detail))
    conn.commit()
    conn.close()


def list_activity(limit=1000):
    conn = get_db()
    rows = conn.execute('SELECT * FROM activity_log ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()
    conn.close()
    return rows


# ---------- 周统计(每人) ----------
def weekly_summary(week_start=None):
    """每用户本周: 打卡次数/总时长/排名, 按时长降序"""
    if week_start is None:
        today = datetime.date.today()
        week_start = today - datetime.timedelta(days=today.weekday())
    start = week_start.isoformat()
    end = (week_start + datetime.timedelta(days=7)).isoformat()
    conn = get_db()
    rows = conn.execute(
        'SELECT r.user_id, u.name, r.punch_time, r.kind, r.duration FROM records r JOIN users u ON u.id=r.user_id '
        'WHERE date(r.punch_time)>=? AND date(r.punch_time)<? ORDER BY r.user_id, r.punch_time',
        (start, end)).fetchall()
    conn.close()
    fmt = '%Y-%m-%d %H:%M:%S'
    users, open_t = {}, {}
    for r in rows:
        uid = r['user_id']
        if uid not in users:
            users[uid] = {'user_id': uid, 'name': r['name'], 'punches': 0, 'seconds': 0}
        users[uid]['punches'] += 1
        if r['kind'] == 'in':
            open_t[uid] = datetime.datetime.strptime(r['punch_time'], fmt)
        elif r['kind'] == 'out':
            if r['duration'] is not None:
                users[uid]['seconds'] += int(r['duration'])
            elif uid in open_t:
                users[uid]['seconds'] += max(0, int((datetime.datetime.strptime(r['punch_time'], fmt) - open_t[uid]).total_seconds()))
            open_t.pop(uid, None)
    result = sorted(users.values(), key=lambda x: -x['seconds'])
    for i, u in enumerate(result, 1):
        u['rank'] = i
        u['hours'] = round(u['seconds'] / 3600, 2)
    return {'week_start': start, 'users': result}

# ---------- 实时在场人数 ----------
def current_presence(stale_hours=14):
    """当前在场: 今天最后一条打卡是 in 且未超时(默认14小时防隔夜残留)"""
    today = datetime.date.today().isoformat()
    conn = get_db()
    rows = conn.execute(
        "SELECT r.user_id, u.name, r.punch_time, r.kind FROM records r JOIN users u ON u.id=r.user_id "
        "WHERE date(r.punch_time)=? ORDER BY r.user_id, r.punch_time", (today,)).fetchall()
    conn.close()
    fmt = '%Y-%m-%d %H:%M:%S'
    now = datetime.datetime.now()
    last = {}
    for r in rows:
        last[r['user_id']] = r
    present = []
    mode = get_punch_mode()
    windows, dl = get_windows_config()
    for uid, r in last.items():
        if r['kind'] != 'in':
            continue
        in_dt = datetime.datetime.strptime(r['punch_time'], fmt)
        if mode == 'unlimited':
            if (now - in_dt).total_seconds() > stale_hours * 3600:
                continue
        else:
            in_m = _to_minutes(in_dt)
            wi = _window_of(windows, in_m)
            nx = windows[wi + 1][0] if wi is not None and wi + 1 < len(windows) else dl
            if wi is None or _to_minutes(now) > nx:
                continue
        present.append({'user_id': uid, 'name': r['name'], 'in_time': r['punch_time'],
                        'minutes': int((now - in_dt).total_seconds() // 60)})
    present.sort(key=lambda x: x['in_time'])
    return present


# ---------- K210 录入: 下一个数字编号 ----------
def next_number_name():
    """返回下一个数字名(1,2,3...): 取现有数字名最大值+1; 无则从 1 开始"""
    conn = get_db()
    rows = conn.execute('SELECT name FROM users').fetchall()
    conn.close()
    nums = []
    for r in rows:
        try:
            nums.append(int(r['name']))
        except (ValueError, TypeError):
            pass
    return str(max(nums) + 1) if nums else '1'

def safe_folder(name):
    """文件夹名安全化(防路径穿越, 允许中文)"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(name)).strip()
    return s or 'user'


# ---------- 语音问候辅助 ----------
def is_first_in_today():
    """打卡后调用: 今天是该用户当天第一次签到, 且是全天第一位签到者"""
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM records WHERE date(punch_time)=date('now','localtime') AND kind='in'").fetchone()[0]
    conn.close()
    return n == 1

# ---------- 人脸/指纹 独立删除与槽位 ----------
def delete_face(user_id):
    """只删人脸样本(保留用户/指纹/记录); 返回删除的样本数"""
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM face_samples WHERE user_id=?", (user_id,)).fetchone()[0]
    conn.execute("DELETE FROM face_samples WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()
    user = get_user_by_id(user_id)
    if user:
        import shutil
        folder = os.path.join(FACE_LIB, safe_folder(user['name']))
        if os.path.isdir(folder):
            shutil.rmtree(folder, ignore_errors=True)
    return n


def delete_fp(user_id):
    """只删指纹绑定(AS608 槽位保留, 只删映射); 返回被删的 fp_id 列表"""
    conn = get_db()
    rows = conn.execute("SELECT fp_id FROM fingerprints WHERE user_id=?", (user_id,)).fetchall()
    conn.execute("DELETE FROM fingerprints WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()
    return [r['fp_id'] for r in rows]


def next_fp_slot():
    """下一个空闲 AS608 槽位(1-160); 全满则覆盖最小槽位"""
    conn = get_db()
    used = set(r[0] for r in conn.execute("SELECT fp_id FROM fingerprints"))
    conn.close()
    for s in range(1, 161):
        if s not in used:
            return s
    return 1
