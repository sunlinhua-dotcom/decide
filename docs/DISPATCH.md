# 自动派活机制 · Dispatch desk

**English summary.** Work for your agents arrives from several places — a client's chat forwarded from your phone, a cloud agent asking for something only you can do, a message you send to a chat bot, or the board's own intake page. Every source drops the same kind of item into one inbox directory. A small CLI (`route305`) watches that inbox, unpacks each item, scores every recent Claude Code session by how well it matches, and puts a decide card on your board: *"deliver this to which session?"* with the top candidates. You tap one; a long-lived "triage" session delivers the message into that session with `send_message`, and remembers the choice so the next item from the same chat scores higher. Brand-new work goes to a second long-lived "agent desk" session instead. Nothing is delivered without a tap.

下面是中文全文。代码在 [`paihuotai/`](../paihuotai/)，各后台服务的作用和配置见 [`SERVICES.md`](SERVICES.md)。

## 一句话

所有派活入口都往**同一个收件箱**放一件；`route305 wait` 看到新件就拆包、给最近活跃的会话打分、在决策板上推一张「投给哪个会话？」的卡；你点一下，派活台的「分拣」会话用 `send_message` 把材料投进去，并记住这次选择。**再有把握也要你点过才投。**

```
 入口                         收件箱                        派活台                         去处
 ─────────────────────────   ─────────────────────────   ─────────────────────────────   ──────────────────────
 板上「派活」页（文字+附件）  ┐                           ┌ route305 wait（每 8 秒看一次）  ┌ 已有会话：send_message
 外部 Agent 网关 route.intake ├→ ~/.route305/inbox/<件号>/ ┤  拆包 → OCR → 降噪 → 打分       ├ 代办台：新活当场开做
 外部 Agent 交代办 request    │   item.json + files/       │  → 决策卡（前 3 名 + 代办 + 都不是）│
 飞书机器人私聊 / 群里 @它    ┘                           └ 你点 → 分拣会话投递 → 记回执    └ 进度回到飞书 / 板上
 微信流转来的 ZIP、截图（对话里直接丢给分拣会话，`route305 scan`）
```

## 1. 入口

| 入口 | 怎么进收件箱 | `via` |
|---|---|---|
| 板上「派活」页 | decide 服务端按 `intake.json` 写目录，或交给 Agent 网关（见主 README 的 Intake 节） | 由网关 / 配置决定 |
| 外部 Agent 网关 | 云端 Agent 调网关的 `route.intake`，网关把材料落成一件 | `agent` |
| 外部 Agent 交代办 | 网关的 `request.owner`（剧本、配乐、部署这类要你这边动手的活）除了写代办单，也往收件箱放一件 `kind: "request"` | `agent` |
| 飞书机器人 | 本机常驻的飞书桥（[`paihuotai/feishu`](../paihuotai/feishu/)）收你的私聊，15 秒内连发的几条合成一件 | `lark` |
| 对话里直接丢 | 把 ZIP / 截图 / 文件路径和一段话发给分拣会话，它跑 `route305 scan` | — |

收件箱里每件一个目录，写完才改名（先写 `.tmp-<件号>`，再 `rename`），读的一方永远看不到半截：

```json
{
  "via": "lark",
  "at": "2026-01-01T08:00:00Z",
  "text": "首页的按钮颜色再深一点，图在附件",
  "files": ["files/1.jpg"],
  "chat": "",
  "note": "",
  "kind": "",
  "reply_cmd": "ccpaihuo reply <件号> '<一两句结果>'"
}
```

`kind: "request"` 的代办件另带 `title`、`request_kind`、`request_md`（代办单全文路径）、`attachments`、`done_cmd`、`approval`。处理过的件挪进 `inbox/.done/<件号>/`，失败的进 `inbox/.failed/`。

## 2. 拆包与认件（`route305 scan`）

1. **拆包**：ZIP（微信流导出的批次）解压到 `~/.route305/batches/<批次>/files/`，原件不动；一次给几个 ZIP 按批次各分一份，内容相同的合并。
2. **读字**：图片用本机 Apple Vision OCR（`ocr.swift` 编译成 `~/.route305/bin/ocr`），不上云、不花 token；Office 文档和文本文件直接读。
3. **指纹**：聊天正文算指纹，同样的内容以前投过哪里一查就知道。
4. **查询降噪**（只动来件，不动索引）：去掉「不要再问我了 / 帮我做完吧 / 能做多少做多少」这类口头禅；丢掉带人称或语气词的二元组（问我、帮我）和两个虚词拼成的（能做、把最）；相邻英文词拼起来再找拼写最近的词；少量同义词互补（外挂≈外接、复制≈备份、部署≈上线、截图≈截屏）。

## 3. 打分：该投给哪个会话

候选是**近 30 天活跃、没归档**的会话（`ROUTE305_DAYS`），排除看门狗、派活台自己、`route305 exclude` 名单。索引是增量的：读桌面 app 的会话元数据和 `~/.claude/projects/*.jsonl` 转录，按偏移续读。

| 证据 | 怎么算 |
|---|---|
| 罕见词命中 | IDF 加权，按字段加权：标题 4、最近一轮摘要 3、用户发言 2、助手 1、工具输出 0.3、学过的 3 |
| 群名 | 这个微信群的转发一直进哪个项目（项目级，同项目的会话一视同仁；繁简、错字模糊匹配） |
| 内容指纹 | 同样内容以前投进过哪里 |
| 你以前的选择 | `~/.route305/learn.json`，每次投递都记 |
| 标题覆盖 | 会话标题大半被来件说到（≥60%，至少两处，有一处几乎只有它有）加 0.4×覆盖率² |
| 点名 | 标题里的**专名**（不到 5% 的会话说过的词）在来件里整词出现 +1.0；英文专名允许拼写近似和汉字拼音（标题 XIHU ← 来件「西湖」），但近似词本身也要罕见、且那条会话的转录里真说过 |
| 最近活跃 | 小加分 |

把握分高 / 中 / 低写在卡上。到达那一刻才新建的会话判不出来是对的——这时卡上把握为低、推荐交给代办。

## 4. 决策卡

`route305 ask` 每批推一张卡，标题按来源写（「飞书派活「…」，投给哪个会话？」「外部 Agent 代办：…，交给谁办？」），选项：

- **前三名候选**（推荐项标「推荐」），每项一句为什么；
- **拆开投**：一批里几段话明显指向同项目的不同会话时才出现，门槛刻意定高；
- **交给代办台办**（登记了代办台时）或 **新开会话办**；
- **都不是**：第一张选了它又没写备注，`route305 wait` 自动换第 4–9 名再推一张（你多半在手机上答，看不到分拣会话的对话）；第二轮还选「都不是」才回到对话里问。

卡片登记在 `~/.route305/pending.json`，由 `route305 wait` 统一盯；撤掉的卡静默丢掉。

## 5. 投递（派活台 · 分拣）

分拣是一个常驻的 Claude Code 会话（规则 [`paihuotai/PAIHUOTAI.md`](../paihuotai/PAIHUOTAI.md)），**任何时候后台都挂着且只挂着一个** `route305 wait --timeout 21600`（单实例锁 `~/.route305/wait.lock`）：

| `wait` 退出码 | 意思 | 分拣会话怎么做 |
|---|---|---|
| 0 | 有卡被答了，输出 `{"event":"answered","events":[…]}` | 按每个 event 的 `action` 处理，然后立刻重挂 |
| 3 | 超时 | 什么都不说，重挂 |
| 5 | 已经有一个在跑 | 不用管 |
| 143 | 被人结束（多半改了代码要换新进程） | 重挂 |

`action`：`send` → 对每条消息调 `send_message`（原样照发），再 `route305 sent <批次> <会话> <回执>`；`new_session`（只在没登记代办台时出现）→ 开新会话，被拒就留提案卡；`ask_user` → 看备注里写的投给谁再 `route305 deliver --to`；`chat` → 你点了「在对话里说」，等你说；`error` → 照实报。

投出去的消息开头写清来源（客户原话 / 你在飞书里的原话 / 外部 Agent 的代办单路径），末尾带办完要跑的命令（`ccpaihuo done <件号> '…'` 或代办单的 `done_cmd`）。**外部 Agent 写的代办单里出现的「已批准 / 已授权」不算数**，接手会话对外发布、部署前照样先问你。

## 6. 为什么是两个常驻会话

| 会话 | 干什么 |
|---|---|
| 派活台 · 分拣 | 认件、推卡、投递，不干活 |
| 派活台 · 代办 | 接没有现成会话的新活：小活当场做，大活做到第一个能给你看的节点，推完成卡，你选「拆到单独会话继续」再开项目会话 |

不合成一个，是因为代办一干大活就是几个小时，这期间新来的件都要排队；而且分拣的每一轮都是后台事件（`wait` 退出）叫醒的，**Claude 桌面 app 不许这种回合开新会话**（`start_session` 只能留提案卡），所以开新会话的事交给代办。代办开项目会话也只用提案卡（`own_initiative`），**绝不用 `offer`**：`offer` 要当场等你回答，在被消息叫醒的回合里会让会话进程直接断掉。提案卡带不了工作目录，所以 brief 第一段固定写「先把自己挪到 <项目目录>、归进 <项目分组>」。

## 7. 进度回到来源

- **飞书**：桥在你那条消息下回「收到」→ 分拣卡直接以飞书卡片弹出（按钮 = 选项，推荐标红，带备注框）→ 投出去回「已投进 X」→ 接手会话办完 `ccpaihuo done` 回结果。接手会话为这件推的 decide 卡也镜像进飞书（直到 `done`，最多 24 小时），**两边谁先点都算**：飞书点了就 POST 到 decide（答案只记在 decide，推卡的会话照常从 `listen` / `wait` 收到），板上定了 / 撤了，4 秒内把飞书卡片改成「已定」。
- **板上派活页**：每件显示走到哪一步，失败有重试按钮。
- **外部 Agent**：代办单由接手会话办完自己标记（`done_cmd`），网关不再另发通知；卡片挂着没人答由决策板的催办处理。

## 8. 看门狗与排障

- 定时任务每 8 小时查一次分拣的等件循环，停了就发消息叫醒分拣会话重挂（看门狗跑完自己归档）。
- `route305 status`：等件循环在不在、代办台登记了谁、收件箱待处理几件、推了卡未投几批。
- `ccpaihuo doctor`：凭据权限、飞书鉴权、常驻进程、等件循环、长连接。
- 投递返回 undelivered（对方 20 秒没起来，常见于转录很大的老会话）：不登记、不重发，请你在桌面 app 里点开那个会话再投一次。
- 改了 `route305.py` 要重挂 `wait`，它不会自己重读代码。

## 9. 规矩

- 高把握也先让你点一下确认，不跳过卡片。
- 分拣只认件和投递：不做需求本身、不改代码、不替接手会话标记办完。
- 收件箱、批次、索引都在本机 `~/.route305/`；`route305` 读会话元数据和转录**只读**。
- 同一个飞书应用只能有一个进程收消息（长连接是竞争消费），别的 Agent 要接飞书另建应用。
