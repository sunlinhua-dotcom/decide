"""decide 卡片 ⇄ 飞书卡片：把飞书派的活相关的决策卡镜像进飞书，两边谁先点都算，另一边跟着改。

镜像范围：飞书派来的那件的分拣卡，加上接手会话投递之后推的卡，
直到它 `ccpaihuo done` 说办完，最多 24 小时。答案只记在 decide：飞书点了就 POST 到 decide（不带 ack，
推卡的会话照常从 listen / wait 收到），板上点了就把飞书卡片改成「已定」。
"""
import glob
import importlib.machinery
import importlib.util
import json
import logging
import os
import shutil
import threading
import time

DECIDE_BIN = os.environ.get('CCPAIHUO_DECIDE_BIN') or shutil.which('decide') or 'decide'   # decide CLI 的文件路径（当模块载入）
SESS_META = os.path.expanduser('~/Library/Application Support/Claude/claude-code-sessions')
FOLLOW_SECS = 24 * 3600

log = logging.getLogger('ccpaihuo')
_dm = None


def dmod():
    """直接借 decide 命令自己的鉴权与 http，不另存密钥。"""
    global _dm
    if _dm is None:
        loader = importlib.machinery.SourceFileLoader('decide_cli', DECIDE_BIN)
        spec = importlib.util.spec_from_loader('decide_cli', loader)
        _dm = importlib.util.module_from_spec(spec)
        loader.exec_module(_dm)
    return _dm


def api(method, path, body=None, timeout=20):
    d = dmod()
    return d.http(d.conf(), method, path, body, timeout=timeout)


def board_url(card):
    return f'{dmod().conf()["base"]}/p/{card.get("project", "")}#c-{card["id"]}'


def cli_id(local_id):
    """桌面 app 的 local_ 会话号 → decide 卡片上记的 CLI 会话号。"""
    for f in glob.glob(os.path.join(SESS_META, '*', '*', f'{local_id}.json')):
        try:
            v = json.load(open(f)).get('cliSessionId')
            if v:
                return v
        except Exception:
            pass
    return None


def cut(s, n):
    s = ' '.join(str(s or '').split())
    return s if len(s) <= n else s[:n - 1] + '…'


def _pt(s):
    return {'tag': 'plain_text', 'content': s}


def render(card):
    """decide 卡片 → 飞书卡片 JSON 2.0。待定时带按钮和备注框，定了以后只剩结果。"""
    st, ans = card.get('status'), card.get('answer') or {}
    opts = card.get('options') or []
    if st == 'pending':
        tpl = 'blue'
    elif st == 'cancelled':
        tpl = 'grey'
    elif ans.get('chat'):
        tpl = 'orange'
    else:
        tpl = 'green'
    seq = card.get('seq')
    header = {'title': _pt(cut((f'{seq} · ' if seq else '') + card.get('title', ''), 120)), 'template': tpl}
    if card.get('sub'):
        header['subtitle'] = _pt(cut(card['sub'], 120))
    md = []
    for k, v in (card.get('facts') or [])[:8]:
        md.append(f'**{k}**：{cut(v, 300)}')
    if st == 'pending':
        for i, o in enumerate(opts):
            rec = " <font color='red'>**推荐**</font>" if o.get('rec') else ''
            md.append(f'**{i + 1}. {cut(o.get("label"), 60)}**{rec}' + (f"\n<font color='grey'>{cut(o.get('desc'), 160)}</font>" if o.get('desc') else ''))
    else:
        if st == 'cancelled':
            md.append('**这张卡已撤销。**')
        elif ans.get('chat'):
            md.append('**改在对话里说了。**')
        else:
            when = time.strftime('%m-%d %H:%M', time.localtime(ans.get('at') or time.time()))
            md.append(f"**已定：{'、'.join(ans.get('labels') or []) or '—'}**（{ans.get('device') or '—'} · {when}）")
            if ans.get('note'):
                md.append(f'意见：{cut(ans["note"], 600)}')
    links = [f'[{cut(l.get("t") or l.get("url"), 40)}]({l["url"]})' for l in (card.get('links') or []) if l.get('url')]
    nm = len(card.get('media') or [])
    links.append(f'[在决策板上打开{f"（有 {nm} 张图）" if nm else ""}]({board_url(card)})')
    md.append(' · '.join(links))
    els = [{'tag': 'markdown', 'content': '\n\n'.join(md)}]
    if st == 'pending':
        form = []
        if card.get('note', True):
            form.append({'tag': 'input', 'name': 'note', 'width': 'fill', 'max_length': 1000,
                         'placeholder': _pt(cut(card.get('note_hint') or '备注（可不填）', 60))})
        if card.get('multi'):
            form.append({'tag': 'multi_select_static', 'name': 'pick', 'width': 'fill', 'placeholder': _pt('选一个或几个'),
                         'options': [{'text': _pt(cut(o.get('label'), 40)), 'value': o['id']} for o in opts]})
            form.append({'tag': 'button', 'name': 'multi', 'text': _pt('提交'), 'type': 'primary_filled',
                         'width': 'fill', 'form_action_type': 'submit'})
        else:
            for i, o in enumerate(opts):
                form.append({'tag': 'button', 'name': f'o_{i}', 'text': _pt(cut(f'{i + 1}. {o.get("label")}', 40)),
                             'type': 'primary_filled' if o.get('rec') else 'default', 'width': 'fill',
                             'form_action_type': 'submit'})
        if card.get('note', True):
            form.append({'tag': 'button', 'name': 'note_only', 'text': _pt('不选，只写意见'), 'type': 'text',
                         'width': 'fill', 'form_action_type': 'submit'})
        els.append({'tag': 'form', 'name': 'f', 'elements': form})
    return {'schema': '2.0', 'config': {'update_multi': True, 'width_mode': 'fill'}, 'header': header,
            'body': {'elements': els}}


class Mirror:
    """registry：decide 卡片 id → {飞书消息号, 件号, rev, status}。只由飞书桥一个进程写。"""

    def __init__(self, path, sender, patcher):
        self.path, self.send, self.patch = path, sender, patcher
        self.lock = threading.Lock()
        try:
            self.reg = json.load(open(path))
        except Exception:
            self.reg = {}
        self.by_msg = {v['msg']: k for k, v in self.reg.items() if v.get('msg')}

    def save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.reg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def ensure(self, card_id, item_id, reply_to, card=None):
        """没镜像过就发一张飞书卡片，回在那件的原消息下面。"""
        if card_id in self.reg:
            return
        card = card or api('GET', f'/api/cards/{card_id}')
        mid = self.send(reply_to, render(card))
        with self.lock:
            self.reg[card_id] = {'msg': mid, 'item': item_id, 'rev': card.get('rev'), 'status': card.get('status'),
                                 'at': time.time()}
            if mid:
                self.by_msg[mid] = card_id
            self.save()
        log.info('镜像卡片 %s（编号 %s）→ 飞书 %s', card_id, card.get('seq'), 'ok' if mid else '发送失败')

    def refresh(self, pending):
        """pending：decide 当前待定卡 {id: card}。镜像过的卡 rev 变了（板上定了 / 撤销 / 改了 / 撤回）就改飞书卡片。"""
        for cid, v in list(self.reg.items()):
            if not v.get('msg') or time.time() - v.get('at', 0) > 3 * 86400:
                continue
            card = pending.get(cid)
            if card is None and v.get('status') != 'pending':
                continue
            if card is None:
                try:
                    card = api('GET', f'/api/cards/{cid}')
                except Exception as e:
                    log.warning('读卡片 %s 失败：%s', cid, e)
                    continue
            if card.get('rev') != v.get('rev'):
                ok = self.patch(v['msg'], render(card))
                log.info('同步卡片 %s：%s → %s（飞书 %s）', cid, v.get('status'), card.get('status'), 'ok' if ok else '失败')
                with self.lock:
                    v.update(rev=card.get('rev'), status=card.get('status'))
                    self.save()

    def answer(self, msg_id, action_name, form):
        """飞书里点了按钮 → 记到 decide。返回 (toast 文案, 新卡片 JSON 或 None, 成功否)。"""
        cid = self.by_msg.get(msg_id)
        if not cid:
            return '这张卡片对不上决策板了', None, False
        card = api('GET', f'/api/cards/{cid}', timeout=5)
        if card.get('status') != 'pending':
            return '这张已经定过或撤销了', render(card), False
        opts = card.get('options') or []
        note = (form.get('note') or '').strip()
        if action_name == 'note_only':
            if not note:
                return '先在备注框里写意见', None, False
            choice = ['note']
        elif action_name == 'multi':
            choice = [x for x in (form.get('pick') or []) if any(o['id'] == x for o in opts)]
            if not choice:
                return '先选一个', None, False
        elif action_name.startswith('o_') and action_name[2:].isdigit() and int(action_name[2:]) < len(opts):
            choice = [opts[int(action_name[2:])]['id']]
        else:
            return '按钮对不上', None, False
        r = api('POST', f'/api/cards/{cid}/answer', {'choice': choice, 'note': note, 'device': '飞书'}, timeout=5)
        with self.lock:
            v = self.reg[cid]
            v.update(rev=r.get('rev'), status=r.get('status'))
            self.save()
        labels = '、'.join((r.get('answer') or {}).get('labels') or [])
        log.info('飞书里定了卡片 %s：%s', cid, labels)
        return f'已记下：{labels}', render(r), True
