# Contributing · 参与贡献

**English** · [中文](#中文说明)

Pull requests are welcome — bug fixes, new card types, an English web UI, better docs, ports to other agents.

## Run it locally

```bash
git clone https://github.com/<you>/decide.git && cd decide
DECIDE_HOME=/tmp/decide-dev python3 server/decide_server.py      # throwaway data dir
DECIDE_HOME=/tmp/decide-dev bin/decide open                       # the CLI finds the key there
DECIDE_HOME=/tmp/decide-dev bin/decide q "Does it work?" "Yes" "No"
```

The web page is plain HTML / CSS / JS in `web/app/`: edit, reload, done. When you change `app.js` or `style.css`, bump the `?v=` number in `index.html` and `help.html` so open boards pick up the new files.

## Before you open a pull request

```bash
python3 -m unittest discover -s tests -v     # server + CLI end to end
node --test tests/*.test.mjs                        # Claude Code hook logic (Node 20.3+)
```

- Keep it dependency-free: the server and CLI use the Python standard library only; the web page has no build step.
- One topic per pull request; describe what the user sees change, and add a screenshot for UI changes.
- Add or update a test when you change the API, the CLI or the hooks.
- Never commit keys, passwords, `data/` or real media.
- By submitting a pull request you agree that your contribution is licensed under the Apache License 2.0, like the rest of the project.

## Ideas

- English (and other languages) for the web UI
- More agent integrations (Codex, Gemini CLI, Cursor …) alongside the Claude Code hooks
- Ready-made `DECIDE_GEN_CMD` adapters for popular image APIs
- Docker image

---

## 中文说明

欢迎提 PR：修 bug、加新卡片类型、做英文界面、改文档、接别的 AI 工具都行。

**本地跑起来**：照上面的命令，用 `DECIDE_HOME=/tmp/decide-dev` 指一个临时数据目录，不会碰到你正在用的板。网页在 `web/app/`，纯 HTML / CSS / JS，改完刷新即可；改了 `app.js` 或 `style.css` 记得把 `index.html`、`help.html` 里的 `?v=` 加一，让开着的页面拿到新文件。

**提 PR 之前**：跑 `python3 -m unittest discover -s tests -v` 和 `node --test tests/*.test.mjs`，两套都要过。

- 保持零依赖：服务端和命令行只用 Python 标准库，网页不引入构建步骤。
- 一个 PR 只做一件事，写清用户看到的变化，界面改动附截图。
- 改了 API、命令行或钩子，就补或改对应的测试。
- 不要提交密钥、密码、`data/` 目录和真实素材。
- 提交 PR 即表示你同意贡献内容按 Apache License 2.0 授权，与项目其余部分一致。
