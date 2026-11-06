# Changelog

## 1.0.0 — 2026-11-06

首个版本。

### 新增

- `SKILL.md` — 多渠道机器人适配器入口：触发场景、三层架构、接入流程、渠道差异、
  必守不变量、故障速查、配置参考、测试与验收。
- `references/qqbot-integration.md` — QQ 官方 Bot 全量接入指南：
  扫码绑定、AES-256-GCM 解密、AccessToken 刷新、WebSocket 网关/心跳/Resume、
  文本与 Markdown、富媒体上传、语音直传、附件下载、踩坑清单。
- `references/wxclaw-integration.md` — 微信 wxclaw / iLink Bot 全量接入指南：
  请求头三件套、扫码长轮询、getupdates 游标与去重、文本与富媒体发送、
  AES-ECB 上传/下载解密、多账号回填、诊断接口、踩坑清单。
- `references/protocol-cheatsheet.md` — 两个渠道的端点、请求头、payload、
  媒体映射、去重与状态上报并排速查。
- `references/adapter-contract.md` — 标准消息、BaseAdapter 契约、dispatch 投递顺序、
  Manager 生命周期、会话/产物/语音/管理员/推送，以及新增第三个渠道的骨架。
- `agents/openai.yaml` 与 `.codex-plugin/plugin.json` — 平台展示与插件元数据。
- `assets/icon-1024.png`、`icon-512.png`、`icon-256.png`、`icon-128.png` — 技能图标。
- `tools/make_icon.py` — 本地矢量图标生成脚本，可复现、可改色，不依赖外部服务。
- `README.md` — 安装、使用、目录结构、打包说明。

### 工程化

- `scripts/package_skill.py` — 本地与 CI 共用的打包脚本：校验 frontmatter、
  构建 `.skill` / `.zip` / Codex 插件包，并生成 `sha256sums.txt`。
- `.github/workflows/release.yml` — 推送 `v*` tag 或手动触发时，校验 tag 与
  `SKILL.md` 版本一致，自动打包并创建 / 更新 GitHub Release。
- `.gitignore` — 忽略 `dist/`、缓存与临时文件。

### 基线

内容对齐以下已跑通实现：

- `backend/app/channels/qqbot_adapter.py`
- `backend/app/channels/wxclaw_adapter.py`
- `backend/app/channels/base_adapter.py`
- `backend/app/channels/manager.py`
- `backend/app/api/v1/qqbot_login.py`
- `backend/app/api/v1/wxclaw_login.py`
- `backend/app/api/v1/channels.py`
