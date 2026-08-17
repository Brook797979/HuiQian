#!/bin/bash
# 慧签 稳定网络 v4: Pi 开机必开热点 HuiQian/12345678 (10.42.0.1)
# 电脑/手机连上后: 后端 http://10.42.0.1:8000 ; RustDesk 用 ID 或 10.42.0.1 直连
sleep 8

SUDO=""
[ "$(id -u)" != "0" ] && SUDO="sudo"

MODE=$($SUDO iw dev wlan0 info 2>/dev/null | grep 'type ' | awk '{print $2}')

# 1) 若 wlan0 是客户端模式, 断开后开热点
if [ "$MODE" = "managed" ]; then
  ACTIVE=$($SUDO nmcli -t -f NAME,DEVICE con show --active 2>/dev/null | grep ':wlan0' | head -1 | cut -d: -f1)
  [ -n "$ACTIVE" ] && $SUDO nmcli con down "$ACTIVE" >/dev/null 2>&1
  sleep 2
fi

# 2) 不是 AP 模式就开热点
if [ "$MODE" != "AP" ]; then
  $SUDO nmcli dev wifi hotspot ifname wlan0 ssid "HuiQian" password "12345678" >/dev/null 2>&1
  sleep 3
fi

# 3) 确保热点 IP = 10.42.0.1
IP=$($SUDO ip -4 addr show wlan0 | grep -oP '(?<=inet\s)10\.42\.[0-9.]+' | head -1)
if [ -z "$IP" ]; then
  $SUDO nmcli con mod Hotspot ipv4.method shared >/dev/null 2>&1
  $SUDO nmcli con up Hotspot >/dev/null 2>&1
  sleep 3
fi

# 4) NAT: 让热点设备经 eth0 上网(有网时)
echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null
if command -v iptables >/dev/null 2>&1; then
  $SUDO iptables -t nat -C POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null || $SUDO iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
fi

IP2=$($SUDO ip -4 addr show wlan0 | grep -oP '(?<=inet\s)\S+' | head -1)
echo "慧签: 热点已开启 HuiQian / 12345678 @ ${IP2:-无IP}"