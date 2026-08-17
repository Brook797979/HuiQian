# 慧签 · 小程序 API 接口文档

> 更新：2026-08-17（新增密码登录/改密码，共 25 个）｜ 后端已上线并实测通过
> 对接人：软件同学 ｜ 后端地址会变，见第 0 节

## 0. 后端地址（重要）

> 稳定方案（推荐）：**树莓派自己开热点 `HuiQian`（密码 12345678）**，电脑/手机都连这个热点，后端固定地址如下。Pi 开机必开热点，无需额外配置。

| 场景 | 地址 |
|---|---|
| **Pi 热点（稳定/比赛现场/演示）** | `http://10.42.0.1:8000` |
| 以太网直连电脑（开发调试） | `http://192.168.137.235:8000` |
| Pi 连手机热点（旧方案，不推荐） | `http://<K210 LCD 显示的IP>:8000` |

- 全部接口返回 JSON；GET 用 query 参数，POST 用 JSON body（enroll/punch 用 multipart）。
- 时间格式：`YYYY-MM-DD HH:MM:SS`；照片地址 = 后端地址 + `/static/photos/<文件名>`。
- 前端把 BaseURL 做成**可配置常量/设置页**，方便现场切换。
- **软件同学远程联调**：人在现场就连接 `HuiQian` 热点用 `10.42.0.1:8000`；若不在同一网络（远程），需要内网穿透（cpolar，已在 Pi 装好，需注册拿 token 开启）或 Tailscale 组网。

## 0.1 绑定与登录（密码登录，离线可用）

**不依赖 wx.login**（离线时拿不到 openid）。做法：
1. 小程序本地生成一个 UUID，存本地缓存，当作 `openid`；
2. 用户输入 **姓名 + 密码** → 调 `/api/login` 验证并绑定 openid；
3. 之后所有"我的"接口传这个 openid 即可。

**密码规则**：至少 8 位，且至少包含两种字符（小写/大写/数字/符号）。
**默认密码**：录入人脸时自动生成 `123456qmx`（符合规则）；管理员可在树莓派桌面软件「修改密码」中改。

## 1. 接口总览

| # | 接口 | 方法 | 用途 |
|---|---|---|---|
| 1 | `/api/health` | GET | 健康检查 |
| 2 | `/api/presence` | GET | 实时在场人数 |
| 3 | `/api/enroll` | POST | 录入人脸（multipart） |
| 4 | `/api/punch` | POST | 打卡（multipart，可传图） |
| 5 | `/api/bind` | POST | 绑定微信标识到用户 |
| 6 | `/api/me` | GET | 我的信息（含角色） |
| 7 | `/api/set_role` | POST | 设置角色 0学生/1管理员 |
| 8 | `/api/users` | GET | 用户列表 |
| 9 | `/api/records` | GET | 打卡记录 |
| 10 | `/api/weekly` | GET | 我的周统计 |
| 11 | `/api/stats` | GET | 全员周统计+排名 |
| 12 | `/api/activity` | GET | 操作日志 |
| 13 | `/api/face_samples` | GET | 人脸样本列表 |
| 14 | `/api/fingerprint/bind` | POST | 绑定指纹 |
| 15 | `/api/fingerprint/list` | GET | 指纹映射列表 |
| 16 | `/api/rename` | POST | 改名 |
| 17 | `/static/photos/<文件>` | GET | 打卡照片 |
| 18 | `/api/delete_user` | POST | 删除用户（级联删记录/指纹/人脸） |
| 19 | `/api/settings` | GET | 查打卡模式与时间段 |
| 20 | `/api/settings` | POST | 保存打卡模式/时间段（管理员） |
| 21 | `/api/delete_face` | POST | 只删人脸样本（保留用户/指纹/记录） |
| 22 | `/api/delete_fp` | POST | 只删指纹绑定（保留人脸） |
| 23 | `/api/fingerprint/enroll` | POST | 服务端录指纹（语音提示，按2次手指） |
| 24 | `/api/login` | POST | 登录（姓名+密码，绑定 openid） |
| 25 | `/api/set_password` | POST | 管理员改密码（≥8位，含两种字符） |

## 2. 接口详情

### 1) GET /api/health
无参数。返回：
```json
{"ok": true, "msg": "慧签后端运行中"}
```

### 2) GET /api/presence
无参数。实时在场（当天 in 未签退者）。
```json
{"ok": true, "count": 2,
 "users": [
   {"user_id": 1, "name": "梁健", "in_time": "2026-08-15 09:00:12", "minutes": 35},
   {"user_id": 2, "name": "刘恩泽", "in_time": "2026-08-15 09:05:40", "minutes": 30}
 ]}
```

### 3) POST /api/enroll
multipart：`name`（姓名，可中文）+ `image`（照片文件）。
```json
{"ok": true, "name": "梁健", "user_id": 1, "samples": 3, "photo": "face_library/梁健/sample_03.jpg"}
```
失败：`{"ok": false, "msg": "未检测到人脸"}`

### 4) POST /api/punch
multipart：`image`（可选，不传则 Pi 现场拍照）。返回打卡结果。
```json
{"ok": true, "name": "梁健", "kind": "in", "dist": 0.52, "blink": false,
 "record_id": 5, "photo": "20260814_151514_848.jpg",
 "time": "2026-08-14 15:15:14"}
```
失败：`{"ok": false, "msg": "未识别", "dist": 0.63}`（msg 见第 4 节）

### 5) POST /api/bind
JSON：`openid` +（`name` 或 `user_id`）。
```json
{"ok": true, "user": {"id": 1, "name": "梁健", "role": 0, "wechat_openid": "...", "created_at": "..."}, "already": false}
```
已绑过：`already: true`；用户不存在：404 `{"ok": false, "msg": "用户不存在, 请先由管理员录入"}`

### 6) GET /api/me?openid=xxx
```json
{"ok": true, "user": {"id": 1, "name": "梁健", "role": 0, "wechat_openid": "...", "created_at": "..."}}
```
未绑定：404 `{"ok": false, "msg": "未绑定"}`（小程序据此提示先去绑定）

### 7) POST /api/set_role
JSON：`user_id` + `role`（0=学生，1=管理员）。
```json
{"ok": true}
```

### 8) GET /api/users
```json
{"ok": true, "users": [
  {"id": 1, "name": "梁健", "role": 0, "created_at": "...", "sample_count": 3, "fp_count": 0}
]}
```
- `sample_count`：人脸样本数；`fp_count`：指纹数

### 9) GET /api/records?user_id=1&date=2026-08-15
`date` 可选（不传返回全部）。
```json
{"ok": true, "records": [
  {"id": 24, "user_id": 1, "name": "梁健", "punch_time": "2026-08-14 22:45:44", "kind": "out", "photo": "20260814_224544_829.jpg"}
]}
```

### 10) GET /api/weekly?user_id=1&week_start=2026-08-10
`week_start` 可选（周一，不传按本周）。
```json
{"ok": true, "stats": {
  "week_start": "2026-08-10", "total_seconds": 3701, "total_hours": 1.03,
  "days": [{"date": "2026-08-14", "in": "2026-08-14 17:28:13", "out": "2026-08-14 22:45:44", "seconds": 3701}],
  "records": [ ...原始打卡记录... ]
}}
```

### 11) GET /api/stats?week_start=2026-08-10
全员周统计，按总时长降序、含排名（排行榜用）。
```json
{"ok": true, "stats": {"week_start": "2026-08-10", "users": [
  {"user_id": 1, "name": "梁健", "punches": 20, "seconds": 3701, "hours": 1.03, "rank": 1}
]}}
```

### 12) GET /api/activity
操作日志（谁/何时/做了什么）。
```json
{"ok": true, "logs": [
  {"id": 26, "log_time": "2026-08-14 22:45:44", "user": "梁健", "action": "打卡", "detail": "out 20260814_224544_829.jpg"}
]}
```

### 13) GET /api/face_samples?user_id=1
```json
{"ok": true, "samples": [
  {"id": 1, "user_id": 1, "embedding": "[-0.1457, ...]", "photo": "face_library/梁健/sample_01.jpg", "created_at": "..."}
]}
```

### 14) POST /api/fingerprint/bind
JSON：`user_id` + `fp_id`（AS608 槽位号）。
```json
{"ok": true}
```

### 15) GET /api/fingerprint/list
```json
{"ok": true, "fingerprints": [{"id": 1, "fp_id": 1, "created_at": "...", "name": "梁健"}]}
```

### 16) POST /api/rename
JSON：`user_id` + `name`。同步改数据库和人脸文件夹。
```json
{"ok": true}
```
重名：409 `{"ok": false, "msg": "该名字已存在"}`

### 17) GET /static/photos/<文件名>
返回打卡照片文件（直接在 `<image>` 里当 src 用）。


### 18) POST /api/delete_user
JSON：`user_id`。删除用户（数据库级联删 人脸样本/指纹映射/打卡记录 + 删 face_library 文件夹）。
```json
{"ok": true}
```
失败：404 `{"ok": false, "msg": "用户不存在"}`

### 19) GET /api/settings
查打卡模式与时间段（打卡设置页用）。
```json
{"ok": true, "punch_mode": "window",      // "unlimited"=无限次(比赛用) / "window"=日常时间段
 "windows": [["07:30","10:00"],["10:15","13:30"],["14:00","18:00"],["18:30","22:30"]],
 "out_deadline": "23:00"}                  // 每天最晚可签退
```

### 20) POST /api/settings
保存打卡模式/时间段（仅管理员页调用）。
JSON（可只传要改的字段）：
```json
{"punch_mode": "window",
 "windows": [["07:30","10:00"],["10:15","13:30"],["14:00","18:00"],["18:30","22:30"]],
 "out_deadline": "23:00"}
```
```json
{"ok": true}
```
参数错：400 `{"ok": false, "msg": "..."}`

## 3. 关键流程

- **学生**：绑定（/api/bind）→ /api/me 拿 role → 首页今日状态+/api/presence → /api/records 打卡明细 → /api/weekly 周统计 → /api/stats 排行榜
- **管理员**：学生功能 + /api/enroll 录人 + /api/users 看全员 + /api/records?user_id= 查明细 + /api/set_role 设角色 + /api/activity 看日志 + /api/rename 改名 + /api/settings 改打卡模式/时间段 + /api/delete_user 删人

## 4. 打卡失败 msg 说明（/api/punch、现场联动）

| msg | 含义 | 提示语 |
|---|---|---|
| 未识别(dist=0.xx) | 没匹配上任何人 | 请正对镜头、站 0.5~1 米 |
| 未检测到左右摇头 | 活体没过 | 请左右摇头 |
| 请睁开眼睛(活体检测) | 闭眼 | 请睁开眼睛 |
| 未检测到人脸 / 未能提取人脸特征 | 没拍到清晰正脸 | 请调整位置和光线 |
| 请退后一点 | 脸被截断/太近 | 请退后 |
| 当前不在可打卡时间 | 日常模式(窗口)下，当前时刻不在任何打卡时段 | 提示当前不可打卡，并显示可用时段 |

## 4.5 打卡模式 / 时长规则（重要 · 周统计口径）
- **unlimited（无限次）**：比赛现场用，随时可签到/签退，时长 = 签退时间 - 签到时间。
- **window（日常时间段）**：默认 4 段（07:30-10:00 / 10:15-13:30 / 14:00-18:00 / 18:30-22:30），每天最晚签退 23:00。
  - 段内签退：时长 = 签退 - 签到；
  - **两段之间**签退：时长只算到**所在段截止**；
  - 超过两段间隔（已进入下一段打卡）才签退：**该段有效时长 = 0**（签退成功但不算时间），下一次才记为签到；
  - 不在任何时段点打卡 → `/api/punch` 返回 `msg=当前不在可打卡时间`。
- `/api/weekly` 的 `total_seconds / total_hours`、`/api/stats` 的 `seconds / hours` **已经是修正后的有效时长**，小程序**直接展示即可，不要自己按时间差重算**。
- `/api/records` 目前每条返回 `kind/photo/punch_time`（不含 duration）；若小程序要在明细里显示"有效时长"，告诉我，我在后端补一个 `duration` 字段。

## 5. 现场/离线注意事项
1. 开发者工具 + 真机预览勾选 **"不校验合法域名"**（http 局域网地址必须）；
2. 演示手机**提前在有网环境打开过一次小程序**（缓存代码包，离线才能打开）；
3. BaseURL 做成可配置，现场切热点地址；
4. 数据都在 Pi 本地数据库，**断网不影响**；个人主体小程序需把成员加为"体验成员"才能打开。
