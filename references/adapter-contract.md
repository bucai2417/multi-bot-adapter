# 适配器契约与渠道运行时

> 本文件描述「平台适配器 / BaseAdapter / Manager」三层之间**必须遵守**的接口与行为。
> 新增第三个渠道时，照这份契约实现即可复用现有的会话、产物、语音、管理员和推送能力。

---

## 1. 标准消息

适配器把平台事件转成统一 dict，交给 `BaseAdapter.dispatch`：

```python
{
    "platform": "qqbot",              # 渠道类型
    "message_id": "唯一消息 id",       # 去重、被动回复用
    "user_id": "发送者标识",           # 会话隔离键
    "group_id": "群标识或 None",
    "content": "文本内容",
    "files": [
        {
            "type": "image|video|voice|file",
            "url": "https://...",       # 直链（可选）
            "name": "a.png",
            "aes_key": "...",           # wxclaw 加密媒体（可选）
            "encrypt_query_param": "..." # wxclaw CDN（可选）
        }
    ],
    "raw_data": {...}                  # 原始事件，排错用
}
```

约束：

- `message_id` 必须稳定；没有平台 id 时用时间戳兜底，但要做去重。
- `user_id` 不能为空；为空的消息直接丢弃。
- `content` 可以是空串，但 `files` 非空时仍应 dispatch（纯图片/文件消息）。
- 附件描述用占位文本拼进 `content`（如 `[图片]`、`[文件: a.pdf]`），
  真正的二进制由 `download_inbound` 处理。

---

## 2. 适配器基类

```python
class BaseAdapter(ABC):
    platform: str = "base"

    def __init__(self, config: dict, user_id: int, on_message):
        self.config = config
        self.user_id = user_id
        self.on_message = on_message       # async fn(std_msg) -> reply_text
        self.is_running = False
        self.session: aiohttp.ClientSession | None = None
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self):
        await self.connect()
        self.is_running = True

    async def stop(self):
        self._stop_event.set()
        self.is_running = False
        cancel(self._task)
        await self.disconnect()
        close(self.session)

    # 子类实现
    async def connect(self): ...
    async def disconnect(self): ...
    async def send(self, target_id, content, is_group,
                   file_path=None, file_name="", file_type="file") -> dict: ...
    async def send_file_bytes(self, target_id, file_name, data,
                              file_type, is_group) -> dict: ...
    async def download_inbound(self, att: dict) -> bytes | None: ...

    async def download_bytes(self, url, timeout=30) -> bytes: ...  # 已提供
    @staticmethod
    def detect_file_type(name_or_url) -> str: ...                   # 已提供
```

### 实现要求

| 方法 | 要求 |
|---|---|
| `connect` | 启动后台轮询/WebSocket 任务，立即返回；鉴权失败要抛异常 |
| `disconnect` | 关闭连接；不要在这里取消 Manager 管理的任务 |
| `send` | 文本或单文件；返回平台响应；失败返回 `{"error": "..."}` |
| `send_file_bytes` | 二进制产物投递；同样返回 dict |
| `download_inbound` | 下载并解密；失败返回 `None`，不要抛给上层 |

`send_file_bytes` 的 `file_type` 取 `image / video / voice / file`。
平台不支持的类型（如微信出站语音）应在适配器内降级，而不是返回成功。

---

## 3. dispatch 的投递顺序

```python
async def dispatch(std_msg):
    if not should_process(std_msg):
        return
    reply = await on_message(std_msg)

    media = pop_reply_media()          # 本轮产物 + 语音
    has_voice = any(m.type == "voice" for m in media)
    voice_ok = has_voice

    for m in media:
        try:
            await _send_reply_media(target, m, is_group)
        except Exception:
            if m.type == "voice":
                voice_ok = False
                await _send_reply_media(target, {**m, "type": "file"}, is_group)
            else:
                log warning

    if reply and not (has_voice and voice_ok):
        await send(target, reply, is_group)
```

规则：

1. **先附件，后文本**。
2. 语音成功时不再重复发文本；语音失败时降级为文件并补发文本。
3. 单个附件失败只记录日志，不阻断其它附件和文本。
4. `_reply_media` 是一次性 ContextVar，消费后清空，避免下一条消息重复发送。
5. `_send_reply_media` 会检查返回 dict 的 `error` 与 `ret`，非 0 视为失败。

---

## 4. Manager 生命周期

```python
async def start_channel(row):
    await stop_channel(row.user_id, row.id)   # 先停旧实例
    cfg = json.loads(row.config)
    cfg["__channel_id"] = row.id              # 内部注入
    adapter = Adapter(cfg, row.user_id, on_message)
    _adapters[(user_id, row.id)] = adapter
    _channel_locks[(user_id, row.id)] = asyncio.Lock()
    _adapter_tasks[...] = asyncio.create_task(_run(adapter))
    await _mark_status(row.id, "running")
```

- 适配器崩溃 → `_mark_status("error", str(exc))`，前端可读。
- `stop_channel` 同时取消该渠道正在跑的对话任务。
- `load_all` 在服务启动时拉起所有 `is_active=True` 的渠道。
- `push_message` 支持临时启动渠道、推送后停止，不影响常驻状态。

---

## 5. 消息处理流程

```
入站标准消息
  → 停止指令？ → 中断当前任务并确认
  → 有任务在跑？ → 打断旧任务，继续处理新消息
  → 串行锁内执行：
      1. 下载附件 → 保存 workspace/inbox → 登记产物
      2. 抽取文本 / 语音转写 / 图片多模态
      3. 斜杠命令（/myuid、/help）
      4. reply_mode=workflow → 跑工作流并回复
      5. 否则 → 取会话历史 → 组装 channel_note → run_agent
      6. 落库用户/助手消息、用量、会话记忆
      7. 更新 msg_in/msg_out/last_sender_id/last_group_id
      8. 收集本轮产物 + send_artifact 指定的历史产物 + 语音
      9. 返回 reply，由 dispatch 投递
```

### 会话

- 每个「渠道 + 发送者」一个会话。
- 标题：`渠道名 · 发送者尾号`（网页会话列表可读）。
- 历史消息会加载；渠道对话不是「每句都是第一次」。

### 管理员

- `admin_ids` 是逗号分隔字符串，适配器解析为 set。
- 只有管理员能用管理工具（定时任务、渠道/模型/技能/MCP/工作流管理等）。
- `/myuid` 返回当前发送者 ID，方便用户填配置。

### 产物

- 本轮开始前记录 `artifact_base_id`。
- 入站附件登记为产物后，刷新起点，避免把用户刚发的文件当 AI 产物回发。
- 本轮新建产物 + `send_artifact` 指定的历史产物一起投递。
- 语音同时落一份产物，网页端可播放。

### 语音

- 模型调用 `send_voice` → manager 合成 mp3（使用渠道 `tts_voice_id`）。
- 超过 20MB 不投递语音，回落到文本。
- 语音发送成功不重复发文本；失败降级文件 + 文本。

---

## 6. 主动推送

`manager.push_message(user_id, channel_id, content, target="", is_group=False)`：

1. 显式 `target` 非空 → 直接发（配合 `is_group`）。
2. 否则取 `admin_ids` 排序后的第一个。
3. 否则取 `last_sender_id`。
4. 老数据兜底：从会话标题恢复。
5. 群聊发起的任务用 `last_group_id` 回到原群。

返回 `{ok, target, error}`。未运行时若渠道 `is_active`，会临时启动、推送后停止。

---

## 7. 新增第三个渠道的骨架

```python
from app.channels.base_adapter import BaseAdapter

class NewPlatformAdapter(BaseAdapter):
    platform = "newplatform"

    def __init__(self, config, user_id, on_message):
        super().__init__(config, user_id, on_message)
        self.token = config.get("token", "")
        self.admin_ids = _parse_list(config.get("admin_ids", []))

    async def connect(self):
        if not self.token:
            raise RuntimeError("缺少 token")
        self._stop_event.clear()
        self._task = asyncio.create_task(self._poll_loop())

    async def disconnect(self):
        pass

    async def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                events = await self._fetch()
                for ev in events:
                    std = self._parse(ev)
                    if std:
                        await self.dispatch(std)
            except Exception as exc:
                log.warning("poll error: %s", str(exc)[:200])
            await asyncio.sleep(2)

    async def send(self, target_id, content, is_group,
                   file_path=None, file_name="", file_type="file"):
        if file_path:
            return await self._send_file(target_id, file_path, file_name,
                                         file_type, is_group)
        return await self._send_text(target_id, content, is_group)

    async def send_file_bytes(self, target_id, file_name, data,
                              file_type, is_group):
        return await self._send_media(target_id, file_name, data,
                                      file_type, is_group)

    async def download_inbound(self, att):
        try:
            return await self.download_bytes(att.get("url", ""))
        except Exception:
            return None
```

接入 Manager 只需要在 `_load_adapter_row` 里增加一个 `elif row.channel_type == "newplatform"` 分支，
并在渠道类型定义里补字段。其余会话、产物、语音、管理员、推送能力自动复用。

---

## 8. 契约验收清单

- [ ] 标准消息字段齐全，`message_id` / `user_id` 非空
- [ ] `connect` 不阻塞，`disconnect` 能干净退出
- [ ] `send` / `send_file_bytes` 失败返回 `error`，不抛给 dispatch
- [ ] `download_inbound` 失败返回 `None`
- [ ] 入站去重；重连/游标回退不重复回复
- [ ] 附件先发、文本后发；语音失败降级
- [ ] 入站附件落 workspace 并登记产物
- [ ] 管理员白名单、`/myuid`、工作流模式可用
- [ ] 错误状态写入 `status` / `last_error`
- [ ] 敏感配置脱敏，占位符不覆盖真值
