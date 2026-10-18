// The decide board as one more place to answer a
// local Claude session's permission request, question, plan or new-session
// offer. The hooks open cards here next to their other channels and take
// whichever answer comes first; every failure resolves to "no answer" so a hook
// falls back to Claude's own dialog and never allows anything by default.
//
// Board address and key come from ~/.config/decide/config.json (written by the
// `decide` CLI) or DECIDE_BASE / DECIDE_KEY for a local test copy.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const DECIDE_PROJECT = 'claude-allow';
const PROJECT_NAME = 'Claude 授权';
const MAX_CODE = 60_000;
const MAX_TITLE = 200;
const DESKTOP_SESSIONS_DIR = process.env.CLAUDE_DESKTOP_SESSIONS_DIR
  || path.join(os.homedir(), 'Library', 'Application Support', 'Claude', 'claude-code-sessions');

const readJson = (file) => {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
};

/** `{ base, key }` for the board, or null when this machine has no decide CLI set up. */
export function decideConfig() {
  const file = readJson(process.env.DECIDE_CONFIG || path.join(os.homedir(), '.config', 'decide', 'config.json')) ?? {};
  const base = process.env.DECIDE_BASE || file.base;
  const key = process.env.DECIDE_KEY || file.key;
  if (typeof base !== 'string' || !/^https?:\/\//.test(base) || typeof key !== 'string' || !key) return null;
  return { base: base.replace(/\/+$/, ''), key };
}

/* ---------- Masking ---------- */

const DOTS = '•••';
const SECRET_PATTERNS = [
  // Authorization headers and bearer tokens.
  [/(\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}/gi, `$1${DOTS}`],
  [/(\bAuthorization:\s*(?:Basic|Token)\s+)\S+/gi, `$1${DOTS}`],
  // user:password@ in URLs.
  [/(\/\/[^/\s:@'"]+:)[^@\s/'"]+@/g, `$1${DOTS}@`],
  // name=value / name: value where the name says it is secret.
  [/((?:api[_-]?key|access[_-]?key|secret(?:[_-]?key)?|client[_-]?secret|private[_-]?key|token|auth[_-]?token|password|passwd|pwd|passphrase)["']?\s*[:=]\s*["']?)([^\s"'&,;]{4,})/gi, `$1${DOTS}`],
  // --password x / --token=x / -p'x' style flags.
  [/(--?(?:password|passwd|token|api-key|apikey|secret|key|pass)(?:=|\s+))(['"]?)[^\s'"]{4,}\2/gi, `$1$2${DOTS}$2`],
  // Well-known token shapes.
  [/\b(sk-(?:ant-|proj-)?)[A-Za-z0-9_-]{16,}/g, `$1${DOTS}`],
  [/\b(gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}/g, `$1${DOTS}`],
  [/\b(xox[abposr]-)[A-Za-z0-9-]{10,}/g, `$1${DOTS}`],
  [/\b(glpat-)[A-Za-z0-9_-]{16,}/g, `$1${DOTS}`],
  [/\bAKIA[0-9A-Z]{16}\b/g, `AKIA${DOTS}`],
  [/\b(AIza)[0-9A-Za-z_-]{30,}/g, `$1${DOTS}`],
  [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g, `eyJ${DOTS}`],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, `-----BEGIN PRIVATE KEY----- ${DOTS} -----END PRIVATE KEY-----`],
];

/**
 * Long runs that mix letters and digits, the shape of a generated key. A git
 * hash (40 lowercase hex) stays, and so do ids made of short dash-separated
 * pieces (UUIDs, worktree names).
 */
const maskLongRuns = (text) => text.replace(/[A-Za-z0-9_-]{32,}/g, (run) => {
  if (!/\d/.test(run) || !/[A-Za-z]/.test(run)) return run;
  if (/^[0-9a-f]{40}$/.test(run)) return run;
  if (run.split(/[-_]+/).every((piece) => piece.length <= 12)) return run;
  return `${run.slice(0, 4)}${DOTS}`;
});

/** What a card may show of `text`: anything that looks like a token, key or password is replaced by dots. */
export function maskSecrets(text) {
  let out = String(text ?? '');
  for (const [pattern, replacement] of SECRET_PATTERNS) out = out.replace(pattern, replacement);
  return maskLongRuns(out);
}

/* ---------- Where the request came from ---------- */

/** Desktop's title for a CLI session; only files touched in the last three days are read. */
export function desktopTitle(cliSessionId, baseDir = DESKTOP_SESSIONS_DIR) {
  if (typeof cliSessionId !== 'string' || !cliSessionId) return null;
  const since = Date.now() - 3 * 24 * 3600 * 1000;
  const walk = (dir, depth) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return null;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory() && depth < 2) {
        const found = walk(full, depth + 1);
        if (found !== null) return found;
      } else if (entry.isFile() && depth === 2 && entry.name.endsWith('.json')) {
        try {
          if (fs.statSync(full).mtimeMs < since) continue;
          const text = fs.readFileSync(full, 'utf8');
          if (!text.includes(cliSessionId)) continue;
          const data = JSON.parse(text);
          const ids = [data.cliSessionId, ...(Array.isArray(data.priorCliSessionIds) ? data.priorCliSessionIds : [])];
          if (ids.includes(cliSessionId)) return typeof data.title === 'string' ? data.title.trim() : '';
        } catch {
          // Desktop may be mid-write.
        }
      }
    }
    return null;
  };
  const title = walk(baseDir, 0);
  return title || null;
}

export function sessionContext(payload, lookupTitle = desktopTitle) {
  const cwd = typeof payload?.cwd === 'string' ? payload.cwd : '';
  let title = null;
  try {
    title = lookupTitle(payload?.session_id);
  } catch {
    title = null;
  }
  return { cwd, project: cwd ? path.basename(cwd) : '', title };
}

/* ---------- Cards ---------- */

const clip = (text, max) => {
  const value = String(text ?? '');
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
};
const firstLine = (text) => String(text ?? '').split('\n').map((line) => line.trim()).find(Boolean) ?? '';
const oneLine = (text) => String(text ?? '').replace(/\s+/g, ' ').trim();

function baseSpec(ctx, title, extra = {}) {
  const from = [ctx.project, ctx.title || (ctx.cwd ? '终端会话' : '')].filter(Boolean).join(' · ');
  const facts = [['项目', ctx.project], ['会话', ctx.title || ''], ['目录', ctx.cwd]].filter(([, value]) => value);
  return {
    kind: 'perm',
    title: clip(maskSecrets(title), MAX_TITLE),
    sub: clip(maskSecrets([from, extra.sub].filter(Boolean).join('：')), 600),
    facts: facts.map(([k, v]) => [k, clip(maskSecrets(v), 2000)]),
    code: extra.code ? clip(maskSecrets(extra.code), MAX_CODE) : '',
    priority: 9,
    ...(extra.note_hint && { note_hint: extra.note_hint }),
  };
}

const quoteLines = (text, mark) => String(text ?? '').split('\n').map((line) => `${mark} ${line}`).join('\n');

function editDiff(input) {
  const edits = Array.isArray(input.edits) ? input.edits : [input];
  return edits
    .filter((edit) => edit && (typeof edit.old_string === 'string' || typeof edit.new_string === 'string'))
    .map((edit) => `${quoteLines(edit.old_string, '-')}\n${quoteLines(edit.new_string, '+')}`)
    .join('\n···\n');
}

/** Title, explanatory line and full text for one tool call. */
function describeCall(toolName, input) {
  const file = input.file_path || input.notebook_path || input.path || '';
  if (toolName === 'Bash') {
    return { title: `Bash · ${clip(firstLine(input.command), 140)}`, sub: input.description, code: input.command };
  }
  if (toolName === 'Edit' || toolName === 'MultiEdit') {
    return { title: `改文件 · ${path.basename(file) || file}`, sub: file, code: `${file}\n${editDiff(input)}` };
  }
  if (toolName === 'Write') {
    const lines = String(input.content ?? '').split('\n');
    const head = lines.slice(0, 400).join('\n') + (lines.length > 400 ? `\n…（共 ${lines.length} 行）` : '');
    return { title: `写文件 · ${path.basename(file) || file}`, sub: `${file}（${lines.length} 行）`, code: head };
  }
  if (toolName === 'NotebookEdit') {
    return { title: `改笔记本 · ${path.basename(file)}`, sub: file, code: input.new_source };
  }
  if (toolName === 'WebFetch') return { title: `打开网页 · ${clip(input.url, 140)}`, sub: input.prompt, code: input.url };
  if (toolName === 'WebSearch') return { title: `搜索 · ${clip(input.query, 140)}`, code: input.query };
  const json = JSON.stringify(input, null, 2);
  const mcp = /^mcp__(.+?)__(.+)$/.exec(toolName);
  if (mcp) return { title: `MCP · ${mcp[1]} · ${mcp[2]}`, sub: oneLine(input.description || input.title || ''), code: json };
  return { title: `${toolName} · ${clip(oneLine(input.command || file || input.url || input.query || input.description || ''), 140)}`, code: json };
}

/** Rules that "本会话一直允许" adds: Claude's own suggestions moved to this session, else this exact call. */
function sessionRules(toolName, input, suggestions) {
  const fromClaude = (Array.isArray(suggestions) ? suggestions : [])
    .filter((item) => item && item.type === 'addRules' && Array.isArray(item.rules) && item.behavior === 'allow')
    .map((item) => ({ ...item, destination: 'session' }));
  if (fromClaude.length) return fromClaude;
  const ruleContent = toolName === 'Bash' && typeof input.command === 'string' ? input.command : undefined;
  return [{ type: 'addRules', rules: [ruleContent ? { toolName, ruleContent } : { toolName }], behavior: 'allow', destination: 'session' }];
}

const describeRules = (updates) => updates
  .flatMap((item) => item.rules ?? [])
  .map((rule) => (rule.ruleContent ? `${rule.toolName}(${clip(oneLine(rule.ruleContent), 60)})` : `${rule.toolName} 全部`))
  .join('、');

const denyMessage = (note) => (note
  ? `用户在 decide 决策板上拒绝了这一步，意见：${note}`
  : '用户在 decide 决策板上拒绝了这一步。停下来，等用户的进一步说明。');

/**
 * The cards for one PermissionRequest and how their answers become the hook's
 * decision. `toDecision` gets one answer per card, in order.
 */
export function permissionCards(payload, ctx) {
  const toolName = String(payload.tool_name);
  const input = payload.tool_input && typeof payload.tool_input === 'object' ? payload.tool_input : {};

  if (toolName === 'AskUserQuestion' && Array.isArray(input.questions) && input.questions.length) {
    const questions = input.questions.slice(0, 8);
    const cards = questions.map((question, index) => {
      const options = (Array.isArray(question.options) ? question.options : []).slice(0, 10).map((option, i) => ({
        id: `o${i}`, label: clip(option?.label ?? `选项 ${i + 1}`, 80), desc: clip(option?.description ?? '', 400),
      }));
      const multi = question.multiSelect === true;
      if (!multi) options.push({ id: 'other', label: '都不是，我来写', desc: '写下你的答案，原样交给 Claude', style: 'bad' });
      const count = questions.length > 1 ? `（${index + 1}/${questions.length}）` : '';
      return {
        ...baseSpec(ctx, `提问${count} · ${question.header ? `${question.header}：` : ''}${question.question ?? ''}`, { note_hint: '写下你的答案，会原样交给 Claude' }),
        multi,
        options,
      };
    });
    return {
      cards,
      toDecision(answers) {
        const out = {};
        questions.forEach((question, index) => {
          const answer = answers[index];
          out[question.question] = answer.choice.includes('other') ? (answer.note || '（用户没写）') : answer.labels.join(', ');
        });
        return { behavior: 'allow', answers: out };
      },
    };
  }

  if (toolName === 'ExitPlanMode') {
    const plan = String(input.plan ?? '');
    const head = firstLine(plan).replace(/^#+\s*/, '');
    return {
      cards: [{
        ...baseSpec(ctx, `计划待批准 · ${head}`, { code: plan, note_hint: '哪里要改？Claude 改完会再请你批准' }),
        options: [
          { id: 'auto', label: '批准，自动接受编辑', desc: '照计划开工，改文件不再逐个问', style: 'good' },
          { id: 'manual', label: '批准，改文件逐个确认', desc: '照计划开工，每次改文件还会来问' },
          { id: 'redo', label: '要改', desc: '写哪里要改，Claude 改完再来问', style: 'bad' },
        ],
      }],
      toDecision([answer]) {
        if (answer.choice[0] === 'auto') return { behavior: 'allow', mode: 'acceptEdits' };
        if (answer.choice[0] === 'manual') return { behavior: 'allow', mode: 'default' };
        return {
          behavior: 'deny',
          message: answer.note ? `用户要改计划：${answer.note}` : '用户没批准这个计划。先问清楚要改哪里，再重新提交。',
          interrupt: false,
        };
      },
    };
  }

  const call = describeCall(toolName, input);
  const rules = sessionRules(toolName, input, payload.permission_suggestions);
  return {
    cards: [{
      ...baseSpec(ctx, call.title, { sub: call.sub, code: call.code, note_hint: '为什么拒绝、该怎么做？会转给 Claude（可不写）' }),
      options: [
        { id: 'allow', label: '允许', desc: '只这一次', style: 'good' },
        { id: 'always', label: '本会话一直允许', desc: maskSecrets(`以后不再问：${describeRules(rules)}`).slice(0, 400) },
        { id: 'deny', label: '拒绝', desc: '可附意见，意见会转给 Claude', style: 'bad' },
      ],
    }],
    toDecision([answer]) {
      if (answer.choice[0] === 'allow') return { behavior: 'allow' };
      if (answer.choice[0] === 'always') return { behavior: 'allow', updatedPermissions: rules };
      return { behavior: 'deny', message: denyMessage(answer.note), interrupt: !answer.note };
    },
  };
}

/** The card for a "New session / Do it here" offer, and the PreToolUse output for each answer. */
export function offerCards(payload, ctx) {
  const input = payload.tool_input && typeof payload.tool_input === 'object' ? payload.tool_input : {};
  const title = String(input.title ?? '').trim() || '（没有标题）';
  return {
    cards: [{
      ...baseSpec(ctx, `开新会话？ · ${title}`),
      options: [
        { id: 'new', label: '新会话', desc: 'Claude 写好任务书，马上在新会话里开工', style: 'good' },
        { id: 'here', label: '在这里做', desc: '不开新会话，就在当前会话里做' },
      ],
    }],
    toOutput([answer]) {
      const reason = answer.choice[0] === 'new'
        ? `用户在 decide 决策板上选了「新会话」。不要再用 offer：立刻重新调用 mcp__ccd_session__start_session，initiation 用 "user_asked"，title 用「${title}」，带上完整的 prompt（任务书）和 background。`
        : `用户在 decide 决策板上选了「在这里做」：不开新会话，直接在本会话里做「${title}」。`;
      return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: reason } };
    },
  };
}

/* ---------- Talking to the board ---------- */

const sleep = (ms, signal) => new Promise((resolve) => {
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => {
    clearTimeout(timer);
    resolve();
  }, { once: true });
});

async function call(config, pathname, { method = 'GET', body, timeoutMs = 15_000, signal } = {}) {
  const signals = [AbortSignal.timeout(timeoutMs), signal].filter(Boolean);
  const response = await fetch(`${config.base}${pathname}`, {
    method,
    headers: { 'X-Decide-Key': config.key, ...(body && { 'Content-Type': 'application/json' }) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.any(signals),
  });
  const json = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(json.error || `HTTP ${response.status}`);
  return json;
}

/**
 * Opens `cards` on the board and waits until every one is answered.
 * `result` resolves to their answers in order, or null when one was cancelled,
 * time ran out or the board could not be reached. `cancel()` takes down the
 * cards still pending; the hooks call it once any channel has answered.
 */
export function openCards(config, cards, waitMs) {
  const controller = new AbortController();
  const ids = [];
  let closed = false;

  const result = (async () => {
    const deadline = Date.now() + waitMs;
    const expires = deadline / 1000 + 30;
    for (const spec of cards) {
      if (controller.signal.aborted) return null;
      const created = await call(config, '/api/cards', {
        method: 'POST',
        body: { ...spec, expires, project: DECIDE_PROJECT, project_name: PROJECT_NAME, source: 'claude-hook' },
        signal: controller.signal,
      });
      ids.push(created.id);
    }
    let failures = 0;
    while (!controller.signal.aborted && Date.now() < deadline) {
      try {
        const listed = await call(config, `/api/cards?ids=${ids.join(',')}&limit=${ids.length}`, { signal: controller.signal });
        failures = 0;
        const byId = new Map(listed.cards.map((card) => [card.id, card]));
        const found = ids.map((id) => byId.get(id));
        if (found.some((card) => !card || card.status === 'cancelled')) return null;
        if (found.every((card) => card.status === 'answered' && card.answer)) return found.map((card) => card.answer);
        const left = Math.max(1, Math.min(25, (deadline - Date.now()) / 1000));
        const polled = Date.now();
        await call(config, `/api/poll?since=${listed.ver}&timeout=${Math.round(left)}`, { timeoutMs: (left + 10) * 1000, signal: controller.signal });
        // A board that answers the long poll at once must not turn this into a busy loop.
        if (Date.now() - polled < 250) await sleep(250, controller.signal);
      } catch {
        if (controller.signal.aborted || ++failures >= 6) return null;
        await sleep(Math.min(2000 * failures, 10_000), controller.signal);
      }
    }
    return null;
  })().catch(() => null);

  return {
    result,
    /** Takes the board's copy of these cards down; never throws, gives up after a few seconds. */
    async cancel() {
      if (closed) return;
      closed = true;
      controller.abort();
      await Promise.all(ids.map((id) => call(config, `/api/cards/${id}/cancel`, { method: 'POST', body: {}, timeoutMs: 4000 }).catch(() => {})));
    },
  };
}
