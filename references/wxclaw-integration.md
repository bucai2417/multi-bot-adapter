# 微信 wxclaw（iLink Bot）接入完全指南

> 本文档基于**实际调试一个能跑通的 wxclaw 渠道**的全过程整理，
> 记录了每一个踩过的坑和对应修复。照此实现可避免数天试错。
>
> 适配对象：微信 iLink Bot（`ilinkai.weixin.qq.com`，非官方协议）
> 已在生产验证：文本收发、图片/文件双向传输、24h 会话窗口。
>
> 实现位置：
> `backend/app/channels/wxclaw_adapter.py`（适配器）
> `backend/app/api/v1/wxclaw_login.py`（扫码登录 / 诊断）
> `backend/app/api/v1/channels.py`、`backend/app/channels/manager.py`（渠道管理）

---

## 0. 一分钟速览

```
扫码登录  get_bot_qrcode → get_qrcode_status → 拿到 bot_token
收消息    POST /ilink/bot/getupdates   （长轮询，游标 get_updates_buf）
发文本    POST /ilink/bot/sendmessage （msg 包裹 + message_type=2）
发文件    getuploadurl → CDN 上传（AES-ECB 加密）→ sendmessage（item.type=4）
收文件    CDN download（encrypt_query_param）→ AES-ECB 解密
```

**三个必踩的坑**（后面详解）：

1. 缺 `AuthorizationType: ilink_bot_token` 头 → 一律返回 `errcode -14 session timeout`
2. `X-WECHAT-UIN` 不能为空 → 必须随机 base64
3. `upload_param` 在响应**顶层**而非 `data` 里

**两个容易忽略但会偶发的问题**：

4. `aes_key` 的多种编码要按「hex → base64 → 原文」顺序尝试，
   顺序错了会解出乱码（32 字符 hex 的 base64 解码恰好也是合法 AES 长度）
5. 同一 `message_id` 会因轮询游标回退被重复投递，适配器已做去重

---

## 1. 认证：请求头三件套

这是**最容易卡住的地方**。微信对不合规请求统一返回：

```json
{"errcode": -14, "errmsg": "session timeout"}
```

⚠️ **重要**：这个错误码对「无 token」「空 token」「假 token」「格式错误」**一律相同**，
所以看到 -14 时**无法判断**是 token 失效还是请求头写错。必须逐项核对。

### 正确的请求头

```python
def _headers(self) -> dict:
    uin = self.x_wechat_uin
    if not uin:
        # 必须随机 base64，空字符串会被判为无效会话
        uin = base64.b64encode(
            str(int.from_bytes(os.urandom(4), "big")).encode()
        ).decode()
    auth = (self.token if self.token.lower().startswith("bearer ")
            else f"Bearer {self.token}")
    return {
        "Content-Type": "application/json",
        "AuthorizationType": self.auth_type,   # 默认 ilink_bot_token，缺这行必失败
        "Authorization": auth,                 # 必须有 "Bearer " 前缀
        "X-WECHAT-UIN": uin,                   # 必须非空随机 base64
    }
```

| 头 | 是否必须 | 说明 |
|---|---|---|
| `AuthorizationType: ilink_bot_token` | **必须** | 缺了直接 -14，且无任何提示；可通过配置 `authorization_type` 覆盖 |
| `Authorization: Bearer <token>` | 必须 | 注意带 `Bearer ` 前缀；已带前缀时不重复添加 |
| `X-WECHAT-UIN` | **必须非空** | 4 字节随机整数的 base64；空串会失败；可用 `x_wechat_uin` 固定 |
| `Content-Type: application/json` | 必须 | 虽然是 JSON，但服务器返回 octet-stream |

---

## 2. 扫码登录

### 2.1 取二维码

```
GET https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3
```

响应（注意 `Content-Type: application/octet-stream`，但内容是 JSON）：

```json
{
  "qrcode": "f6d237c207675b0bd1fa5cf2be377f97",
  "qrcode_img_content": "https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=...&bot_type=3",
  "ret": 0
}
```

- `qrcode` → 后续轮询用的 ticket
- `qrcode_img_content` → 给用户扫的链接（后端用 `segno` 本地转成 PNG dataURL）

**本项目接口**：

| 接口 | 请求 | 说明 |
|---|---|---|
| `POST /api/v1/channels/wxclaw/scan-login` | `{channel_id?, api_url?, login_qr_path?, bot_type?}` | 取二维码；若渠道已存在且响应里带 token，会立即落库并热启动 |
| `POST /api/v1/channels/wxclaw/login-status` | `{channel_id?, api_url?, ticket?, login_check_path?}` | 轮询扫码状态；成功后回填 token 并热启动 |
| `POST /api/v1/channels/wxclaw/diagnose` | `{channel_id}` | 诊断当前 token 是否被上游接受 |

路径均可配置（`login_qr_path`、`login_check_path`），默认：

```
login_qr_path     = ilink/bot/get_bot_qrcode
login_check_path  = ilink/bot/get_qrcode_status
```

`_normalize_url` 会处理 `api_url` 以 `/v1` 结尾的情况，路径挂在同级。

### 2.2 轮询扫码状态

```
GET https://ilinkai.weixin.qq.com/ilink/bot/get_qrcode_status?qrcode=<ticket>
Header: iLink-App-ClientVersion: 1
```

**这是长轮询**：用户未扫码时请求会挂起，超时是正常现象（后端 12 秒超时）。

状态流转：

```json
{"ret": 0, "status": "wait"}       // 等待扫码
{"ret": 0, "status": "confirmed",  // 扫码成功！
 "bot_token": "xxxx@im.bot:0600004...",
 "ilink_bot_id": "xxxx@im.bot",
 "ilink_user_id": "o9cq8...@im.wechat",
 "baseurl": "https://ilinkai.weixin.qq.com"}
{"ret": 0, "status": "expired"}    // 二维码过期（约 40 秒），需重新获取
```

### 2.3 关键实现要点

```python
try:
    async with session.get(url, headers={"iLink-App-ClientVersion": "1"},
                           timeout=aiohttp.ClientTimeout(total=12)) as resp:
        raw = await resp.read()          # 不能 resp.json()：MIME 是 octet-stream
        text = raw.decode("utf-8", "replace").strip()
        if text:
            parsed = _extract_login_payload(json.loads(text))
            if parsed.get("token"):
                return parsed            # 成功
            if parsed.get("status") == "expired":
                return None              # 重新取码
except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
    return "wait"                        # ⚠️ 超时=继续等，不是错误
except Exception:
    return "error"
```

⚠️ **坑**：长轮询超时如果当成 error 返回，前端会误以为扫码失败。

⚠️ **坑**：二维码约 **40 秒**过期，用户必须及时扫。

### 2.4 登录响应宽松解析

上游字段名在不同版本间有差异，`_extract_login_payload` 做了兼容：

| 目标 | 候选字段（按优先级） |
|---|---|
| `token` | `token` / `access_token` / `bot_token` / `auth_token` |
| `qr` | `qr_url` / `qrcode_url` / `qrcode_img_content` / `qrcodeUrl` / `url` / `qr` / `qr_base64` / `image` |
| `ticket` | `ticket` / `uuid` / `login_id` / `qrcode` / `key` / `state` |
| `status` | `status` / `state`（默认 `wait`） |
| `bot_id` | `ilink_user_id` / `user_id` / `myuid` / `ilink_bot_id` / `bot_id` |

解析时会**主动丢弃占位 token**（含 `已自动填入`、`****`、`••`），
与保存环节的 `_sanitize_config` 形成双重保险。

### 2.5 保存 token 与多账号

扫码成功后，`_upsert_account` 写入：

```json
{
  "token": "xxxx@im.bot:060000...",
  "api_url": "https://ilinkai.weixin.qq.com",
  "x_wechat_uin": "",
  "accounts": [
    {"name": "o9cq8...@im.wechat", "token": "...", "api_url": "...",
     "x_wechat_uin": "", "enabled": true}
  ]
}
```

- `accounts` 里按 `name`（通常是 `bot_id` / `ilink_user_id`）更新或追加
- 顶层 `token` / `api_url` / `x_wechat_uin` 始终同步，适配器实际读顶层字段
- `accounts` 里的 token 同样是敏感字段，列表接口会逐项脱敏

⚠️ **坑**：前端千万不要把占位符（如 `••••••（已自动填入）`）当 token 提交回来，
否则会覆盖真 token。保存接口 `_sanitize_config` 会过滤占位串并沿用旧值。

### 2.6 回填到输入框（前端）

⚠️ **坑**：扫码成功后**不要**把表单里的 token 清空只留一句「已自动配置」——
用户看不到值会以为没生效。正确做法：

- `scan-login` / `login-status` 响应里带上 `data.token`（真值）与 `data.api_url`
- 前端把 `data.token` 直接写进「Bot Token」输入框，并自动切换为明文显示
- 新建渠道时还没有渠道记录可落库，token 只存在于表单，
  必须提示用户「点保存创建渠道」，否则关掉弹窗 token 就丢了
- 已有渠道时后端会自动落库并热启动；启动失败通过 `start_error` 回传，不影响已保存的 token

### 2.7 诊断 token

`POST /api/v1/channels/wxclaw/diagnose` 会用与适配器完全一致的请求头发一次
`getupdates`：

| 结果 | 判定 |
|---|---|
| `errcode=0` / `ret=0` | token 有效 |
| `errcode=-14` | token 失效，提示重新扫码 |
| 长轮询超时 | **token 有效**（正在等消息） |
| 其它 errcode | 原样返回错误码与 msg |

---

## 3. 收消息（长轮询）

```
POST https://ilinkai.weixin.qq.com/ilink/bot/getupdates
```

### 请求体

```json
{
  "get_updates_buf": "",                        // 游标；首次为空，之后填上次返回值
  "base_info": {"channel_version": "1.0.3"}     // ⚠️ 必须有
}
```

⚠️ **坑**：字段名是 `get_updates_buf`，**不是** `sync_buf`。
用错字段名同样返回 -14（因为认证校验也看 base_info）。

`channel_version` 可通过渠道配置覆盖（默认 `1.0.3`）。

### 响应

```json
{
  "ret": 0,
  "get_updates_buf": "next_cursor_string",
  "msgs": [ { ...单条消息... } ]
}
```

游标读取（兼容两种返回字段）：

```python
self.cursor = str(
    updates.get("get_updates_buf")
    or updates.get("next_get_updates_buf")
    or self.cursor
)
```

### 消息去重

同一 `message_id` 可能因游标回退被重复投递。适配器维护 `_seen_updates`：

- 命中重复 `message_id` → 跳过
- 集合超过 3000 条时裁剪到最近 1500 条

### 错误码与轮询节奏

服务器返回 `Content-Type: application/octet-stream`，但内容是 JSON。
**不能用 `resp.json()`**（aiohttp 会因 MIME 不符抛错）：

```python
raw = await resp.read()
text = raw.decode("utf-8", "replace").strip()
data = json.loads(text)

# 错误码：正常响应是 ret=0，认证失败返回 errcode
code = data.get("errcode") if "errcode" in data else data.get("ret", 0)
if int(code or 0) != 0:
    raise WxclawApiError(code, data.get("errmsg") or data.get("msg") or "", path)
```

⚠️ **坑**：`errcode` 和 `ret` 是两个不同字段。
正常响应带 `ret: 0`；某些错误只带 `errcode`。**两个都要判断**，
否则会把 `ret: 0` 误判为失败。

轮询循环的错误处理：

| 情况 | 行为 |
|---|---|
| `-14 session timeout` | 状态上报「登录已失效，请重新扫码」，轮询放慢到 **10 秒**一次 |
| 其它 `WxclawApiError` | 前 2 次记 warning + 上报错误，之后继续轮询 |
| 普通异常 | 第 1 次与每第 30 次记日志，避免刷屏 |
| 恢复正常 | 清空错误状态，上报 `running` |

状态通过 `manager.mark_status` 写入渠道记录的 `status` / `last_error`，前端可见。

---

## 4. 消息结构解析

`msgs` 里每条消息：

```json
{
  "message_type": 1,
  "from_user_id": "o9cq8...@im.wechat",
  "to_user_id": "",
  "message_id": "...",
  "context_token": "...",          // ⚠️ 回复时必须带回
  "item_list": [ ... ]
}
```

`message_type` 只处理 `1..5`；缺 `from_user_id` 的消息直接丢弃。

`item_list` 各类型：

| type | 含义 | 关键字段 |
|---|---|---|
| 1 | 文本 | `text_item.text` |
| 2 | 图片 | `image_item.media.{aes_key, encrypt_query_param, full_url}` 或 `image_item.cdn_big_img_url` |
| 3 | 语音 | `voice_item.text`（已转文字时） |
| 4 | 文件 | `file_item.{file_name, filename, len, media.{aes_key, encrypt_query_param}}` |
| 5 | 视频 | — |

⚠️ **坑**：`context_token` 必须保存（按 `from_user_id` 索引），发消息时要带回。
有效期 **24 小时**（微信限制：用户主动发消息后 24h 内可回复）。
适配器读取时校验 `time.time() - updated_at > 24h`，过期即视为空。

**图片 URL 优先级**：`cdn_big_img_url` → `media.full_url` → `img.full_url`。
拿到直链可直接下载；同时保留 `aes_key` / `encrypt_query_param` 供 CDN 下载兜底。

**标准消息**：

```python
{
    "platform": "wxclaw",
    "message_id": "...",
    "user_id": "from_user_id",
    "group_id": None,            # ilink bot 为私聊形态
    "content": "文本 / [图片] / [文件: x.pdf] 拼接",
    "files": [{"type": "image|file", "name": "...",
               "url": "...", "aes_key": "...", "encrypt_query_param": "..."}],
    "raw_data": raw,
}
```

---

## 5. 发文本

```python
now_ms = int(time.time() * 1000)
payload = {
    "msg": {
        "from_user_id": "",
        "to_user_id": str(user_id),
        "client_id": f"af_{now_ms}_{os.urandom(4).hex()}",   # 必须唯一
        "message_type": 2,
        "message_state": 2,
        "create_time_ms": now_ms,
        "update_time_ms": now_ms,
        "context_token": self._context_for(user_id),          # 无记录时为空串
        "item_list": [{"type": 1, "text_item": {"text": content}}],
    },
    "base_info": {"channel_version": self.channel_version},
}
```

⚠️ **坑**：结构必须是 `{"msg": {...}, "base_info": {...}}` 两层包裹。
早期版本用扁平的 `{"msg_type": 1, "to_user_id": ...}` 会静默失败。

⚠️ **注意**：文本发送**始终携带** `context_token` 字段（没有记录时是空串）；
媒体发送只在有值时才带（见 6.4）。如果遇到文本被上游判无效会话，
可以改为与媒体一致的「非空才带」策略。

---

## 6. 发文件（核心难点）

### 6.1 完整流程

```
1. getuploadurl   → 拿到 upload_param / upload_full_url
2. CDN 上传       → POST 加密后的字节，响应头 x-encrypted-param
3. sendmessage    → item.type=2/4，media 放在 *_item 内部
```

### 6.2 步骤一：取上传地址

```python
raw_md5 = hashlib.md5(data).hexdigest()
aes_raw = os.urandom(16)                       # 真正的 AES key
enc = aes_ecb_encrypt(data, aes_raw)           # 加密后的密文
aes_key_hex = aes_raw.hex()
aes_key_b64 = base64.b64encode(aes_key_hex.encode("ascii")).decode()
file_key = os.urandom(16).hex()

up = await self._post("ilink/bot/getuploadurl", {
    "filekey": file_key,
    "media_type": 3,                 # 1=图片 3=文件（⚠️ 不是 2/4/5）
    "to_user_id": str(user_id),
    "rawsize": len(data),            # 原始字节数
    "rawfilemd5": raw_md5,           # 原始内容的 MD5
    "filesize": len(enc),            # 加密后字节数
    "no_need_thumb": True,
    "aeskey": aes_key_hex,
    "base_info": {"channel_version": self.channel_version},
})
```

⚠️ **最大的坑**：`upload_param` 可能出现在**响应顶层**，而不是 `data` 里！

```python
# 实测真实响应：
# {"upload_param": "WlItLVVkZGZYT1lOZ...", "ret": 0}

up = up if isinstance(up, dict) else {}
udata = up.get("data") if isinstance(up.get("data"), dict) else up
full = str(udata.get("upload_full_url") or up.get("upload_full_url") or "").strip()
param = str(udata.get("upload_param") or up.get("upload_param") or "").strip()
```

只读 `up["data"]["upload_param"]` 会永远拿到空值 → 直接返回「获取上传地址失败」。

### 6.3 步骤二：CDN 上传

```python
from urllib.parse import quote

CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"

if full.startswith("http"):
    cdn_url = full
elif param:
    cdn_url = (f"{CDN_BASE}/upload"
               f"?encrypted_query_param={quote(param, safe='')}"
               f"&filekey={quote(file_key, safe='')}")

async with session.post(cdn_url, data=enc,          # ⚠️ 传加密后的字节
                        headers={"Content-Type": "application/octet-stream"}) as resp:
    if resp.status not in (200, 201, 204):
        return {"error": f"CDN 上传失败 HTTP {resp.status}"}
    # ⚠️ 关键：从响应头取加密回执，且要大小写不敏感
    encrypt_param = ""
    for k, v in resp.headers.items():
        if k.lower() == "x-encrypted-param":
            encrypt_param = v
            break
```

⚠️ **坑**：CDN 端点若参数无效返回 **403**（不是 404），说明路径正确但 param 不对。

### 6.4 步骤三：发送媒体消息

```python
cdn_media = {"aes_key": aes_key_b64, "encrypt_type": 1}
if encrypt_param:
    cdn_media["encrypt_query_param"] = encrypt_param

# ⚠️ media 必须放在 *_item 内部，不是 item 外层
item = {
    "type": 4,
    "file_item": {
        "media": cdn_media,
        "file_name": file_name,      # ⚠️ 字段名是 file_name，不是 filename
        "len": str(len(data)),       # ⚠️ 必须带原始文件长度
    },
}

msg = {
    "from_user_id": "",
    "to_user_id": str(user_id),
    "client_id": f"af_{now_ms}_{os.urandom(4).hex()}",
    "message_type": 2,
    "message_state": 2,
    "create_time_ms": now_ms,
    "update_time_ms": now_ms,
    "item_list": [item],
}
# context_token 是可选的：为空时不要传空串（会被上游判为无效会话）
ctx_token = self._context_for(str(user_id))
if ctx_token:
    msg["context_token"] = ctx_token

payload = {"msg": msg, "base_info": {"channel_version": self.channel_version}}
```

**`aes_key` 的编码**：发送消息里的 `cdn_media.aes_key` 是
`base64(aes_key_hex)`（把 32 字符 hex 串再做 base64），不是原始 16 字节 key。

### 6.5 图片 vs 文件 vs 视频 vs 语音

| 媒体 | getuploadurl media_type | item.type | item 内部字段 |
|---|---|---|---|
| 图片 | 1 | 2 | `image_item: {media, mid_size: 加密字节数}` |
| 视频 | 2 | 5 | `video_item: {media, video_size, play_length: 0}` |
| 文件 | 3 | 4 | `file_item: {media, file_name, len: 原文字节数}` |
| 语音 | **不支持出站** | — | 协议未开放；语音/视频一律按文件（media_type=3, item.type=4）发送 |

⚠️ **当前公开发送路径只区分图片 / 其它**：

```python
up_type = 1 if media_type == 1 else 3
```

`_send_media` 里虽然保留了 `up_type == 2` 的视频分支，但 `send()` 与
`send_file_bytes()` 都不会传 2，所以**视频和语音实际都走文件通道**。
需要原生视频消息时，把 `send_file_bytes` 的映射改成
`{"image": 1, "video": 2}.get(file_type, 3)` 即可。

⚠️ **语音出站**：入站有 `type=3` 的 `voice_item`（仅转写文本），但出站没有对应的上传/消息类型。
腾讯官方 `openclaw-qqbot` 插件与外部参考实现均只有图片/文件分支，因此微信侧发不了语音气泡，
mp3 只能以文件形式送达（微信内可点开播放）。QQ 渠道原生支持语音，见 `qqbot-integration.md`。

### 6.6 AES 加密实现

```python
def aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    """AES-ECB + PKCS7 padding，key 必须 16/24/32 字节。"""
    from Crypto.Cipher import AES
    pad = 16 - len(data) % 16
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(data + bytes([pad]) * pad)
```

⚠️ **坑（血泪）**：`send_file_bytes(data)` 传的是**原始字节**，
不要在里面再 `base64.b64decode(data)` —— 会抛 `Incorrect padding`。
适配器通过 `raw_data=` 参数直接复用原始字节，避免临时文件读写。

---

## 7. 收文件（下载 + 解密）

### 7.1 从 item_list 提取下载信息

```python
elif itype == 4:
    fi = item.get("file_item") or {}
    fname = fi.get("filename") or fi.get("file_name") or "文件"
    media = fi.get("media") or {}
    att = {
        "type": "file",
        "name": fname,
        "aes_key": media.get("aes_key", ""),
        "encrypt_query_param": media.get("encrypt_query_param", ""),
    }
    # 兼容旧字段：直接给了 url
    if fi.get("url"):
        att["url"] = fi["url"]
```

⚠️ **坑**：只存文件名是不够的，**必须保存 `encrypt_query_param` + `aes_key`**，
否则无法下载解密（早期实现只留了文件名，等于放弃文件内容）。

图片同理，从 `image_item.media` 取两个字段，并额外保留直链 URL。

### 7.2 下载（优先直链，回退 CDN）

```python
url = att.get("url") or ""
eqp = att.get("encrypt_query_param") or ""
if not url and eqp:
    url = (f"{CDN_BASE}/download"
           f"?encrypted_query_param={quote(eqp, safe='')}")
if not url:
    return None

async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
    raw = await resp.read()      # 有 aes_key 时这是密文
```

### 7.3 解密

`aes_key` 的编码方式不稳定，实测有三种，需按**确定度从高到低**尝试：

```python
def _try_decrypt_cdn(data: bytes, aes_key: str) -> bytes | None:
    from Crypto.Cipher import AES
    candidates: list[bytes] = []

    def _add(b: bytes) -> None:
        if b and len(b) in (16, 24, 32) and b not in candidates:
            candidates.append(b)

    # 1) 直接 hex：形状最确定（32 字符）
    try:
        _add(bytes.fromhex(aes_key))
    except ValueError:
        pass

    # 2) base64 → 可能是原始 16 字节，也可能是 hex 字符串
    try:
        padded = aes_key + "=" * (-len(aes_key) % 4)
        decoded = base64.b64decode(padded)
        try:
            _add(bytes.fromhex(decoded.decode("ascii")))
        except (UnicodeDecodeError, ValueError):
            pass
        _add(decoded)
    except Exception:
        pass

    # 3) 原文 utf-8（16/24/32 字节时）
    _add(aes_key.encode("utf-8"))

    if not candidates or len(data) % 16 != 0:
        return None

    for key in candidates:
        try:
            plain = AES.new(key, AES.MODE_ECB).decrypt(data)
            pad = plain[-1]
            if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
                return plain[:-pad]
        except Exception:
            continue
    return None
```

⚠️ **坑（顺序很关键）**：纯 hex 串（32 字符）base64 解码后正好也是 24 字节的
「合法 AES 长度」。用错密钥解密时有 1/256 概率 PKCS7 恰好通过，从而返回垃圾明文。
所以必须**先按 hex 试**，再按 base64 试；否则会偶发文件乱码。

⚠️ **坑**：错误 key 解密不会报错，只会返回乱码。
必须用 padding 合法性判断，否则会把垃圾数据当文件。

⚠️ **兜底行为**：解密失败时适配器**返回原始密文字节**并记
`wxclaw_decrypt_failed` 警告，而不是返回 `None`。这样至少文件被保存下来，
但内容可能是乱码——排查时应优先看这条日志。

---

## 8. 完整消息往返时序

```
用户微信 ──> bot
   │
   ├─ getupdates 返回 msgs（含 item_list + context_token）
   │
   ├─ [若有文件] CDN download → AES 解密 → 存盘 → 抽取文本
   │
   ├─ 拼成用户消息 → 调 LLM（图片转多模态 image_url）
   │
   └─ 回复：sendmessage（带 context_token）
            [若有产物] getuploadurl → CDN upload → sendmessage(type=2/4)
```

**渠道内的投递顺序**（`BaseAdapter.dispatch`）：

1. 先发本轮所有附件（产物 / 语音）
2. 语音发送成功 → 不再重复发文本；语音失败 → 降级成文件重发，并补发文本
3. 最后发文本回复

---

## 9. 运维提示

| 项目 | 建议 |
|---|---|
| 二维码 | 40 秒过期，前端需支持「刷新二维码」 |
| context_token | 24h 有效；超时后无法主动推送，需用户先发消息 |
| 轮询间隔 | 默认 2 秒（`poll_interval_sec`）；错误时放慢 |
| session timeout | 放慢到 10 秒并上报状态，提示用户重新扫码 |
| 媒体大小 | 建议 < 10MB；语音投递上限 20MB，超限自动跳过语音只发文本 |
| 端口占用 | 启动脚本应自动清理残留进程 |
| 多账号 | `accounts` 列表按 bot_id 管理；顶层 token 是实际生效值 |
| 状态排查 | 渠道列表看 `status` / `last_error`；必要时调用 `diagnose` |

---

## 10. 踩坑清单（速查）

| # | 现象 | 原因 | 修复 |
|---|---|---|---|
| 1 | 一直 `-14 session timeout` | 缺 `AuthorizationType` 头 | 加 `ilink_bot_token` |
| 2 | 同上 | `X-WECHAT-UIN` 为空 | 随机 base64 |
| 3 | 同上 | payload 用 `sync_buf` | 改 `get_updates_buf` + `base_info` |
| 4 | 同上 | 缺 `base_info.channel_version` | 补 `1.0.3`（可配置） |
| 5 | `resp.json()` 报 MIME 错 | 响应是 octet-stream | 手动 `read()` + `json.loads()` |
| 6 | `ret: 0` 被当失败 | 只看 errcode | 两个字段都判断 |
| 7 | 扫码后立刻 `-14` | 前端把占位符当 token 提交 | 后端 `_sanitize_config` + 解析层双重过滤 |
| 7b | 扫码成功但 Token 输入框是空的 | 前端拿到 token 后清空了表单 | 后端回传 `data.token`，前端填进输入框 |
| 8 | `Incorrect padding` | 原始字节被 base64 解码 | 直接传 bytes（用 `raw_data=`） |
| 9 | 「获取上传地址失败」 | `upload_param` 在顶层 | 顶层 + `data` 双查 |
| 10 | 上传成功但文件没到 | `item.type` 用错 | 1/2/3 → 2/5/4 |
| 11 | 同上 | `media` 放在 item 外层 | 放进 `*_item` 内部 |
| 12 | 同上 | 缺 `len` / 字段名 `filename` | 补 `len`、改 `file_name` |
| 13 | 收到文件但内容空 | 没存 `encrypt_query_param`/`aes_key` | 解析时一并保存 |
| 14 | 解密出乱码 | 错误 key 未校验 / 尝试顺序错 | 先 hex 再 base64；PKCS7 校验 |
| 15 | 偶发文件乱码 | 32 字符 hex 被先按 base64 解成 24 字节 key | 同上，hex 优先 |
| 16 | 同一条消息回两次 | 轮询游标回退导致重复投递 | 按 `message_id` 去重（3000/1500） |
| 17 | 类无法实例化 | 辅助函数写在 class 中间（缩进错） | 移到类外 |
| 18 | 一直 `Server stopped` | 旧进程占端口 | 启动前 taskkill |
| 19 | `timeout /t` 报错 | PATH 里有 GNU timeout | 改用 `ping -n 3` |
| 20 | AI 说看不到图片 | 渠道没把图传给模型 | 走多模态 `image_url` |
| 21 | 二维码一扫就过期 | 有效期约 40 秒 | 及时扫 / 自动刷新 |
| 22 | 列表接口泄露 token | `accounts` 未逐项脱敏 | `_mask_cfg` 递归处理 list/dict |
| 23 | 改完配置后渠道不生效 | 未触发重启 | `PATCH` 配置会 stop → start |
| 24 | 视频/语音发成了文件 | 发送路径只映射图片/文件 | 需要原生视频时把 `send_file_bytes` 映射改全 |

---

## 11. 渠道运行时（manager）

两份渠道文档共用同一套运行时，wxclaw 侧要点：

### 11.1 生命周期与状态

- `start_channel`：先停旧实例 → 创建适配器 → 后台轮询任务 → 状态 `running`
- 轮询异常 / token 失效：适配器通过 `manager.mark_status` 上报 `error` + 原因
- `stop_channel`：同时取消该渠道正在跑的对话任务，避免继续消耗模型配额

### 11.2 消息处理

- 同一渠道串行处理；新消息会**打断**上一条正在执行的任务
- 「停止 / 取消 / 算了 / /stop」等短指令会立即中断当前任务并回复确认
- 每个「渠道 + 发送者」一个独立会话，标题形如 `渠道名 · 发送者尾号`
- 入站附件保存到 `artifact_dir/{user_id}/workspace/inbox/`，并登记为会话产物
- 图片走多模态、语音自动转写、文档自动抽取文本
- 回复时先附件后文本；产物来自本轮新建文件 + `send_artifact` 指定的历史产物

### 11.3 管理员、命令与工作流

- `admin_ids` 里的人可用管理工具（定时任务、渠道/模型/技能/MCP/工作流管理等）
- `/myuid` 返回当前发送者 ID；`/help` 显示渠道内命令
- `reply_mode=workflow` 时，消息内容作为工作流输入，执行结果直接回复，不建会话

### 11.4 主动推送（定时任务等）

`manager.push_message` 的目标选择顺序：

1. 调用方显式指定 `target`（群/私聊）
2. 渠道 `admin_ids` 排序后的第一个
3. 最近一次入站发送者 `last_sender_id`
4. 老数据兜底：从会话标题恢复最近发送者

---

## 12. 配置与 API 参考

### 12.1 渠道配置项

来源 `CHANNEL_TYPES["wxclaw"]` 与适配器：

| 配置键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `token` | password | — | Bot Token（扫码自动获取） |
| `api_url` | string | `https://ilinkai.weixin.qq.com` | 官方地址，一般不改 |
| `x_wechat_uin` | password | `""` | 留空则每次随机；一般无需填写 |
| `poll_interval_sec` | number | `2` | 轮询间隔 |
| `admin_ids` | string | `""` | 管理员 ID，逗号分隔；发 `/myuid` 获取 |
| `tts_voice_id` | string | `""` | 语音回复音色；留空用默认 |
| `authorization_type` | string | `ilink_bot_token` | 隐藏配置；一般不改 |
| `channel_version` | string | `1.0.3` | 隐藏配置；`base_info.channel_version` |
| `get_updates_path` | string | `ilink/bot/getupdates` | 隐藏配置 |
| `send_message_path` | string | `ilink/bot/sendmessage` | 隐藏配置 |
| `login_qr_path` | string | `ilink/bot/get_bot_qrcode` | 隐藏配置 |
| `login_check_path` | string | `ilink/bot/get_qrcode_status` | 隐藏配置 |
| `accounts` | list | — | 多账号记录；适配器实际读顶层 `token` |
| `__channel_id` | int | 运行时注入 | 内部字段，用于状态上报，不要手填 |

### 12.2 API

| 接口 | 用途 |
|---|---|
| `GET /api/v1/channels/types` | 返回渠道类型与字段定义 |
| `POST /api/v1/channels` | 创建渠道 |
| `PATCH /api/v1/channels/{id}` | 更新配置（过滤占位符、合并旧值、重启渠道） |
| `POST /api/v1/channels/test` | 用当前配置试连（wxclaw：发一次 getupdates，超时视为有效） |
| `POST /api/v1/channels/{id}/start` / `stop` | 启动 / 停止 |
| `GET /api/v1/channels/{id}/config/{key}` | 查看单个敏感配置明文 |
| `POST /api/v1/channels/wxclaw/scan-login` | 取二维码 |
| `POST /api/v1/channels/wxclaw/login-status` | 轮询扫码状态并落库 |
| `POST /api/v1/channels/wxclaw/diagnose` | 诊断 token 是否被上游接受 |

**敏感字段**：`token`、`x_wechat_uin`（以及 `accounts[].token`）都会脱敏，
列表只返回 `前4****后4`（长度 ≤8 时全 `****`）。

---

## 13. 回归测试

| 文件 | 覆盖点 |
|---|---|
| `backend/tests/test_wxclaw_protocol.py` | 请求头三件套、Bearer 前缀、游标字段、`base_info`、`msg` 包裹、`ret=0` 语义、`channel_version` 默认/可配 |
| `backend/tests/test_wxclaw_channel.py` | 占位符过滤、配置合并、登录响应兼容解析、官方 confirmed 字段、octet-stream JSON |
| `backend/tests/test_channel_media.py` | 媒体类型映射（1/3）、上传地址顶层/嵌套、原始字节上传、图片 `item.type=2`、解密 key 编码、入站文件/图片字段、产物落库、语音降级 |

运行：

```bash
cd backend
pytest tests/test_wxclaw_protocol.py tests/test_wxclaw_channel.py tests/test_channel_media.py -q
```

---

## 14. 参考实现位置

本仓库中的实现：

| 文件 | 内容 |
|---|---|
| `backend/app/channels/wxclaw_adapter.py` | 适配器：认证、轮询、收发、媒体、解密、状态上报 |
| `backend/app/api/v1/wxclaw_login.py` | 扫码登录、状态轮询、token 诊断、多账号回填 |
| `backend/app/channels/base_adapter.py` | 渠道基类：dispatch、附件发送、语音降级 |
| `backend/app/channels/manager.py` | 渠道管理：会话、入站文件、产物登记、推送 |
| `backend/app/api/v1/channels.py` | 渠道 CRUD、字段定义、脱敏、试连 |

---

*最后更新：2026-11-06*
