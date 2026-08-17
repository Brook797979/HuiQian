#!/usr/bin/env python3
"""慧签 人脸录入助手
用法: python3 enroll.py [姓名]
提示: 站到 Pi5 摄像头前(完整正脸、别太近), 脚本自动连拍3张并录入
"""
import sys, os, time
import cv2
import requests

BASE = 'http://127.0.0.1:8000'
SHOTS = 3        # 每人数
MAX_RETRY = 4    # 每张最多重试次数


def main():
    name = sys.argv[1].strip() if len(sys.argv) > 1 else input('输入姓名: ').strip()
    if not name:
        print('姓名不能为空'); return

    from picamera2 import Picamera2
    picam2 = Picamera2()
    picam2.configure(picam2.create_still_configuration(main={'size': (1280, 720)}))
    picam2.start()
    time.sleep(0.5)

    ok = 0
    for i in range(SHOTS):
        for attempt in range(MAX_RETRY):
            print('\n第 %d/%d 张: 请正对镜头, 完整人脸, 保持不动...' % (i + 1, SHOTS))
            time.sleep(2.0)
            arr = picam2.capture_array()
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            tmp = '/tmp/enroll_shot.jpg'
            cv2.imwrite(tmp, bgr)
            with open(tmp, 'rb') as f:
                r = requests.post(BASE + '/api/enroll',
                                  data={'name': name},
                                  files={'image': ('shot.jpg', f, 'image/jpeg')})
            try:
                j = r.json()
            except Exception:
                j = {}
            if r.status_code == 200 and j.get('ok'):
                print('  ✅ 成功 (第 %d 张, 样本 %d)' % (i + 1, j.get('samples')))
                ok += 1
                break
            print('  ⚠️ %s, 重试(%d/%d)...' % (j.get('msg', '未知错误'), attempt + 1, MAX_RETRY))
        time.sleep(1.0)

    picam2.stop(); picam2.close()
    print('\n===== 完成: %s 录入成功 %d/%d 张 =====' % (name, ok, SHOTS))
    if ok == 0:
        print('提示: 检查站位(脸别太近被截断)或光线后再试')


if __name__ == '__main__':
    main()