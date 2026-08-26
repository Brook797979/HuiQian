# 慧签 HuiQian · 智能考勤系统

> 离线可用、**双因子（指纹+人脸）+ 活体防代签** 的完整考勤系统。
> 架构：**K210 端侧触发 + 树莓派 5 核心 + AS608 指纹 + 微信小程序/桌面端**。

## ✨ 亮点
- 🔐 **双因子防代签**：指纹（你有的）+ 人脸（你是的）+ 随机动作活体（左右摇头、上下点头、张嘴）
- 📴 **完全离线**：数据本地存储，断网照常打卡
- 🔗 **全链路闭环**：硬件 → 识别 → 数据库 → API → 小程序/桌面，一键打卡、周统计、排行榜
- 🔊 **人性化语音**：当天第一位"早起的鸟儿有虫吃"、21 点后"辛苦了晚安"、时段结束前放大音量提醒

## 🏗 项目结构
```
├── backend/                 # 树莓派 5 后端（核心）
│   ├── app.py              # Flask API（约 25 个接口，供小程序/桌面调用）
│   ├── attendance.py       # 数据层：SQLite / 打卡规则 / 周统计 / 密码
│   ├── auth_middleware.py  # 网页管理端 Bearer Token 管理员认证
│   ├── admin_cli.py        # 初始化管理员账号的命令行工具
│   ├── tests/              # 后端认证与接口保护测试
│   ├── face_engine.py      # 人脸识别：MediaPipe + dlib 128维特征 + 活体
│   ├── as608.py            # AS608 指纹模块驱动
│   ├── voice.py            # 语音播报（aplay → bluealsa → 蓝牙音箱）
│   ├── k210_link.py        # K210 联动服务（串口协议/打卡/录入/指纹）
│   ├── enroll.py / enroll_fp.py   # 录入辅助
│   ├── models/             # MediaPipe 人脸模型
│   ├── voice/              # 预生成语音（含三种随机动作提示音）
│   ├── systemd/            # 开机自启服务
│   └── scripts/            # 网络/蓝牙/部署脚本
├── k210/                   # K210 端固件 + 刷机脚本
│   ├── k210_main_standalone.py   # K210 主程序（面板/打卡/录入/排行榜）
│   └── push_file.py / push_main_chunked.py   # 刷机工具
└── README.md
```

## 🚀 快速启动（树莓派 5）
```bash
cd backend
pip install -r requirements.txt
python3 app.py            # 后端 API → 0.0.0.0:8000
python3 k210_link.py      # 联动服务（需 K210 串口）
```
开机自启：把 `backend/systemd/*.service` 复制到 `/etc/systemd/system/` 并 `systemctl enable`。
K210 固件烧录：`python3 k210/push_file.py k210/k210_main_standalone.py /flash/main.py`（K210 需连电脑 COM 口）。

## 🌐 网络方案（稳定：Pi 自开热点）
- 树莓派开机自动开热点 **`HuiQian`（密码 `12345678`）**，IP `10.42.0.1`；
- 电脑/手机连上热点后，后端地址 = **`http://10.42.0.1:8000`**；
- 小程序/真机需勾选「不校验合法域名」；接口细节见 `docs/小程序API接口文档.md`。

## 🔑 密码登录
- 录入人脸时自动生成默认密码 **`123456qmx`**；
- `/api/login`（姓名+密码）登录并绑定 openid；`/api/set_password` 管理员改密码；
- 规则：≥8 位、至少两种字符；密码**加盐哈希**存储。

## 🛡️ 网页管理端管理员登录

网页管理端使用独立的 `admin_accounts` 表，不复用学生 `users` 表，也不依赖微信 `openid`。管理员密码使用 PBKDF2-SHA256 哈希保存，登录后返回 12 小时有效的 Bearer Token。

首次在树莓派初始化管理员（只执行一次）：

```bash
cd backend
python3 admin_cli.py create-admin --username admin
```

命令行会交互式要求输入两次密码。管理员用户名只能使用 3～32 位字母、数字、下划线或连字符；密码至少 10 位，并至少包含两类字符。不要把真实密码写进代码或提交到仓库。

网页先请求登录接口：

```http
POST /api/admin/auth/login
Content-Type: application/json

{"username":"admin","password":"你的管理员密码"}
```

登录成功后，把返回的 `token` 放入后续管理请求的请求头：

```http
Authorization: Bearer <token>
```

管理员认证接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/admin/auth/login` | 管理员登录并签发会话令牌 |
| POST | `/api/admin/auth/logout` | 撤销当前会话 |
| GET | `/api/admin/auth/me` | 查询当前管理员 |
| GET | `/api/admin/accounts` | 查看管理员账号 |
| POST | `/api/admin/accounts` | 新增管理员 |
| POST | `/api/admin/accounts/<id>/status` | 启用或禁用管理员 |
| POST | `/api/admin/accounts/<id>/password` | 重置管理员密码 |

网页管理端使用的用户、考勤、统计、样本、指纹和系统设置接口均要求这个请求头。`/api/health`、`/api/punch`、`/api/login`、`/api/bind`、`/api/me` 保持公开，供硬件打卡和学生端使用。

退出登录、禁用账号或重置密码后，原会话令牌立即失效；系统始终保留至少一个启用的管理员，避免后台无人可登录。

部署或迁移树莓派时至少备份：

```text
backend/huiqian.db
backend/face_library/
backend/static/photos/
```

其中 `huiqian.db` 包含管理员账号哈希、会话、学生、样本索引和考勤记录；不要在日志、截图或网页前端保存管理员密码。

认证测试运行方式（开发机或树莓派虚拟环境）：

```bash
cd backend
python3 -m unittest tests.test_admin_auth -v
python3 -m py_compile app.py attendance.py auth_middleware.py admin_cli.py
```

## 🖥 核心流程
| 流程 | 说明 |
|---|---|
| 打卡 | K210 检测人脸 → TRIGGER → Pi 指纹(可选) → 人脸+随机动作活体 → 写库+语音 |
| 录入 | 录入信息：人脸 3 张 →（有 AS608）指纹 2 次 → 自动编号/默认密码 |
| 排行榜 | K210 请求 → Pi 生成排行图片 → K210 显示翻页 |
| 小程序 | 登录 → 我的/周统计/明细/照片/排行榜/在场人数 |

## 🤝 团队
2 软件 + 2 硬件，全链路自研（含自制蓝牙音箱、自主调试解决蜂鸣器/串口/音频等工程问题）。
