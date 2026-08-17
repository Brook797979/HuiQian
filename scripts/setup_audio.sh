#!/bin/bash
# 慧签 音频自动配置: 检测USB音箱 -> 写/etc/asound.conf -> 调音量
# 用法: sudo bash setup_audio.sh   (插入USB音箱后重跑一次, 或重启自动执行)
set -u
APLAY=$(command -v aplay || echo /usr/bin/aplay)

pick_usb=$($APLAY -l 2>/dev/null | grep -iE '^card [0-9]+:.*usb' | sed -E 's/^card ([0-9]+):.*/\1/' | head -1)
if [ -z "$pick_usb" ]; then
    # 回退: 任意非 HDMI 卡
    for n in $($APLAY -l 2>/dev/null | sed -nE 's/^card ([0-9]+):.*/\1/p'); do
        line=$($APLAY -l 2>/dev/null | grep -E "^card $n:" | head -1)
        if ! echo "$line" | grep -qi 'hdmi'; then
            pick_usb=$n
            break
        fi
    done
fi

if [ -z "$pick_usb" ]; then
    echo "[huiqian-audio] 未找到可用声卡(USB音箱未插入?)"
    exit 0
fi

cat > /etc/asound.conf <<EOF
# 慧签自动生成: 默认音频设备 -> 声卡 $pick_usb
pcm.!default {
    type plug
    slave.pcm {
        type hw
        card $pick_usb
    }
}
ctl.!default {
    type hw
    card $pick_usb
}
EOF

for ctl in Master Speaker PCM "Headphone"; do
    amixer -c "$pick_usb" sset "$ctl" 85% >/dev/null 2>&1 || true
done
echo "[huiqian-audio] 音频设备已配置: card $pick_usb"