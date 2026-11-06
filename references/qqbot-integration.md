# QQ 官方 Bot 接入指南

> 与 `wxclaw-integration.md` 配套。QQ 走 **WebSocket 网关 + REST API**，
> 比微信 iLink 稳定（有官方文档），坑少很多。
>
> 适配实现：`backend/app/channels/qqbot_adapter.py`
> 扫码连接：`backend/app/api/v1/qqbot_login.py`
> 渠道管理：`backend/app/api/v1/channels.py`、`backend/app/channels/manager.py`

---

## 0. 一分钟速览

```
扫码/手填        → app_id + client_secret
换 AccessToken   POST https://bots.qq.com/app/getAppAccessToken   （2h 有效）
拿网关地址       GET  https://api.sgroup.qq.com/gateway
连 WebSocket     Hello → Identify / Resume → Dispatch 事件
收消息           GROUP_AT_MESSAGE_CREATE / GROUP_MESSAGE_CREATE / C2C_MESSAGE_CREATE
发文本           POST /v2/{groups|users}/{target}/messages        （msg_type 0/2）
发富媒体         POST /v2/{groups|users}/{target}/files
                 → POST /v2/{groups|users}/{target}/messages      （msg_type 7）
```

**三个最容易踩的坑**：

1. `Authorization` 前缀必须是 **`QQBot`**，不是 `Bearer`
2. 被动回复必须带 `msg_id`；**空串不能传**（要条件省略）
3. AccessToken **2 小时**过期，必须缓存 + 提前刷新；遇到 401/403 要强制刷新后重试一次

---

## 1. 凭据与配置

### 1.1 扫码连接（推荐，自动拿 AppID + ClientSecret）

不用去 q.qq.com 手动复制凭据，直接扫码接管机器人。

**本项目接口**：

| 接口 | 请求 | 返回 |
|---|---|---|
| `POST /api/v1/channels/qqbot/scan-login` | `{env, channel_id?}` | `task_id`、`key`、`qr`、`qr_display` |
| `POST /api/v1/channels/qqbot/login-status` | `{task_id, key, env, channel_id?}` | `logged_in`、`status`、`app_id`、`client_secret`、`saved`、`start_error` |

**上游协议**（已线上实测）：

```
1) POST https://q.qq.com/lite/create_bind_task
   body   {"key": "<32 字节随机数的 base64>"}
   返回   {"retcode":0,"msg":"success","data":{"task_id":"..."}}

2) 手机 QQ 扫二维码，二维码内容：
   https://q.qq.com/qqbot/openclaw/connect.html?task_id=<task_id>&_wv=2

3) POST https://q.qq.com/lite/poll_bind_result      （建议每 2 秒轮询）
   body   {"task_id": "<task_id>"}
   返回   {"retcode":0,"data":{"status":2,"bot_appid":"...","bot_encrypt_secret":"..."}}
```

`status` 枚举与前端状态：

| 值 | 含义 | 返回的 `status` |
|---|---|---|
| 0 | NONE | `wait` |
| 1 | PENDING（还没扫） | `wait` |
| 2 | COMPLETED | `logged_in` |
| 3 | EXPIRED | `expired`（需刷新二维码） |

**ClientSecret 解密**（`bot_encrypt_secret` 是 AES-256-GCM 密文）：

```
key   = base64decode(第 1 步自己生成的 key)          # 32 字节
raw   = base64decode(bot_encrypt_secret)
iv    = raw[:12]
tag   = raw[-16:]
ct    = raw[12:-16]
secret = AES-256-GCM(key, nonce=iv, mac_len=16).decrypt_and_verify(ct, tag)
```

实现见 `decrypt_secret()`（`qqbot_login.py`）。密文布局是 **iv(12) + ciphertext + authTag(16)**，无 AAD。

**扫码完成后的动作**（实现细节）：

- 拿到 `bot_appid` + 解密后的 `client_secret`
- 若请求带 `channel_id`：写入渠道配置、`is_active=True`、立即热启动该渠道
- 启动失败不返回 500：`start_error` 单独回传，凭据已经落库
- 响应里回传明文 `client_secret` 供前端填表；列表接口之后会脱敏
- 二维码由后端用 `segno` 本地生成 PNG dataURL，不依赖外部二维码服务

⚠️ **注意**：

- 扫码页 URL 里的 `openclaw` **只是路径名**，不是绑定关系，是通用的 QQ Bot 绑定页
- 测试环境域名 `test.q.qq.com`，生产 `q.qq.com`（请求参数 `env` 控制）
- 这些是**未公开接口**，官方可能变更；失败时保留手动填写 AppID/Secret 的入口
- 扫码只解决「拿凭据」；机器人**运行时**认证仍是第 2 节的换 token

### 1.2 手动填写

到 [q.qq.com](https://q.qq.com) 开放平台复制 `AppID` 与 `ClientSecret` 填入渠道配置即可。

### 1.3 渠道配置项

来源 `backend/app/api/v1/channels.py` 的 `CHANNEL_TYPES["qqbot"]` 与适配器 `__init__`：

| 配置键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `app_id` | string | — | 机器人 AppID（必填） |
| `client_secret` | password | — | 机器人 ClientSecret（必填） |
| `admin_ids` | string | `""` | 管理员 ID，**逗号分隔**；在 QQ 里发 `/myuid` 获取 |
| `require_mention` | boolean | `false` | UI 中「群聊仅回复@消息」。**当前实现只解析、未参与过滤**：群消息是否触发由平台事件决定（通常只有 @ 才推 `GROUP_AT_MESSAGE_CREATE`） |
| `disable_group` | boolean | `false` | 停用群聊（`_dispatch` 中直接丢弃群消息） |
| `disable_private` | boolean | `false` | 停用私聊（同上） |
| `markdown_support` | boolean | `true` | 私聊与群聊都用 Markdown（`msg_type=2`）；关闭后回落纯文本 |
| `tts_voice_id` | string | `""` | 语音回复音色；留空用默认音色 |
| `__channel_id` | int | 运行时注入 | 内部字段，管理进程注入，用于状态上报，不要手填 |

**管理员与推送**：

- `admin_ids` 里的人才能在渠道内使用管理类工具（定时任务、渠道/技能管理等）
- 未配置 `admin_ids` 时，定时任务推送会退回到最近一次入站发送者（`last_sender_id`）或最近群（`last_group_id`）
- 用户不知道自己的 ID 时，让他在 QQ 里给机器人发 `/myuid`

---

## 2. 认证：AppID + ClientSecret 换 AccessToken

```
POST https://bots.qq.com/app/getAppAccessToken
Content-Type: application/json

{"appId": "<app_id>", "clientSecret": "<client_secret>"}
```

响应：

```json
{"access_token": "xxxxxx", "expires_in": 7200}
```

**实现要点**（`_get_access_token`）：

- token 以 `expires_in` 为准（缺省 7200 秒）
- 缓存有效条件：`time.time() < _token_expire - 60`，即到期前 60 秒刷新
- 双检 + `asyncio.Lock`，避免并发重复刷新
- `force=True` 用于 401/403 后的强制刷新

```python
async def _get_access_token(self, force: bool = False) -> str:
    if not force and self._access_token and time.time() < self._token_expire - 60:
        return self._access_token
    async with self._token_lock:
        if not force and self._access_token and time.time() < self._token_expire - 60:
            return self._access_token
        session = await self._ensure_session()
        async with session.post(
            TOKEN_URL, json={"appId": self.app_id, "clientSecret": self.client_secret}
        ) as resp:
            data = await resp.json()
        token = str(data.get("access_token", "") or "").strip()
        if not token:
            raise RuntimeError(f"获取 AccessToken 失败：{data}")
        self._access_token = token
        self._token_expire = time.time() + int(data.get("expires_in", 7200) or 7200)
        return token
```

**统一 API 包装**（`_api`）：

- 所有 REST 调用都带 `Authorization: QQBot <token>`
- 遇到 401 / 403 时，**强制刷新一次 token 再重试**
- 其它网络异常重试一次（间隔 0.5s）
- 返回 JSON；非 JSON 响应包成 `{"raw": text}`，HTTP >= 400 抛 `RuntimeError`

---

## 3. 收消息：WebSocket 网关

QQ Bot 有两种接法，本实现用 **WebSocket 长连接**（不需要公网回调）：

| 方式 | 优点 | 缺点 |
|---|---|---|
| WebSocket 网关（本实现） | 无需公网 IP / 回调 | 断线需重连、要处理心跳与 Resume |
| HTTP 回调 | 官方推荐 | 需公网 HTTPS 端点 |

### 3.1 连接流程

```
GET  /gateway                       ← 每次重连都重新取地址
WS   连接 gateway
收    op=10 Hello（拿 heartbeat_interval）
发    op=2 Identify（首连） 或 op=6 Resume（有 session_id + seq）
循环  收 Dispatch(op=0) → 处理事件
发    op=1 Heartbeat（按 interval，携带 last_seq）
```

### 3.2 OpCode 与 Intents

| OpCode | 常量 | 含义 |
|---|---|---|
| 0 | `OP_DISPATCH` | 事件分发 |
| 1 | `OP_HEARTBEAT` | 心跳 |
| 2 | `OP_IDENTIFY` | 首次鉴权 |
| 6 | `OP_RESUME` | 断线恢复 |
| 7 | `OP_RECONNECT` | 服务端要求重连 |
| 9 | `OP_INVALID_SESSION` | 会话失效，需清 session 重新 Identify |
| 10 | `OP_HELLO` | 连接建立，返回心跳间隔 |
| 11 | `OP_HEARTBEAT_ACK` | 心跳确认 |

Intents：

```python
INTENT_GROUP_AND_C2C  = 1 << 25   # 群聊 + 私聊(C2C) 消息
INTENT_DIRECT_MESSAGE = 1 << 12   # 频道私信
intents = INTENT_GROUP_AND_C2C | INTENT_DIRECT_MESSAGE
```

> ⚠️ 当前实现只消费 **群聊 / C2C** 三类事件（见 3.3）。频道（guild）的
> `AT_MESSAGE_CREATE`、`DIRECT_MESSAGE_CREATE` 虽然申请了 intent，但**没有注册处理分支**，
> 收到也不会进对话链路。需要频道能力时要在 `ALL_EVENTS` 和 `_dispatch` 里补。

### 3.3 已处理事件

| 事件 | 场景 | 目标 ID |
|---|---|---|
| `C2C_MESSAGE_CREATE` | 私聊消息 | `author.user_openid` |
| `GROUP_AT_MESSAGE_CREATE` | 群内 @ 机器人 | `group_openid` |
| `GROUP_MESSAGE_CREATE` | 群消息（是否推送取决于平台订阅） | `group_openid` |

### 3.4 心跳、重连与去重

- 心跳间隔取 Hello 的 `heartbeat_interval`（毫秒 → 秒）
- 断线重连退避：`[1, 2, 3, 5, 8, 10]` 秒
- `READY` 记录 `session_id`；`RESUMED` 表示恢复成功
- `op=9` 清空 `session_id` 与 `last_seq`，下次走 Identify
- Dispatch 事件按消息 `id` 去重：`_recent_msg_ids` 超过 5000 时裁剪到最近 2000，
  防止重连重放导致重复回复

---

## 4. 入站消息解析

`_dispatch(event_type, data)` 把平台事件转成项目标准消息：

```python
{
    "platform": "qqbot",
    "message_id": "...",
    "user_id": "member_openid 或 user_openid",
    "group_id": "group_openid 或 None",
    "content": "去掉 <@!123> 后的文本",
    "files": [{"type": "image|video|voice|file", "url": "...", "name": "..."}],
    "raw_data": data,
}
```

**解析要点**：

- 发送者 ID：优先 `author.member_openid`（群），再 `author.user_openid`（私聊）
- @ 提及用正则 `<@!?[^>]*>` 去掉，避免把 `<@!123>` 当成正文喂给模型
- 群聊/私聊开关：`disable_group`、`disable_private` 在解析阶段直接丢弃
- 附件字段差异很大，适配器会打一条 `qqbot_inbound_attachments` 日志
  （记录每个附件的 key 与 `content_type`），方便排查类型误判

### 4.1 附件类型判定

**必须用 `content_type`，不能只看扩展名**：QQ 的语音/视频附件经常没有
`filename`（或没有扩展名）。

| `content_type` 前缀 | 归为 |
|---|---|
| `image/` | `image`（会作为多模态 `image_url` 发给模型） |
| `audio/` | `voice`（走语音转写） |
| `video/` | `video` |
| 其它 / 缺失 | `file`（按文档解析器抽文本） |

缺 `filename` 时补一个带扩展名的占位名（如 `image.png`、`voice.silk`），
否则后续按扩展名解析会失败。

附件 URL 可能以 `/` 开头（相对路径），下载前补成
`https://multimedia.nt.qq.com` + 原路径。QQ 给的是**直链**，无需解密。

### 4.2 被动回复的 `msg_id`

QQ 平台只在收到消息后的短时间内允许「被动回复」；群聊消息必须带原消息 `msg_id`。

实现方式：

- 入站时按 **回复目标**（群 `group_openid` / 私聊 `user_openid`）写入
  `self._sessions[target] = {"msg_id": msg_id}`
- 出站文本/媒体时从这里取 `msg_id`，有值才放进 body
- **空字符串不能传**：官方 SDK 用 `...(msgId ? {msg_id} : {})` 条件展开，
  传空串会被判无效回复

⚠️ 每个目标只保留**最新一条**入站 `msg_id`。同一目标并发多条消息时，
后到的消息会覆盖前一个，回复可能引用错消息；正常串行对话不受影响。

---

## 5. 发文本

```
POST https://api.sgroup.qq.com/v2/users/{openid}/messages        # 私聊
POST https://api.sgroup.qq.com/v2/groups/{group_openid}/messages # 群聊

Header: Authorization: QQBot {access_token}

Body:
{
  "content": "文本",
  "msg_type": 0,              # 0=纯文本，2=Markdown
  "msg_seq": 12345,
  "msg_id": "<可选，被动回复的原消息 id>"
}
```

**实现要点**：

- `msg_seq` 由适配器生成：`(毫秒时间戳 % 1e8 ^ random) % 65535`，用于消息去重窗口
- `msg_id` 有值才带，空串条件省略
- 群聊与私聊走同一个 `_send_text`，只有 URL 前缀不同
- 本项目当前**不发送** `X-Union-Appid` 头（早期文档提过，实测不需要）

### 5.1 Markdown 消息

机器人有 Markdown 权限时（`markdown_support=true`，默认开启），
**私聊与群聊都**改用：

```json
{
  "content": "**加粗** 文本",
  "msg_type": 2,
  "markdown": {"content": "**加粗** 文本"},
  "msg_seq": 12345,
  "msg_id": "..."
}
```

官方 `openclaw-qqbot` 的 `buildMessageBody` 对 C2C / 群聊使用同一套构造，
只按 `markdownSupport` 一个开关切换，**不区分会话类型**。本项目对齐这一点：

```python
use_md = self.markdown_support and bool((content or "").strip())
msg_type = 2 if use_md else 0
```

⚠️ Markdown 内容为空会被平台拒绝，**空正文时一律回落 `msg_type=0` 纯文本**。
关闭 `markdown_support` 后同样回落纯文本，且不带 `markdown` 字段。

---

## 6. 发富媒体（三步：上传 → 拿 file_info → 发消息）

QQ 的媒体发送分两步，但**无需加密**，比微信简单。

### 6.1 上传媒体

```
POST https://api.sgroup.qq.com/v2/users/{openid}/files        # 私聊
POST https://api.sgroup.qq.com/v2/groups/{group_openid}/files # 群聊

Body:
{
  "file_type": 1,            # 见下表
  "file_data": "<base64>",   # 本项目直接传 base64
  "url": "",                 # 与 file_data 二选一；当前实现固定传空串
  "srv_send_msg": false      # false=只上传，不自动发送
}
```

`file_type` 取值：

| 值 | 类型 | 说明 |
|---|---|---|
| 1 | 图片 | |
| 2 | 视频 | |
| 3 | 语音 | **支持 wav / mp3 / silk 直传，无需转码**；单文件 ≤20MB |
| 4 | 文件 | |

响应：

```json
{"file_uuid": "...", "file_info": "...", "ttl": 3600}
```

⚠️ **要点**：

- `srv_send_msg: false` 表示只上传，返回 `file_info` 供第二步使用
- 想一步到位可设 `true`，但无法附带文本内容
- `file_info` 有 TTL（约 1 小时），需及时发送
- **当前实现不传 `file_name`**。官方文档标注 `file_type=4` 需要 `file_name`；
  如果遇到文件上传被拒，在 body 里补 `"file_name": name` 即可（适配器已把文件名
  保留到临时文件的 suffix 中）

### 6.2 发送媒体消息

```
POST https://api.sgroup.qq.com/v2/{groups|users}/{target}/messages

Body:
{
  "msg_type": 7,                          # 7=富媒体
  "msg_seq": 12345,
  "media": {"file_info": "<上一步的 file_info>"},
  "msg_id": "<可选，回复的目标消息>"
}
```

`msg_type` 对应关系：

| msg_type | 内容 |
|---|---|
| 0 | 纯文本 |
| 2 | Markdown |
| 3 | Ark 模板 |
| 7 | 富媒体（图片/视频/语音/文件） |

### 6.3 完整实现

```python
async def _send_media(self, target_id, source, file_name,
                      file_type, is_group, is_ftype=False):
    # source 可以是本地路径，也可以是 http(s) URL（先下载成 bytes）
    data = (await self.download_bytes(source)
            if str(source).startswith("http")
            else Path(source).read_bytes())

    file_type_num = (file_type if is_ftype else
                     {"image": 1, "video": 2, "voice": 3}.get(file_type, 4))

    srv_type = "groups" if is_group else "users"
    b64 = base64.b64encode(data).decode()

    # 1) 上传拿 file_info
    up = await self._api("POST", f"/v2/{srv_type}/{target_id}/files", {
        "file_type": file_type_num,
        "url": "",
        "srv_send_msg": False,
        "file_data": b64,
    })
    file_info = str((up or {}).get("file_info", "") or "")
    if not file_info:
        return {"error": f"媒体上传失败(file_type={file_type_num}, {len(data)}B)：{up}"}

    # 2) 发媒体消息
    body = {"msg_type": 7, "msg_seq": _next_msg_seq(),
            "media": {"file_info": file_info}}
    msg_id = (self._sessions.get(str(target_id)) or {}).get("msg_id", "")
    if msg_id:
        body["msg_id"] = msg_id
    return await self._api("POST", f"/v2/{srv_type}/{target_id}/messages", body)
```

**错误信息**会带上 `file_type` 和字节数（如 `file_type=3, 12345B`），
便于区分「格式被拒」和「会话失效」。

---

## 7. 语音回复链路

渠道里用户说「用语音说 / 读出来 / 发语音」时，模型调用 `send_voice`：

```
send_voice 工具
  → manager 合成 mp3（使用渠道配置的 tts_voice_id，留空用默认音色）
  → 放入本轮 media 列表 {"type": "voice", "name": "reply.mp3", "data": ...}
  → BaseAdapter.dispatch 先发媒体、后发文本
  → QQAdapter.send_file_bytes(file_type="voice")
  → file_type=3 直传原始 mp3（不转码）
```

**降级规则**：

- 合成音频超过 **20MB**（`MAX_VOICE_BYTES`）：不投递语音，只发文本
- 平台拒收语音（格式/权限/超限）：`dispatch` 自动把该条降级为**文件**再发一次，
  同时补发文本，用户至少能点开播放
- 语音发送成功时：**不再重复发一遍相同文本**（语音已承载正文）
- 语音同时会落一份产物到会话，网页端打开该会话也能播放

---

## 8. 渠道运行时（manager）

两份渠道文档共用同一套运行时，QQ 侧要点：

### 8.1 生命周期与状态

- `start_channel`：先停旧实例 → 创建适配器 → 后台任务 `start()` → 状态 `running`
- 适配器崩溃：状态置 `error` 并写 `last_error`，前端可见
- `stop_channel`：同时取消该渠道正在跑的对话任务，避免继续消耗模型配额
- 适配器内部可通过 `manager.mark_status` 主动上报运行/错误状态

### 8.2 消息处理

- 同一渠道 `(user_id, channel_id)` 串行处理；新消息会**打断**上一条正在执行的任务
- 「停止 / 取消 / 算了 / /stop」等短指令会立即中断当前任务并回复确认
- 每个「渠道 + 发送者」一个独立会话，标题形如 `渠道名 · 发送者尾号`
- 入站附件保存到 `artifact_dir/{user_id}/workspace/inbox/`，并登记为会话产物（网页端可见）
- 图片会转成多模态 `image_url` 发给模型；语音自动转写；文档自动抽取文本
- 回复时按「先附件，后文本」发送；产物来自本轮新建文件 + `send_artifact` 指定的历史产物

### 8.3 管理员与工作流

- `admin_ids` 里的人可用管理工具（定时任务、渠道/模型/技能/MCP/工作流管理等）
- `/myuid` 返回当前发送者 ID；`/help` 显示渠道内命令
- `reply_mode=workflow` 时，消息内容作为工作流输入，执行结果直接回复，不建会话

### 8.4 主动推送（定时任务等）

`manager.push_message` 的目标选择顺序：

1. 调用方显式指定 `target`（群/私聊）
2. 渠道 `admin_ids` 排序后的第一个
3. 最近一次入站发送者 `last_sender_id`
4. 老数据兜底：从会话标题恢复最近发送者

群聊里发起的定时任务会记住 `last_group_id`，能回到原群。

---

## 9. 与微信的关键差异

| 项目 | 微信 wxclaw | QQ Bot |
|---|---|---|
| 认证 | `AuthorizationType` + 随机 UIN | `Authorization: QQBot <token>` |
| token 有效期 | 扫码后长期（但 24h 会话窗口） | 2 小时，需自动刷新 |
| 收消息 | HTTP 长轮询 | WebSocket 长连接（心跳 + Resume） |
| 响应格式 | octet-stream 伪装 JSON | 标准 JSON |
| 发文件 | getuploadurl → CDN 上传 → sendmessage | files 上传 → messages |
| 文件加密 | **AES-ECB 加密** | **无加密** |
| 回复限制 | 24h 内（context_token） | 群聊需 `msg_id` 被动回复 |
| 错误码 | 统一 -14，难定位 | 有明确 HTTP / code |
| 出站语音 | **不支持**（降级为文件） | **原生支持**（file_type=3） |
| 富媒体消息 | item.type 2/4 | msg_type=7 + `media.file_info` |

---

## 10. 踩坑清单

| # | 现象 | 原因 | 修复 |
|---|---|---|---|
| 1 | 401 / 鉴权失败 | `Authorization` 前缀写错 | 用 `QQBot` 不是 `Bearer` |
| 2 | 发消息 403 | 群聊缺 `msg_id` | 被动回复必须带原消息 id |
| 3 | 传了空 `msg_id` 被判无效 | body 里放了空串 | 有值才带，空串条件省略 |
| 4 | token 频繁失效 | 没缓存 / 没提前刷新 | 缓存 + 提前 60s 刷新 + 加锁；401/403 强制刷新重试 |
| 5 | 上传成功但发不出 | 忘了第二步 sendmessage | 上传返回 `file_info` 后再发 |
| 6 | `file_info` 过期 | 有 TTL | 上传后立即发送 |
| 7 | 群消息无响应 | 平台只在 @ 时推 `GROUP_AT_MESSAGE_CREATE` | 让用户 @ 机器人；`require_mention` 目前不参与过滤 |
| 8 | 私聊永远是纯文本 | 早先写了 `is_group and` 才用 Markdown | 私聊/群聊共用同一套构造 |
| 9 | Markdown 发送被拒 | 正文为空 | 空正文回落 `msg_type=0` |
| 10 | 收到附件下载失败 | 直链已过期 / 相对路径没补域名 | 收到后立即下载；`/` 开头补 `https://multimedia.nt.qq.com` |
| 11 | 语音被当成文件 | 只看扩展名，附件没 filename | 用 `content_type` 判定类型 |
| 12 | 后续解析失败 | 缺 filename 没占位名 | 补带扩展名的占位名（`image.png`） |
| 13 | 回复重复 | 断线重连重放事件 | 按消息 id 去重（5000/2000） |
| 14 | 回复引用了错消息 | 同一目标只保留最新 `msg_id` | 正常串行对话即可；并发场景需按消息上下文管理 |
| 15 | 文件上传被拒（file_type=4） | 当前上传 body 未带 `file_name` | 在 files 请求里补 `file_name` |
| 16 | 频道消息完全没反应 | 只处理群聊/C2C，未注册 guild 事件 | 需要时在 `ALL_EVENTS`/`_dispatch` 补 |

---

## 11. API 与配置参考

| 接口 | 用途 |
|---|---|
| `GET /api/v1/channels/types` | 返回渠道类型与字段定义（前端动态渲染表单） |
| `POST /api/v1/channels` | 创建渠道 |
| `PATCH /api/v1/channels/{id}` | 更新配置（自动过滤占位符、合并旧值、重启渠道） |
| `POST /api/v1/channels/test` | 用当前填写的配置试连（QQ：换一次 AccessToken） |
| `POST /api/v1/channels/{id}/start` / `stop` | 启动 / 停止 |
| `GET /api/v1/channels/{id}/config/{key}` | 查看单个敏感配置明文（`client_secret` / `token` / `x_wechat_uin`） |
| `POST /api/v1/channels/qqbot/scan-login` | 生成绑定任务与二维码 |
| `POST /api/v1/channels/qqbot/login-status` | 轮询扫码状态，完成后落库并热启动 |

**敏感字段脱敏**：`SENSITIVE_KEYS = {client_secret, token, x_wechat_uin}`。
列表接口只返回 `前4****后4`（长度 ≤8 时全 `****`），需要明文走 reveal 接口。

**配置保存防覆盖**：`_sanitize_config` 会丢弃含 `已自动填入` / `****` 的占位串，
并沿用旧值，避免前端把脱敏值当真实值提交、清掉扫码得到的凭据。

---

## 12. 回归测试

| 文件 | 覆盖点 |
|---|---|
| `backend/tests/test_qqbot_login.py` | 绑定 key 32 字节、GCM 解密、错误 key/短密文/错长度、扫码状态机、二维码生成 |
| `backend/tests/test_qqbot_sessions.py` | 入站 `msg_id` 落 `_sessions`、群聊按 `group_openid` 索引、语音 `file_type=3` 不转码、被动回复带 `msg_id`、空 `msg_id` 省略、Markdown 私聊/群聊、空正文回落、附件 `content_type` 判定、直链/相对路径下载 |

运行：

```bash
cd backend
pytest tests/test_qqbot_login.py tests/test_qqbot_sessions.py -q
```

---

## 13. 参考实现

| 文件 | 内容 |
|---|---|
| `backend/app/channels/qqbot_adapter.py` | WebSocket 网关、token 管理、文本/媒体收发、附件下载 |
| `backend/app/channels/base_adapter.py` | 渠道基类：dispatch、附件发送、媒体降级 |
| `backend/app/channels/manager.py` | 渠道生命周期、会话、入站文件、产物、推送 |
| `backend/app/api/v1/qqbot_login.py` | 扫码绑定、AES-256-GCM 解密、凭据落库 |
| `backend/app/api/v1/channels.py` | 渠道 CRUD、字段定义、脱敏、试连 |

---

*最后更新：2026-11-06*
