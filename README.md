# Decide · 拍板

[![tests](https://github.com/sunlinhua-dotcom/decide/actions/workflows/tests.yml/badge.svg)](https://github.com/sunlinhua-dotcom/decide/actions/workflows/tests.yml)

**English** · [中文](#拍板--decide)

When one person works with a crowd of AI agents, the agents keep stopping to ask: *is this cut good enough? which of these four frames? Postgres or SQLite? may I run this command?* Decide puts every one of those questions on a single board. Each question is a card; you answer one card at a time on a second screen or on your phone, and the answer goes straight back to the session that asked.

- **Agents push cards** with one CLI call (`decide q`, `decide ask`, `decide pick`).
- **You tap** "approve", "redo + a note", or the option you want.
- **The answer is routed back** to the exact session that asked (`decide listen` / `decide wait`), which carries on working.

No framework, no build step: a Python 3 standard-library server with SQLite, a vanilla-JS web page, and a single-file CLI.

## Features

- **Image and video review** — before / after side by side, *approve* or *redo* with a note; magnifier, lightbox, drag-to-compare.
- **Pick one** — candidate images A / B / C / D; click the image you want, or "pick this, but change…".
- **Text questions** — `decide q "question" "option::cost" …` renders every option as a big card; keys 1–9 answer, a recommended option is marked.
- **`@prompt` options** — write `"A::cost::@a cat on a windowsill"` and the card goes up immediately; images are generated in the background by *your* image command and filled into the option cards as they arrive.
- **SVG diagram cards** — attach a flow / architecture / comparison diagram with `--svg`; it renders crisp and loads in milliseconds.
- **Dense plans become readable text** — a busy plan SVG (three or more boxed sections, 400+ characters) is split into native web text sections, each with an optional background-generated illustration; simple diagrams stay as they are. Open the original with "view original".
- **Done cards you can click** — links on a card become buttons under the title: pass `--link "Docs::https://…"`, or just write the URL in the title, facts or SVG and it is picked up automatically. `decide q … --video clip.mp4` attaches the clip to confirm, playable right on the board. `decide link <n>` adds links to a card already on the board.
- **Multi-select and free-form notes** — multi-select cards get checkboxes and a confirm bar; on any card press W ("write here") to answer with your own note instead of a preset option.
- **Layout fitted to the content** — image cards size themselves from the real aspect ratios; long option lists and short screens keep the title visible.
- **Always-on second screen** — long polling, so new cards appear instantly; projects as tabs, a four-up grid for four projects at once, per-project windows.
- **On your phone** — thumb-reach buttons, swipe between cards, add to home screen, log in with a password.
- **Claude Code relay** — permission requests, `AskUserQuestion`, plan approval and Claude Desktop's "start a new session?" offers become cards too. The board races Claude's own dialog: whichever you answer first wins and the other disappears. Secrets in commands are masked on the card.
- **"I'll say it in chat"** — dismiss a batch of cards and give feedback in the conversation instead; the agent is told not to ask again.
- **Hand work back the other way** — an optional "派活" (intake) page: paste a client's message, attach photos, screenshots or files straight from a phone camera or album (drag-and-drop and paste on desktop), and hand it to your triage agent. See *Intake* below.

## Quick start

Requirements: Python 3.9+. Node.js 20.3+ only if you use the Claude Code hooks. Nothing to install globally.

```bash
git clone https://github.com/sunlinhua-dotcom/decide.git
cd decide
python3 server/decide_server.py
```

The first start creates `data/` with a SQLite database and a random key (`data/key`), and prints a login link like `http://127.0.0.1:8796/#k=…`. Open it, or in a second terminal:

```bash
bin/decide open                                   # opens the board, logged in
bin/decide q "Ship v1 today?" "Ship it::all tests pass" "Wait::one more review" --rec 1
```

The card appears on the board right away. The CLI prints the card id; wait for your answer with:

```bash
bin/decide wait <id>          # prints "Ship it" (or whatever you tapped) and exits 0
```

`bin/decide` finds the local server's key in `data/key` by itself, so no configuration is needed on the same machine. Try a review card with images too:

```bash
bin/decide ask -p demo -t "Shot 12 · close-up of the cup" --before old.jpg --after new.jpg --crash "handle broke" --fix "new reference"
```

Optional: put the CLI on your PATH with `ln -s "$PWD/bin/decide" ~/.local/bin/decide`. Thumbnails use `sips` on macOS; elsewhere install Pillow into a project virtualenv (`python3 -m venv .venv && .venv/bin/pip install Pillow`, then run the CLI from that venv). Without either, images are uploaded as they are.

## Using it with AI agents

Tell your agents (for example in `CLAUDE.md` / `AGENTS.md`):

```text
When you need my decision, don't ask in chat: run
  decide q "<question>" "<option>::<one-line cost>" … --rec <n>
then keep `decide listen` running in the background; it returns when I answer.
If I answer in chat instead, run `decide chat` to clear your pending cards.
```

`decide listen` returns the answers to cards pushed from the current session (sessions are told apart by `CLAUDE_CODE_SESSION_ID`, or `DECIDE_SESSION` for other agents). Answers given while nobody is listening are kept and delivered on the next `listen`. `decide wait <id>` waits for specific cards, which suits sub-agents and scripts. Run `bin/decide help` for every command and flag.

### Claude Code hooks

Two hooks in `hooks/` turn Claude Code's own prompts into cards. Register them in `~/.claude/settings.json` (use absolute paths):

```json
{
  "hooks": {
    "PermissionRequest": [
      { "hooks": [{ "type": "command", "command": "node /abs/path/decide/hooks/decide-permission-hook.mjs", "timeout": 1860 }] }
    ],
    "PreToolUse": [
      { "matcher": "mcp__ccd_session__start_session",
        "hooks": [{ "type": "command", "command": "node /abs/path/decide/hooks/decide-offer-hook.mjs", "timeout": 1860 }] }
    ]
  }
}
```

- `decide-permission-hook.mjs` — tool permissions (allow once / allow for this session / deny with a note), `AskUserQuestion` (one card per question, "none of these, I'll write it"), and `ExitPlanMode` (approve with auto-accept edits, approve with manual edits, or send changes back).
- `decide-offer-hook.mjs` — Claude Desktop's "New session / Do it here" offers.

Cards land in the project `claude-allow`. If the board cannot be reached, nobody answers in time, or anything fails, the hooks print nothing and Claude falls back to its own dialog, so nothing is ever allowed by default. The hooks read the board address and key from `~/.config/decide/config.json` (or `DECIDE_BASE` / `DECIDE_KEY`); `~/.config/decide/hooks.json` can set `{"enabled": true, "waitSec": 1800, "decideOffers": true}`.

**Answers that never reached the session.** If `decide listen` dies or is never re-hung, an answer can sit on the board while the session keeps saying it is waiting. Two more hooks close that gap:

```json
"UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "/abs/path/decide/bin/decide hook prompt", "timeout": 10 }] }],
"Stop": [{ "hooks": [{ "type": "command", "command": "/abs/path/decide/bin/decide hook stop", "timeout": 10 }] }]
```

- `hook prompt` adds any answered-but-undelivered cards to the context of the user's next message.
- `hook stop` blocks the end of a turn once if there are such answers, or if the session has pending cards that no `listen` / `wait` is watching.
- Both print nothing on any error.

## Configuration

**Server** (environment variables; see [`.env.example`](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `DECIDE_HOST` / `DECIDE_PORT` | `127.0.0.1` / `8796` | Listen address |
| `DECIDE_HOME` | `./data` | Database, key file and media |
| `DECIDE_MEDIA` | `$DECIDE_HOME/media` | Uploaded images and videos |
| `DECIDE_PUBLIC` | `http://127.0.0.1:8796` | The address people open; `https://` turns on Secure cookies |
| `DECIDE_KEY` | generated into `$DECIDE_HOME/key` | API key for the CLI and hooks (≥ 24 chars) |
| `DECIDE_PASSWORD` | none | Optional web password for phones; without it, log in with the key |
| `DECIDE_TRUST_PROXY` | `0` | `1` = use `CF-Connecting-IP` / `X-Forwarded-For` for login rate limiting |
| `DECIDE_SERVE_STATIC` | `1` | `0` = your reverse proxy serves `web/` and media itself |

**CLI and hooks** (environment variables, or `~/.config/decide/config.json`; see [`config.example.json`](config.example.json)):

| Variable | Config key | Meaning |
|---|---|---|
| `DECIDE_BASE` | `base` | Board address, default `http://127.0.0.1:8796` |
| `DECIDE_KEY` | `key` | Server key; falls back to this repo's `data/key` |
| `DECIDE_SSH` / `DECIDE_MEDIA_ROOT` | `ssh` / `media_root` | Optional: upload media with rsync (`user@host` or `local`) into the server's `DECIDE_MEDIA`; default is HTTP upload |
| `DECIDE_GEN_CMD` | `gen_cmd` | Image command for `@prompt` options (below) |
| `DECIDE_PROJECT` | — | Default project; otherwise the git repository name |
| `DECIDE_SESSION` | — | Session id for `listen`; defaults to `CLAUDE_CODE_SESSION_ID` |

**`@prompt` images.** Point `DECIDE_GEN_CMD` at any command that writes one image file. `{prompt}`, `{out}`, `{w}` and `{h}` are substituted as whole arguments (no shell involved):

```bash
export DECIDE_GEN_CMD='my-image-tool {prompt} --out {out} --width {w} --height {h}'
```

Several commands, one per line (or a JSON array in `gen_cmd`), race each other: each takes the next image, and an idle one re-runs an image another has been stuck on for 25 seconds. A card is never blocked by image generation; options without a picture can still be chosen.

**Intake ("派活").** The page appears in the header only when `$DECIDE_HOME/intake.json` exists (re-read on every request, override the path with `DECIDE_INTAKE_CONF`). Attachments upload as soon as they are added (≤ 95 MB each, ≤ 30 per item, kept for two days if not handed over); the item is then forwarded in the background and the page shows its status, with a retry button on failure. Two destinations:

```json
{ "dir": "/path/to/triage/inbox" }
```

writes one folder per item (`item.json` with `text`, `chat`, `note`, `files`, plus `files/`), renamed into place only when complete;

```json
{ "gateway": "https://jobs.example.com", "token": "…", "capability": "route.intake" }
```

uploads each file with `POST /v1/files` (raw body, `X-Filename` header) and submits `POST /v1/jobs {capability, params: {text, chat, note}, inputs: {files}, idempotency_key}`, then follows `GET /v1/jobs/<id>`. The token stays on the server; give it the narrowest scope your gateway allows.

## Deploying

For use beyond one machine, run the server behind HTTPS. [`deploy/Caddyfile.example`](deploy/Caddyfile.example) is a complete Caddy site (automatic certificates, long-poll friendly), and [`deploy/decide.service.example`](deploy/decide.service.example) a systemd unit. Set `DECIDE_PUBLIC=https://your-domain`, `DECIDE_TRUST_PROXY=1` and a `DECIDE_PASSWORD`. A Cloudflare Tunnel in front of `127.0.0.1:8796` works the same way.

The key grants full access to the board: keep it out of repositories and chat logs. Uploaded media is served with a sandboxing Content-Security-Policy, and the CLI strips scripts from SVGs before upload.

## Project layout

```text
server/decide_server.py   HTTP API, long polling, login, static files (stdlib + SQLite)
bin/decide                CLI: ask / pick / q / wait / listen / resolve / chat / ls / open / doctor …
web/app/                  the board (vanilla JS, no build step); web/public/: login page, icons
hooks/                    Claude Code PermissionRequest / PreToolUse hooks
deploy/                   Caddy and systemd examples
tests/                    python3 -m unittest discover -s tests · node --test tests/*.test.mjs
```

The web UI is in Chinese for now — an English UI is one of the most welcome pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Author

Built by **BrightSun**, who runs a one-person studio with a crowd of AI agents and needed one place to say yes or no. Pull requests welcome.

## License

[Apache License 2.0](LICENSE) — see also [NOTICE](NOTICE).

---

# 拍板 · Decide

[English](#decide--拍板) · **中文**

一个人带着一群 AI 干活，它们会不停停下来问：这一版能不能过？四张图挑哪张？用 Postgres 还是 SQLite？这条命令能不能跑？拍板（Decide）把所有要人拍板的事集中到一块板上：每件事一张卡，你在副屏或手机上一次看一张、点一下，答案直接回到发问的那个会话。

- **AI 推卡**：一条命令（`decide q`、`decide ask`、`decide pick`）。
- **你来点**：「通过」「返修 + 一句意见」，或者点你要的那个选项。
- **答案原路回去**：哪个会话问的就回哪个会话（`decide listen` / `decide wait`），它接着干活。

不用框架、不用构建：Python 3 标准库服务 + SQLite，原生 JS 网页，单文件命令行。

## 主要功能

- **图片 / 视频审片**：上一版和这一版并排，点「通过」或「返修」并附意见；放大镜、大图层、拖线对比。
- **挑图**：候选 A / B / C / D，点图即选，也可以「选这张，但要改……」。
- **文字选择题**：`decide q "问题" "选项::一句代价" …` 把每个选项画成大卡片，按 1–9 直接选，标出推荐项。
- **选项写 `@画面`，后台配图**：写 `"A::代价::@一只橘猫坐在窗台上"`，卡片立刻上板，配图用你自己的出图命令在后台出，出一张补一张。
- **SVG 示意图卡**：用 `--svg` 附流程 / 架构 / 对比图，字清楚、秒开。
- **密密麻麻的方案图转成网页文字**：三节以上、400 字以上的方案 SVG 自动拆成一节节网页原生文字，每节可在后台配一张图；简单的图照原样贴。「看原图」能看原图。
- **完成卡能点开**：卡上的链接排在标题下面成按钮：写 `--link "文档::https://…"`，或者把网址写进标题、信息栏、SVG 里自动认出来。`decide q … --video 片.mp4` 附上要确认的片子，板上直接播放。`decide link <编号>` 给已上板的卡补链接。
- **多选和自由意见**：多选卡有勾选框和确定条；任何卡按 W「在这里写意见」，不选现成选项、直接写自己的意见。
- **按内容排版**：带图卡按图片真实比例算版面；选项多、屏幕矮时标题不会被盖住。
- **副屏常开**：长轮询，新卡即时出现；项目分栏、四宫格同时看四个项目、每个项目单开一个窗口。
- **手机上随时确认**：按钮在大拇指够得着的地方，左右滑翻卡，可添加到主屏幕，输密码登录。
- **Claude 授权中继**：Claude Code 的授权确认、`AskUserQuestion` 提问、计划批准，以及 Claude Desktop「要不要开新会话」都会出卡。和 Claude 自己的对话框抢答，哪边先答算哪边，另一边自动消失；命令里的密钥在卡上打码。
- **「在对话里说」**：一组卡一键清掉，改在对话里提意见，AI 不会再追问这几张。
- **反过来派活**：可选的「派活」页，粘客户原话、手机直接拍照 / 从相册或文件加附件（电脑可拖放、粘贴截图），交给你的分拣 Agent。见下方配置说明。

## 快速开始

需要 Python 3.9+；只有用 Claude Code 钩子时才需要 Node.js 20.3+。不用全局安装任何东西。

```bash
git clone https://github.com/sunlinhua-dotcom/decide.git
cd decide
python3 server/decide_server.py
```

第一次启动会建 `data/`，里面是 SQLite 数据库和随机生成的密钥 `data/key`，终端会打印一个 `http://127.0.0.1:8796/#k=…` 的登录链接。直接打开它，或者另开一个终端：

```bash
bin/decide open                                   # 浏览器打开决策板，自动登录
bin/decide q "今天发 v1 吗？" "发::测试全过了" "再等等::还差一轮审查" --rec 1
```

卡片马上出现在板上。命令会打印卡片 id，等你的答案：

```bash
bin/decide wait <id>          # 打印你点的选项，退出码 0
```

同一台机器上 `bin/decide` 会自己去 `data/key` 找密钥，不用配置。再试一张带图的审片卡：

```bash
bin/decide ask -p demo -t "T12 杯子特写 · 手柄朝右" --before old.jpg --after new.jpg --crash "手柄断了" --fix "换了参考图"
```

可选：`ln -s "$PWD/bin/decide" ~/.local/bin/decide` 把命令放进 PATH。缩略图在 macOS 上用自带的 `sips`；其他系统在项目虚拟环境里装 Pillow（`python3 -m venv .venv && .venv/bin/pip install Pillow`，再用这个 venv 跑命令行）。两样都没有时图片原样上传。

## 接给 AI 用

在给 AI 的说明里（比如 `CLAUDE.md` / `AGENTS.md`）写上：

```text
要我拍板时别在对话里问，跑
  decide q "<问题>" "<选项>::<一句代价>" … --rec <n>
然后在后台挂着 `decide listen`，我点完它就返回。
我要是直接在对话里回答了，跑 `decide chat` 把你推的待定卡清掉。
```

`decide listen` 只返回当前会话推的卡的答案（按 `CLAUDE_CODE_SESSION_ID` 区分会话，别的 AI 用 `DECIDE_SESSION`）。没人监听时点的答案不会丢，下次 `listen` 一启动就拿到。`decide wait <id>` 只等指定的卡，适合子代理和脚本。全部命令见 `bin/decide help`。

### Claude Code 钩子

`hooks/` 里的两个钩子把 Claude Code 自己的确认框也变成卡片。在 `~/.claude/settings.json` 里注册（写绝对路径），配置同上方英文部分的 JSON：

- `decide-permission-hook.mjs`：工具授权（允许一次 / 本会话一直允许 / 拒绝并附意见）、`AskUserQuestion`（每个问题一张卡，带「都不是，我来写」）、`ExitPlanMode`（批准并自动接受编辑、批准但逐个确认、要改）。
- `decide-offer-hook.mjs`：Claude Desktop 的「新会话 / 在这里做」。

卡片进 `claude-allow` 项目。决策板连不上、到点没人答、或者出任何错，钩子都什么也不输出，Claude 退回自己的对话框，绝不会默认放行。钩子从 `~/.config/decide/config.json`（或 `DECIDE_BASE` / `DECIDE_KEY`）读地址和密钥；`~/.config/decide/hooks.json` 可写 `{"enabled": true, "waitSec": 1800, "decideOffers": true}`。

**答案没送到会话**：`decide listen` 死了或没重挂时，用户已经在板上答了，会话还在说「在等你定」。再加两个钩子补上（JSON 同上方英文部分）：

- `hook prompt`：用户发下一条消息时，把已答但没送到的卡塞进上下文。
- `hook stop`：本轮结束时，有这种答案，或本会话有待定卡却没有 `listen` / `wait` 在听，就拦一次。
- 出任何错都什么也不输出。

## 配置说明

服务端和命令行的全部环境变量见上方英文部分的两张表，以及 [`.env.example`](.env.example)、[`config.example.json`](config.example.json)（都有中文注释）。要点：

- 服务端默认只听 `127.0.0.1:8796`，数据放 `./data`；对外用时设 `DECIDE_PUBLIC=https://你的域名`、`DECIDE_TRUST_PROXY=1`，再设一个 `DECIDE_PASSWORD` 方便手机登录。
- 命令行默认连 `http://127.0.0.1:8796`；连远端服务就设 `DECIDE_BASE` 和 `DECIDE_KEY`，或写进 `~/.config/decide/config.json`。
- **派活入口**：`$DECIDE_HOME/intake.json` 存在才在顶栏出现（每次现读，路径可用 `DECIDE_INTAKE_CONF` 改）。写 `{"dir": "收件箱目录"}` 就每件落一个子目录（`item.json` + `files/`，写完才改名）；写 `{"gateway": "…", "token": "…", "capability": "route.intake"}` 就经任务网关的 `POST /v1/files`、`POST /v1/jobs` 交件。附件单个 95 MB 以内、一次最多 30 个；令牌只放服务器，权限给到最小。
- **`@画面` 配图**：`DECIDE_GEN_CMD` 指向任何能出一张图的命令，`{prompt}` `{out}` `{w}` `{h}` 会被整体替换（不经过 shell）。多条命令一行一条（或在 `gen_cmd` 里写数组），它们从同一个队列抢活，谁空谁出；配图永远不挡卡片，没图的选项照样能选。

## 部署

多台设备用时把服务放在 HTTPS 后面：[`deploy/Caddyfile.example`](deploy/Caddyfile.example) 是一份完整的 Caddy 站点配置（自动证书、适配长轮询），[`deploy/decide.service.example`](deploy/decide.service.example) 是 systemd 服务。用 Cloudflare Tunnel 接 `127.0.0.1:8796` 也一样。

密钥等于整块板的全部权限，别放进仓库和聊天记录。上传的媒体带沙箱 CSP 出，命令行上传 SVG 前会去掉脚本。

## 目录

见上方英文部分的 Project layout。网页界面目前是中文——英文界面是最欢迎的 PR 之一，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 作者

**BrightSun**：一个人带着一群 AI 开工作室，需要一个地方集中说「行」或「不行」，于是写了它。欢迎提 PR。

## 许可证

[Apache License 2.0](LICENSE)，另见 [NOTICE](NOTICE)。
