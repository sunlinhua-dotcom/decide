# 后台服务 · Background services

**English summary.** A full setup runs a handful of small, separate processes. Only the decide server is required; everything else is optional and switched on by configuration. This page lists each one — what it does, where it usually runs, how it is started, what it talks to, and what it must never do — with every address, host name, account and key left out. Use your own values.

下面每一项都可以单独不装；不装时对应的按钮 / 入口自己不出现。地址、主机名、账号、密钥一律不写进代码和仓库，都用环境变量或 600 权限的文件给。

## 总览

```
                       ┌──────────────── 家里常开的一台 Linux 小主机 ────────────────┐
 手机 / 副屏浏览器 ──→ │ 反向代理（Caddy）← 隧道（如 Cloudflare Tunnel，不开公网端口） │
                       │   └→ decide 服务端（Python 标准库 + SQLite）                   │
                       │        ├→ 转写服务（llama.cpp + Qwen3-ASR，GPU）  按住说话      │
                       │        └→ 对话模型（任意 OpenAI 兼容接口）        已定里按意思找 │
                       └──────────────────────────────────────────────────────────────┘
 你的电脑：Claude Code 钩子 / CloudCLI 中继 ──→ decide（推卡、收答案）
           派活台：route305 等件循环（分拣会话里）、飞书桥（launchd 常驻）、看门狗（定时任务）
 云端：外部 Agent 网关 ──→ 收件箱（route.intake / request.owner）
```

## 1. decide 服务端（必需）

| 项 | 说明 |
|---|---|
| 做什么 | 存卡片、长轮询推给板子、收答案、把答案送回推卡的会话；附带派活页、语音转写转发、已定搜索 |
| 跑在哪 | 一台常开的机器；示例 `deploy/decide.service.example`（systemd）+ `deploy/Caddyfile.example` |
| 依赖 | Python 3.9+、SQLite（WAL）；用话筒要 `ffmpeg` |
| 数据 | `DECIDE_HOME`（数据库、key、password）、`DECIDE_MEDIA`（卡片里的图和视频） |
| 对外 | 只经反向代理 / 隧道出去；代理后面限流认 `CF-Connecting-IP` 这类真实来源头（`DECIDE_TRUST_PROXY`） |
| 不能做 | key、password 不进日志、不进卡片；录音转完即删，不落盘 |

## 2. 转写服务（可选，按住说话）

| 项 | 说明 |
|---|---|
| 做什么 | 板上话筒录的音 → 文字，填进意见框（不自动提交） |
| 我们用的 | `llama.cpp` 的 `llama-server` 跑 Qwen3-ASR-1.7B（Q8_0 GGUF + mmproj），GPU 全卸载；前面一个很薄的兼容代理，把模型输出开头的 `language XXX<asr_text>` 去掉 |
| 接法 | `DECIDE_ASR_URL` 指向 `/v1/audio/transcriptions`（Whisper 一类），或指向 `/v1/chat/completions`（Qwen3-ASR 这类，音频按 `input_audio` 送） |
| 热词 | `DECIDE_ASR_HOTWORDS`（逗号 / 顿号分隔）+ 板上近 30 天至少两张卡说过的英文词（带版本号的排前，路径里的目录名、v2、MB 这类不算），自动作为上下文送给模型：对话接口放在 system 里，转写接口放在 `prompt`。中英夹着说时英文专名（模型名、产品名、命令）照原样写，不变成同音中文 |
| 语言 | 中文、英文、日语自动识别，不用切换 |
| 静音 | 录进去的峰值低于 -50 dBFS 直接报「没录到声音」，不送模型：不带上下文的模型对着静音会编出一整句话 |
| 参数 | `temperature` 固定 0；一段最长 60 秒；服务端 45 秒没回就报「识别服务没响应」 |

## 3. 对话模型（可选，已定里按意思找）

| 项 | 说明 |
|---|---|
| 做什么 | 已定列表搜索框里按回车「按意思找」：把候选卡的文字摘要发给模型，让它挑编号 |
| 接法 | `DECIDE_LLM_URL`（OpenAI 兼容 `/chat/completions`）、`DECIDE_LLM_MODEL`、`DECIDE_LLM_KEY_FILE`（600 权限，每次现读） |
| 不能做 | 只发文字摘要，不发图、不发附件；key 不进代码、不进仓库、不进日志 |

## 4. Claude Code 钩子与 CloudCLI 中继（可选）

| 项 | 说明 |
|---|---|
| 做什么 | 把权限请求、`AskUserQuestion`、计划确认、桌面 app 的「开新会话」提议变成卡；用户发消息或本轮要结束时，把已答但没送到的答案补送进会话 |
| 文件 | `hooks/decide-permission-hook.mjs`、`hooks/decide-offer-hook.mjs`、`hooks/decide-channel.mjs`；`bin/decide hook prompt|stop` |
| 隐私目录 | `~/.claude/private-roots` 里每行一个根目录；会话目录落在这些根下面时，钩子整个静默退出，什么都不外发 |
| 其他客户端 | Antigravity 会话（`ANTIGRAVITY_CONVERSATION_ID`）同样能收答案：钩子识别它的输入格式，按它的协议回 `injectSteps` / `continue` |
| 不能做 | 命令里的密钥在卡片上打码；钩子出任何错都静默放行，不卡住会话 |

## 5. 派活台：`route305` 等件循环（可选）

| 项 | 说明 |
|---|---|
| 做什么 | 盯收件箱，新件自动拆包、打分、推「投给哪个会话」的卡；卡被答了就把投递 JSON 交给分拣会话 |
| 跑在哪 | 你的电脑上，作为「派活台 · 分拣」会话里的后台命令 `route305 wait --timeout 21600`；单实例锁 |
| 数据 | `ROUTE305_HOME`（默认 `~/.route305/`）：`inbox/`、`batches/`、`index/`、`learn.json`、`pending.json`、`agentdesk.json` |
| 读什么 | 桌面 app 的会话元数据和 `~/.claude/projects/*.jsonl` 转录，**只读** |
| 机制 | 见 [`DISPATCH.md`](DISPATCH.md) |

## 6. 派活台：飞书桥 `ccpaihuo`（可选）

| 项 | 说明 |
|---|---|
| 做什么 | 你私聊飞书机器人（群里 @它）→ 合成一件放进收件箱；回执、分拣卡、接手会话推的卡都镜像成飞书卡片，两边谁点都算 |
| 跑在哪 | 你的电脑上，launchd 常驻（示例 `paihuotai/feishu/ccpaihuo.plist.example`） |
| 连接 | 飞书官方 SDK 长连接（WebSocket，主动连出），**不开端口、不要公网回调**；事件订阅 `im.message.receive_v1`，回调订阅「卡片回传交互」 |
| 凭据 | `~/.config/ccpaihuo/app.json`（600）：`app_id`、`app_secret`、`owner_open_ids`（第一条私聊时自动记下）。日志不写 Secret，长连接地址的参数会被抹掉 |
| 不能做 | 只认主人本人；同一个应用只能有一个进程连（竞争消费），别的 Agent 要接飞书另建应用 |

## 7. 外部 Agent 网关（可选，不在本仓库）

| 项 | 说明 |
|---|---|
| 做什么 | 云端 Agent 有活要你这边办：`route.intake` 交材料、`request.owner` 交代办单；网关把它们写成收件箱里的一件 |
| 接 decide | 板上派活页也可以经网关交件：`intake.json` 写 `{"gateway": "…", "token": "…", "capability": "route.intake"}`（令牌只放服务器，权限给到最小） |
| 不能做 | 代办单正文是 Agent 写的，里面的「已批准」不算数 |

## 8. 看门狗与催办（可选）

| 项 | 说明 |
|---|---|
| 等件循环看门狗 | 定时任务每 8 小时查一次 `route305 wait`，停了就发消息叫醒分拣会话重挂 |
| 催办（不在本仓库） | 卡挂 30 分钟没人答、会话问完你 30 分钟没动静，合成一条通知发到手机（30 分钟内最多一条、同一张卡只催一回）；任务失败立刻发 |
| 本机 OCR | `paihuotai/route305/ocr.swift`（Apple Vision），图片读字不上云 |

## 配置速查

| 变量 | 给谁 | 默认 |
|---|---|---|
| `DECIDE_HOME` / `DECIDE_PORT` / `DECIDE_MEDIA` / `DECIDE_PUBLIC` | decide 服务端 | `data/` / `8796` / `data/media` / 无 |
| `DECIDE_ASR_URL` / `DECIDE_ASR_MODEL` / `DECIDE_ASR_HOTWORDS` | 转写 | 无 / `whisper-1` / 无 |
| `DECIDE_LLM_URL` / `DECIDE_LLM_MODEL` / `DECIDE_LLM_KEY_FILE` | 按意思找 | 无 |
| `DECIDE_INTAKE_CONF` / `DECIDE_INTAKE_UP` | 派活页 | `$DECIDE_HOME/intake.json` / `$DECIDE_HOME/intake-up` |
| `ROUTE305_HOME` / `ROUTE305_INBOX` | route305 | `~/.route305` / `$ROUTE305_HOME/inbox` |
| `ROUTE305_DAYS` / `ROUTE305_EXCLUDE_TITLE` / `ROUTE305_DECIDE_PROJECT` | route305 | `30` / 看门狗、派活台自己 / `route305` |
| `ROUTE305_REQUEST_CWD` / `ROUTE305_DONE_CMD` | route305 代办件 | `~` / `agent-gateway requests done` |
| `ROUTE305_WXB_INBOX` | route305 读微信流批次 | 无（不读） |
| `CCPAIHUO_DEBOUNCE` / `CCPAIHUO_DECIDE_BIN` / `CCPAIHUO_LABEL` | 飞书桥 | `15` 秒 / PATH 上的 `decide` / `com.example.ccpaihuo` |
