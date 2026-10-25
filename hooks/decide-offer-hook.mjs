#!/usr/bin/env node
// Claude Code PreToolUse hook: Claude Desktop's "New session / Do it here"
// offer (mcp__ccd_session__start_session with initiation "offer") becomes a
// card on the decide board as well.
//
// The offer card only appears in Desktop once the tool runs, so while this
// hook waits the board is the one place to answer. The answer comes back to
// Claude as the reason the call was stopped: "New session" tells it to start
// the session at once with a full brief, "Do it here" tells it to carry on in
// this session. When the board cannot be reached, nobody answers in time, or
// anything goes wrong, this prints nothing and Desktop shows its own card.
//
// Register in ~/.claude/settings.json under hooks.PreToolUse:
//   { "matcher": "mcp__ccd_session__start_session",
//     "hooks": [{ "type": "command", "command": "node <this file>", "timeout": 1860 }] }
// Settings shared with the permission hook, in ~/.config/decide/hooks.json:
//   { "waitSec": 1800, "decide": true, "decideOffers": true }

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { decideConfig, offerCards, openCards, sessionContext } from './decide-channel.mjs';

// 隐私项目：会话目录落在 ~/.claude/private-roots 列的根目录下（每行一个），整个钩子静默退出，什么都不外发
try {
  const roots = fs.readFileSync(path.join(os.homedir(), '.claude', 'private-roots'), 'utf8')
    .split('\n').map((s) => s.trim().replace(/\/+$/, '')).filter((s) => s && !s.startsWith('#'));
  const dirs = [process.env.CLAUDE_PROJECT_DIR, process.cwd()].filter(Boolean);
  if (dirs.some((d) => roots.some((r) => d === r || d.startsWith(r + '/')))) process.exit(0);
} catch (_) {}

const CONFIG_FILE = process.env.DECIDE_HOOK_CONFIG || path.join(os.homedir(), '.config', 'decide', 'hooks.json');
const DEFAULT_WAIT_SEC = 1800;

const readJson = (file) => {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
};

const readStdin = () => new Promise((resolve) => {
  let data = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { data += chunk; });
  process.stdin.on('end', () => resolve(data));
  process.stdin.on('error', () => resolve(data));
});

async function main() {
  let payload;
  try {
    payload = JSON.parse(await readStdin());
  } catch {
    return;
  }
  if (payload?.hook_event_name !== 'PreToolUse' || payload.tool_name !== 'mcp__ccd_session__start_session') return;
  if (payload.tool_input?.initiation !== 'offer' || payload.tool_input?.proposal_id) return;

  const config = readJson(CONFIG_FILE) ?? {};
  if (config.enabled === false || config.decide === false || config.decideOffers === false) return;
  const board = decideConfig();
  if (!board) return;
  const waitMs = Math.max(5, Number(config.waitSec) || DEFAULT_WAIT_SEC) * 1000;

  const { cards: specs, toOutput } = offerCards(payload, sessionContext(payload));
  const cards = openCards(board, specs, waitMs);
  let done = false;
  const stop = async () => {
    if (done) return;
    done = true;
    await cards.cancel();
    process.exit(0);
  };
  for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.once(signal, () => void stop());
  const safety = setTimeout(() => void stop(), waitMs + 15_000);

  const answers = await cards.result;
  clearTimeout(safety);
  if (answers && !done) process.stdout.write(`${JSON.stringify(toOutput(answers))}\n`);
  done = true;
  await cards.cancel();
}

main().then(() => process.exit(0), () => process.exit(0));
