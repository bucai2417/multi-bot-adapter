# 协议速查：QQ 官方 Bot vs 微信 wxclaw

> 只放「接渠道时最常翻」的端点、请求头、字段和映射。
> 完整说明见 `qqbot-integration.md` / `wxclaw-integration.md`。

---

## 1. 基础信息

| 项目 | QQ 官方 Bot | 微信 wxclaw（iLink Bot） |
|---|---|---|
| API 基址 | `https://api.sgroup.qq.com` | `https://ilinkai.weixin.qq.com` |
| 取 token | `POST https://bots.qq.com/app/getAppAccessToken` | 扫码后直接拿 `bot_token` |
| 收消息 | WebSocket 网关 | `POST /ilink/bot/getupdates` 长轮询 |
| 发消息 | `POST /v2/{groups|users}/{target}/messages` | `POST /ilink/bot/sendmessage` |
| 媒体上传 | `POST /v2/{groups|users}/{target}/files` | `POST /ilink/bot/getuploadurl` + CDN |
| 响应格式 | 标准 JSON | octet-stream 伪装 JSON |
| 错误定位 | HTTP 状态 + code | 统一 `errcode=-14` / `ret` |

---

## 2. 请求头

**QQ**

```http
Authorization: QQBot <access_token>
Content-Type: application/json
```

**wxclaw**

```http
Content-Type: application/json
AuthorizationType: ilink_bot_token
Authorization: Bearer <bot_token>
X-WECHAT-UIN: <4 字节随机整数的 base64，非空>
```

> QQ 的 `Authorization` 前缀是 `QQBot`，不是 `Bearer`。
> wxclaw 三件套缺一不可，缺失统一返回 `-14`。

---

## 3. 收消息

### QQ WebSocket

```
GET /gateway → {url}
WS connect
← op=10 Hello {heartbeat_interval}
→ op=2 Identify {token, intents, shard}   # 首连
   或 op=6 Resume {token, session_id, seq} # 重连
← op=0 Dispatch {t, d, s}
→ op=1 Heartbeat {last_seq}
```

| OpCode | 含义 |
|---|---|
| 0 | Dispatch |
| 1 | Heartbeat |
| 2 | Identify |
| 6 | Resume |
| 7 | Reconnect |
| 9 | Invalid Session（清 session 重连） |
| 10 | Hello |
| 11 | Heartbeat ACK |

Intents：`1 << 25`（群聊 + C2C）、`1 << 12`（频道私信）。

事件：

| 事件 | 目标 ID |
|---|---|
| `C2C_MESSAGE_CREATE` | `author.user_openid` |
| `GROUP_AT_MESSAGE_CREATE` | `group_openid` |
| `GROUP_MESSAGE_CREATE` | `group_openid` |

### wxclaw 长轮询

```json
POST /ilink/bot/getupdates
{
  "get_updates_buf": "<上次游标，首次空>",
  "base_info": {"channel_version": "1.0.3"}
}
```

响应：

```json
{"ret": 0, "get_updates_buf": "<新游标>", "msgs": [...]}
```

游标兼容：`get_updates_buf` → `next_get_updates_buf` → 旧游标。

---

## 4. 消息结构

### QQ 入站

```json
{
  "id": "msg-id",
  "author": {"member_openid": "群成员", "user_openid": "私聊用户"},
  "group_openid": "群 id",
  "content": "<@!123> 你好",
  "attachments": [
    {"filename": "a.png", "url": "https://...",
     "content_type": "image/png", "size": 1234}
  ]
}
```

- @ 提及用 `<@!?[^>]*>` 清掉
- 附件类型以 `content_type` 为准：`image/`、`audio/`、`video/`、其它
- 相对 URL（`/` 开头）补 `https://multimedia.nt.qq.com`
- 缺 `filename` 时补带扩展名的占位名

### wxclaw 入站

```json
{
  "message_type": 1,
  "from_user_id": "o9cq8...@im.wechat",
  "message_id": "...",
  "context_token": "...",
  "item_list": [
    {"type": 1, "text_item": {"text": "你好"}},
    {"type": 2, "image_item": {"media": {"aes_key": "...", "encrypt_query_param": "..."}}},
    {"type": 3, "voice_item": {"text": "转写文本"}},
    {"type": 4, "file_item": {"file_name": "a.pdf", "len": 123,
                              "media": {"aes_key": "...", "encrypt_query_param": "..."}}},
    {"type": 5}
  ]
}
```

`context_token` 按 `from_user_id` 缓存 24 小时。

---

## 5. 发文本

### QQ

```json
POST /v2/{groups|users}/{target}/messages
{
  "content": "**你好**",
  "msg_type": 2,
  "markdown": {"content": "**你好**"},
  "msg_seq": 12345,
  "msg_id": "<被动回复时才带>"
}
```

- `markdown_support=false` 或正文为空 → `msg_type=0`，不带 `markdown`
- 私聊与群聊使用同一套构造
- `msg_id` 空串不能传

### wxclaw

```json
POST /ilink/bot/sendmessage
{
  "msg": {
    "from_user_id": "",
    "to_user_id": "<user_id>",
    "client_id": "af_<ms>_<rand>",
    "message_type": 2,
    "message_state": 2,
    "create_time_ms": 0,
    "update_time_ms": 0,
    "context_token": "<24h 内有效>",
    "item_list": [{"type": 1, "text_item": {"text": "你好"}}]
  },
  "base_info": {"channel_version": "1.0.3"}
}
```

---

## 6. 发媒体

### QQ

| 步骤 | 请求 |
|---|---|
| 1. 上传 | `POST /v2/{groups\|users}/{target}/files`，body：`file_type`、`file_data`（base64）、`srv_send_msg=false` |
| 2. 发送 | `POST /v2/{groups\|users}/{target}/messages`，body：`msg_type=7`、`media.file_info` |

`file_type`：

| 值 | 类型 |
|---|---|
| 1 | 图片 |
| 2 | 视频 |
| 3 | 语音（wav/mp3/silk 直传，≤20MB） |
| 4 | 文件 |

### wxclaw

| 步骤 | 请求 |
|---|---|
| 1. 取地址 | `POST ilink/bot/getuploadurl`：`filekey`、`media_type`、`rawsize`、`rawfilemd5`、`filesize`、`aeskey`、`no_need_thumb` |
| 2. CDN 上传 | `POST {CDN}/upload?encrypted_query_param=...&filekey=...`，body 为**加密后的字节** |
| 3. 发消息 | `POST ilink/bot/sendmessage`，item 内放 `media` |

`media_type`：1=图片，3=文件（视频/语音也走 3）。

| item.type | 内部字段 |
|---|---|
| 2 | `image_item: {media, mid_size}` |
| 4 | `file_item: {media, file_name, len}` |
| 5 | `video_item: {media, video_size, play_length}`（当前发送路径未启用） |

CDN 回执：响应头 `x-encrypted-param`（大小写不敏感）。

---

## 7. 媒体下载与解密

| 项目 | QQ | wxclaw |
|---|---|---|
| 下载 | 直链 `attachments[].url` | 直链 `url` 或 `CDN/download?encrypted_query_param=` |
| 加密 | 无 | AES-ECB + PKCS7 |
| 密钥 | — | `aes_key`，候选顺序 hex → base64 → 原文 |
| 校验 | — | PKCS7 padding 合法才算成功 |
| 失败 | 返回 None + 日志 | 返回原始密文 + 警告日志 |

---

## 8. 语音

| 项目 | QQ | wxclaw |
|---|---|---|
| 入站 | `content_type audio/*` → 转写 | `voice_item.text`（已转写） |
| 出站 | `file_type=3` 直传 | 协议未开放，降级为文件 |
| 大小 | ≤20MB | 文件通道，建议 <10MB |
| 失败降级 | 改发文件 + 补文本 | 同左 |

---

## 9. 去重与状态

| 项目 | QQ | wxclaw |
|---|---|---|
| 去重键 | 事件 `id` | `message_id` |
| 去重容量 | 5000 → 裁到 2000 | 3000 → 裁到 1500 |
| 重连 | 退避 `[1,2,3,5,8,10]` 秒，Resume | 轮询失败放慢；`-14` 放慢到 10 秒 |
| 状态上报 | 崩溃/异常 → `error` | `mark_status` → `error` + `last_error` |

---

## 10. 一页检查表

- [ ] QQ 请求头用 `QQBot`；wxclaw 三件套齐全
- [ ] QQ token 缓存 + 提前 60s 刷新；401/403 强制刷新重试
- [ ] wxclaw `get_updates_buf` 与 `base_info.channel_version` 正确
- [ ] 两个渠道都做消息 id 去重
- [ ] QQ `msg_id` 空串省略；wxclaw `context_token` 24h
- [ ] wxclaw `upload_param` 顶层 + `data` 双查
- [ ] wxclaw AES key 顺序 hex → base64 → 原文，PKCS7 校验
- [ ] QQ 附件类型用 `content_type`，相对 URL 补域名
- [ ] 语音失败降级文件并补文本；超大语音跳过
- [ ] 敏感配置脱敏；占位符不落库；错误写入 `last_error`
