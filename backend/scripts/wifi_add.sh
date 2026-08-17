#!/bin/bash
# 用法: ./wifi_add.sh "热点SSID" "密码"
# 保存后开机自动连; 连不上会自动开热点 HuiQian/12345678
if [ $# -lt 1 ]; then
  echo "用法: $0 \"SSID\" \"密码\""
  exit 1
fi
nmcli dev wifi connect "$1" password "$2"
echo "已保存 WiFi: $1"
