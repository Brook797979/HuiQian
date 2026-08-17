#!/bin/bash
# 慧签 开机自动连接蓝牙音箱 XFW-M18 (MAC 84:C6:22:F6:BF:90)
MAC=84:C6:22:F6:BF:90
# 等蓝牙控制器就绪(最多15秒)
for i in $(seq 1 15); do
    bluetoothctl show >/dev/null 2>&1 && break
    sleep 1
done
bluetoothctl power on >/dev/null 2>&1
# 重试连接(最多10次, 每次10秒)
for i in $(seq 1 10); do
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q 'Connected: yes'; then
        echo "[huiqian-bt] 已连接"
        exit 0
    fi
    timeout 10 bluetoothctl connect "$MAC" >/dev/null 2>&1
    sleep 2
done
if bluetoothctl info "$MAC" 2>/dev/null | grep -q 'Connected: yes'; then
    echo "[huiqian-bt] 连接成功"
    exit 0
else
    echo "[huiqian-bt] 连接失败(音箱是否开机?)"
    exit 0
fi