#!/usr/bin/env node
// Claude Code PermissionRequest hook: opens a card on the decide board for every
// permission request, question (AskUserQuestion) and plan approval
// (ExitPlanMode), and prints the answer given there.
//
// Claude shows its own dialog at the same time and takes whichever answer comes
// first. An answer on the board closes that dialog; an answer in the dialog
// takes the card down (the hook watches the session transcript, and on macOS
// notices a Bash command starting). When this script prints nothing (board not
// reachable, nobody answered in time, answered in the dialog), Claude simply
// keeps waiting on its own dialog, so nothing is ever allowed by default.
//
// Register in ~/.claude/settings.json under hooks.PermissionRequest:
//   { "hooks": [{ "type": "command", "command": "node /abs/path/to/hooks/decide-permission-hook.mjs", "timeout": 1860 }] }
// Board address and key: ~/.config/decide/config.json or DECIDE_BASE / DECIDE_KEY.
// Optional settings in ~/.config/decide/hooks.json (DECIDE_HOOK_CONFIG overrides the path):
//   { "enabled": true, "waitSec": 1800, "decide": true }

import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { decideConfig, openCards, permissionCards, sessionContext } from './decide-channel.mjs';

// 隐私项目：会话目录落在 ~/.claude/private-roots 列的根目录下（每行一个），整个钩子静默退出，什么都不外发
try {
  const roots = fs.readFileSync(path.join(os.homedir(), '.claude', 'private-roots'), 'utf8')
    .split('\n').map((s) => s.trim().replace(/\/+$/, '')).filter((s) => s && !s.startsWith('#'));
  const dirs = [process.env.CLAUDE_PROJECT_DIR, process.cwd()].filter(Boolean);
  if (dirs.some((d) => roots.some((r) => d === r || d.startsWith(r + '/')))) process.exit(0);
} catch (_) {}

const CONFIG_FILE = process.env.DECIDE_HOOK_CONFIG || path.join(os.homedir(), '.config', 'decide', 'hooks.json');
const DEFAULTS = { enabled: true, waitSec: 1800, decide: true };
const TRANSCRIPT_POLL_MS = 1000;
const TRANSCRIPT_TAIL_BYTES = 512 * 1024;

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

/**
 * True when every field `part` has is equal in `whole`. Claude parses the tool
 * input before the hook sees it, so keys can be reordered and defaults filled in
 * (AskUserQuestion gains `multiSelect`), and an exact JSON comparison never matches.
 */
function contains(whole, part) {
  if (part === null || typeof part !== 'object') return whole === part;
  if (whole === null || typeof whole !== 'object') return false;
  if (Array.isArray(part)) {
    return Array.isArray(whole) && whole.length === part.length && part.every((item, index) => contains(whole[index], item));
  }
  return Object.keys(part).every((key) => contains(whole[key], part[key]));
}

const sameInput = (a, b) => contains(a, b) || contains(b, a);

/**
 * Watches the session transcript, and the transcripts of its subagents, for the
 * tool's result, which appears once the request was answered somewhere else
 * (Claude's own dialog). Calls `onSettled` once; returns a function that stops.
 */
function watchTranscript(transcriptPath, toolName, toolInput, onSettled) {
  if (typeof transcriptPath !== 'string' || !transcriptPath) return () => {};
  // A subagent's calls are written to <session>/subagents/agent-*.jsonl next to the main file.
  const subagentDir = path.join(path.dirname(transcriptPath), path.basename(transcriptPath, '.jsonl'), 'subagents');
  // Unanswered calls of this tool seen so far; the ones whose input matches are
  // watched, and failing a match, any call of the same tool.
  const open = new Map();
  const files = new Map();
  let stopped = false;

  const listFiles = () => {
    let names = [];
    try {
      names = fs.readdirSync(subagentDir).filter((name) => name.endsWith('.jsonl')).map((name) => path.join(subagentDir, name));
    } catch {
      // No subagents yet.
    }
    return [transcriptPath, ...names];
  };

  /** Files present now are read from their recent tail; files that appear later are read whole. */
  const track = (file, existedAtStart) => {
    if (files.has(file)) return;
    let offset = 0;
    try {
      offset = existedAtStart ? Math.max(0, fs.statSync(file).size - TRANSCRIPT_TAIL_BYTES) : 0;
    } catch {
      return;
    }
    files.set(file, { offset, pending: '', firstPass: existedAtStart });
  };

  const readNew = (file, state) => {
    let fd;
    try {
      const size = fs.statSync(file).size;
      if (size <= state.offset) return [];
      fd = fs.openSync(file, 'r');
      const buffer = Buffer.alloc(size - state.offset);
      fs.readSync(fd, buffer, 0, buffer.length, state.offset);
      state.offset = size;
      const lines = (state.pending + buffer.toString('utf8')).split('\n');
      state.pending = lines.pop() ?? '';
      return lines;
    } catch {
      return [];
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
    }
  };

  const scanFile = (file, state) => {
    for (const line of readNew(file, state)) {
      let entry;
      try {
        entry = JSON.parse(line);
      } catch {
        continue;
      }
      const content = Array.isArray(entry?.message?.content) ? entry.message.content : [];
      if (entry?.type === 'assistant') {
        for (const block of content) {
          if (block?.type === 'tool_use' && block.name === toolName) open.set(block.id, sameInput(block.input, toolInput));
        }
      } else if (entry?.type === 'user') {
        const results = content.filter((block) => block?.type === 'tool_result' && open.has(block.tool_use_id));
        if (results.length === 0) continue;
        const anyMatch = [...open.values()].some(Boolean);
        const settled = !state.firstPass && results.some((block) => !anyMatch || open.get(block.tool_use_id));
        for (const block of results) open.delete(block.tool_use_id);
        if (settled) return true;
      }
    }
    state.firstPass = false;
    return false;
  };

  const scan = (atStart = false) => {
    if (stopped) return;
    for (const file of listFiles()) track(file, atStart);
    for (const [file, state] of files) {
      if (scanFile(file, state)) {
        stop();
        onSettled();
        return;
      }
    }
  };

  const timer = setInterval(scan, TRANSCRIPT_POLL_MS);
  timer.unref();
  scan(true);
  function stop() {
    stopped = true;
    clearInterval(timer);
  }
  return stop;
}

const listProcesses = () => new Promise((resolve) => {
  execFile('ps', ['-axww', '-o', 'pid=,ppid=,command='], { timeout: 3000, maxBuffer: 16 * 1024 * 1024 }, (error, stdout) => {
    if (error) return resolve([]);
    resolve(stdout.split('\n').map((line) => {
      const match = /^\s*(\d+)\s+(\d+)\s+(.*)$/.exec(line);
      return match ? { pid: Number(match[1]), ppid: Number(match[2]), command: match[3] } : null;
    }).filter(Boolean));
  });
});

/** A stretch of the command that survives the shell quoting Claude wraps it in. */
const commandProbe = (command) => {
  const pieces = String(command).split(/['\n\\]/).map((piece) => piece.trim()).filter((piece) => piece.length >= 6);
  const longest = pieces.sort((a, b) => b.length - a.length)[0];
  return longest ? longest.slice(0, 60) : null;
};

/**
 * Notices a Bash call starting to run, which means Claude's own dialog allowed
 * it. The transcript only shows the result once the command ends, so without
 * this a long command answered in Desktop would leave the card up everywhere.
 */
function watchCommandStart(toolName, toolInput, onStarted) {
  const probe = toolName === 'Bash' ? commandProbe(toolInput?.command) : null;
  if (!probe || process.platform !== 'darwin') return () => {};
  let stopped = false;
  let claudePid = null;
  let known = null;

  const tick = async () => {
    if (stopped) return;
    const processes = await listProcesses();
    if (claudePid === null) {
      // The hook runs as claude → sh → node; the claude process is the one that runs the command.
      const byPid = new Map(processes.map((item) => [item.pid, item]));
      let current = byPid.get(process.ppid);
      for (let depth = 0; current && depth < 6; depth++) {
        if (/(^|\/)claude(\s|$)/.test(current.command)) {
          claudePid = current.pid;
          break;
        }
        current = byPid.get(current.ppid);
      }
      if (claudePid === null) {
        stop();
        return;
      }
    }
    const running = processes.filter((item) => item.ppid === claudePid && item.pid !== process.pid && item.command.includes(probe));
    if (known === null) {
      known = new Set(running.map((item) => item.pid));
      return;
    }
    if (running.some((item) => !known.has(item.pid))) {
      stop();
      onStarted();
    }
  };

  const timer = setInterval(() => void tick(), TRANSCRIPT_POLL_MS);
  timer.unref();
  void tick();
  function stop() {
    stopped = true;
    clearInterval(timer);
  }
  return stop;
}

function hookOutput(decision, payload) {
  const suggestions = payload.permission_suggestions;
  if (decision.behavior === 'allow') {
    const out = { behavior: 'allow' };
    // AskUserQuestion and ExitPlanMode ignore a bare "allow": the answer has to
    // come back inside the tool input, the way Claude's own dialog returns it.
    if (decision.answers) out.updatedInput = { ...payload.tool_input, answers: decision.answers };
    else if (payload.tool_name === 'ExitPlanMode') out.updatedInput = { ...payload.tool_input };
    if (Array.isArray(decision.updatedPermissions)) out.updatedPermissions = decision.updatedPermissions;
    else if (decision.mode) out.updatedPermissions = [{ type: 'setMode', mode: decision.mode, destination: 'session' }];
    else if (decision.always && Array.isArray(suggestions) && suggestions.length) out.updatedPermissions = suggestions;
    return { hookSpecificOutput: { hookEventName: 'PermissionRequest', decision: out } };
  }
  return {
    hookSpecificOutput: {
      hookEventName: 'PermissionRequest',
      decision: {
        behavior: 'deny',
        message: decision.message || 'The user denied this on the decide board. Stop and wait for their instructions.',
        interrupt: decision.interrupt !== false,
      },
    },
  };
}

async function main() {
  let payload;
  try {
    payload = JSON.parse(await readStdin());
  } catch {
    return;
  }
  if (payload?.hook_event_name !== 'PermissionRequest' || typeof payload.tool_name !== 'string') return;

  const config = { ...DEFAULTS, ...(readJson(CONFIG_FILE) ?? {}) };
  if (config.enabled === false || config.decide === false) return;
  const board = decideConfig();
  if (!board) return;
  const waitMs = Math.max(5, Number(config.waitSec) || DEFAULTS.waitSec) * 1000;

  let specs;
  let toDecision;
  try {
    ({ cards: specs, toDecision } = permissionCards(payload, sessionContext(payload)));
  } catch {
    return;
  }

  await new Promise((resolve) => {
    let settled = false;
    const cards = openCards(board, specs, waitMs);
    const finish = async (output) => {
      if (settled) return;
      settled = true;
      stopWatching();
      clearTimeout(safety);
      if (output) process.stdout.write(`${JSON.stringify(output)}\n`);
      await cards.cancel();
      resolve();
    };

    cards.result.then((answers) => {
      if (!answers) return finish(null);
      let output = null;
      try {
        output = hookOutput(toDecision(answers), payload);
      } catch {
        output = null;
      }
      return finish(output);
    });

    // Answered in Claude's own dialog: take the card down so every device clears it.
    const stopTranscript = watchTranscript(payload.transcript_path, payload.tool_name, payload.tool_input, () => finish(null));
    const stopCommandWatch = watchCommandStart(payload.tool_name, payload.tool_input, () => finish(null));
    const stopWatching = () => {
      stopTranscript();
      stopCommandWatch();
    };
    const safety = setTimeout(() => finish(null), waitMs + 15_000);
    for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.once(signal, () => finish(null));
  });
}

main().then(() => process.exit(0), () => process.exit(0));
