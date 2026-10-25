# 派活台 · Dispatch desk

**English.** Optional add-on for decide. Every place work comes from (an external agent gateway, a Feishu/Lark bot, the board's intake page, or files you drop into a chat) writes the same kind of item into one inbox. `route305` scores your recent Claude Code sessions against each item and puts a card on the board: *"deliver this to which session?"*. When you tap a session, a long-lived "triage" session delivers the item into it with `send_message`. Nothing is delivered until you have tapped. macOS + Claude desktop app only, because it reads the app's session metadata. Mechanism: [`docs/DISPATCH.md`](../docs/DISPATCH.md). Services and config: [`docs/SERVICES.md`](../docs/SERVICES.md).

```
paihuotai/
├── PAIHUOTAI.md          两个常驻会话共用的规则模板
├── route305/
│   ├── route305          启动器（软链到 PATH）
│   ├── route305.py       拆包、打分、推卡、等件、投递
│   ├── ocr.swift         本机 Apple Vision OCR（图片读字不上云）
│   ├── desk/CLAUDE.md    「派活台 · 分拣」的引子
│   └── agentdesk/CLAUDE.md「派活台 · 代办」的引子
└── feishu/               飞书机器人桥（可选），见 feishu/README.md
```

## 装

需要 macOS、Claude 桌面 app（Code 标签页）、Python 3.9+、已经配好的 `decide` 命令（`decide doctor` 能通）。

```bash
ln -sf "$PWD/paihuotai/route305/route305" ~/.local/bin/route305
```

```bash
mkdir -p ~/.route305/bin && swiftc -O paihuotai/route305/ocr.swift -o ~/.route305/bin/ocr
```

```bash
route305 status
```

OCR 可以不装（图片就不读字）。想让「西湖 ↔ XIHU」这类拼音近似也能对上，装 `pypinyin`（启动器用 `python3 -I`，要装进系统 Python 的 site-packages）。

## 开两个常驻会话

1. 桌面 app 里在 `paihuotai/route305/desk` 开一个会话，第一句话说「开工」。它照 `PAIHUOTAI.md` 的「分拣 · 开工」给自己改名、挂上 `route305 wait`。
2. 在 `paihuotai/route305/agentdesk` 再开一个会话，改名「派活台 · 代办」，然后登记它：

```bash
route305 agentdesk "派活台 · 代办"
```

不登记代办也能用：卡片上的选项会从「交给代办台办」换成「新开会话办」，分拣会话被拒时退成提案卡。

## 命令

| 命令 | 做什么 |
|---|---|
| `route305 scan <路径…> [--text 文字]` | 拆包、读字、打分，建一个批次，打印候选 |
| `route305 ask [批次…]` | 把候选推成 decide 卡；不给批次就推上一次 scan 的全部 |
| `route305 wait [--timeout 秒]` | 常驻：收件箱来件自动 scan + ask；有卡被答就打印投递 JSON 退出（0），超时 3，已有一个在跑 5 |
| `route305 deliver <批次> [--to 会话] [--note …]` | 读答案（或直接指定），打印要 `send_message` 的消息 |
| `route305 sent <批次> <会话> <回执…>` | 记下投递回执，下次同类来件会更偏向这个会话 |
| `route305 agentdesk [会话] [--off]` | 登记 / 查看 / 注销代办台 |
| `route305 exclude <会话…>` | 不让某些会话当候选 |
| `route305 index` / `status` / `show <批次>` | 刷新索引 / 看状态 / 看一批的详情 |

## 往收件箱放件

任何程序都能当入口：在 `~/.route305/inbox/` 下先写 `.tmp-<件号>/item.json`（附件放同目录 `files/`），写完 `rename` 成 `<件号>/`。格式见 [`docs/DISPATCH.md`](../docs/DISPATCH.md) 第 1 节。

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `ROUTE305_HOME` | `~/.route305` | 收件箱、批次、索引、学习记录都在这里 |
| `ROUTE305_INBOX` | `$ROUTE305_HOME/inbox` | 收件箱 |
| `ROUTE305_DAYS` | `30` | 只看最近多少天活跃的会话 |
| `ROUTE305_EXCLUDE_TITLE` | `看门狗\|中继\|分拣台\|代办台\|派活台\|route305` | 标题匹配就不当候选（正则） |
| `ROUTE305_DECIDE_PROJECT` | `route305` | 卡片挂在板上哪个项目下 |
| `ROUTE305_REQUEST_CWD` | `~` | 代办件默认的工作目录 |
| `ROUTE305_DONE_CMD` | `agent-gateway requests done` | 代办单没带 `done_cmd` 时提示的办完命令 |
| `ROUTE305_WXB_INBOX` | 空 | 另一个按批次落盘的收件目录（含 `Ready/ Staging/ Failures/`）；不用留空 |

## 隐私

- `route305` 读桌面 app 的会话元数据和 `~/.claude/projects/*.jsonl` 转录，**只读**；索引只存在本机 `~/.route305/index/`。
- 卡片上只出现会话标题、一句理由和来件摘要，不上传转录。
- OCR 在本机跑。
