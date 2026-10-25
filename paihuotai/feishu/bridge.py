#!/usr/bin/env python3
"""ccpaihuo — 飞书机器人接派活台（route305）。

  ccpaihuo run                    常驻：长连接收飞书消息 → 写进 route305 收件箱 → 把分拣进度回到飞书（launchd 跑这个）
  ccpaihuo reply <件号|消息号> 文字   在那条飞书消息下回一句（中途进度、要主人拍板时用）
  ccpaihuo done <件号> 文字         回结果并标成办完：之后那个会话推的卡不再镜像进飞书
  ccpaihuo send 文字               直接私聊主人一句
  ccpaihuo mirror <编号|卡片 id>…   把任意一张 decide 卡弹到飞书私聊，之后双向同步
  ccpaihuo status                  最近的件和各自走到哪一步
  ccpaihuo doctor                  查凭据、飞书连通、route305 等件循环
  ccpaihuo restart | logs

凭据只在 ~/.config/ccpaihuo/app.json（600）；日志里不写 Secret，长连接地址的参数会被抹掉。
只认主人本人：私聊直接收，群里要 @它；owner_open_ids 为空时记下第一个私聊的人（应用可用范围只有主人）。
"""
import glob
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser('~')
CONF = os.path.join(HOME, '.config', 'ccpaihuo', 'app.json')
STATE_DIR = os.path.join(HOME, '.ccpaihuo')
STATE = os.path.join(STATE_DIR, 'state.json')
LOG = os.path.join(HOME, 'Library', 'Logs', 'ccpaihuo.log')
RHOME = os.environ.get('ROUTE305_HOME', os.path.join(HOME, '.route305'))
INBOX = os.path.join(RHOME, 'inbox')
BATCHES = os.path.join(RHOME, 'batches')
LABEL = os.environ.get('CCPAIHUO_LABEL', 'com.example.ccpaihuo')   # launchd 标签
DEBOUNCE = float(os.environ.get('CCPAIHUO_DEBOUNCE', '15'))   # 同一个聊天连发几条，停 15 秒才算一件
TRACK_DAYS = 3
SGT = timezone(timedelta(hours=8))

log = logging.getLogger('ccpaihuo')


def now_ms():
    return int(time.time() * 1000)


def sgt(ms):
    return datetime.fromtimestamp(ms / 1000, SGT).strftime('%m-%d %H:%M')


def jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def jsave(p, d, mode=0o644):
    tmp = p + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def conf():
    c = jload(CONF)
    if not c or not c.get('app_id') or not c.get('app_secret'):
        sys.exit(f'缺凭据：{CONF}（要有 app_id、app_secret）')
    return c


def client(c=None):
    import lark_oapi as lark
    c = c or conf()
    return lark.Client.builder().app_id(c['app_id']).app_secret(c['app_secret']).log_level(lark.LogLevel.WARNING).build()


# ---------- 发消息 ----------

def _text(t):
    return json.dumps({'text': t}, ensure_ascii=False)


def reply(cli, message_id, text=None, card=None):
    """回在那条消息下面；card 给的是飞书卡片 JSON。成功返回新消息号，失败返回 None。"""
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
    content, kind = (json.dumps(card, ensure_ascii=False), 'interactive') if card else (_text(text), 'text')
    req = ReplyMessageRequest.builder().message_id(message_id).request_body(
        ReplyMessageRequestBody.builder().content(content).msg_type(kind).build()).build()
    r = cli.im.v1.message.reply(req)
    if not r.success():
        log.warning('回消息失败 %s code=%s msg=%s', message_id, r.code, r.msg)
        return None
    return r.data.message_id


def patch_card(cli, message_id, card):
    from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
    req = PatchMessageRequest.builder().message_id(message_id).request_body(
        PatchMessageRequestBody.builder().content(json.dumps(card, ensure_ascii=False)).build()).build()
    r = cli.im.v1.message.patch(req)
    if not r.success():
        log.warning('改卡片失败 %s code=%s msg=%s', message_id, r.code, r.msg)
    return r.success()


def send_owner(cli, text=None, card=None):
    """私聊主人。成功返回消息号，失败返回 None。"""
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    owners = conf().get('owner_open_ids') or []
    if not owners:
        log.warning('还不知道主人是谁：先在飞书里给机器人发一条私聊')
        return None
    content, kind = (json.dumps(card, ensure_ascii=False), 'interactive') if card else (_text(text), 'text')
    req = CreateMessageRequest.builder().receive_id_type('open_id').request_body(
        CreateMessageRequestBody.builder().receive_id(owners[0]).content(content).msg_type(kind).build()).build()
    r = cli.im.v1.message.create(req)
    if not r.success():
        log.warning('私聊发送失败 code=%s msg=%s', r.code, r.msg)
        return None
    return r.data.message_id


def download(cli, message_id, key, kind, dest_dir, n, name=''):
    from lark_oapi.api.im.v1 import GetMessageResourceRequest
    req = GetMessageResourceRequest.builder().message_id(message_id).file_key(key).type(kind).build()
    r = cli.im.v1.message_resource.get(req)
    if not r.success():
        log.warning('下载失败 %s %s code=%s msg=%s', kind, key[:16], r.code, r.msg)
        return None
    name = name or r.file_name or (key[-12:] + ('.jpg' if kind == 'image' else ''))
    safe = re.sub(r'[/\\:\x00]', '_', name)[-120:]
    p = os.path.join(dest_dir, f'{n:02d}-{safe}')
    with open(p, 'wb') as f:
        f.write(r.file.read())
    return os.path.basename(p)


# ---------- 状态 ----------

_state_lock = threading.Lock()


def state():
    return jload(STATE, {'items': {}, 'seen': []}) or {'items': {}, 'seen': []}


def save_state(s):
    os.makedirs(STATE_DIR, exist_ok=True)
    s['seen'] = s.get('seen', [])[-500:]
    jsave(STATE, s)


# ---------- 收消息 ----------

def parse(msg):
    """一条飞书消息 → (文字, [(资源 key, 类型, 文件名)])。"""
    try:
        c = json.loads(msg.content or '{}')
    except Exception:
        c = {}
    t, res = msg.message_type, []
    if t == 'text':
        text = c.get('text', '')
    elif t == 'post':
        body = c if 'content' in c else next((v for v in c.values() if isinstance(v, dict)), {})
        bits = [body.get('title') or '']
        for line in body.get('content') or []:
            row = []
            for el in line:
                if el.get('tag') in ('text', 'a', 'code_block'):
                    row.append(el.get('text', ''))
                elif el.get('tag') == 'img' and el.get('image_key'):
                    res.append((el['image_key'], 'image', ''))
                elif el.get('tag') == 'media' and el.get('file_key'):
                    res.append((el['file_key'], 'file', ''))
            bits.append(''.join(row))
        text = '\n'.join(b for b in bits if b)
    elif t == 'image':
        text = ''
        res.append((c.get('image_key', ''), 'image', ''))
    elif t in ('file', 'audio', 'media'):
        text = ''
        res.append((c.get('file_key', ''), 'file', c.get('file_name', '')))
    else:
        text = f'[{t} 类型的消息，桥不认识]'
    text = re.sub(r'@_user_\d+\s*', '', text).strip()
    return text, [r for r in res if r[0]]


class Bridge:
    def __init__(self):
        import cards
        self.c = conf()
        self.cli = client(self.c)
        self.q = queue.Queue()
        self.buf = {}          # chat_id → {'msgs': [...], 'last': 时间}
        os.makedirs(STATE_DIR, exist_ok=True)
        self.cards = cards
        self.mirror = cards.Mirror(os.path.join(STATE_DIR, 'cards.json'),
                                   lambda mid, card: reply(self.cli, mid, card=card) if mid else send_owner(self.cli, card=card),
                                   lambda mid, card: patch_card(self.cli, mid, card))

    # 飞书卡片上点了按钮：同步记到 decide，当场把卡片改成结果（飞书要 3 秒内回）
    def on_card(self, data):
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
        ev = data.event
        try:
            if not ev.operator or ev.operator.open_id not in self.owners():
                return P2CardActionTriggerResponse({'toast': {'type': 'error', 'content': '只有主人能点'}})
            act = ev.action
            toast, card, ok = self.mirror.answer(ev.context.open_message_id, act.name or '', act.form_value or {})
        except Exception as e:
            log.exception('处理卡片点击出错')
            return P2CardActionTriggerResponse({'toast': {'type': 'error', 'content': f'没记上：{str(e)[:60]}'}})
        resp = {'toast': {'type': 'success' if ok else 'warning', 'content': toast}}
        if card:
            resp['card'] = {'type': 'raw', 'data': card}
        return P2CardActionTriggerResponse(resp)

    # SDK 回调：只做最少的事，马上返回（飞书 3 秒内没回执会重推）
    def on_message(self, data):
        try:
            self.q.put(data.event)
        except Exception:
            log.exception('入队失败')

    def owners(self):
        return self.c.get('owner_open_ids') or []

    def accept(self, ev):
        msg, sender = ev.message, ev.sender
        oid = getattr(sender.sender_id, 'open_id', None) if sender and sender.sender_id else None
        if getattr(sender, 'sender_type', 'user') != 'user' or not oid:
            return False
        if not self.owners():
            if msg.chat_type != 'p2p':
                return False
            self.c['owner_open_ids'] = [oid]
            jsave(CONF, self.c, 0o600)
            log.info('记下主人 open_id（首个私聊）')
        if oid not in self.owners():
            log.info('不是主人发的，忽略（chat_type=%s）', msg.chat_type)
            return False
        if msg.chat_type != 'p2p' and not (msg.mentions or []):
            return False          # 群里没 @它
        return True

    def handle(self, ev):
        msg = ev.message
        with _state_lock:
            s = state()
            if msg.message_id in s['seen']:
                return
            s['seen'].append(msg.message_id)
            save_state(s)
        if not self.accept(ev):
            return
        text, res = parse(msg)
        if not res and text.lower() in ('ping', '/ping', '在吗', '在吗?', '在吗？', '/状态'):
            n = len([d for d in os.listdir(INBOX) if not d.startswith('.')]) if os.path.isdir(INBOX) else 0
            reply(self.cli, msg.message_id, f'在。派活台{"在等件" if wait_running() else "这会儿没在等件"}，收件箱里还有 {n} 件没分拣。')
            return
        b = self.buf.setdefault(msg.chat_id, {'msgs': [], 'last': 0})
        b['msgs'].append({'id': msg.message_id, 'text': text, 'res': res, 'chat_type': msg.chat_type})
        b['last'] = time.time()
        log.info('收到 %s 一条（文字 %d 字、附件 %d 个），攒着', msg.message_type, len(text), len(res))

    def flush(self, chat_id, b):
        msgs = b['msgs']
        ts = datetime.now(timezone.utc)
        iid = ts.strftime('%Y%m%dT%H%M%S') + '-lark_' + msgs[-1]['id'][-8:]
        tmp = os.path.join(INBOX, '.tmp-' + iid)
        os.makedirs(os.path.join(tmp, 'files'), exist_ok=True)
        files, texts, n = [], [], 0
        for m in msgs:
            if m['text']:
                texts.append(m['text'])
            for key, kind, name in m['res']:
                n += 1
                f = download(self.cli, m['id'], key, kind, os.path.join(tmp, 'files'), n, name)
                if f:
                    files.append(f)
        last = msgs[-1]['id']
        item = {'via': 'lark', 'job': '', 'at': ts.isoformat().replace('+00:00', 'Z'), 'chat': '',
                'note': '', 'files': files, 'text': '\n'.join(texts),
                'lark': {'chat_id': chat_id, 'message_id': last, 'message_ids': [m['id'] for m in msgs]},
                'reply_cmd': f'ccpaihuo reply {iid} \'<一两句结果>\''}
        with open(os.path.join(tmp, 'item.json'), 'w') as f:
            json.dump(item, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(INBOX, iid))
        with _state_lock:
            s = state()
            s['items'][iid] = {'message_id': last, 'chat_id': chat_id, 'stage': 'queued', 'at': now_ms(),
                               'n_msgs': len(msgs), 'n_files': len(files)}
            save_state(s)
        log.info('写进收件箱 %s（%d 条消息、%d 个文件）', iid, len(msgs), len(files))
        what = f'{len(msgs)} 条' + (f'（含 {len(files)} 个文件）' if files else '')
        tail = '' if wait_running() else '（分拣台这会儿没在等件，件先放收件箱，它回来自动处理）'
        reply(self.cli, last, f'收到 {what}，交给派活台分拣了，副屏出卡后告诉你。{tail}')

    # 追每件走到哪一步，有变化就回一句
    def track(self):
        with _state_lock:
            s = state()
        changed = False
        for iid, it in list(s['items'].items()):
            if it['stage'] in ('delivered', 'failed', 'merged', 'expired'):
                continue
            say, prev = None, it['stage']
            if now_ms() - it['at'] > TRACK_DAYS * 86400_000:
                it['stage'] = 'expired'
            elif os.path.isdir(os.path.join(INBOX, '.failed', iid)):
                it['stage'], say = 'failed', '这件分拣失败了，原件在 ~/.route305/inbox/.failed/' + iid
            else:
                bdir = find_batch(iid, it)
                if bdir:
                    it['batch'] = os.path.basename(bdir)
                    card = jload(os.path.join(bdir, 'card.json'), {}) or {}
                    dl = jload(os.path.join(bdir, 'delivery.json'), {}) or {}
                    if dl.get('results'):
                        sent = [m for m in dl.get('messages', []) if m.get('to') in dl['results']]
                        it['stage'], it['delivered_at'], it['targets'] = 'delivered', now_ms(), [m['to'] for m in sent]
                        say = ('已投进' + '、'.join(f'『{m.get("title") or m["to"]}』' for m in sent)
                               + '。那边为这件推的决策卡也会弹到这里，办完会在这条下面回你。')
                    elif dl.get('action') == 'new_session':
                        it['stage'], say = 'delivered', f'要新开会话「{dl.get("title", "")}」办，分拣台在处理，桌面里可能要你点一下。'
                    elif card.get('id') and card.get('id') != it.get('card'):
                        it['card'], it['stage'] = card['id'], 'carded'
                        self.mirror.ensure(card['id'], iid, it['message_id'])
                        if not self.mirror.reg.get(card['id'], {}).get('msg'):   # 飞书卡片发不出去就退回一句文字
                            seq = re.search(r'编号 (\d+)', card.get('seq_line', ''))
                            say = f'副屏上有 1 道题（编号 {seq.group(1) if seq else "?"}），飞书卡片没发出去，请在副屏上点。'
                elif os.path.isdir(os.path.join(INBOX, '.done', iid)) and now_ms() - it['at'] > 90_000:
                    it['stage'], say = 'merged', '这件和副屏上一批还没定的内容相同，已经并进去了，不另出卡。'
            if it['stage'] != prev or say:
                changed = True
                if say:
                    reply(self.cli, it['message_id'], say)
                log.info('%s：%s → %s', iid, prev, it['stage'])
        if changed:
            with _state_lock:
                s2 = state()
                s2['items'].update({k: v for k, v in s['items'].items()})
                save_state(s2)

    # 投递之后接手会话推的卡也镜像进来；顺手把镜像过的卡和 decide 对一遍
    def follow(self):
        pending = {c['id']: c for c in self.cards.api('GET', '/api/cards?status=pending&limit=200').get('cards', [])}
        for f in glob.glob(os.path.join(STATE_DIR, 'mirror-req', '*')):   # ccpaihuo mirror <卡片> 手动要求的
            try:
                self.mirror.ensure(os.path.basename(f), '', None)
            finally:
                os.remove(f)
        with _state_lock:
            s = state()
        for iid, it in s['items'].items():
            if it.get('stage') != 'delivered' or not it.get('delivered_at'):
                continue
            if os.path.exists(os.path.join(STATE_DIR, 'done', iid)) or now_ms() - it['delivered_at'] > self.cards.FOLLOW_SECS * 1000:
                continue
            sess = self.sess_cache.setdefault(iid, [x for x in (self.cards.cli_id(t) for t in it.get('targets', [])) if x])
            for cid, c in pending.items():
                if c.get('session') in sess and c.get('created', 0) * 1000 >= it['delivered_at'] - 5000:
                    self.mirror.ensure(cid, iid, it['message_id'], c)
        self.mirror.refresh(pending)

    def worker(self):
        last_track = last_follow = 0
        self.sess_cache = {}
        while True:
            try:
                ev = self.q.get(timeout=1)
                self.handle(ev)
            except queue.Empty:
                pass
            except Exception:
                log.exception('处理消息出错')
            try:
                for chat_id, b in list(self.buf.items()):
                    if b['msgs'] and time.time() - b['last'] >= DEBOUNCE:
                        del self.buf[chat_id]
                        self.flush(chat_id, b)
                if time.time() - last_track >= 5:
                    last_track = time.time()
                    self.track()
            except Exception:
                log.exception('写件或追进度出错')
            try:
                if time.time() - last_follow >= 4:
                    last_follow = time.time()
                    self.follow()
            except Exception as e:
                log.warning('同步决策卡出错：%s', str(e)[:200])

    def run(self):
        import lark_oapi as lark
        os.makedirs(INBOX, exist_ok=True)
        threading.Thread(target=self.worker, daemon=True).start()
        h = (lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(self.on_message)
             .register_p2_card_action_trigger(self.on_card).build())
        ws = lark.ws.Client(self.c['app_id'], self.c['app_secret'], event_handler=h, log_level=lark.LogLevel.INFO)
        log.info('启动，连飞书长连接')
        ws.start()


def find_batch(iid, it):
    if it.get('batch'):
        d = os.path.join(BATCHES, it['batch'])
        if os.path.isdir(d):
            return d
    since = it['at'] / 1000 - 120
    for d in sorted(glob.glob(os.path.join(BATCHES, '*')), reverse=True)[:40]:
        fp = os.path.join(d, 'fingerprint.json')
        if os.path.exists(fp) and os.path.getmtime(fp) >= since and (jload(fp, {}) or {}).get('inbox') == iid:
            return d
    return None


def wait_running():
    import fcntl
    p = os.path.join(RHOME, 'wait.lock')
    if not os.path.exists(p):
        return False
    with open(p, 'a+') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
        except OSError:
            return True


class Redact(logging.Filter):
    """SDK 会打印带鉴权参数的长连接地址，抹掉问号后面的部分。"""
    def filter(self, rec):
        rec.msg = re.sub(r'(wss?://[^\s?]+)\?\S+', r'\1?…', str(rec.getMessage()))
        rec.args = ()
        return True


def setup_log(to_file):
    import lark_oapi  # noqa: F401  先导入，它会给 Lark 日志挂一个 stdout handler，下面换掉
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    h = logging.FileHandler(LOG) if to_file else logging.StreamHandler()
    h.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s', '%m-%d %H:%M:%S'))
    for name in ('ccpaihuo', 'Lark'):
        lg = logging.getLogger(name)
        lg.handlers[:] = [h]
        lg.filters[:] = [Redact()]     # 挂在 logger 上，任何 handler 之前先抹
        lg.setLevel(logging.INFO)
        lg.propagate = False


def cmd_reply(args, done=False):
    if len(args) < 2:
        sys.exit('用法：ccpaihuo reply|done <件号|飞书消息号> 文字')
    ref, text = args[0], ' '.join(args[1:])
    it = state()['items'].get(ref)
    mid = it['message_id'] if it else ref
    if not mid.startswith('om_'):
        sys.exit(f'找不到件 {ref}（ccpaihuo status 看最近的件号）')
    setup_log(False)
    ok = reply(client(), mid, text)
    if ok and done and it:
        os.makedirs(os.path.join(STATE_DIR, 'done'), exist_ok=True)
        open(os.path.join(STATE_DIR, 'done', ref), 'w').write(text)
    print(('已回飞书' + ('，这件标成办完，之后推的卡不再镜像' if done and it else '')) if ok else '回飞书失败，看上面的错误')
    sys.exit(0 if ok else 1)


def cmd_status(_):
    items = sorted(state()['items'].items(), key=lambda kv: kv[1]['at'], reverse=True)[:15]
    print(f'飞书桥 {"在跑" if launchd_pid() else "没在跑"}；route305 等件循环 {"在跑" if wait_running() else "没在跑"}')
    for iid, it in items:
        print(f'{sgt(it["at"])}  {iid}  {it["stage"]:<9} {it.get("batch", "")}  消息 {it.get("n_msgs", "?")} 条 / 文件 {it.get("n_files", 0)} 个')


def launchd_pid():
    r = subprocess.run(['launchctl', 'list', LABEL], capture_output=True, text=True)
    m = re.search(r'"PID" = (\d+)', r.stdout)
    return int(m.group(1)) if m else None


def cmd_doctor(_):
    import requests
    c = conf()
    st = os.stat(CONF).st_mode & 0o777
    print(f'凭据文件 {CONF} 权限 {oct(st)}' + ('' if st == 0o600 else '  ← 应为 600'))
    print('主人 open_id：' + ('已记下' if c.get('owner_open_ids') else '还没有（等第一条私聊）'))
    t0 = time.time()
    r = requests.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                      json={'app_id': c['app_id'], 'app_secret': c['app_secret']}, timeout=20).json()
    print(f'飞书鉴权：{"通过" if r.get("code") == 0 else "失败 " + str(r.get("code")) + " " + str(r.get("msg"))}（{time.time() - t0:.1f} 秒）')
    pid = launchd_pid()
    print(f'常驻进程：{"pid " + str(pid) if pid else "没在跑"}')
    print(f'route305 等件循环：{"在跑" if wait_running() else "没在跑（分拣台会重挂）"}')
    tail = subprocess.run(['tail', '-n', '200', LOG], capture_output=True, text=True).stdout if os.path.exists(LOG) else ''
    conn = [l for l in tail.splitlines() if 'connected to' in l or 'disconnected' in l or 'connect failed' in l]
    print('长连接最近一条：' + (conn[-1] if conn else '日志里还没有'))


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else 'status'
    if cmd == 'run':
        setup_log(True)
        Bridge().run()
    elif cmd == 'reply':
        cmd_reply(a[1:])
    elif cmd == 'done':
        cmd_reply(a[1:], done=True)
    elif cmd == 'send':
        setup_log(False)
        print('已发' if send_owner(client(), ' '.join(a[1:])) else '发送失败，看上面的错误')
    elif cmd == 'mirror':
        os.makedirs(os.path.join(STATE_DIR, 'mirror-req'), exist_ok=True)
        for ref in a[1:]:
            cid = ref.lstrip('#')
            if cid.isdigit():
                import cards
                cid = (cards.api('GET', f'/api/cards?seq={int(cid)}&limit=1').get('cards') or [{}])[0].get('id') or ''
            if not cid:
                sys.exit(f'没有卡片 {ref}')
            open(os.path.join(STATE_DIR, 'mirror-req', cid), 'w').close()
            print(f'已排队：{ref} 几秒内弹到飞书私聊')
    elif cmd == 'status':
        cmd_status(a[1:])
    elif cmd == 'doctor':
        cmd_doctor(a[1:])
    elif cmd == 'restart':
        subprocess.run(['launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/{LABEL}'])
        print('已重启')
    elif cmd == 'logs':
        os.execvp('tail', ['tail', '-n', a[1] if len(a) > 1 else '40', LOG])
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
