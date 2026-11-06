---
name: multi-bot-adapter
version: "1.0.0"
author: "bucai2417"
display_name: "多渠道机器人适配器"
display_name_en: "Multi-Bot Adapter"
description: "Add or maintain multi-channel chat bot adapters for QQ official Bot and WeChat wxclaw / iLink Bot in an agent framework. Use when integrating a new messaging channel, implementing QR-code login, AppID/ClientSecret or bot token authentication, WebSocket gateway or long-polling intake, text and rich-media send/receive, voice reply, artifact delivery, admin commands, scheduled push, channel status reporting, or debugging protocol errors such as QQBot auth, passive reply msg_id, wxclaw errcode -14, upload_param, AES media encryption, context_token, message dedupe, and content_type detection. 多渠道机器人适配器：QQ 官方 Bot 与微信 wxclaw/iLink Bot 接入、扫码登录、WebSocket 网关、长轮询、文本/图片/文件/语音收发、渠道管理、管理员命令、定时推送、协议排错。"
description_zh: "多渠道机器人适配器。适用于给 agent 框架接入或维护 QQ 官方 Bot、微信 wxclaw/iLink Bot：扫码登录、AppID+ClientSecret 或 Bot Token 认证、WebSocket 网关与长轮询收消息、文本/图片/文件/语音收发、产物投递、管理员命令、定时推送、渠道状态上报，以及 QQBot 鉴权、被动回复 msg_id、wxclaw errcode -14、upload_param、AES 媒体加密、context_token、消息去重、content_type 判定等协议排错。"
description_en: "Add or maintain multi-channel chat bot adapters for QQ official Bot and WeChat wxclaw / iLink Bot in an agent framework. Use when integrating a new messaging channel, implementing QR-code login, AppID/ClientSecret or bot token authentication, WebSocket gateway or long-polling intake, text and rich-media send/receive, voice reply, artifact delivery, admin commands, scheduled push, channel status reporting, or debugging protocol errors such as QQBot auth, passive reply msg_id, wxclaw errcode -14, upload_param, AES media encryption, context_token, message dedupe, and content_type detection."
metadata:
  scope: "chat-channels"
  languages: "en,zh"
---

# 多渠道机器人适配器 / Multi-Bot Adapter

> 把「聊天平台协议」收敛成一套可复用的渠道适配器：
> **QQ 官方 Bot（WebSocket 网关 + REST）** 与 **微信 wxclaw / iLink Bot（HTTP 长轮询 + REST）**。
>
> 本 skill 来自一个已跑通的 agent 框架渠道实现，覆盖认证、扫码、收消息、发文本、富媒体、
> 语音、产物投递、渠道管理与排错。目标不是讲协议百科，而是让下一位开发者**照着接就能通**。

---

## 何时使用（触发场景）

只要任务涉及下面任一情况，先读本 skill，再按路由打开对应 reference：

- 给 agent / 机器人框架**新增一个消息渠道**（QQ、微信、或照着现有渠道仿写第三个平台）。
- 实现或修改**扫码登录**、AppID/ClientSecret、Bot Token、`access_token` 刷新。
- 接**收消息**：QQ WebSocket 网关 / 心跳 / Resume，或 wxclaw `getupdates` 长轮询。
- 接**发消息**：纯文本、Markdown、图片、视频、语音、文件、产物回传。
- 处理**被动回复**：QQ `msg_id`、微信群/私聊会话、`context_token` 24h 窗口。
- 排查协议错误：`-14 session timeout`、401/403、`upload_param` 为空、文件解密乱码、附件类型误判。
- 做渠道运行时：管理员白名单、`/myuid`、工作流模式、定时任务推送、错误状态上报。

**不适用**：纯 Web 前端聊天、只调用大模型 API、与消息渠道无关的 IM 协议研究。
遇到第三种平台时，仍可用本 skill 的「适配器契约」和「必守不变量」作为接入模板。

---

## 核心架构

```
                       ┌──────────────────────────────┐
   QQ 官方 Bot  ──────► │  QQBotAdapter                │
   WebSocket + REST    │  - token 缓存/刷新            │
                       │  - gateway/心跳/Resume        │
                       │  - 文本 + 富媒体 + 语音        │
                       └──────────────┬───────────────┘
                                      │
                       ┌──────────────▼───────────────┐
   微信 wxclaw  ──────► │  WxclawAdapter               │
   长轮询 + REST       │  - 请求头三件套                │
                       │  - getupdates 游标/去重        │
                       │  - AES 媒体上传/下载解密        │
                       └──────────────┬───────────────┘
                                      │  标准消息
                       ┌──────────────▼───────────────┐
                       │  BaseAdapter.dispatch        │
                       │  过滤 → on_message → 回复     │
                       │  先附件，后文本；语音失败降级   │
                       └──────────────┬───────────────┘
                                      │
                       ┌──────────────▼───────────────┐
                       │  Channel Manager             │
                       │  生命周期/会话/产物/推送/状态   │
                       └──────────────────────────────┘
```

三层职责必须分清：

1. **适配器**只负责平台协议：认证、连接、解析、发送、下载。不要写业务逻辑。
2. **BaseAdapter** 负责标准化投递：过滤、`on_message`、附件顺序、语音降级。
3. **Manager** 负责运行时：渠道启停、会话隔离、入站文件、产物登记、管理员、推送、状态。

---

## 适配器契约

### 标准消息

所有适配器把平台事件转成同一结构，交给 `dispatch`：

```python
{
    "platform": "qqbot | wxclaw",
    "message_id": "...",
    "user_id": "发送者标识",
    "group_id": "群标识，私聊为 None",
    "content": "文本（附件用占位描述拼接）",
    "files": [
        {"type": "image | video | voice | file",
         "url": "...",              # 有直链就放
         "name": "文件名",
         "aes_key": "...",           # wxclaw 加密媒体
         "encrypt_query_param": "..."}
    ],
    "raw_data": {...},               # 原始事件，排错用
}
```

### 必须实现的方法

```python
class BaseAdapter(ABC):
    platform: str

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def send(self, target_id: str, content: str, is_group: bool,
                   file_path: str | None = None, file_name: str = "",
                   file_type: str = "file") -> dict: ...

    async def send_file_bytes(self, target_id: str, file_name: str,
                              data: bytes, file_type: str,
                              is_group: bool) -> dict: ...

    async def download_inbound(self, att: dict) -> bytes | None: ...
```

约定：

- `send` / `send_file_bytes` 返回平台响应 dict；失败返回 `{"error": "..."}`，不要静默吞掉。
- `download_inbound` 接收 `files[]` 中的一项，返回原始字节；失败返回 `None` 并记日志。
- 平台响应里的 `ret` / `errcode` 要在适配器内部判定；BaseAdapter 只检查 `error` 和 `ret != 0`。
- 附件下载与发送都要设置超时，避免一个坏附件拖死整条消息。

### 生命周期与状态

- `connect()` 只负责启动后台任务（轮询或 WebSocket），不要阻塞。
- `disconnect()` 关闭连接；后台任务由 BaseAdapter 统一取消。
- 连接/鉴权失败要抛出异常，让 Manager 把渠道状态置为 `error` 并写 `last_error`。
- 运行期错误通过 `manager.mark_status(user_id, channel_id, "error"|"running", message)` 上报。
- 内部配置 `__channel_id` 由 Manager 注入，适配器不要要求用户手填。

---

## 接入流程（照着做）

### 1. 选渠道并准备凭据

| 渠道 | 凭据 | 获取方式 |
|---|---|---|
| QQ 官方 Bot | `app_id` + `client_secret` | 扫码连接（推荐）或 q.qq.com 开放平台 |
| 微信 wxclaw | `token`（`xxx@im.bot:...`） | 扫码登录（推荐）或手动粘贴 |

扫码的完整接口、二维码状态机、凭据落库与热启动，见对应 reference 的「扫码登录」章节。

### 2. 认证

- **QQ**：`POST https://bots.qq.com/app/getAppAccessToken`，token 2 小时过期；
  缓存 + 到期前 60 秒刷新 + 加锁；401/403 强制刷新后重试一次。
- **wxclaw**：请求头三件套缺一不可——`AuthorizationType: ilink_bot_token`、
  `Authorization: Bearer <token>`、非空随机 `X-WECHAT-UIN`；payload 必须带
  `base_info.channel_version`。

### 3. 收消息

- **QQ**：`GET /gateway` → WebSocket → `Hello` → `Identify/Resume` → `Dispatch`。
  处理 `C2C_MESSAGE_CREATE`、`GROUP_AT_MESSAGE_CREATE`、`GROUP_MESSAGE_CREATE`；
  心跳、重连退避、消息 id 去重。
- **wxclaw**：`POST ilink/bot/getupdates` 长轮询，游标 `get_updates_buf`；
  兼容 `next_get_updates_buf`；按 `message_id` 去重；`-14` 时放慢轮询并上报错误。

### 4. 发消息

- **QQ**：`/v2/{groups|users}/{target}/messages`；文本 `msg_type=0/2`；
  富媒体先上传 `/files` 拿 `file_info`，再发 `msg_type=7`；
  被动回复 `msg_id` 有值才带，空串必须省略。
- **wxclaw**：`ilink/bot/sendmessage`，`{"msg": {...}, "base_info": {...}}` 两层包裹；
  `message_type=2`；`context_token` 按发送者缓存 24h。

### 5. 媒体与语音

- **QQ**：`file_type` 1=图片、2=视频、3=语音、4=文件；语音支持 wav/mp3/silk 直传，
  不做转码；上传返回 `file_info` 后立即发送（有 TTL）。
- **wxclaw**：`getuploadurl` → CDN 上传（AES-ECB 加密）→ `sendmessage`；
  `media_type` 1=图片、3=文件；`upload_param` 可能在响应顶层；
  `media` 必须放在 `*_item` 内部；文件带 `file_name` 与 `len`。
- **语音出站**：QQ 原生支持；微信协议未开放，只能降级成文件。
- **降级规则**：语音发送失败 → 改发文件 + 补发文本；语音成功 → 不重复发文本；
  超过 20MB 的语音不投递。

### 6. 渠道运行时

- 每个「渠道 + 发送者」一个独立会话；同一渠道串行处理，新消息打断旧任务。
- 「停止/取消/算了」等短指令中断当前任务。
- 入站附件保存到工作区 `inbox/`，登记为会话产物；图片走多模态，语音转写，文档抽文本。
- 管理员 `admin_ids` 才可用管理工具；`/myuid` 返回发送者 ID。
- 定时推送目标：显式 `target` → `admin_ids` 第一个 → `last_sender_id` → `last_group_id`。

---

## 两个渠道的关键差异

| 项目 | QQ 官方 Bot | 微信 wxclaw |
|---|---|---|
| 收消息 | WebSocket 网关（心跳 + Resume） | HTTP 长轮询（游标） |
| 认证头 | `Authorization: QQBot <token>` | `AuthorizationType` + `Bearer` + `X-WECHAT-UIN` |
| token | 2 小时，自动刷新 | 扫码后长期，但 24h 会话窗口 |
| 响应 | 标准 JSON | octet-stream 伪装 JSON |
| 文本回复 | `msg_type=0/2` + `msg_seq` | `msg` 包裹 + `message_type=2` |
| 被动回复 | 群聊必须带 `msg_id` | 带 `context_token` |
| 媒体加密 | 无 | AES-ECB（上传/下载） |
| 媒体流程 | `/files` → `file_info` → `msg_type=7` | `getuploadurl` → CDN → `sendmessage` |
| 出站语音 | 原生支持 | 不支持，降级为文件 |
| 错误定位 | HTTP / code 较明确 | 统一 `-14`，需逐项核对请求头 |

---

## 必守不变量（上线前逐条核对）

- [ ] token / secret 不落日志、不返回前端明文；列表接口脱敏，敏感字段单独 reveal。
- [ ] 保存配置时过滤占位符（`已自动填入`、`****`），缺省敏感字段沿用旧值。
- [ ] QQ `msg_id` 空串不传；wxclaw `context_token` 过期（24h）视为空。
- [ ] wxclaw 三个请求头齐全，`base_info.channel_version` 存在。
- [ ] wxclaw `upload_param` 顶层和 `data` 双查；上传后立即发送。
- [ ] wxclaw 解密 key 尝试顺序：**hex → base64 → 原文**，并用 PKCS7 校验。
- [ ] QQ 附件类型用 `content_type` 判定，缺文件名补带扩展名的占位名。
- [ ] QQ `content_type` 缺失时按扩展名兜底；相对 URL 补 `https://multimedia.nt.qq.com`。
- [ ] 两个渠道都按消息 id 去重，防止重连/游标回退导致重复回复。
- [ ] 单条消息/附件失败只影响当前项，不能让整条消息崩掉。
- [ ] 连接失败、鉴权失败、token 失效都要上报渠道 `status` / `last_error`。
- [ ] 语音失败降级为文件并补发文本；超大语音直接走文本。

---

## 常见故障速查

| 现象 | 渠道 | 首要排查 |
|---|---|---|
| `-14 session timeout` | wxclaw | 三个请求头、`get_updates_buf`、`base_info.channel_version` |
| 401 / 鉴权失败 | QQ | `Authorization` 前缀必须是 `QQBot` |
| 群聊 403 / 无响应 | QQ | 被动回复缺 `msg_id`；群消息通常只在 @ 时触发 |
| `upload_param` 为空 | wxclaw | 响应在顶层，不是 `data` |
| 文件上传成功但没到 | wxclaw | `item.type`、`media` 位置、`file_name`/`len` |
| 文件内容乱码 | wxclaw | aes_key 编码/尝试顺序、PKCS7、是否错误 key |
| 语音被当成文件 | QQ | 用 `content_type=audio/*` 判定 |
| 回复重复 | 两者 | 消息 id 去重 |
| 私聊永远纯文本 | QQ | 不要写 `is_group and markdown_support` |
| Token 输入框为空 | wxclaw | 登录响应回传 `data.token`，前端填回输入框 |

更深的原因与修复见 `references/qqbot-integration.md` 与
`references/wxclaw-integration.md` 的「踩坑清单」。

---

## 配置参考（最小集）

**QQ**

```json
{
  "app_id": "...",
  "client_secret": "...",
  "admin_ids": "openid1,openid2",
  "markdown_support": true,
  "disable_group": false,
  "disable_private": false,
  "tts_voice_id": ""
}
```

**wxclaw**

```json
{
  "token": "xxxx@im.bot:...",
  "api_url": "https://ilinkai.weixin.qq.com",
  "x_wechat_uin": "",
  "poll_interval_sec": 2,
  "admin_ids": "wxid1,wxid2",
  "tts_voice_id": ""
}
```

隐藏但可配置：`authorization_type`、`channel_version`、`get_updates_path`、
`send_message_path`、`login_qr_path`、`login_check_path`。

---

## 测试与验收

```bash
# QQ：扫码解密、状态机、msg_id、语音、Markdown、附件类型
pytest tests/test_qqbot_login.py tests/test_qqbot_sessions.py -q

# wxclaw：请求头、游标、占位符、媒体映射、上传地址、解密
pytest tests/test_wxclaw_protocol.py tests/test_wxclaw_channel.py tests/test_channel_media.py -q
```

验收最少覆盖：

1. 扫码成功 → 凭据落库 → 渠道热启动。
2. 私聊/群聊各发一条文本，确认会话隔离与被动回复。
3. 发一张图、一个文件、一条语音；确认附件顺序与降级。
4. 断开重连/游标回退时不重复回复。
5. 错误 token 能给出可读的 `last_error`，而不是静默卡死。

---

## 参考文件路由

| 需要什么 | 打开 |
|---|---|
| QQ 全量协议、扫码、WebSocket、富媒体、语音、排错 | `references/qqbot-integration.md` |
| 微信全量协议、请求头、长轮询、加密上传/下载、排错 | `references/wxclaw-integration.md` |
| 两个渠道的端点/字段并排速查 | `references/protocol-cheatsheet.md` |
| 基类契约、标准消息、Manager 生命周期与推送 | `references/adapter-contract.md` |
| 版本记录 | `CHANGELOG.md` |
| 安装与发布 | `README.md` |

---

*版本 1.0.0 · 作者 bucai2417 · 最后更新 2026-11-06*
