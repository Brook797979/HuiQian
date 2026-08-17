#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""慧签 语音播报模块 (Pi5 侧) — aplay 版
用系统 aplay 播放 voice/*.wav (经 /etc/asound.conf -> bluealsa -> 蓝牙音箱)。
比 pygame 更可靠: 每次播放独立进程, 蓝牙重连也能自动恢复, 不占设备。
支持: play / play_seq / play_loud; 无声卡时静默跳过, 不影响打卡。
"""
import os, glob, logging, time, threading, subprocess, shutil

log = logging.getLogger('voice')

BASE = os.path.dirname(os.path.abspath(__file__))
VOICE_DIR = os.path.join(BASE, 'voice')
_sounds = {}          # name -> wav 路径
_available = False


def init():
    """加载语音文件清单; 检测 aplay 是否可用"""
    global _available, _sounds
    _sounds = {}
    for f in sorted(glob.glob(os.path.join(VOICE_DIR, '*.wav')) + glob.glob(os.path.join(VOICE_DIR, '*.mp3'))):
        name = os.path.splitext(os.path.basename(f))[0]
        _sounds[name] = f
    if shutil.which('aplay') is None:
        _available = False
        log.warning('语音模块不可用: 未找到 aplay')
        return
    _available = True
    log.info('语音模块就绪(aplay), 已加载 %d 条语音', len(_sounds))


def _play_path(path, timeout=30):
    """用 aplay 播放一个文件(阻塞, 播完返回)"""
    try:
        subprocess.run(['aplay', '-D', 'default', path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except Exception as e:
        log.warning('播放失败 %s: %s', os.path.basename(path), e)


def play(name):
    """后台线程播一条; 找不到/失败静默"""
    if not _available:
        return False
    path = _sounds.get(name)
    if path is None:
        log.warning('语音不存在: %s', name)
        return False
    threading.Thread(target=_play_path, args=(path,), daemon=True).start()
    return True


def play_seq(names, gap=0.15):
    """后台线程顺序播放多条(旋律+语音)"""
    if not _available:
        return False
    def _worker():
        for n in names:
            p = _sounds.get(n)
            if p:
                _play_path(p)
            time.sleep(gap)
    threading.Thread(target=_worker, daemon=True).start()
    return True


def _get_vol():
    try:
        out = subprocess.check_output(['amixer', 'sget', 'Master'], text=True, stderr=subprocess.DEVNULL)
        m = __import__('re').search(r'\[(\d+)%\]', out)
        return int(m.group(1)) if m else 60
    except Exception:
        return 60


def _set_vol(pct):
    try:
        subprocess.check_call(['amixer', 'sset', 'Master', '%d%%' % int(pct)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def play_loud(name, boost_to=85):
    """放大音量播放(提醒用): 提升到 boost_to%, 播完恢复原音量"""
    if not _available:
        return False
    path = _sounds.get(name)
    if path is None:
        log.warning('语音不存在: %s', name)
        return False
    def _worker():
        cur = _get_vol()
        _set_vol(max(cur, boost_to))
        try:
            _play_path(path)
        finally:
            _set_vol(cur)
    threading.Thread(target=_worker, daemon=True).start()
    return True