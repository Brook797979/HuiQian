#!/usr/bin/env python3
"""慧签 指纹录入助手 (Pi5 侧, AS608 直连)
用法: python3 enroll_fp.py 姓名
流程: 找到 AS608 -> 录指纹到下一个槽位 -> 绑定到该用户(/api/fingerprint/bind)
"""
import sys, glob
import requests
import as608

BASE = 'http://127.0.0.1:8000'
FP_BAUD = 57600


def find_as608():
    ports = []
    for pat in ('/dev/ttyUSB*', '/dev/ttyACM*'):
        ports += sorted(glob.glob(pat))
    if len(ports) < 2:
        return None, None   # 只有K210, 不探测
    for p in ports:
        try:
            f = as608.AS608(p, baud=FP_BAUD)
            if f.verify_password():
                return f, p
            f.close()
        except Exception:
            pass
    return None, None


def main():
    name = sys.argv[1].strip() if len(sys.argv) > 1 else input('姓名: ').strip()
    if not name:
        print('姓名不能为空')
        return
    users = requests.get(BASE + '/api/users').json().get('users', [])
    user = next((u for u in users if u['name'] == name), None)
    if user is None:
        print('用户 "%s" 不存在, 请先在桌面软件录入人脸' % name)
        return
    fp, port = find_as608()
    if fp is None:
        print('未找到 AS608, 请检查: 接线/供电/第二块USB-TTL已插Pi5')
        return
    print('AS608: %s | 用户: %s' % (port, name))
    fps = requests.get(BASE + '/api/fingerprint/list').json().get('fingerprints', [])
    slot = max([f['fp_id'] for f in fps], default=0) + 1
    if slot > 160:
        slot = 1
    print('请按手指两次录入指纹(槽位 %d)...' % slot)
    code, sid = fp.enroll(slot)
    fp.close()
    if code != 0:
        print('录入失败(code=%s), 请重试' % code)
        return
    r = requests.post(BASE + '/api/fingerprint/bind', json={'user_id': user['id'], 'fp_id': sid})
    print('绑定结果:', r.json())
    print('完成: %s 指纹已录入槽位 %d' % (name, sid))


if __name__ == '__main__':
    main()
