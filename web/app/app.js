'use strict';
/* decide 前端：一次一张、两个按钮、点完自动下一张。答案经后台监听交回 Claude 会话。 */

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
/* 「推荐」两个字一律醒目（10-19 用户定）：拆出来包一层，颜色按底色在 style.css .kw-rec 里换 */
// 节标题本身是红色时，「推荐」改琥珀色才看得出
function isRed(c) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(c || '').trim());
  if (!m) return /^(red|crimson|firebrick|tomato)$/i.test(String(c || '').trim());
  const x = m[1].length === 3 ? [...m[1]].map(ch => ch + ch).join('') : m[1];
  const [r, g, b] = [0, 2, 4].map(i => parseInt(x.slice(i, i + 2), 16));
  return r > 150 && g < 110 && b < 110;
}
function hl(t) {
  if (t == null || t === '' || !String(t).includes('推荐')) return t;
  return String(t).split(/(推荐)/).filter(Boolean).map(p => p === '推荐' ? h('b', { class: 'kw-rec' }, p) : p);
}
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

const S = { ver: 0, skew: 0, loaded: false, projects: [], cards: [], byId: new Map(), cur: {}, multi: new Map(), activeQ: 0, last: null, wide: new Set() };
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
  S.intake = !!j.intake; $('#ph-link').hidden = !S.intake;
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
    if (S.note || phDirty() || $('#lb').classList.contains('on')) { staleSince = staleSince || Date.now(); return; }
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
  const d = p.match(/^\/done\/([a-z0-9]{4,16})$/);
  if (d) return { type: 'review', id: d[1] };
  if (p === '/paihuo') return { type: 'paihuo' };
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
  const key = r.type + ':' + (r.slug || r.id || (r.slugs || []).join(','));
  if (main.dataset.view !== key) { main.replaceChildren(); main.dataset.view = key; main._ui = null; main._q = null; }
  if (r.type === 'focus') {
    const k = 'f:' + (r.slug || '');
    if (!main._ctx || main._ctx.key !== k) main._ctx = { slug: r.slug, key: k };
    renderFocus(main, main._ctx, {});
  }
  else if (r.type === 'grid') renderGrid(main, r.slugs);
  else if (r.type === 'paihuo') return renderPaihuo(main);
  else if (r.type === 'review') renderReview(main, r.id);
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
  $$('.right a[data-k]').forEach(a => a.classList.toggle('on', a.dataset.k === (r.type === 'review' ? 'done' : r.type)));
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
    // 手机上带图 / 视频的卡：题目和选项按钮一起钉在底部，图在上面；不然题目排在图下面、被底栏盖住（525）
    ui.root.classList.toggle('sheet', !o.compact && !tileMode(c));
    ui.root.replaceChildren(...kids(stageEl(c, ctx, o), infoEl(c, ctx, o), factsEl(c, o), o.compact ? null : railEl(q, ctx)));
    ui.id = c.id; ui.rev = c.rev;
    if (!o.compact) watchSheet(ui.root);
    if (!o.compact && tileMode(c) && figIdx(c).length) guardFigs(host, ctx, o, c);
  } else if (!o.compact) {
    const old = $('.rail', ui.root), nr = railEl(q, ctx);
    old ? old.replaceWith(nr) : ui.root.append(nr);
  }
}
/* 运行时兜底：文字题排法下量一次整题的图，任何一张短边被压到看不清就整张卡换回大图 + 底部按钮。
   以后再冒出没见过的卡型（选项多、说明长、图多），也不会把要看的东西挤没（611） */
function guardFigs(host, ctx, o, c) {
  const check = () => {
    const ui = host._ui;
    if (!ui || ui.id !== c.id || ui.rev !== c.rev || S.wide.has(c.id) || !tileMode(c)) return;
    const min = innerWidth > 760 ? 90 : 60;
    const small = [...ui.root.querySelectorAll('.qstage .stage .frame')].some(f => {
      const r = f.getBoundingClientRect(), m = f.querySelector('img, video');
      const ready = m && (m.tagName === 'VIDEO' ? m.readyState >= 1 : m.complete && m.naturalWidth);
      return r.width > 0 && (ready || r.height > 0) && Math.min(r.width, r.height) < min;
    });
    if (!small || (c.options || []).some(x => x.media != null)) return;
    S.wide.add(c.id); ui.rev = null; ctx.review ? render() : renderFocus(host, ctx, o);
  };
  setTimeout(check, 500); setTimeout(check, 1600);
}
function emptyEl(slug) {
  return h('div', { class: 'empty' }, h('h2', null, '都定完了'),
    h('p', null, (slug ? pname(slug) + '没有待定的卡片。' : '') + '新卡片会自己出现，页面开着就行。'));
}
/* 几张图就并排几张：试每种列数，选让图片总面积最大的那种 */
const NARROW = 260, CAP_NARROW = 70; // 与 style.css 里 @container (max-width: 260px) 的说明行高度一致
function bestCols(ars, W, H, gap, cap) {
  const n = ars.length; let best = 1, bestA = -1;
  for (let c = 1; c <= n; c++) {
    const r = Math.ceil(n / c), cw = (W - gap * (c - 1)) / c, ch = (H - gap * (r - 1)) / r - (typeof cap === 'function' ? cap(cw) : cap);
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
/* 整题的图本身就是要比的东西（611：六条同种子视频 + 六个文字选项）：两张以上照片 / 视频，不算示意图 */
const subjectFigs = c => figIdx(c).filter(i => c.media[i].kind === 'video' || !/\.svg$/i.test(c.media[i].src || '')).length >= 2;
function tileMode(c) {
  const opts = c.options || [];
  if (isPerm(c) && opts.every(x => x.media == null)) return true;
  // 兜底量过：文字题排法把图压到看不清，这张卡改回大图 + 底部按钮
  if (S.wide.has(c.id) && opts.every(x => x.media == null)) return false;
  if (mixed(c)) return true;
  // 选项带说明不再把要比的视频 / 照片挤成细条（611 在 1280×690 上六条视频只剩 17 px 高）
  return opts.length >= 2 && opts.every(x => x.media == null) && (!figIdx(c).length || Boolean(c.plan) || (opts.some(x => x.desc) && !subjectFigs(c)));
}
/* 每个普通选项都能「选它 + 写哪里要改」；通过 / 返修这类本身带意见框的、多选卡不加 */
const canRevise = (c, x) => !isPerm(c) && !c.multi && c.status === 'pending' && x.style !== 'bad' && x.style !== 'good';
function reviseBtn(c, x, ctx, cls) {
  return h('button', { class: cls || 'rv', type: 'button', title: `选「${x.label}」并写哪里要改`, onclick: e => { e.stopPropagation(); revise(c, x.id, ctx); } }, '要改…');
}
function tilesEl(c, ctx) {
  const opts = c.options || [], sel = S.multi.get(c.id) || new Set();
  const noting = S.note && S.note.id === c.id ? S.note.oid : null;
  const mine = mineOf(c);
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
      h('button', { class: 'tile' + (x.style === 'good' ? ' go' : x.style === 'bad' ? ' no' : '') + (x.rec ? ' rec' : '') + (sel.has(x.id) || noting === x.id || mine.ids.has(x.id) ? ' sel' : ''),
        type: 'button', 'data-oid': x.id, title: i < 9 ? '按 ' + (i + 1) + (rv ? '；Shift + ' + (i + 1) + ' 选它并写意见' : '') : '', onclick: () => choose(c, x.id, ctx) },
        optPic(c, x) || x.gen ? picEl(c, x, ctx) : null,
        h('span', { class: 'tile-k' }, c.multi ? h('span', { class: 'tile-ck', 'aria-hidden': 'true' }) : null, i < 9 ? String(i + 1) : '', mine.ids.has(x.id) ? h('span', { class: 'mine' }, mine.tag) : null),
        x.rec ? h('span', { class: 'tile-tag' }, '推荐') : null,
        h('span', { class: 'tile-t' }, hl(x.label)),
        x.desc ? h('span', { class: 'tile-d' }, hl(x.desc)) : null),
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
      h('div', null, p.title ? h('h2', null, hl(p.title)) : null, p.sub ? h('p', null, hl(p.sub)) : null),
      src >= 0 ? h('button', { class: 'plan-src', type: 'button', onclick: () => openLb(c, src, ctx) }, '看原图') : null) : null,
    (p.lines || []).length ? h('ul', { class: 'plan-l' }, p.lines.map(x => h('li', null, hl(x)))) : null,
    (p.secs || []).length ? h('div', { class: 'plan-g' + (p.secs.length === 1 ? ' one' : '') }, p.secs.map((x, i) =>
      h('section', { class: 'plan-s' + (x.pic || x.gen ? ' has-pic' : '') + (isRed(x.color) ? ' rc' : ''), style: x.color ? `--c:${x.color}` : null },
        pic(x),
        h('div', { class: 'plan-b' }, h('h3', null, hl(x.t)), x.lines.map(y => h('p', null, hl(y))))))) : null,
    (p.foot || []).length ? h('div', { class: 'plan-f' }, p.foot.map(x => h('p', null, hl(x)))) : null);
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
  // 格子窄（743：八张竖图排三四列）时说明行固定两行高、「要改」放第一行，算排法时按这个高度留
  const hasTxt = keep.some(i => optOf.get(i) ? all[i].caption : (all[i].label || all[i].caption));
  const capOf = (base, cw) => hasTxt && cw < NARROW ? Math.max(base, CAP_NARROW) : base;
  if (media.length > 1) gridFit(st, media, () => innerWidth <= 760 ? { gap: 8, cap: cw => capOf(longCap ? 50 : 22, cw), h: innerHeight * 0.62 } : { gap: 16, cap: cw => capOf(longCap ? 62 : 30, cw) });
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
    const txt = opt ? (m.caption || '') : [m.label, m.caption ? ' · ' + m.caption : ''].join('');
    const cap = h('div', { class: 'cap' + (txt ? ' txt' : '') },
      opt ? h('span', { class: 'lt' + (opt.label.length > 3 ? ' long' : '') }, hl(opt.label)) : (isBroken(m.label) ? h('i', { class: 'dot' }) : null),
      opt && opt.rec ? h('span', { class: 'rec' }, '推荐') : null,
      opt && mineOf(c).ids.has(opt.id) ? h('span', { class: 'mine' }, mineOf(c).tag) : null,
      h('span', { class: 'ct', title: txt || null }, hl(txt)),
      m.kind === 'video' ? h('button', { class: 'zoom', type: 'button', onclick: () => openLb(c, i, ctx) }, '放大') : null,
      opt && canRevise(c, opt) ? reviseBtn(c, opt, ctx, 'rv cap-rv') : null);
    st.append(h('figure', { class: 'fig' + (opt ? ' pick' : '') + (opt && opt.rec ? ' rec' : '') + (opt && mineOf(c).ids.has(opt.id) ? ' mine' : '') }, frame, cap));
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

// 底栏多高，图区就让出多高（标题行数、链接、正在写意见都会变高）
let sheetRO;
function watchSheet(root) {
  const info = $('.info', root);
  sheetRO?.disconnect();
  if (!info || !root.classList.contains('sheet') || typeof ResizeObserver === 'undefined') return root.style.removeProperty('--sheet');
  sheetRO = new ResizeObserver(() => root.style.setProperty('--sheet', Math.ceil(info.getBoundingClientRect().height) + 'px'));
  sheetRO.observe(info);
}

function infoEl(c, ctx, o) {
  return h('div', { class: 'info' },
    h('div', { class: 'seq', title: '在 Claude 里说「' + pad(c.seq) + ' 返修 …」就能指这一张' }, pad(c.seq)),
    h('div', { class: 'tt' },
      o.compact ? null : h('span', { class: 'proj' }, pname(c.project)),
      // 长标题在手机上字号小一档：三位数编号挤窄了标题列，40 字的标题会折成四行（441）
      h('h1', { class: [...(c.title || '')].length > 30 ? 'long' : null }, hl(c.title)),
      c.sub ? h('p', null, hl(c.sub)) : null,
      o.compact ? null : linksEl(c),
      o.review ? rvAnsEl(c) : null),
    o.review ? rvActs(c, ctx) : actsEl(c, ctx));
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
    const cls = 'b' + (x.media != null && x.label.length <= 2 ? ' lt' : '') + (x.style === 'good' ? ' go' : x.style === 'bad' ? ' no' : '') + (x.rec ? ' rec' : '') + (sel.has(x.id) ? ' sel' : '');
    // 「A · 一整句」这种选项名：手机按钮只显示 A，全名在图下面（429）
    const pre = (x.label.match(/^\s*([A-Za-z0-9]{1,2}|[①-⑩])\s*[·.、:：]\s*\S/) || x.label.match(/^\s*([①-⑳])\s+\S/) || [])[1];
    box.append(h('button', { class: cls + (pre ? ' hs' : ''), type: 'button', title: (x.desc ? x.desc + ' · ' : '') + '按 ' + (i + 1), 'data-oid': x.id, onclick: () => choose(c, x.id, ctx) },
      x.rec ? h('b', { class: 'rtag' }, '推荐') : null,
      pre ? [h('span', { class: 'bl-full' }, hl(x.label)), h('span', { class: 'bl-short' }, pre)] : hl(x.label)));
  });
  // 手机上「③ ④ ⑤ … 都还油」混排：缩写成编号的按钮均分，没编号的按字长给够，不被截成「都…」（611）
  const hs = box.querySelectorAll('.b.hs').length;
  if (hs && hs < box.querySelectorAll('.b[data-oid]').length) box.classList.add('mixs');
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
  return h('div', { class: 'facts' }, fs.map(([k, v]) => h('span', null, h('b', null, FACT_NAME[k] || k), hl(v))), keys);
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
  if (ctx.review) return rvStep(d);
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
const doneList = () => S.cards.filter(c => c.status !== 'pending').sort((a, b) => b.updated - a.updated).slice(0, 200);
function renderDone(main) {
  const done = doneList();
  const rows = done.map(c => {
    const a = c.answer || {};
    const m = (c.media || []).find(x => x.kind === 'img');
    const bad = a.redo || (c.options || []).some(x => (a.choice || []).includes(x.id) && x.style === 'bad');
    const verdict = c.status === 'cancelled' ? (isPerm(c) ? '别处答了' : '已撤销') : (a.labels || []).join('、');
    const deliver = c.status === 'cancelled' || isPerm(c) ? '' : c.acked ? '已交给 Claude' : c.session ? 'Claude 还没收到' : '';
    const meta = [pname(c.project), ago(c.updated), a.device, deliver].filter(Boolean).join(' · ');
    // 整行点开重看（10-21）：只看不问意见，要返修在里面点
    const open = () => { S.rvOrder = done.map(x => x.id); go('/done/' + c.id); };
    return h('div', { class: 'row', role: 'link', tabindex: '0', title: '点开重看', onclick: open, onkeydown: e => { if (e.key === 'Enter' && e.target === e.currentTarget) { e.preventDefault(); open(); } } },
      h('span', { class: 'n s' }, pad(c.seq)),
      m ? h('img', { src: m.thumb || m.src, alt: '', loading: 'lazy' }) : h('span', { class: 'ph' }),
      h('div', null, h('div', { class: 't' }, hl(c.title)), h('div', { class: 'm' }, meta), a.note ? h('div', { class: 'm' }, (a.redo ? '返修：' : '备注：') + a.note) : null),
      h('span', { class: 'v' + (c.status === 'cancelled' ? ' cx' : bad ? ' no' : ''), title: verdict }, verdict),
      isPerm(c) ? h('span') : h('button', { class: 'x', type: 'button', onclick: e => { e.stopPropagation(); reopenCard(c); } }, '撤回重定'));
  });
  const projs = S.projects.map(p => h('div', { class: 'prow' + (p.archived ? ' arch' : '') },
    h('span', null, p.name), h('span', { class: 'm', style: 'font-size:12px;color:var(--ink-3)' }, `待定 ${queue(p.slug).length} · 已定 ${p.answered}`),
    h('button', { class: 'x', type: 'button', onclick: async () => { await api('/api/projects', { method: 'POST', body: { slug: p.slug, archived: !p.archived } }); await loadState(); } }, p.archived ? '恢复' : '归档')));
  main.replaceChildren(h('div', { class: 'page' },
    h('h2', { class: 'h' }, '已定', rows.length ? h('small', null, '点一行重看；要改在里面点「返修」') : null),
    rows.length ? rows : h('p', { style: 'color:var(--ink-3)' }, '还没有定过的卡片。'),
    projs.length ? h('div', { class: 'projs' }, h('h3', null, '项目（做完的归档，就不再出现在顶部）'), projs) : null));
}
async function reopenCard(c) {
  try { await api('/api/cards/' + c.id + '/reopen', { method: 'POST', body: {} }); S.cur['f:' + c.project] = c.id; go('/p/' + c.project); }
  catch (e) { toast(e.message); }
}

/* ---------- 已定重看（10-21 用户定）：点开只看，不问意见；点「返修」才出意见框，提交后交回推卡的会话 ---------- */
/* 当时选的是哪几项；返修过的卡标「原来选的」 */
function mineOf(c) {
  const a = c.status === 'pending' ? null : c.answer;
  if (!a) return { ids: new Set(), tag: '' };
  return a.redo ? { ids: new Set((a.prev && a.prev.choice) || []), tag: '原来选的' } : { ids: new Set(a.choice || []), tag: '你选的' };
}
const rvOrder = () => { const ids = doneList().map(c => c.id); return S.rvOrder && S.rvOrder.some(id => S.byId.has(id)) ? S.rvOrder.filter(id => S.byId.has(id)) : ids; };
function rvCard(id) { return S.byId.get(id) || (S.rvExtra && S.rvExtra.get(id)); }
function renderReview(main, id) {
  const c = rvCard(id);
  if (!c) {
    // 太早的卡不在 /api/state 里（只给最近 400 张）：单独拉一次
    if (!main._ui) {
      main._ui = { root: h('section', { class: 'focus review' }, h('div', { class: 'empty' }, h('p', null, '载入中…'))), id: null };
      main.append(main._ui.root);
      api('/api/cards/' + id).then(x => { (S.rvExtra ||= new Map()).set(x.id, x); main._ui = null; main.replaceChildren(); render(); })
        .catch(() => main._ui.root.replaceChildren(h('div', { class: 'empty' }, h('h2', null, '找不到这张卡'), h('p', null, h('a', { href: '/done', 'data-nav': true }, '← 回已定列表')))));
    }
    return;
  }
  if (c.status === 'pending') { S.cur['f:' + c.project] = c.id; history.replaceState(null, '', '/p/' + c.project); return render(); }
  let ui = main._ui;
  if (!ui || ui.id === null) { main.replaceChildren(); ui = main._ui = { root: h('section', { class: 'focus review' }), id: undefined, rev: null }; main.append(ui.root); }
  // 交没交给 Claude 不改 rev：ack 变了也要重画，不然一直显示「Claude 还没收到」
  if (ui.id === c.id && ui.rev === c.rev && ui.ack === c.acked) return;
  const ctx = main._ctx = { key: 'r:' + c.id, review: true, id: c.id };
  ui.root.classList.toggle('textq', tileMode(c));
  ui.root.classList.toggle('sheet', !tileMode(c));
  ui.root.replaceChildren(...kids(rvNav(c), stageEl(c, ctx, {}), infoEl(c, ctx, { review: true }), factsEl(c, { compact: true })));
  ui.id = c.id; ui.rev = c.rev; ui.ack = c.acked;
  watchSheet(ui.root);
  if (tileMode(c) && figIdx(c).length) guardFigs(main, ctx, {}, c);
}
function rvNav(c) {
  const ids = rvOrder(), i = ids.indexOf(c.id), n = ids.length;
  return h('nav', { class: 'rvnav', 'aria-label': '翻看已定' },
    h('a', { class: 'back', href: '/done', 'data-nav': true }, '← 已定列表'),
    h('button', { class: 'tx', type: 'button', disabled: i <= 0, onclick: () => rvStep(-1) }, '‹ 上一张'),
    h('span', { class: 'pos' }, i >= 0 ? `${i + 1} / ${n}` : ''),
    h('button', { class: 'tx', type: 'button', disabled: i < 0 || i >= n - 1, onclick: () => rvStep(1) }, '下一张 ›'),
    hover ? h('span', { class: 'keys' }, isPerm(c) ? '← → 翻看　Esc 回列表' : '← → 翻看　R 返修　Esc 回列表') : null);
}
/* 当时怎么定的：选了什么、写的意见、在哪台设备、几时、交没交给 Claude */
function rvAnsEl(c) {
  const a = c.answer || {};
  const when = [a.device, ago(a.at || c.updated)].filter(Boolean).join(' · ');
  const deliver = c.status === 'cancelled' || isPerm(c) ? '' : c.acked ? '已交给 Claude' : c.session ? 'Claude 还没收到' : '没绑会话';
  const opts = c.options || [], mine = mineOf(c);
  // 大图 + 底部按钮的卡：选项在重看时不画按钮，在这里排一行，当时选的那项标黑
  const chips = !tileMode(c) && !opts.some(x => x.media != null) && opts.length
    ? h('div', { class: 'rv-opts' }, opts.map(x => h('span', { class: 'rv-opt' + (mine.ids.has(x.id) ? ' on' : '') + (x.style === 'bad' ? ' no' : '') }, hl(x.label)))) : null;
  let head;
  if (c.status === 'cancelled') head = [h('b', null, isPerm(c) ? '别处答了' : '已撤销'), '这张没有定，是 Claude 撤掉或你选了在对话里说'];
  else if (a.redo) head = [h('b', { class: 'no' }, '你点了返修'), (a.prev && (a.prev.labels || []).length) ? `原来选的：${a.prev.labels.join('、')}` : ''];
  else head = [h('b', null, '你定的：' + (a.labels || []).join('、'))];
  return h('div', { class: 'rv-ans' },
    h('div', { class: 'rv-h' }, head),
    a.note ? h('div', { class: 'rv-note' }, (a.redo ? '返修意见：' : '当时写的：') + a.note) : null,
    a.redo && a.prev && a.prev.note ? h('div', { class: 'rv-note old' }, '原来写的：' + a.prev.note) : null,
    chips,
    h('div', { class: 'rv-m' }, [when, deliver].filter(Boolean).join(' · ')));
}
function rvActs(c, ctx) {
  if (isPerm(c)) return null;  // 授权卡答完 Claude 就继续了：只看
  if (S.note && S.note.id === c.id && S.note.oid === '__redo') return redoNoteEl(c, ctx);
  return h('div', { class: 'acts rv-acts' },
    h('span', { class: 'chats' }, h('button', { class: 'chat', type: 'button', title: '把这张放回待定队列，重新选一次', onclick: () => reopenCard(c) }, '撤回重定')),
    h('button', { class: 'b no', type: 'button', title: '写哪里要改，交回推这张卡的 Claude 会话（按 R）', onclick: () => redoOpen(c, ctx) }, '返修'));
}
function rvRefreshActs(c, ctx) {
  const root = $('#main .focus.review'), a = root && $('.info > .acts', root);
  if (a) a.replaceWith(rvActs(c, ctx) || '');
}
function redoOpen(c, ctx) {
  if (isPerm(c) || c.status === 'pending') return;
  S.note = { id: c.id, oid: '__redo', text: S.note && S.note.id === c.id ? S.note.text : '' };
  rvRefreshActs(c, ctx);
}
function redoNoteEl(c, ctx) {
  const ta = h('textarea', { class: 'note', rows: 2, placeholder: '哪里要改？写清楚改哪、改成什么样', 'aria-label': '返修意见' });
  ta.value = S.note.text || '';
  ta.addEventListener('input', () => { if (S.note) S.note.text = ta.value; });
  const btn = h('button', { class: 'b no', type: 'button' }, '提交返修');
  const send = async () => {
    const note = ta.value.trim();
    if (!note) { ta.focus(); return; }
    if (btn.disabled) return;
    btn.disabled = true; btn.textContent = '提交中…';
    try {
      const r = await api('/api/cards/' + c.id + '/redo', { method: 'POST', body: { note, device: device() } });
      S.note = null;
      const where = !c.session ? '已记下（这张没绑会话，Claude 不会自动收到）' : r.listening ? '已传给 Claude' : 'Claude 下次查看时收到';
      toast([h('span', { class: 'n' }, pad(c.seq)), ` 返修 · ${where}`]);
      await loadState();
    } catch (e) { btn.disabled = false; btn.textContent = '提交返修'; toast('没提交上：' + e.message); }
  };
  const cancel = () => { S.note = null; rvRefreshActs(c, ctx); };
  btn.addEventListener('click', send);
  ta.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); send(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel(); }
  });
  setTimeout(() => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }, 30);
  return h('div', { class: 'acts noting' }, h('div', { class: 'nfor' }, h('b', null, '返修'), c.session ? ' · 交回推这张卡的 Claude 会话' : ' · 这张没绑会话，只记在板上'), ta,
    h('div', { class: 'nbtns' },
      h('span', { class: 'nh' }, '回车提交 · Shift + 回车换行 · Esc 取消'),
      h('button', { class: 'tx', type: 'button', onclick: cancel }, '取消'), btn));
}
function rvStep(d) {
  const ids = rvOrder(), i = ids.indexOf(S.route.id), j = i + d;
  if (i < 0 || j < 0 || j >= ids.length) return;
  if (S.note && S.note.oid === '__redo') S.note = null;
  history.replaceState(null, '', '/done/' + ids[j]); render(); window.scrollTo(0, 0);
}
function rvKey(e) {
  const c = rvCard(S.route.id);
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); rvStep(e.key === 'ArrowRight' ? 1 : -1); }
  else if (e.key === 'Escape') { e.preventDefault(); go('/done'); }
  else if ((e.key === 'r' || e.key === 'R') && c) { e.preventDefault(); redoOpen(c, $('#main')._ctx); }
  else if ((e.key === 'o' || e.key === ' ') && c && (c.media || []).length) { e.preventDefault(); openLb(c, 0, $('#main')._ctx); }
}

/* ---------- 派活：文字 + 附件交给分拣台（10-20） ---------- */
const PH = { key: '', text: '', chat: '', note: '', files: [], recent: [], info: null, busy: false, active: 0, seq: 0 };
const PH_LABEL = { sending: '交件中…', submitted: '网关已收，排队中', queued: '已交分拣台', failed: '没交出去' };
function phKey() { const a = new Uint8Array(8); crypto.getRandomValues(a); return [...a].map(x => x.toString(16).padStart(2, '0')).join(''); }
function phDirty() { return !!(PH.text.trim() || PH.chat.trim() || PH.note.trim() || PH.files.length || PH.busy); }
function fmtSize(n) { return n < 1 << 20 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / (1 << 20)).toFixed(n < 10 << 20 ? 1 : 0) + ' MB'; }
async function phLoad() {
  try { const j = await api('/api/intake'); PH.info = j; PH.recent = j.recent || []; } catch (e) { PH.loadErr = e.message; }
  phPaint();
}
function phAdd(list) {
  const max = PH.info?.max_bytes || 95 << 20, cap = PH.info?.max_files || 30;
  for (const f of list) {
    if (PH.files.length >= cap) { toast(`一次最多 ${cap} 个附件`); break; }
    const it = { lid: ++PH.seq, file: f, name: f.name || ('粘贴的图片-' + PH.seq + '.png'), size: f.size, pct: 0, fid: '', err: '' };
    if (f.size > max) it.err = `超过 ${fmtSize(max)}`;
    if (/^image\//.test(f.type)) it.thumb = URL.createObjectURL(f);
    PH.files.push(it);
  }
  phPump(); phPaint();
}
function phPump() {
  for (const it of PH.files) {
    if (PH.active >= 3) break;
    if (it.fid || it.err || it.xhr) continue;
    PH.active++;
    const x = it.xhr = new XMLHttpRequest();
    x.open('POST', '/api/intake/file');
    x.setRequestHeader('X-Filename', encodeURIComponent(it.name));
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = e => { if (e.lengthComputable) { it.pct = e.loaded / e.total; phChip(it); } };
    x.onloadend = () => {
      PH.active--; it.xhr = null;
      if (!PH.files.includes(it)) return phPump();
      if (x.status === 401) return location.reload();
      let j = {}; try { j = JSON.parse(x.responseText || '{}'); } catch {}
      if (x.status === 201 && j.id) { it.fid = j.id; it.pct = 1; }
      else it.err = j.error || (x.status ? '上传失败（' + x.status + '）' : '网络断了');
      phPump(); phPaint();
    };
    x.send(it.file);
  }
}
function phRemove(it) {
  if (it.xhr) it.xhr.abort();
  if (it.thumb) URL.revokeObjectURL(it.thumb);
  PH.files = PH.files.filter(x => x !== it); phPaint();
}
function phChip(it) {
  const el = PH.el && PH.el.querySelector(`[data-lid="${it.lid}"]`);
  if (!el) return;
  const st = it.err ? it.err : it.fid ? fmtSize(it.size) : Math.round(it.pct * 100) + '%';
  el.className = 'ph-chip' + (it.err ? ' bad' : it.fid ? ' ok' : ' up');
  el.querySelector('.st').textContent = st;
  el.querySelector('.bar i').style.width = (it.fid ? 100 : Math.round(it.pct * 100)) + '%';
}
async function phSubmit() {
  if (PH.busy) return;
  if (PH.files.some(x => !x.fid && !x.err)) return toast('附件还在上传，传完再交');
  if (PH.files.some(x => x.err)) return toast('有附件没传上去，先删掉或重试');
  if (!PH.text.trim() && !PH.files.length) return toast('写几句话或加个附件');
  PH.busy = true; phPaint();
  try {
    const row = await api('/api/intake', { method: 'POST', body: { key: PH.key, text: PH.text, chat: PH.chat, note: PH.note, files: PH.files.map(x => x.fid) } });
    PH.files.forEach(x => x.thumb && URL.revokeObjectURL(x.thumb));
    Object.assign(PH, { key: phKey(), text: '', chat: '', note: '', files: [] });
    PH.recent = [row, ...PH.recent.filter(r => r.id !== row.id)];
    PH.el = null;  // 重画编辑区，清掉输入框
    toast('交出去了，分拣台判断好会在这块板上请你确认');
  } catch (e) { toast(e.message); }
  PH.busy = false; phPaint();
}
async function phRetry(id) {
  try { const row = await api('/api/intake/' + id + '/retry', { method: 'POST', body: {} }); PH.recent = PH.recent.map(r => r.id === id ? row : r); phPaint(); }
  catch (e) { toast(e.message); }
}
setInterval(() => { if (S.route?.type === 'paihuo' && !document.hidden && PH.recent.some(r => r.status === 'sending' || r.status === 'submitted')) phLoad(); }, 4000);
document.addEventListener('paste', e => {
  if (S.route?.type !== 'paihuo') return;
  const fs = [...(e.clipboardData?.files || [])];
  if (!fs.length) return;
  e.preventDefault(); phAdd(fs);
});
function renderPaihuo(main) {
  document.title = baseTitle = '派活 · decide';
  if (!PH.key) PH.key = phKey();
  if (!PH.info && !PH.loading) { PH.loading = true; phLoad(); }
  if (main._ph && PH.el && main.contains(PH.el)) return phPaint();
  main._ph = true; PH.el = null; phPaint();
}
function phPaint() {
  const main = $('#main');
  if (S.route?.type !== 'paihuo' || !main._ph) return;
  if (!PH.el || !main.contains(PH.el)) {
    const pick = h('input', { type: 'file', multiple: true, class: 'ph-file', onchange: e => { phAdd([...e.target.files]); e.target.value = ''; } });
    const field = (k, tag, attrs) => h(tag, { ...attrs, oninput: e => { PH[k] = e.target.value; phState(); } });
    const ta = field('text', 'textarea', { class: 'ph-ta', rows: 7, placeholder: '客户原话 / 要做的事。微信里复制过来原样粘贴就行，不用整理。', 'aria-label': '客户原话或要做的事' });
    const chat = field('chat', 'input', { class: 'ph-in', maxlength: 120, placeholder: '哪个群 / 哪位客户（可不填）', 'aria-label': '来自哪个群或客户' });
    const note = field('note', 'input', { class: 'ph-in', maxlength: 2000, placeholder: '给分拣台的提示，如「片三的」（可不填）', 'aria-label': '给分拣台的提示' });
    ta.value = PH.text; chat.value = PH.chat; note.value = PH.note;
    const box = h('section', { class: 'ph-box' },
      ta,
      h('div', { class: 'ph-chips', id: 'ph-chips' }),
      h('div', { class: 'ph-row' },
        h('label', { class: 'ph-add' }, pick, h('span', null, '＋ 附件'), h('small', null, hover ? '拖进来、粘贴截图也行' : '拍照 / 相册 / 文件')),
        h('span', { class: 'ph-lim', id: 'ph-lim' })),
      h('div', { class: 'ph-meta' }, chat, note),
      h('button', { class: 'ph-go', id: 'ph-go', type: 'button', onclick: phSubmit }, '交给分拣台'));
    let depth = 0;
    box.addEventListener('dragenter', e => { if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); depth++; box.classList.add('drop'); } });
    box.addEventListener('dragover', e => { if ([...(e.dataTransfer?.types || [])].includes('Files')) e.preventDefault(); });
    box.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; box.classList.remove('drop'); } });
    box.addEventListener('drop', e => { e.preventDefault(); depth = 0; box.classList.remove('drop'); phAdd([...(e.dataTransfer?.files || [])]); });
    PH.el = h('div', { class: 'page ph' },
      h('h2', { class: 'h' }, '派活'),
      h('p', { class: 'ph-sub' }, '交给分拣台：它判断这件事该给哪个 Claude 会话，再到这块板上请你确认。'),
      box,
      h('div', { class: 'ph-recent', id: 'ph-recent' }));
    main.replaceChildren(PH.el);
  }
  $('#ph-chips').replaceChildren(...PH.files.map(it => {
    const ext = (it.name.match(/\.([a-z0-9]{1,5})$/i) || [])[1] || '文件';
    const el = h('div', { class: 'ph-chip', 'data-lid': it.lid, title: it.name },
      it.thumb ? h('img', { src: it.thumb, alt: '' }) : h('span', { class: 'ext' }, ext.toUpperCase()),
      h('span', { class: 'nm' }, it.name),
      h('span', { class: 'st' }),
      h('span', { class: 'bar' }, h('i')),
      it.err && it.file.size <= (PH.info?.max_bytes || 95 << 20) ? h('button', { class: 'rt', type: 'button', onclick: () => { it.err = ''; it.pct = 0; phPump(); phPaint(); } }, '重试') : null,
      h('button', { class: 'rm', type: 'button', 'aria-label': '删掉这个附件', onclick: () => phRemove(it) }, '×'));
    return el;
  }));
  PH.files.forEach(phChip);
  phState();
  const rows = PH.recent.map(r => {
    const st = PH_LABEL[r.status] || r.status;
    const meta = [ago(r.created), r.chat, r.files.length ? r.files.length + ' 个附件' : ''].filter(Boolean).join(' · ');
    return h('div', { class: 'ph-item ' + r.status },
      h('span', { class: 'ph-st' }, st),
      h('div', null,
        h('div', { class: 't' }, r.text || (r.files.map(f => f.name).join('、')) || '（空）'),
        h('div', { class: 'm' }, meta),
        r.error ? h('div', { class: 'm err' }, r.error) : null),
      r.status === 'failed' ? h('button', { class: 'x', type: 'button', onclick: () => phRetry(r.id) }, '重试') : h('span'));
  });
  $('#ph-recent').replaceChildren(...kids(
    PH.loadErr ? h('p', { class: 'm err' }, '载入失败：' + PH.loadErr) : null,
    rows.length ? [h('h3', null, '最近交的'), rows] : null));
}

function phState() {
  const go = $('#ph-go'); if (!go) return;
  const off = PH.info && !PH.info.enabled;
  const up = PH.files.filter(x => !x.fid && !x.err).length;
  $('#ph-lim').textContent = PH.files.length ? `${PH.files.length} 个附件` + (up ? `，${up} 个在传` : '') : `单个不超过 ${fmtSize(PH.info?.max_bytes || 95 << 20)}`;
  go.disabled = off || PH.busy || !!up || PH.files.some(x => x.err) || (!PH.text.trim() && !PH.files.length);
  go.textContent = off ? '派活入口没开' : PH.busy ? '正在交…' : up ? '附件上传中…' : '交给分拣台';
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
  if (S.route?.type === 'review') return rvKey(e);
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
  $('#lb-t').replaceChildren(h('span', { class: 'n' }, pad(c.seq)), hl(c.title));
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
      h('span', { class: 'lb-lab' }, hl(label)),
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
    box.append(h('figure', { class: 'lb-cell' + (k === LB.i ? ' cur' : '') + (o && o.rec ? ' rec' : '') }, h('div', { class: 'im' }, el),
      h('figcaption', null, o ? h('b', null, o.label) : (isBroken(m.label) ? h('i', { class: 'dot' }) : null), h('span', null, hl(o ? (m.caption || m.label || '') : [m.label, m.caption ? ' · ' + m.caption : ''].join(''))))));
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
