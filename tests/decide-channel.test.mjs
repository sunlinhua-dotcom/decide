import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';

import { maskSecrets, offerCards, openCards, permissionCards } from '../hooks/decide-channel.mjs';

const ctx = { cwd: '/home/me/project', project: 'project', title: 'Fix login' };
const answer = (choice, note = '') => ({ choice: [choice].flat(), labels: [choice].flat(), note });

test('maskSecrets hides tokens, keys and passwords but keeps ordinary ids', () => {
  const masked = maskSecrets([
    "curl -H 'Authorization: Bearer abcDEF1234567890xyz' https://u:hunter22@example.com", // gitleaks:allow (fake secret fixture)
    'export OPENAI_API_KEY=sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX',
    'mysql --password=s3cretpass',
    'echo ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
    'git show 3f1c2a9b8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b',
    'cd /tmp/0c72371f-4796-4a3a-b36d-7bb71bc5fda4/admiring-mendeleev-e4a1d9',
    'key 9fA3kQ7zL1mN8pR2tV6wX0yB4cD5eG7h',
  ].join('\n'));
  for (const secret of ['abcDEF1234567890xyz', 'hunter22', 'ABCDEFGHIJKLMNOPQRSTUVWX', 's3cretpass', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', '9fA3kQ7zL1mN8pR2tV6wX0yB4cD5eG7h']) {
    assert.ok(!masked.includes(secret), `${secret} should be masked`);
  }
  assert.ok(masked.includes('3f1c2a9b8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b'));
  assert.ok(masked.includes('0c72371f-4796-4a3a-b36d-7bb71bc5fda4/admiring-mendeleev-e4a1d9'));
});

test('a Bash request becomes one card whose answers map to allow, session rules and deny', () => {
  const payload = {
    tool_name: 'Bash',
    tool_input: { command: 'git push origin main --token=abcd1234efgh', description: 'Push' }, // gitleaks:allow (fake secret fixture)
    permission_suggestions: [{ type: 'addRules', rules: [{ toolName: 'Bash', ruleContent: 'git push:*' }], behavior: 'allow', destination: 'localSettings' }],
  };
  const { cards, toDecision } = permissionCards(payload, ctx);
  assert.equal(cards.length, 1);
  assert.equal(cards[0].kind, 'perm');
  assert.match(cards[0].title, /^Bash · git push origin main/);
  assert.ok(!cards[0].code.includes('abcd1234efgh'));
  assert.deepEqual(cards[0].options.map((o) => o.id), ['allow', 'always', 'deny']);
  assert.match(cards[0].options[1].desc, /Bash\(git push:\*\)/);

  assert.deepEqual(toDecision([answer('allow')]), { behavior: 'allow' });
  assert.deepEqual(toDecision([answer('always')]).updatedPermissions, [
    { type: 'addRules', rules: [{ toolName: 'Bash', ruleContent: 'git push:*' }], behavior: 'allow', destination: 'session' },
  ]);
  assert.deepEqual(toDecision([answer('deny', '先跑测试')]), { behavior: 'deny', message: '用户在 decide 决策板上拒绝了这一步，意见：先跑测试', interrupt: false });
  assert.equal(toDecision([answer('deny')]).interrupt, true);
});

test('without suggestions, "always" allows this exact command, or the whole tool, for the session', () => {
  const bash = permissionCards({ tool_name: 'Bash', tool_input: { command: 'make test' } }, ctx);
  assert.deepEqual(bash.toDecision([answer('always')]).updatedPermissions[0].rules, [{ toolName: 'Bash', ruleContent: 'make test' }]);
  const mcp = permissionCards({ tool_name: 'mcp__github__create_issue', tool_input: { title: 'x' } }, ctx);
  assert.equal(mcp.cards[0].title, 'MCP · github · create_issue');
  assert.deepEqual(mcp.toDecision([answer('always')]).updatedPermissions[0].rules, [{ toolName: 'mcp__github__create_issue' }]);
});

test('AskUserQuestion gets one card per question and the answers keyed by question text', () => {
  const questions = [
    { question: 'Which host?', options: [{ label: 'host-a' }, { label: 'host-b' }] },
    { question: 'Which parts?', multiSelect: true, options: [{ label: 'A' }, { label: 'B' }] },
  ];
  const { cards, toDecision } = permissionCards({ tool_name: 'AskUserQuestion', tool_input: { questions } }, ctx);
  assert.equal(cards.length, 2);
  assert.equal(cards[0].options.at(-1).id, 'other');
  assert.equal(cards[1].multi, true);
  const decision = toDecision([
    { choice: ['other'], labels: ['都不是，我来写'], note: 'host-a' },
    { choice: ['o0', 'o1'], labels: ['A', 'B'], note: '' },
  ]);
  assert.deepEqual(decision, { behavior: 'allow', answers: { 'Which host?': 'host-a', 'Which parts?': 'A, B' } });
});

test('a plan card approves into a mode or sends the requested changes back', () => {
  const { cards, toDecision } = permissionCards({ tool_name: 'ExitPlanMode', tool_input: { plan: '# Login page\n1. Swap button' } }, ctx);
  assert.equal(cards[0].title, '计划待批准 · Login page');
  assert.deepEqual(toDecision([answer('auto')]), { behavior: 'allow', mode: 'acceptEdits' });
  assert.deepEqual(toDecision([answer('manual')]), { behavior: 'allow', mode: 'default' });
  assert.equal(toDecision([answer('redo', '先改 2')]).message, '用户要改计划：先改 2');
});

test('a new-session offer turns into a PreToolUse denial that says what the user chose', () => {
  const { toOutput } = offerCards({ tool_input: { initiation: 'offer', title: 'Fix login' } }, ctx);
  assert.match(toOutput([answer('new')]).hookSpecificOutput.permissionDecisionReason, /user_asked/);
  assert.match(toOutput([answer('here')]).hookSpecificOutput.permissionDecisionReason, /在这里做/);
});

/** A board that answers every card it is given once `answerAfterMs` has passed. */
function fakeBoard(answerAfterMs) {
  const cards = new Map();
  let ver = 1;
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      const url = new URL(req.url ?? '/', 'http://x');
      const send = (data) => res.end(JSON.stringify(data));
      if (req.method === 'POST' && url.pathname === '/api/cards') {
        const id = `c${cards.size + 1}`;
        cards.set(id, { status: 'pending', answer: null });
        if (answerAfterMs !== null) {
          setTimeout(() => {
            cards.set(id, { status: 'answered', answer: { choice: ['allow'], labels: ['允许'], note: '' } });
            ver += 1;
          }, answerAfterMs);
        }
        return send({ id });
      }
      const cancel = /^\/api\/cards\/(\w+)\/cancel$/.exec(url.pathname);
      if (cancel) {
        const card = cards.get(cancel[1]);
        if (card?.status === 'pending') card.status = 'cancelled';
        return send({});
      }
      if (url.pathname === '/api/cards') {
        const ids = (url.searchParams.get('ids') ?? '').split(',');
        return send({ ver, cards: ids.map((id) => ({ id, ...cards.get(id) })) });
      }
      if (url.pathname === '/api/poll') return setTimeout(() => send({ ver }), 100);
      res.statusCode = 404;
      send({});
    });
  });
  return { server, cards };
}

test('openCards returns the answers once every card is answered', async () => {
  const { server } = fakeBoard(200);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const opened = openCards({ base: `http://127.0.0.1:${port}`, key: 'k' }, [{ title: 'a' }], 10_000);
  const answers = await opened.result;
  assert.deepEqual(answers, [{ choice: ['allow'], labels: ['允许'], note: '' }]);
  await opened.cancel();
  server.close();
});

test('cancel() takes pending cards down and the result settles without an answer', async () => {
  const { server, cards } = fakeBoard(null);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const opened = openCards({ base: `http://127.0.0.1:${port}`, key: 'k' }, [{ title: 'a' }], 10_000);
  await new Promise((resolve) => setTimeout(resolve, 300));
  await opened.cancel();
  assert.equal(await opened.result, null);
  assert.equal(cards.get('c1')?.status, 'cancelled');
  server.close();
});

test('an unreachable board settles without an answer', async () => {
  const opened = openCards({ base: 'http://127.0.0.1:1', key: 'k' }, [{ title: 'a' }], 5_000);
  assert.equal(await opened.result, null);
});
