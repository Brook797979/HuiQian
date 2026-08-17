# 慧签 HuiQian · 智能考勤后端

低成本、离线可用、双因子（指纹+人脸+活体）防代签考勤系统 —— 树莓派 5 后端 + K210 端侧 + 微信小程序/桌面端。

## 目录结构
| 路径 | 说明 |
|---|---|
| `app.py` | Flask 后端 API（小程序/桌面端调用，约 25 个接口） |
| `attendance.py` | 数据层：SQLite、打卡规则（无限/窗口）、周统计、密码管理 |
| `face_engine.py` | 人脸识别：MediaPipe 检测/关键点 + dlib 128 维特征 + 活体（摇头/眨眼） |
| `as608.py` | AS608 光学指纹模块驱动 |
| `voice.py` | 语音播报（aplay → bluealsa → 蓝牙音箱，自动重连） |
| `k210_link.py` | K210 联动服务：串口协议、打卡/录入、指纹、语音 |
| `enroll.py` / `enroll_fp.py` | 录入辅助 |
| `models/` | MediaPipe 人脸检测/关键点模型 |
| `voice/` | 23 条预生成语音 |
| `systemd/` | 开机自启服务配置 |
| `scripts/` | 网络/蓝牙/部署脚本 |
| `k210/` | K210 端固件（k210_main_standalone.py）与刷机脚本 |

## 快速启动（树莓派5）
```bash
pip install -r requirements.txt
# 后端 API
python3 app.py                 # 0.0.0.0:8000
# 联动服务（需 K210 串口）
python3 k210_link.py
```
开机自启：把 `systemd/*.service` 复制到 `/etc/systemd/system/` 并 `systemctl enable`。

## 联网说明（重要）
- 稳定方案：**树莓派自开热点 `HuiQian`（密码 12345678）**，手机/电脑连上后访问 `http://10.42.0.1:8000`。
- 小程序真机/开发者工具需勾选「不校验合法域名」。

## 密码登录
- 录入人脸时自动生成默认密码 `123456qmx`；
- `/api/login`（姓名+密码）登录并绑定 openid；`/api/set_password` 管理员改密码；
- 规则：≥8 位、至少两种字符；密码加盐哈希存储。

## API 一览（约 25 个）
health / login / bind / me / set_role / users / records / weekly / stats / activity / presence /
enroll / punch / rename / delete_user / delete_face / delete_fp / fingerprint/bind / fingerprint/list /
fingerprint/enroll / settings(GET/POST) / face_samples