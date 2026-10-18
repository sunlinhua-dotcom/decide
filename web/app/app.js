'use strict';
/* decide 前端：一次一张、两个按钮、点完自动下一张。答案经后台监听交回 Claude 会话。 */

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') el.style.cssText = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const kids = (...k) => k.flat(Infinity).filter(x => x != null && x !== false);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const pad = n => String(n || 0).padStart(2, '0');
const hover = matchMedia('(hover: hover)').matches;

if (/^#k=/.test(location.hash)) history.replaceState(null, '', location.pathname + location.search);

const S = { ver: 0, skew: 0, loaded: false, projects: [], cards: [], byId: new Map(), cur: {}, multi: new Map(), activeQ: 0, last: null };
const proj = slug => S.projects.find(p => p.slug === slug);
const pname = slug => proj(slug)?.name || slug;

function device() {
  const ua = navigator.userAgent;
  if (/iPad|Macintosh/.test(ua) && navigator.maxTouchPoints > 1) return 'iPad';
  if (/iPhone/.test(ua)) return 'iPhone';
  if (/Android/.test(ua)) return 'Android';
  if (/Macintosh/.test(ua)) return 'Mac';
  if (/Windows/.test(ua)) return 'Windows';
  return '浏览器';
}
function ago(ts) {
  const d = Math.max(0, Date.now() / 1000 - S.skew - ts);
  if (d < 45) return '刚刚';
  if (d < 3600) return Math.round(d / 60) + ' 分钟前';
  if (d < 86400) return Math.round(d / 3600) + ' 小时前';
  const t = new Date(ts * 1000);
  return `${t.getMonth() + 1} 月 ${t.getDate()} 日`;
}

/* ---------- 接口与实时 ---------- */
async function api(path, opts = {}) {
  const init = { credentials: 'same-origin', ...opts };
  if (opts.body && typeof opts.body !== 'string') { init.body = JSON.stringify(opts.body); init.headers = { 'Content-Type': 'application/json' }; }
  const r = await fetch(path, init);
  if (r.status === 401) { location.reload(); throw new Error('未登录'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('出错了（' + r.status + '）'));
  return j;
}
async function loadState() {
  const j = await api('/api/state');
  S.ver = j.ver; S.skew = Date.now() / 1000 - j.now;
  S.projects = j.projects; S.cards = j.cards; S.byId = new Map(j.cards.map(c => [c.id, c]));
  const fresh = S.seen ? j.cards.filter(c => c.status === 'pending' && !S.seen.has(c.id)) : [];
  S.seen = new Set(j.cards.filter(c => c.status === 'pending').map(c => c.id));
  if (fresh.length) startFlash();
  S.loaded = true;
  render();
}
function setConn(s) { const el = $('#conn'); el.className = 'conn ' + s; }
async function pollLoop() {
  let fails = 0;
  for (;;) {
    try {
      const j = await api('/api/poll?since=' + S.ver + '&timeout=25');
      fails = 0; setConn('live');
      if (j.ver !== S.ver) await loadState();
    } catch {
      fails++; setConn(fails > 3 ? 'down' : 'retry');
      await sleep(Math.min(1000 * 2 ** fails, 15000));
      if (fails > 1) loadState().catch(() => {});
    }
  }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) { stopFlash(); loadState().catch(() => {}); } });
/* 手机锁屏 / 切后台回来：iOS 可能不发 visibilitychange（页面从缓存恢复），长轮询也可能挂在死连接上，再补两道 */
window.addEventListener('pageshow', e => { if (e.persisted) loadState().catch(() => {}); });
window.addEventListener('online', () => loadState().catch(() => {}));

let baseTitle = 'decide', flashTimer = null;
function startFlash() {
  if (flashTimer || !document.hidden) return;
  let on = false;
  flashTimer = setInterval(() => { on = !on; document.title = on ? '● 新卡片' : baseTitle; }, 1000);
}
function stopFlash() { if (flashTimer) { clearInterval(flashTimer); flashTimer = null; document.title = baseTitle; } }

/* 页面开着期间部署了新版本：自动刷新到新版（正在写意见时不刷，写完再刷） */
const BUILD = (document.currentScript?.src.match(/[?&]v=(\d+)/) || [])[1] || '';
let staleSince = 0;
async function checkBuild() {
  if (!BUILD) return;
  try {
    const html = await (await fetch('/', { cache: 'no-store', credentials: 'same-origin' })).text();
    const v = (html.match(/app\.js\?v=(\d+)/) || [])[1];
    if (!v || v === BUILD) return;
    if (S.note || $('#lb').classList.contains('on')) { staleSince = staleSince || Date.now(); return; }
    location.reload();
  } catch {}
}
setInterval(checkBuild, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkBuild(); });

/* ---------- 路由 ---------- */
function parseRoute() {
  const p = location.pathname.replace(/\/+$/, '') || '/';
  const q = new URLSearchParams(location.search);
  if (p === '/grid') return { type: 'grid', slugs: (q.get('p') || '').split(',').filter(Boolean).slice(0, 4) };
  if (p === '/done') return { type: 'done' };
  const m = p.match(/^\/p\/([a-z0-9-]+)$/);
  return { type: 'focus', slug: m ? m[1] : null };
}
function go(url) { history.pushState(null, '', url); render(); window.scrollTo(0, 0); }
window.addEventListener('popstate', render);
document.addEventListener('click', e => {
  const a = e.target.closest('a[data-nav]');
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.target === '_blank') return;
  e.preventDefault(); go(a.getAttribute('href'));
});

function queue(slug) {
  return S.cards.filter(c => c.status === 'pending' && (slug ? c.project === slug : !proj(c.project)?.archived))
    .sort((a, b) => (b.priority - a.priority) || (a.created - b.created));
}

function render() {
  const r = S.route = parseRoute();
  renderBar(r);
  const main = $('#main');
  if (!S.loaded) return;
  const key = r.type + ':' + (r.slug || (r.slugs || []).join(','));
  if (main.dataset.view !== key) { main.replaceChildren(); main.dataset.view = key; main._ui = null; main._q = null; }
  if (r.type === 'focus') {
    const k = 'f:' + (r.slug || '');
    if (!main._ctx || main._ctx.key !== k) main._ctx = { slug: r.slug, key: k };
    renderFocus(main, main._ctx, {});
  }
  else if (r.type === 'grid') renderGrid(main, r.slugs);
  else renderDone(main);
  const n = r.type === 'focus' ? queue(r.slug).length : r.type === 'grid' ? r.slugs.reduce((s, x) => s + queue(x).length, 0) : queue(null).length;
  baseTitle = (n ? `(${n}) ` : '') + (r.type === 'focus' && r.slug ? pname(r.slug) + ' · ' : '') + 'decide';
  if (!flashTimer) document.title = baseTitle;
}

function renderBar(r) {
  const total = queue(null).length;
  const ps = S.projects.filter(p => !p.archived).sort((a, b) => (b.pending - a.pending) || (b.last - a.last));
  const tab = (href, label, n, on) => h('a', { class: 'tab' + (on ? ' on' : '') + (n ? '' : ' zero'), href, 'data-nav': true }, label, h('span', { class: 'n' }, n));
  $('#tabs').replaceChildren(...kids(
    tab('/', '全部', total, r.type === 'focus' && !r.slug),
    ps.map(p => tab('/p/' + p.slug, p.name, queue(p.slug).length, r.type === 'focus' && r.slug === p.slug))));
  const left = r.type === 'focus' ? queue(r.slug).length : 0;
  $('#left').replaceChildren(...(r.type === 'focus' && left ? ['还剩', h('span', { class: 'n' }, left)] : []));
  $$('.right a[data-k]').forEach(a => a.classList.toggle('on', a.dataset.k === r.type));
}

/* ---------- 一次一张 ---------- */
function curCard(ctx) {
  const q = queue(ctx.slug);
  let id = S.cur[ctx.key];
  if (!q.some(c => c.id === id)) {
    const i = Math.min(Math.max(ctx.idx ?? 0, 0), q.length - 1); // 空队列时 idx 是 -1，新卡来了要从 0 取，否则停在「都定完了」
    id = i >= 0 ? q[i].id : null;
    S.cur[ctx.key] = id;
  }
  ctx.idx = q.findIndex(c => c.id === id);
  return { q, c: id ? S.byId.get(id) : null };
}
function renderFocus(host, ctx, o) {
  let ui = host._ui;
  if (!ui) { ui = host._ui = { root: h('section', { class: 'focus' }), id: undefined, rev: null }; host.append(ui.root); }
  const { q, c } = curCard(ctx);
  if (!c) {
    if (ui.id !== null) { ui.root.replaceChildren(emptyEl(ctx.slug)); ui.id = null; }
    return;
  }
  if (ui.id !== c.id || ui.rev !== c.rev) {
    ui.root.classList.toggle('textq', tileMode(c));
    ui.root.replaceChildren(...kids(stageEl(c, ctx, o), infoEl(c, ctx, o), factsEl(c, o), o.compact ? null : railEl(q, ctx)));
    ui.id = c.id; ui.rev = c.rev;
  } else if (!o.compact) {
    const old = $('.rail', ui.root), nr = railEl(q, ctx);
    old ? old.replaceWith(nr) : ui.root.append(nr);
  }
}
function emptyEl(slug) {
  return h('div', { class: 'empty' }, h('h2', null, '都定完了'),
    h('p', null, (slug ? pname(slug) + '没有待定的卡片。' : '') + '新卡片会自己出现，页面开着就行。'));
}
/* 几张图就并排几张：试每种列数，选让图片总面积最大的那种 */
function bestCols(ars, W, H, gap, cap) {
  const n = ars.length; let best = 1, bestA = -1;
  for (let c = 1; c <= n; c++) {
    const r = Math.ceil(n / c), cw = (W - gap * (c - 1)) / c, ch = (H - gap * (r - 1)) / r - cap;
    if (cw <= 0 || ch <= 0) continue;
    let a = 0;
    for (const ar of ars) { const w = Math.min(cw, ch * ar); a += w * w / ar; }
    if (a > bestA * 1.02) { bestA = a; best = c; }
  }
  return best;
}
function gridFit(el, media, opt) {
  const ars = media.map(m => m.w && m.h ? m.w / m.h : 16 / 9);
  const fit = () => {
    const W = el.clientWidth; if (!W) return;
    const o = opt(), H = o.h || el.clientHeight; if (!H) return;
    const cols = bestCols(ars, W, H, o.gap, o.cap);
    el.style.setProperty('--cols', cols);
    el.style.setProperty('--rows', Math.ceil(media.length / cols));
    el.classList.add('multi');
  };
  new ResizeObserver(fit).observe(el);
  queueMicrotask(fit); // 后台标签页里 ResizeObserver 可能不触发，挂上 DOM 后先算一次
}
function isBroken(label) { return /崩|错|废|旧版|上一版/.test(label || ''); }

/* 文字选择题：选项不带图，就把每个选项画成一块大卡片（示意图有的话放在上面） */
/* 授权卡：本机 Claude 的钩子正等着这张卡，答了 Claude 就继续，所以不能撤回、不能「在对话里说」 */
const isPerm = c => c.kind === 'perm';
/* 不属于任何选项的图 = 整题的示意图；属于选项的图放进那个选项卡 */
const figIdx = c => { const used = new Set((c.options || []).map(x => x.media).filter(m => m != null)); return (c.media || []).map((_, i) => i).filter(i => !used.has(i) && c.media[i].role !== 'src'); };
/* 既有整题示意图、又有选项自带的图（392：两张方案图 + 三张选项小图）：示意图放上面，选项图进各自的卡，不再五张混排 */
const mixed = c => (c.options || []).some(x => x.media != null) && figIdx(c).length > 0;
function tileMode(c) {
  const opts = c.options || [];
  if (isPerm(c) && opts.every(x => x.media == null)) return true;
  if (mixed(c)) return true;
  return opts.length >= 2 && opts.every(x => x.media == null) && (!figIdx(c).length || Boolean(c.plan) || opts.some(x => x.desc));
}
/* 每个普通选项都能「选它 + 写哪里要改」；通过 / 返修这类本身带意见框的、多选卡不加 */
const canRevise = (c, x) => !isPerm(c) && !c.multi && c.status === 'pending' && x.style !== 'bad' && x.style !== 'good';
function reviseBtn(c, x, ctx, cls) {
  return h('button', { class: cls || 'rv', type: 'button', title: `选「${x.label}」并写哪里要改`, onclick: e => { e.stopPropagation(); revise(c, x.id, ctx); } }, '要改…');
}
function tilesEl(c, ctx) {
  const opts = c.options || [], sel = S.multi.get(c.id) || new Set();
  const noting = S.note && S.note.id === c.id ? S.note.oid : null;
  // 带图的选项排一行（最多 3 列），没图的（如「要改」）单独占一整行、排在下面，不和图卡抢格子
  const pics = hasPics(c), pn = Math.min(3, opts.filter(x => optPic(c, x) || x.gen).length) || 1;
  // 纯文字题：「要改」这类兜底项压成底下一条，正经选项按条数排满，不留空格子（401：4 句 + 要改 曾排成 3+2）
  const slim = x => !pics && x.style === 'bad' && opts.length >= 3;
  const main = opts.filter(x => !slim(x)), m = main.length;
  const long = main.reduce((a, x) => a + (x.label || '').length, 0) / Math.max(1, m) > 14;
  const cols = m <= 3 ? m : m === 4 ? (long ? 2 : 4) : m <= 6 ? 3 : 4;
  return h('div', { class: 'tiles', style: `--n:${cols};--pn:${pn}`, role: 'group', 'aria-label': '选项' }, opts.map((x, i) => {
    const rv = canRevise(c, x);
    return h('div', { class: 'tilew' + (rv ? ' has-rv' : '') + (pics && !(optPic(c, x) || x.gen) ? ' nopic' : '') + (slim(x) ? ' slim' : '') },
      h('button', { class: 'tile' + (x.style === 'good' ? ' go' : x.style === 'bad' ? ' no' : '') + (x.rec ? ' rec' : '') + (sel.has(x.id) || noting === x.id ? ' sel' : ''),
        type: 'button', 'data-oid': x.id, title: i < 9 ? '按 ' + (i + 1) + (rv ? '；Shift + ' + (i + 1) + ' 选它并写意见' : '') : '', onclick: () => choose(c, x.id, ctx) },
        optPic(c, x) || x.gen ? picEl(c, x, ctx) : null,
        h('span', { class: 'tile-k' }, c.multi ? h('span', { class: 'tile-ck', 'aria-hidden': 'true' }) : null, i < 9 ? String(i + 1) : ''),
        x.rec ? h('span', { class: 'tile-tag' }, '推荐') : null,
        h('span', { class: 'tile-t' }, x.label),
        x.desc ? h('span', { class: 'tile-d' }, x.desc) : null),
      rv ? reviseBtn(c, x, ctx) : null);
  }), c.multi && c.status === 'pending' ? multiBar(c, ctx) : null);
}
/* 多选：选项下面一条，写清可多选、已选几项，确定 / 确定并写意见都在手边（407 的确定按钮藏在标题旁） */
function multiBar(c, ctx) {
  const sel = S.multi.get(c.id) || new Set(), n = sel.size;
  return h('div', { class: 'mbar' },
    h('span', { class: 'mh' }, n ? `已选 ${n} 项` : '可多选：点卡片或按数字勾选', h('small', null, n ? '　回车确定' : '')),
    h('button', { class: 'tx', type: 'button', disabled: !n, onclick: () => multiNote(c, ctx) }, '确定并写意见'),
    h('button', { class: 'b go', type: 'button', disabled: !n, onclick: () => submit(c, [...sel], ctx) }, n ? `确定（${n} 项）` : '确定'));
}
/* 选项配图：后台千问出图，出来之前画占位 */
function optPic(c, x) { return x.pic || (x.media != null ? (c.media || [])[x.media] : null); }
function picEl(c, x, ctx) {
  const p = optPic(c, x);
  if (!p) return h('span', { class: 'tile-pic wait', 'aria-hidden': 'true' }, h('span', null, '配图生成中…'));
  const own = x.media != null && p === (c.media || [])[x.media];
  const ar = p.w && p.h ? `aspect-ratio:${p.w} / ${p.h}` : null;
  // 选项自带的图点一下是放大看，不是选它（选它点卡片其余地方或按数字键）
  const zoom = own ? e => { e.stopPropagation(); openLb(c, x.media, ctx); } : null;
  return h('span', { class: 'tile-pic' + (/\.svg$/i.test(p.src) ? ' fit' : '') + (own ? ' zoomable' : ''), style: ar, title: own ? '点图放大' : null, onclick: zoom },
    h('img', { src: p.thumb && !/\.svg$/i.test(p.src) ? p.thumb : p.src, alt: '', loading: 'lazy', decoding: 'async' }));
}
const hasPics = c => (c.options || []).some(x => x.pic || x.gen || x.media != null);
function stageEl(c, ctx, o) {
  if (!tileMode(c)) return mediaStage(c, ctx, o);
  const has = figIdx(c).length > 0 || Boolean(c.code) || Boolean(c.plan);
  // 有示意图、选项不多：宽屏上选项卡竖排在图右边，图拿满整个高度（10-17 用户嫌 126 的图太小）
  // 选项带图也进右栏：放不下时 sideFit 会加宽、排两列，再不行才挪到图下方（392 方案图被压到 112 px）
  const side = has && (c.options || []).length <= 5;
  const fig = c.plan ? planEl(c, ctx) : figIdx(c).length ? mediaStage(c, ctx, o, figIdx(c)) : c.code ? codeEl(c) : null;
  const q = h('div', { class: 'qstage' + (has ? ' has-fig' : '') + (side ? ' side' : '') + (hasPics(c) ? ' pics' : '') + (c.plan ? ' planq' : '') }, fig, tilesEl(c, ctx));
  // 方案没有固定宽高比（ars 为空）：求解器取放得下全部选项的最窄右栏，剩下的都给方案文字
  if (side) sideFit(q, c.plan ? [] : figIdx(c).map(i => { const m = c.media[i]; return m.w && m.h ? m.w / m.h : 16 / 9; }));
  return q;
}
/* 一组图在 W×H 里按最佳列数排，能画出的总面积（像素²） */
function gridArea(ars, W, H, gap, cap) {
  let best = 0;
  for (let c = 1; c <= ars.length; c++) {
    const r = Math.ceil(ars.length / c), cw = (W - gap * (c - 1)) / c, ch = (H - gap * (r - 1)) / r - cap;
    if (cw <= 0 || ch <= 0) continue;
    let a = 0;
    for (const ar of ars) { const w = Math.min(cw, ch * ar); a += w * w / ar; }
    best = Math.max(best, a);
  }
  return best;
}
/* 按示意图和选项图的真实宽高比精算排法（10-18 用户：画面浪费太多，要按图的比例动态算）：
   右栏宽度 × 选项列数 × 选项图上限高度逐个试，再试「选项放下面」，
   挑「示意图实际画出的面积 + 选项图实际画出的面积」最大、且选项全放得下的那一种。 */
function sideFit(q, ars) {
  const wideQ = matchMedia('(min-width: 1100px)');
  const pics = q.classList.contains('pics');
  const tilesOf = () => q.querySelector('.tiles');
  const over = () => { const t = tilesOf(); return t && t.scrollHeight > t.clientHeight + 2; };
  const picArea = () => {
    let a = 0, minH = Infinity;
    for (const sp of q.querySelectorAll('.tile-pic')) {
      const b = sp.getBoundingClientRect(), img = sp.querySelector('img');
      const ar = img && img.naturalWidth ? img.naturalWidth / img.naturalHeight : b.width / Math.max(1, b.height);
      const w = Math.min(b.width, b.height * ar); a += w * w / ar; minH = Math.min(minH, w / ar);
    }
    return { a, minH };
  };
  const set = (k, v) => v == null ? q.style.removeProperty(k) : q.style.setProperty(k, v);
  const apply = o => {
    q.classList.toggle('side', o.side); q.classList.toggle('below', !o.side);
    set('--sidew', o.side ? o.S + 'px' : null); set('--tcols', o.tc || null); set('--picmax', o.pm ? o.pm + 'vh' : null);
  };
  const fit = () => {
    if (!q.isConnected || !q.clientHeight) return;
    if (!wideQ.matches) { apply({ side: false }); q.classList.remove('below'); return; }  // 手机照旧上下排
    const W = q.clientWidth, H = q.clientHeight, GAP = 22, CAP = 30;
    const pn = Number(getComputedStyle(tilesOf()).getPropertyValue('--pn')) || 1;
    const tcs = pics ? [...new Set([pn, Math.min(2, pn), 1])] : [1, 2];
    const pms = pics ? [56, 46, 38, 31, 25, 20, 16] : [0];
    const PICMIN = Math.max(130, H * 0.2);
    let best = null, loose = null;
    const consider = (o, figW, figH) => {
      // 示意图满是小字，面积优先给它；选项图只要认得出：每张至少 PICMIN 高，够了再按半权重往大里加
      const f = gridArea(ars, figW, figH, 16, CAP), p = pics ? picArea() : { a: 0, minH: Infinity };
      const a = f + 0.5 * p.a;
      if (p.minH < PICMIN) { if (!loose || a > loose.a * 1.01) loose = { ...o, a }; return; }  // 屏太小谁都够不着保底，就退回面积最大的
      if (!best || a > best.a * 1.01) best = { ...o, a };
    };
    for (const frac of [0.2, 0.24, 0.28, 0.33, 0.38, 0.44, 0.5, 0.56]) {
      const S = Math.round(W * frac);
      for (const tc of tcs) for (const pm of pms) {
        if ((S - 10 * (tc - 1)) / tc < (pics ? 180 : 240)) continue;  // 每列选项太窄字会一字一行
        const o = { side: true, S, tc, pm };
        apply(o);
        if (!over()) { consider(o, W - S - GAP, H); if (!pics) break; }
      }
    }
    for (const pm of pms) {  // 选项放在示意图下面：示意图拿剩下的高度
      const o = { side: false, pm };
      apply(o);
      const T = tilesOf().getBoundingClientRect().height;
      if (H - T - 18 > H * 0.3) consider(o, W, H - T - 18);
    }
    apply(best || loose || { side: false });
  };
  let w = 0, hh = 0;
  new ResizeObserver(() => { if (q.clientWidth === w && q.clientHeight === hh) return; w = q.clientWidth; hh = q.clientHeight; fit(); }).observe(q);
  queueMicrotask(fit);
  for (const img of q.querySelectorAll('.tile-pic img')) img.addEventListener('load', () => { w = 0; fit(); }, { once: true });
}
/* 完整命令 / 改动摘要 / 计划全文：等宽显示，长的先折叠 */
/* 字多的方案图拆出来的方案：网页原生文字排（字号清楚），每节配一张千问示意图；原图在「看原图」里 */
function planEl(c, ctx) {
  const p = c.plan, src = (c.media || []).findIndex(m => m.role === 'src');
  const pic = x => x.pic ? h('div', { class: 'plan-pic' }, h('img', { src: x.pic.thumb || x.pic.src, alt: '', loading: 'lazy' }))
    : x.gen ? h('div', { class: 'plan-pic wait' }, '配图生成中…') : null;
  return h('div', { class: 'plan' },
    p.title || p.sub || src >= 0 ? h('div', { class: 'plan-h' },
      h('div', null, p.title ? h('h2', null, p.title) : null, p.sub ? h('p', null, p.sub) : null),
      src >= 0 ? h('button', { class: 'plan-src', type: 'button', onclick: () => openLb(c, src, ctx) }, '看原图') : null) : null,
    (p.lines || []).length ? h('ul', { class: 'plan-l' }, p.lines.map(x => h('li', null, x))) : null,
    (p.secs || []).length ? h('div', { class: 'plan-g' + (p.secs.length === 1 ? ' one' : '') }, p.secs.map((x, i) =>
      h('section', { class: 'plan-s' + (x.pic || x.gen ? ' has-pic' : ''), style: x.color ? `--c:${x.color}` : null },
        pic(x),
        h('div', { class: 'plan-b' }, h('h3', null, x.t), x.lines.map(y => h('p', null, y)))))) : null,
    (p.foot || []).length ? h('div', { class: 'plan-f' }, p.foot.map(x => h('p', null, x))) : null);
}
function codeEl(c) {
  const lines = c.code.split('\n').length;
  const long = lines > 16 || c.code.length > 1400;
  const pre = h('pre', { class: 'code' + (long ? ' fold' : '') }, c.code);
  if (!long) return h('div', { class: 'codew' }, pre);
  const label = `展开全部（${lines} 行）`;
  const btn = h('button', { class: 'more', type: 'button', onclick: () => { btn.textContent = pre.classList.toggle('fold') ? label : '收起'; } }, label);
  return h('div', { class: 'codew' }, pre, btn);
}
function mediaStage(c, ctx, o, only) {
  const all = c.media || [], keep = only || all.map((_, i) => i).filter(i => all[i].role !== 'src'), media = keep.map(i => all[i]);
  const optOf = new Map((c.options || []).filter(x => x.media != null).map(x => [x.media, x]));
  const st = h('div', { class: 'stage' });
  // 选项名是一整句（429「A · 你的 AI 不慢，慢的是你」）时说明条排两行，算图高要多留一行
  const longCap = keep.some(i => { const o = optOf.get(i); return o && o.label.length > 3; });
  if (media.length > 1) gridFit(st, media, () => innerWidth <= 760 ? { gap: 8, cap: longCap ? 50 : 22, h: innerHeight * 0.62 } : { gap: 16, cap: longCap ? 62 : 30 });
  if (!media.length) {
    st.append(h('div', { class: 'noimg' }, '这张没有图，直接在下面选'));
    return st;
  }
  keep.forEach(i => {
    const m = all[i], opt = optOf.get(i);
    const ar = m.w && m.h ? `--ar:${m.w} / ${m.h}` : null;
    const frame = h('div', { class: 'frame', style: ar });
    if (m.kind === 'video') {
      const v = h('video', { src: m.src + '#t=0.1', controls: true, playsinline: true, 'webkit-playsinline': true, preload: 'metadata', poster: m.poster || null });
      v.addEventListener('loadedmetadata', () => { if (!ar && v.videoWidth) frame.style.setProperty('--ar', `${v.videoWidth} / ${v.videoHeight}`); });
      frame.append(v);
    } else {
      const img = h('img', { src: m.src, alt: m.label || '', decoding: 'async' });
      img.addEventListener('load', () => { if (!ar && img.naturalWidth) frame.style.setProperty('--ar', `${img.naturalWidth} / ${img.naturalHeight}`); });
      frame.append(img);
      attachLoupe(img, m);
    }
    frame.addEventListener('click', e => {
      if (e.target.tagName === 'VIDEO') return;
      openLb(c, i, ctx);
    });
    const cap = h('div', { class: 'cap' },
      opt ? h('span', { class: 'lt' + (opt.label.length > 3 ? ' long' : '') }, opt.label) : (isBroken(m.label) ? h('i', { class: 'dot' }) : null),
      opt && opt.rec ? h('span', { class: 'rec' }, '推荐') : null,
      h('span', null, opt ? (m.caption || '') : [m.label, m.caption ? ' · ' + m.caption : '']),
      m.kind === 'video' ? h('button', { class: 'zoom', type: 'button', onclick: () => openLb(c, i, ctx) }, '放大') : null,
      opt && canRevise(c, opt) ? reviseBtn(c, opt, ctx, 'rv cap-rv') : null);
    st.append(h('figure', { class: 'fig' + (opt ? ' pick' : '') }, frame, cap));
  });
  let x0 = null, y0 = null;
  st.addEventListener('touchstart', e => { if (e.touches.length === 1) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; } }, { passive: true });
  st.addEventListener('touchend', e => {
    if (x0 == null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; x0 = null;
    if (Math.abs(dx) > 70 && Math.abs(dy) < 50) step(ctx, dx < 0 ? 1 : -1);
  }, { passive: true });
  return st;
}

function infoEl(c, ctx, o) {
  return h('div', { class: 'info' },
    h('div', { class: 'seq', title: '在 Claude 里说「' + pad(c.seq) + ' 返修 …」就能指这一张' }, pad(c.seq)),
    h('div', { class: 'tt' },
      o.compact ? null : h('span', { class: 'proj' }, pname(c.project)),
      // 长标题在手机上字号小一档：三位数编号挤窄了标题列，40 字的标题会折成四行（441）
      h('h1', { class: [...(c.title || '')].length > 30 ? 'long' : null }, c.title),
      c.sub ? h('p', null, c.sub) : null,
      o.compact ? null : linksEl(c)),
    actsEl(c, ctx));
}

// 完成卡里做完的站点、版本页：图里写的网址点不了，单独排一行可点的链接（450）
function linksEl(c) {
  const ls = c.links || [];
  if (!ls.length) return null;
  return h('div', { class: 'links' }, ls.map(l => {
    let name = l.t;
    if (!name) try { const u = new URL(l.url); name = (u.host + u.pathname).replace(/\/$/, ''); } catch { name = l.url; }
    return h('a', { href: l.url, target: '_blank', rel: 'noopener noreferrer', title: l.url, onclick: e => e.stopPropagation() }, h('span', { class: 'lk-i', 'aria-hidden': 'true' }, '↗'), h('span', { class: 'lk-t' }, name));
  }));
}

function actsEl(c, ctx) {
  if (S.note && S.note.id === c.id && c.status === 'pending') return noteEl(c, ctx);
  const opts = c.options || [];
  const sel = S.multi.get(c.id) || new Set();
  const box = h('div', { class: 'acts' });
  if (!tileMode(c)) opts.forEach((x, i) => {
    const cls = 'b' + (x.media != null && x.label.length <= 2 ? ' lt' : '') + (x.style === 'good' ? ' go' : x.style === 'bad' ? ' no' : '') + (sel.has(x.id) ? ' sel' : '');
    // 「A · 一整句」这种选项名：手机按钮只显示 A，全名在图下面（429）
    const pre = (x.label.match(/^\s*([A-Za-z0-9]{1,2}|[①-⑩])\s*[·.、:：]\s*\S/) || [])[1];
    box.append(h('button', { class: cls, type: 'button', title: (x.desc ? x.desc + ' · ' : '') + '按 ' + (i + 1), 'data-oid': x.id, onclick: () => choose(c, x.id, ctx) },
      pre ? [h('span', { class: 'bl-full' }, x.label), h('span', { class: 'bl-short' }, pre)] : x.label));
  });
  if (c.multi && !tileMode(c)) box.append(h('button', { class: 'b go', type: 'button', disabled: !sel.size, onclick: () => submit(c, [...sel], ctx) }, sel.size ? `确定（${sel.size} 项）` : '确定'));
  // 手机上选项一长串，确定条在最底下够不着：底部常驻栏里也放一个
  if (c.multi && tileMode(c)) box.append(h('button', { class: 'b go mgo', type: 'button', disabled: !sel.size, onclick: () => submit(c, [...sel], ctx) }, sel.size ? `确定（${sel.size} 项）` : '先勾选'));
  if (isPerm(c)) return box;
  const grp = chatGroup(c);
  // 两个文字链接竖着叠成一小列：按钮多的卡（429：A/B/C + 要改）横排会把标题挤成一列一个字
  box.prepend(h('span', { class: 'chats' },
    h('button', { class: 'chat', type: 'button', title: '意见在 Claude 对话里打，这些卡从这里清掉（按 C）', onclick: () => toChat(c, ctx) },
      grp.length > 1 ? `这 ${grp.length} 张在对话里说` : '在对话里说'),
    h('button', { class: 'chat', type: 'button', title: '不选现成选项，直接在这里写全部意见（按 W）', onclick: () => freeNote(c, ctx) }, '在这里写意见')));
  return box;
}

const FACT_NAME = { '画面产品': '产品', '上次崩因': '上次', '这次改法': '这次' };
function factsEl(c, o) {
  const fs = (c.facts || []).filter(([k]) => !/^(文件|路径)/.test(k));
  const rv = (c.options || []).some(x => canRevise(c, x));
  const keys = hover && !o.compact ? h('span', { class: 'keys' }, (c.options || []).slice(0, 4).map((x, i) => `${i + 1} ${x.label}`).join('　') + (rv ? '　Shift + 数字 选它并写意见' : '') + (c.multi ? '　回车确定' : '') + (isPerm(c) ? '　← → 翻页' : '　W 在这里写意见　C 在对话里说　← → 翻页　Z 撤回')) : null;
  if (!fs.length && !keys) return null;
  return h('div', { class: 'facts' }, fs.map(([k, v]) => h('span', null, h('b', null, FACT_NAME[k] || k), v)), keys);
}

function railEl(q, ctx) {
  const id = S.cur[ctx.key];
  return h('nav', { class: 'rail', 'aria-label': '待定队列' }, q.map(c => {
    const m = (c.media || []).find(x => x.kind === 'img');
    return h('button', { class: c.id === id ? 'on' : '', type: 'button', title: pad(c.seq) + ' ' + c.title, onclick: () => { S.cur[ctx.key] = c.id; render(); } },
      m ? h('img', { src: m.thumb || m.src, alt: '', loading: 'lazy' }) : h('span', { class: 'ph' }, pad(c.seq)));
  }));
}

function step(ctx, d) {
  const q = queue(ctx.slug);
  if (q.length < 2) return;
  const i = q.findIndex(c => c.id === S.cur[ctx.key]);
  S.cur[ctx.key] = q[(i + d + q.length) % q.length].id;
  render();
}

/* ---------- 判断 ---------- */
function choose(c, oid, ctx) {
  if (c.status !== 'pending') return;
  const x = (c.options || []).find(o => o.id === oid);
  if (!c.multi && x && x.style === 'bad') return revise(c, oid, ctx);
  if (c.multi) {
    const sel = S.multi.get(c.id) || new Set();
    sel.has(oid) ? sel.delete(oid) : sel.add(oid);
    S.multi.set(c.id, sel);
    refreshActs(c, ctx);
    return;
  }
  submit(c, [oid], ctx);
}
function revise(c, oid, ctx) {
  if (c.status !== 'pending') return;
  S.note = { id: c.id, oid, text: S.note && S.note.id === c.id ? S.note.text : '' };
  S.cur[ctx.key] = c.id;
  refreshActs(c, ctx);
}
function refreshActs(c, ctx) {
  const host = ctxHost(ctx);
  const t = host && $('.tiles', host);
  if (t) t.replaceWith(tilesEl(c, ctx));
  const a = host && $('.acts', host);
  if (a) a.replaceWith(actsEl(c, ctx));
  else { if (host && host._ui) host._ui.rev = null; render(); }
}
function freeNote(c, ctx) {
  if (c.status !== 'pending' || isPerm(c)) return;
  S.note = { id: c.id, oid: '__note', text: S.note && S.note.id === c.id ? S.note.text : '' };
  S.cur[ctx.key] = c.id;
  refreshActs(c, ctx);
}
function multiNote(c, ctx) {
  const ids = [...(S.multi.get(c.id) || [])];
  if (!ids.length || c.status !== 'pending') return;
  S.note = { id: c.id, oid: '__multi', ids, text: S.note && S.note.id === c.id ? S.note.text : '' };
  refreshActs(c, ctx);
}
function noteEl(c, ctx) {
  const free = S.note.oid === '__note', many = S.note.oid === '__multi';
  const x = free ? { id: 'note', label: '写意见', style: 'bad' }
    : many ? { id: '__multi', label: (c.options || []).filter(o => S.note.ids.includes(o.id)).map(o => o.label).join('、') }
    : (c.options || []).find(o => o.id === S.note.oid) || { id: S.note.oid, label: '返修', style: 'bad' };
  const bad = x.style === 'bad';
  const tip = free ? (c.note_hint || '不用选现成选项，直接写：要哪个、哪里要改、改成什么样') : c.note_hint || (bad ? '哪里要改？写清楚改哪、改成什么样' : `选「${x.label}」，但哪里要改？写清楚改哪、改成什么样`);
  const ta = h('textarea', { class: 'note', rows: 2, placeholder: tip, 'aria-label': x.label + '意见' });
  ta.value = S.note.text || '';
  ta.addEventListener('input', () => { if (S.note) S.note.text = ta.value; });
  const send = () => {
    const note = ta.value.trim();
    if (free && !note) { ta.focus(); return; }
    const ids = many ? S.note.ids : [x.id];
    S.note = null; submit(c, ids, ctx, note);
  };
  const cancel = () => { S.note = null; refreshActs(c, ctx); };
  ta.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); send(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel(); }
  });
  setTimeout(() => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }, 30);
  return h('div', { class: 'acts noting' }, free ? h('div', { class: 'nfor' }, h('b', null, '直接写意见'), ' · 不选现成选项') : bad ? null : h('div', { class: 'nfor' }, h('b', null, x.label), many ? ' · 附意见' : ' · 要改'), ta,
    h('div', { class: 'nbtns' },
      h('span', { class: 'nh' }, '回车提交 · Shift + 回车换行 · Esc 取消'),
      h('button', { class: 'tx', type: 'button', onclick: cancel }, '取消'),
      h('button', { class: 'b no', type: 'button', onclick: send }, free ? '提交意见' : bad ? '提交' + x.label : '提交意见')));
}
function ctxHost(ctx) { return [$('#main'), ...$$('.q .qb')].find(el => el._ctx === ctx); }

async function submit(c, choice, ctx, note) {
  if (!choice.length || c.status !== 'pending') return;
  const labels = (c.options || []).filter(x => choice.includes(x.id)).map(x => x.label).join('、') || '写了意见';
    c.status = 'answered';
  S.multi.delete(c.id);
  render();
  try {
    const r = await api('/api/cards/' + c.id + '/answer', { method: 'POST', body: { choice, note: note || '', device: device() } });
    const perm = isPerm(c);
    S.last = perm ? null : c.id;
    const where = perm ? '已交给 Claude' : !c.session ? '已记下' : r.listening ? '已传给 Claude' : 'Claude 下次查看时收到';
    toast([h('span', { class: 'n' }, pad(c.seq)), ` ${labels}${note ? '（附意见）' : ''} · ${where}`], perm ? null : () => undo(c.id, ctx));
  } catch (e) {
    c.status = 'pending'; render();
    toast('没提交上：' + e.message);
  }
  loadState().catch(() => {});
}
async function undo(cid, ctx) {
  try {
    await api('/api/cards/' + cid + '/reopen', { method: 'POST', body: {} });
    if (ctx) S.cur[ctx.key] = cid;
    toast('已撤回，重新定这一张');
    await loadState();
  } catch (e) { toast('撤回失败：' + e.message); }
}

/* 「在对话里说」：同一个 Claude 会话推来的待定卡一起清掉，那个会话会收到通知 */
function chatGroup(c) {
  return c.session ? S.cards.filter(x => x.status === 'pending' && x.session === c.session) : [c];
}
async function toChat(c, ctx) {
  const grp = chatGroup(c).filter(x => x.status === 'pending');
  if (!grp.length) return;
  grp.forEach(x => { x.status = 'answered'; S.multi.delete(x.id); });
  render();
  try {
    const r = await api('/api/chat', { method: 'POST', body: { ids: grp.map(x => x.id), device: device() } });
    const seqs = r.seqs.map(pad);
    const span = seqs.length > 2 ? `${seqs[0]}–${seqs[seqs.length - 1]}` : seqs.join('、');
    const where = !c.session ? '已清掉' : r.listening ? '已告诉 Claude，去对话里说吧' : 'Claude 下次查看时知道';
    S.last = null;
    toast([h('span', { class: 'n' }, span), ` 在对话里说 · ${where}`], async () => {
      try { for (const id of r.ids) await api('/api/cards/' + id + '/reopen', { method: 'POST', body: {} }); toast('已撤回'); await loadState(); }
      catch (e) { toast('撤回失败：' + e.message); }
    });
  } catch (e) {
    grp.forEach(x => { x.status = 'pending'; }); render();
    toast('没清掉：' + e.message);
  }
  loadState().catch(() => {});
}

let toastTimer = null;
function toast(content, onUndo, hint) {
  const el = $('#toast'), b = $('#toast-b');
  $('#toast-t').replaceChildren(...[content].flat());
  b.hidden = !onUndo;
  b.onclick = onUndo ? () => { el.hidden = true; onUndo(); } : null;
  el.hidden = false;
  el.title = hint || '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, onUndo ? 10000 : 3500);
}

/* ---------- 四宫格 ---------- */
function renderGrid(main, slugs) {
  if (!slugs.length) {
    const ps = S.projects.filter(p => !p.archived).sort((a, b) => (queue(b.slug).length - queue(a.slug).length) || (b.last - a.last)).slice(0, 4);
    if (!ps.length) { main.replaceChildren(h('section', { class: 'focus' }, emptyEl(null))); return; }
    history.replaceState(null, '', '/grid?p=' + ps.map(p => p.slug).join(','));
    return render();
  }
  if (!main._q) {
    const cols = slugs.length === 1 ? 1 : slugs.length === 3 ? 3 : 2;
    const quads = h('div', { class: 'quads', style: '--cols:' + cols });
    main._q = slugs.map((slug, i) => {
      const sel = h('select', { 'aria-label': '换项目', onchange: e => { const s = [...slugs]; s[i] = e.target.value; go('/grid?p=' + s.join(',')); } });
      const count = h('span');
      const body = h('div', { class: 'qb' });
      body._ctx = { slug, key: 'g' + i + ':' + slug };
      const q = h('section', { class: 'q', onpointerdown: () => { S.activeQ = i; $$('.q').forEach((x, j) => x.classList.toggle('act', j === i)); } },
        h('div', { class: 'qh' }, sel, count, h('a', { href: '/p/' + slug, target: '_blank', rel: 'noopener', style: 'margin-left:auto' }, '单独窗口')), body);
      quads.append(q);
      return { slug, sel, count, body };
    });
    main.append(quads);
  }
  main._q.forEach((x, i) => {
    const ps = S.projects.filter(p => !p.archived || p.slug === x.slug);
    x.sel.replaceChildren(...ps.map(p => h('option', { value: p.slug, selected: p.slug === x.slug }, p.name)));
    if (!proj(x.slug)) x.sel.append(h('option', { value: x.slug, selected: true }, x.slug));
    const n = queue(x.slug).length;
    x.count.textContent = n ? `还剩 ${n}` : '';
    renderFocus(x.body, x.body._ctx, { compact: true });
  });
  $$('.q').forEach((x, j) => x.classList.toggle('act', j === S.activeQ));
}

/* ---------- 已定 ---------- */
function renderDone(main) {
  const done = S.cards.filter(c => c.status !== 'pending').sort((a, b) => b.updated - a.updated).slice(0, 200);
  const rows = done.map(c => {
    const a = c.answer || {};
    const m = (c.media || []).find(x => x.kind === 'img');
    const bad = (c.options || []).some(x => (a.choice || []).includes(x.id) && x.style === 'bad');
    const verdict = c.status === 'cancelled' ? (isPerm(c) ? '别处答了' : '已撤销') : (a.labels || []).join('、');
    const deliver = c.status === 'cancelled' || isPerm(c) ? '' : c.acked ? '已交给 Claude' : c.session ? 'Claude 还没收到' : '';
    const meta = [pname(c.project), ago(c.updated), a.device, deliver].filter(Boolean).join(' · ');
    return h('div', { class: 'row' },
      h('span', { class: 'n s' }, pad(c.seq)),
      m ? h('img', { src: m.thumb || m.src, alt: '', loading: 'lazy' }) : h('span', { class: 'ph' }),
      h('div', null, h('div', { class: 't' }, c.title), h('div', { class: 'm' }, meta), a.note ? h('div', { class: 'm' }, '备注：' + a.note) : null),
      h('span', { class: 'v' + (c.status === 'cancelled' ? ' cx' : bad ? ' no' : '') }, verdict),
      isPerm(c) ? h('span') : h('button', { class: 'x', type: 'button', onclick: async () => {
        try { await api('/api/cards/' + c.id + '/reopen', { method: 'POST', body: {} }); S.cur['f:' + c.project] = c.id; go('/p/' + c.project); }
        catch (e) { toast(e.message); }
      } }, '撤回重定'));
  });
  const projs = S.projects.map(p => h('div', { class: 'prow' + (p.archived ? ' arch' : '') },
    h('span', null, p.name), h('span', { class: 'm', style: 'font-size:12px;color:var(--ink-3)' }, `待定 ${queue(p.slug).length} · 已定 ${p.answered}`),
    h('button', { class: 'x', type: 'button', onclick: async () => { await api('/api/projects', { method: 'POST', body: { slug: p.slug, archived: !p.archived } }); await loadState(); } }, p.archived ? '恢复' : '归档')));
  main.replaceChildren(h('div', { class: 'page' },
    h('h2', { class: 'h' }, '已定'),
    rows.length ? rows : h('p', { style: 'color:var(--ink-3)' }, '还没有定过的卡片。'),
    projs.length ? h('div', { class: 'projs' }, h('h3', null, '项目（做完的归档，就不再出现在顶部）'), projs) : null));
}

/* ---------- 键盘 ---------- */
function activeCtx() {
  if (S.route?.type === 'grid') { const q = $$('.q .qb')[S.activeQ]; return q && q._ctx; }
  if (S.route?.type === 'focus') return $('#main')._ctx;
  return null;
}
document.addEventListener('keydown', e => {
  if ($('#lb').classList.contains('on')) return lbKey(e);
  if (e.target.matches('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === '?') { location.href = '/help'; return; }
  const ctx = activeCtx(); if (!ctx) return;
  const c = curCard(ctx).c;
  const dg = (e.code || '').match(/^(?:Digit|Numpad)([1-9])$/);
  if (dg && c) {
    const x = (c.options || [])[+dg[1] - 1];
    if (x && e.shiftKey) { if (canRevise(c, x)) { e.preventDefault(); revise(c, x.id, ctx); } }
    else if (x) { e.preventDefault(); choose(c, x.id, ctx); }
  } else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    e.preventDefault(); step(ctx, e.key === 'ArrowRight' ? 1 : -1);
  } else if ((e.key === 'c' || e.key === 'C') && c && !isPerm(c)) {
    e.preventDefault(); toChat(c, ctx);
  } else if ((e.key === 'w' || e.key === 'W') && c && !isPerm(c)) {
    e.preventDefault(); freeNote(c, ctx);
  } else if ((e.key === 'z' || e.key === 'Z') && S.last) {
    e.preventDefault(); const id = S.last; S.last = null; $('#toast').hidden = true; undo(id, ctx);
  } else if (e.key === 'Enter' && c && c.multi) {
    const sel = S.multi.get(c.id); if (sel && sel.size) { e.preventDefault(); submit(c, [...sel], ctx); }
  } else if ((e.key === 'o' || e.key === ' ') && c && (c.media || []).length) {
    e.preventDefault(); openLb(c, 0, ctx);
  }
});

/* ---------- 放大镜（电脑：鼠标移到图上看细节，滚轮调倍数） ---------- */
const fine = matchMedia('(hover: hover) and (pointer: fine)').matches;
const LOUPE = { el: null, img: null, z: 3 };
function loupeEl() {
  if (!LOUPE.el) { LOUPE.el = h('div', { class: 'loupe', 'aria-hidden': 'true' }, h('span', { class: 'z n' })); document.body.append(LOUPE.el); }
  return LOUPE.el;
}
function hideLoupe() { if (LOUPE.el) LOUPE.el.classList.remove('on'); LOUPE.img = null; }
function attachLoupe(img, m) {
  if (!fine || !m || m.kind === 'video') return;
  const hi = m.orig || m.src;
  img.classList.add('lp');
  img.addEventListener('pointerenter', e => {
    LOUPE.img = img;
    const el = loupeEl();
    el.style.backgroundImage = `url("${img.currentSrc || img.src}")`;
    if (hi !== (img.currentSrc || img.src)) {
      const pre = new Image(); pre.src = hi;
      pre.decode().then(() => { if (LOUPE.img === img) el.style.backgroundImage = `url("${hi}")`; }).catch(() => {});
    }
    moveLoupe(e, img);
  });
  img.addEventListener('pointerleave', () => { if (LOUPE.img === img) hideLoupe(); });
  img.addEventListener('pointermove', e => { if (LOUPE.img === img) moveLoupe(e, img); });
  img.addEventListener('wheel', e => {
    if (LOUPE.img !== img) return;
    e.preventDefault();
    LOUPE.z = Math.min(10, Math.max(1.5, LOUPE.z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    moveLoupe(e, img);
  }, { passive: false });
}
function moveLoupe(e, img) {
  const el = loupeEl(), r = img.getBoundingClientRect();
  const nw = img.naturalWidth || r.width, nh = img.naturalHeight || r.height, k = Math.min(r.width / nw, r.height / nh);
  const dw = nw * k, dh = nh * k, ox = r.left + (r.width - dw) / 2, oy = r.top + (r.height - dh) / 2;
  const x = e.clientX - ox, y = e.clientY - oy;
  if (x < 0 || y < 0 || x > dw || y > dh) { el.classList.remove('on'); return; }
  const L = el.offsetWidth || 260, Z = LOUPE.z;
  el.style.transform = `translate(${e.clientX - L / 2}px, ${e.clientY - L / 2}px)`;
  el.style.backgroundSize = `${dw * Z}px ${dh * Z}px`;
  el.style.backgroundPosition = `${L / 2 - x * Z}px ${L / 2 - y * Z}px`;
  el.firstChild.textContent = '×' + (Math.round(Z * 10) / 10);
  el.classList.add('on');
}
addEventListener('scroll', hideLoupe, { passive: true });

/* ---------- 图层看大图（× / 点空白 / Esc 关） ---------- */
const LB = { c: null, i: 0, cmp: false, ctx: null, grid: false };
function openLb(c, i, ctx) {
  hideLoupe();
  Object.assign(LB, { c, i, cmp: false, ctx: ctx || activeCtx(), grid: (c.media || []).length >= 3 });
  const lb = $('#lb'); lb.hidden = false;
  void lb.offsetWidth;
  lb.classList.add('on');
  document.body.style.overflow = 'hidden';
  drawLb();
}
function closeLb() {
  const lb = $('#lb'); if (lb.hidden) return;
  hideLoupe(); lb.classList.remove('on'); document.body.style.overflow = '';
  setTimeout(() => { if (!lb.classList.contains('on')) { lb.hidden = true; $('#lb-stage').replaceChildren(); } }, 180);
}
function lbActs(c, opt) {
  if (c.status !== 'pending' || c.multi) return [];
  const go = x => () => { closeLb(); choose(c, x.id, LB.ctx); };
  if (LB.grid) return (c.options || []).map(x => h('button', { class: 'b' + (x.media != null && x.label.length <= 2 ? ' lt' : '') + (x.style === 'good' ? ' go' : x.style === 'bad' ? ' no' : ''), type: 'button', onclick: go(x) }, x.label));
  if (opt) return [h('button', { class: 'b go', type: 'button', onclick: go(opt) }, '选 ' + opt.label),
    canRevise(c, opt) ? h('button', { class: 'b no', type: 'button', onclick: () => { closeLb(); revise(c, opt.id, LB.ctx); } }, '选 ' + opt.label + ' · 要改…') : null];
  if ((c.options || []).some(x => x.media != null)) return [];
  return (c.options || []).map(x => h('button', { class: 'b' + (x.style === 'good' ? ' go' : x.style === 'bad' ? ' no' : ''), type: 'button', onclick: go(x) }, x.label));
}
function drawLb() {
  const c = LB.c, media = c.media || [], m = media[LB.i];
  const stage = $('#lb-stage'), foot = $('#lb-foot');
  const imgs = media.map((x, i) => x.kind === 'img' ? i : -1).filter(i => i >= 0);
  const opt = (c.options || []).find(x => x.media === LB.i);
  hideLoupe();
  stage.classList.remove('zoomed');
  $('#lb-t').replaceChildren(h('span', { class: 'n' }, pad(c.seq)), c.title);
  $('#lb-prev').hidden = $('#lb-next').hidden = media.length < 2 || LB.grid;
  if (LB.grid) return drawLbGrid(c, media);
  const tools = [
    media.length >= 2 ? h('button', { class: 'tl', type: 'button', onclick: () => { LB.grid = true; LB.cmp = false; drawLb(); } }, `并排看 ${media.length} 张`) : null,
    m.kind === 'img' && imgs.length >= 2 ? h('button', { class: 'tl' + (LB.cmp ? ' on' : ''), type: 'button', onclick: () => { LB.cmp = !LB.cmp; drawLb(); } }, LB.cmp ? '退出叠图' : '叠图对比') : null];
  const label = [opt ? opt.label : null, m.label || `第 ${LB.i + 1} 张`, m.caption].filter(Boolean).join(' · ');
  const files = (c.facts || []).filter(([k]) => /^(文件|路径)/.test(k)).map(([, v]) => h('span', { class: 'path' }, v));
  let extra = null;
  if (LB.cmp) {
    const j = imgs[(imgs.indexOf(LB.i) + 1) % imgs.length], m2 = media[j];
    const a = h('img', { src: m.orig || m.src, alt: '' }), b = h('img', { src: m2.orig || m2.src, alt: '' }), ln = h('div', { class: 'ln' });
    const range = h('input', { type: 'range', min: '0', max: '100', value: '50', 'aria-label': '分界线' });
    const set = v => { b.style.clipPath = `inset(0 0 0 ${v}%)`; ln.style.left = v + '%'; };
    range.addEventListener('input', () => set(range.value)); set(50);
    stage.replaceChildren(h('div', { class: 'lb-cmp' }, a, b, ln));
    extra = [h('span', null, `左：${m.label || ''}　右：${m2.label || ''}　拖滑杆移动分界线`), range];
  } else if (m.kind === 'video') {
    stage.replaceChildren(h('figure', { class: 'lb-fig' }, h('video', { src: m.src, controls: true, playsinline: true, autoplay: true })));
  } else {
    const img = h('img', { src: m.src, alt: m.label || '' });
    if (m.orig && m.orig !== m.src) { const hi = new Image(); hi.onload = () => { if (img.isConnected && LB.c === c) img.src = m.orig; }; hi.src = m.orig; }
    attachLoupe(img, m);
    if (!fine) img.addEventListener('click', e => {
      const r = img.getBoundingClientRect(), fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
      if (stage.classList.toggle('zoomed')) requestAnimationFrame(() => {
        stage.scrollLeft = img.offsetWidth * fx - stage.clientWidth / 2;
        stage.scrollTop = img.offsetHeight * fy - stage.clientHeight / 2;
      });
    });
    stage.replaceChildren(h('figure', { class: 'lb-fig' }, img));
  }
  const hint = m.kind === 'img' && !LB.cmp ? (fine ? '移上去看细节，滚轮调倍数' : '点图放大，再点还原') : null;
  foot.replaceChildren(...kids(
    h('div', { class: 'lb-meta' },
      h('span', { class: 'lb-lab' }, label),
      media.length > 1 ? h('span', { class: 'n' }, `${LB.i + 1} / ${media.length}`) : null,
      m.w && !LB.cmp ? h('span', null, `${m.w} × ${m.h}`) : null,
      hint ? h('span', null, hint) : null, files),
    h('div', { class: 'lb-tools' }, tools, lbActs(c, opt)),
    extra));
}
function drawLbGrid(c, media) {
  const stage = $('#lb-stage'), foot = $('#lb-foot');
  const optOf = new Map((c.options || []).filter(x => x.media != null).map(x => [x.media, x]));
  const box = h('div', { class: 'lb-grid' });
  media.forEach((m, k) => {
    const o = optOf.get(k);
    let el;
    if (m.kind === 'video') el = h('video', { src: m.src + '#t=0.1', controls: true, playsinline: true, preload: 'metadata', muted: true });
    else {
      el = h('img', { src: m.src, alt: m.label || '' });
      if (m.orig && m.orig !== m.src) { const hi = new Image(); hi.onload = () => { if (el.isConnected) el.src = m.orig; }; hi.src = m.orig; }
      attachLoupe(el, m);
      el.addEventListener('click', () => { LB.grid = false; LB.i = k; drawLb(); });
    }
    box.append(h('figure', { class: 'lb-cell' + (k === LB.i ? ' cur' : '') }, h('div', { class: 'im' }, el),
      h('figcaption', null, o ? h('b', null, o.label) : (isBroken(m.label) ? h('i', { class: 'dot' }) : null), h('span', null, o ? (m.caption || m.label || '') : [m.label, m.caption ? ' · ' + m.caption : '']))));
  });
  stage.replaceChildren(box);
  gridFit(box, media, () => innerWidth <= 760 ? { gap: 8, cap: 24 } : { gap: 14, cap: 28 });
  const hint = fine ? '移上去看细节，滚轮调倍数 · 点一张单独看' : '点一张单独看';
  foot.replaceChildren(...kids(
    h('div', { class: 'lb-meta' }, h('span', { class: 'lb-lab' }, `并排 ${media.length} 张`), h('span', null, hint)),
    h('div', { class: 'lb-tools' }, lbActs(c, null))));
}
function lbStep(d) { const n = (LB.c.media || []).length; if (n < 2 || LB.grid) return; LB.i = (LB.i + d + n) % n; LB.cmp = false; drawLb(); }
function lbKey(e) {
  if (e.key === 'Escape') { e.preventDefault(); closeLb(); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); lbStep(-1); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); lbStep(1); }
}
$('#lb-close').onclick = closeLb;
$('#lb-prev').onclick = () => lbStep(-1);
$('#lb-next').onclick = () => lbStep(1);
$('#lb').addEventListener('click', e => {
  if (e.target === e.currentTarget || e.target.matches('.lb-stage:not(.zoomed), .lb-top, .lb-foot, .lb-fig, .lb-meta, .lb-grid, .lb-cell, .lb-cell .im')) closeLb();
});
{
  let x0 = null, y0 = null;
  const st = $('#lb-stage');
  st.addEventListener('touchstart', e => { if (e.touches.length === 1 && !st.classList.contains('zoomed')) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; } else x0 = null; }, { passive: true });
  st.addEventListener('touchend', e => {
    if (x0 == null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; x0 = null;
    if (Math.abs(dx) > 60 && Math.abs(dy) < 50) lbStep(dx < 0 ? 1 : -1);
  }, { passive: true });
}

/* ---------- 启动 ---------- */
render();
loadState().then(() => setConn('live')).catch(e => { $('#main').replaceChildren(h('section', { class: 'focus' }, h('div', { class: 'empty' }, h('h2', null, '载入失败'), h('p', null, e.message)))); setConn('down'); });
pollLoop();
