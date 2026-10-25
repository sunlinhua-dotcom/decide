# 飞书桥 `ccpaihuo`

**English.** A small Feishu/Lark bot bridge for the dispatch desk. You DM the bot (or @ it in a group). Messages sent within 15 seconds of each other become one inbox item. The triage card and every card the receiving session later posts for that item are mirrored into Feishu as interactive cards, and a tap on either side counts. It uses the official SDK's long connection (outbound WebSocket), so you don't open any ports or need a public callback URL.

## 飞书开放平台里

1. 建一个企业自建应用，开「机器人」能力；可用范围只放你自己。
2. 权限：收发单聊 / 群聊消息、读取消息中的资源文件、更新应用发送的消息卡片。
3. 事件与回调都选「使用长连接接收」：事件订阅 `im.message.receive_v1`，回调订阅「卡片回传交互」。
4. 发布版本。

## 本机

```bash
cd paihuotai/feishu && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
mkdir -p ~/.config/ccpaihuo && cp app.example.json ~/.config/ccpaihuo/app.json && chmod 600 ~/.config/ccpaihuo/app.json
```

把 `app.json` 里的 `app_id`、`app_secret` 换成你应用的值（Secret 只放这一个文件，不进 git、不写进脚本）。`owner_open_ids` 留空：你第一次私聊机器人时自动记下。

```bash
ln -sf "$PWD/ccpaihuo" ~/.local/bin/ccpaihuo
```

常驻：照 `ccpaihuo.plist.example` 改好路径放进 `~/Library/LaunchAgents/`，然后 `launchctl load -w` 它。先前台试一次也行：`.venv/bin/python bridge.py run`。

```bash
ccpaihuo doctor
```

## 命令

| 命令 | 做什么 |
|---|---|
| `ccpaihuo run` | 常驻（launchd 跑这个） |
| `ccpaihuo reply <件号\|消息号> 文字` | 在那条飞书消息下回一句 |
| `ccpaihuo done <件号> 文字` | 回结果并标成办完，之后不再镜像这件的卡 |
| `ccpaihuo send 文字` | 直接私聊你一句 |
| `ccpaihuo mirror <编号\|卡片 id>…` | 把任意一张 decide 卡弹到飞书，之后双向同步 |
| `ccpaihuo status` / `doctor` / `restart` / `logs [行数]` | 最近的件 / 自检 / 重启 / 看日志 |

## 注意

- 同一个飞书应用只能有一个进程连长连接（竞争消费，消息会被随机分走）。别的 Agent 要接飞书，另建应用。
- 只认主人：私聊直接收，群里要 @它，其他人的消息丢弃。
- 日志不写 Secret；长连接地址里的参数会被抹掉。
- 答案只记在 decide：飞书里点了就 POST 到 decide，推卡的会话照常从 `listen` / `wait` 收到。
