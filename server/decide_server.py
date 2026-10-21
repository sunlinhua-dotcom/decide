#!/usr/bin/env python3
"""decide 决策板后端。

存卡片、记答案、长轮询推送；默认也直接出网页和媒体文件（带 Range），一个进程就能用。
纯标准库，SQLite 一个文件。前面挂 Caddy / nginx 做 HTTPS 时，见 deploy/ 里的示例。
所有配置走环境变量，见 .env.example。
"""
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = os.environ.get("DECIDE_HOST", "127.0.0.1")
PORT = int(os.environ.get("DECIDE_PORT", "8796"))
HOME = os.path.abspath(os.path.expanduser(os.environ.get("DECIDE_HOME", os.path.join(ROOT, "data"))))
DB_PATH = os.environ.get("DECIDE_DB", os.path.join(HOME, "decide.db"))
KEY_FILE = os.environ.get("DECIDE_KEY_FILE", os.path.join(HOME, "key"))
PASS_FILE = os.environ.get("DECIDE_PASS_FILE", os.path.join(HOME, "password"))
MEDIA_ROOT = os.path.abspath(os.path.expanduser(os.environ.get("DECIDE_MEDIA", os.path.join(HOME, "media"))))
WEB_ROOT = os.path.abspath(os.environ.get("DECIDE_WEB", os.path.join(ROOT, "web")))
_local = HOST if HOST not in ("0.0.0.0", "::", "") else "127.0.0.1"
PUBLIC_BASE = os.environ.get("DECIDE_PUBLIC", f"http://{_local}:{PORT}").rstrip("/")
SERVE_STATIC = os.environ.get("DECIDE_SERVE_STATIC", "1") != "0"  # 0 = 网页和媒体交给反代出
TRUST_PROXY = os.environ.get("DECIDE_TRUST_PROXY", "0") == "1"    # 1 = 信任反代给的 CF-Connecting-IP / X-Forwarded-For
COOKIE = "decide_k"
COOKIE_MAX_AGE = 400 * 24 * 3600
COOKIE_SECURE = os.environ.get("DECIDE_COOKIE_SECURE", "1" if PUBLIC_BASE.startswith("https://") else "0") == "1"
MAX_UPLOAD = 300 * 1024 * 1024
PUBLIC_FILES = {"/icon.svg", "/icon-180.png", "/icon-512.png", "/manifest.webmanifest", "/robots.txt"}

os.makedirs(HOME, exist_ok=True)
# 密钥：命令行和网页 cookie 用的凭据。DECIDE_KEY 优先；没有就读 key 文件；第一次启动自动生成。
KEY = os.environ.get("DECIDE_KEY", "").strip()
KEY_CREATED = False
if not KEY:
    if not os.path.exists(KEY_FILE):
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, (secrets.token_hex(16) + "\n").encode())
        os.close(fd)
        KEY_CREATED = True
    with open(KEY_FILE) as f:
        KEY = f.read().strip()
if len(KEY) < 24:
    sys.exit("decide：密钥太短，至少 24 个字符")
# 网页登录密码（可选）：设了就能在手机上只输密码登录；不设就用密钥登录（decide open 会自动带上）。
PASSWORD = os.environ.get("DECIDE_PASSWORD", "").strip()
if not PASSWORD and os.path.exists(PASS_FILE):
    with open(PASS_FILE) as f:
        PASSWORD = f.read().strip()
FAILS = {}            # ip -> [失败时间]
FAIL_WINDOW, FAIL_PER_IP, FAIL_GLOBAL = 600, 5, 40

PALETTE = ["#2f6fdf", "#0f9d8a", "#c2410c", "#7c3aed", "#be185d",
           "#15803d", "#b45309", "#0369a1", "#9f1239", "#4d7c0f"]
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ID_RE = re.compile(r"^[a-z0-9]{4,16}$")
OPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
KINDS = {"review", "choose", "ask", "perm"}  # perm：本机 Claude 钩子在等的授权 / 提问 / 计划 / 开新会话
STYLES = {"", "good", "bad"}

lock = threading.RLock()
cond = threading.Condition(lock)
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS projects(
  slug TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cards(
  id TEXT PRIMARY KEY, project TEXT NOT NULL, status TEXT NOT NULL,
  created REAL NOT NULL, updated REAL NOT NULL, rev INTEGER NOT NULL,
  spec TEXT NOT NULL, answer TEXT, source TEXT NOT NULL DEFAULT '',
  batch TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS cards_project ON cards(project);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS intakes(
  id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL, status TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '', chat TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  files TEXT NOT NULL DEFAULT '[]', ref TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '');
""")
cols = {r["name"] for r in db.execute("PRAGMA table_info(cards)")}
for col, ddl in (("seq", "INTEGER"), ("session", "TEXT NOT NULL DEFAULT ''"), ("acked", "INTEGER NOT NULL DEFAULT 0"),
                 ("acked_at", "REAL")):
    if col not in cols:
        db.execute(f"ALTER TABLE cards ADD COLUMN {col} {ddl}")
if db.execute("SELECT 1 FROM cards WHERE seq IS NULL LIMIT 1").fetchone():
    n = 0
    for r in db.execute("SELECT id FROM cards ORDER BY created").fetchall():
        n += 1
        db.execute("UPDATE cards SET seq=? WHERE id=?", (n, r["id"]))
    db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('seq',?)", (str(n),))
db.commit()
row = db.execute("SELECT v FROM meta WHERE k='ver'").fetchone()
VER = [int(row["v"]) if row else 0]
LISTENERS = {}  # session -> 最近一次 /api/listen 的时间
WAITERS = {}  # 卡片 id -> 最近一次 decide wait 来查它的时间
LIVE = 120  # 这么多秒内来过算「有人在听」（listen / wait 最长 55 秒一轮）


class Bad(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
        self.msg = msg


def bump():
    """改了任何东西都调用：版本号 +1，叫醒所有长轮询。调用方须持有 lock。"""
    VER[0] += 1
    db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('ver',?)", (str(VER[0]),))
    db.commit()
    cond.notify_all()


def s(v, n, field):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise Bad(400, f"{field} 必须是字符串")
    if len(v) > n:
        raise Bad(400, f"{field} 超过 {n} 字")
    return v


def safe_join(base, rel):
    """base 下的真实路径；跳出 base（../、软链接）返回 None。"""
    base = os.path.realpath(base)
    f = os.path.realpath(os.path.join(base, rel))
    return f if f == base or f.startswith(base + os.sep) else None


mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


def media_path_ok(p):
    return isinstance(p, str) and p.startswith("/media/") and ".." not in p and len(p) < 300


def clean_pic(m):
    """选项卡上的配图：{src, thumb?, w?, h?}，路径只许 /media/ 下。"""
    if not isinstance(m, dict) or not media_path_ok(m.get("src")):
        return None
    pic = {"src": m["src"]}
    if m.get("thumb") and media_path_ok(m["thumb"]):
        pic["thumb"] = m["thumb"]
    for dim in ("w", "h"):
        if isinstance(m.get(dim), int):
            pic[dim] = m[dim]
    return pic


def clean_plan(raw):
    """方案图拆出来的结构：标题 + 若干小节（标题、颜色、几行字、配图），网页用原生文字排，字号清楚。"""
    if not isinstance(raw, dict):
        return None
    secs = raw.get("secs") or []
    lines = raw.get("lines") or []
    if not isinstance(secs, list) or len(secs) > 12 or not isinstance(lines, list) or len(lines) > 40:
        raise Bad(400, "plan 至多 12 节、40 行")
    plan = {"title": s(raw.get("title"), 200, "plan.title"), "sub": s(raw.get("sub"), 600, "plan.sub"),
            "lines": [s(x, 400, "plan.lines") for x in lines if isinstance(x, str)],
            "foot": [s(x, 400, "plan.foot") for x in (raw.get("foot") or [])[:8] if isinstance(x, str)], "secs": []}
    for x in secs:
        if not isinstance(x, dict):
            raise Bad(400, "plan.secs 每项是对象")
        sec = {"t": s(x.get("t"), 80, "plan.t"),
               "lines": [s(y, 400, "plan.line") for y in (x.get("lines") or [])[:16] if isinstance(y, str)]}
        color = x.get("color")
        if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{3,8}", color):
            sec["color"] = color
        if x.get("gen"):
            sec["gen"] = True
        pic = clean_pic(x.get("pic"))
        if pic:
            sec["pic"] = pic
        plan["secs"].append(sec)
    return plan if plan["secs"] or plan["lines"] else None


def clean_links(raw):
    """卡上可点的链接（完成卡里做完的站点、版本页等）：[{t, url}]，只收 http / https。"""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > 12:
        raise Bad(400, "links 至多 12 条")
    out = []
    for x in raw:
        if not isinstance(x, dict):
            raise Bad(400, "links 每条是 {t, url}")
        url = s(x.get("url"), 2000, "link.url").strip()
        if not re.match(r"https?://[^\s<>\"']+$", url):
            raise Bad(400, "link.url 只能是 http(s) 网址")
        if url not in {o["url"] for o in out}:
            out.append({"t": s(x.get("t"), 60, "link.t").strip(), "url": url})
    return out


def clean_spec(raw):
    if not isinstance(raw, dict):
        raise Bad(400, "卡片必须是 JSON 对象")
    kind = raw.get("kind") or "review"
    if kind not in KINDS:
        raise Bad(400, "kind 只能是 review / choose / ask / perm")
    spec = {
        "kind": kind,
        "title": s(raw.get("title"), 200, "title").strip(),
        "sub": s(raw.get("sub"), 600, "sub"),
        "multi": bool(raw.get("multi")),
        "note": raw.get("note", True) is not False,
        "note_hint": s(raw.get("note_hint"), 120, "note_hint"),
        "priority": int(raw.get("priority") or 0),
        # 等宽文本框：完整命令、改动摘要、计划全文（太长的网页上折叠）
        "code": s(raw.get("code"), 60000, "code"),
    }
    exp = raw.get("expires")
    if isinstance(exp, (int, float)) and exp > 0:
        spec["expires"] = float(exp)
    if not spec["title"]:
        raise Bad(400, "缺 title")
    facts = raw.get("facts") or []
    if not isinstance(facts, list) or len(facts) > 24:
        raise Bad(400, "facts 至多 24 条")
    spec["facts"] = []
    for it in facts:
        if isinstance(it, dict):
            k, v = it.get("k"), it.get("v")
        elif isinstance(it, (list, tuple)) and len(it) == 2:
            k, v = it
        else:
            raise Bad(400, "facts 每条是 [键, 值]")
        spec["facts"].append([s(k, 40, "fact 键"), s(v, 2000, "fact 值")])
    media = raw.get("media") or []
    if not isinstance(media, list) or len(media) > 16:
        raise Bad(400, "media 至多 16 个")
    spec["media"] = []
    for m in media:
        if not isinstance(m, dict) or not media_path_ok(m.get("src")):
            raise Bad(400, "media.src 必须是 /media/ 开头的路径")
        mk = m.get("kind") or "img"
        if mk not in ("img", "video"):
            raise Bad(400, "media.kind 只能是 img / video")
        item = {"kind": mk, "src": m["src"],
                "label": s(m.get("label"), 40, "media.label"),
                "caption": s(m.get("caption"), 400, "media.caption")}
        for extra in ("thumb", "orig", "poster"):
            if m.get(extra):
                if not media_path_ok(m[extra]):
                    raise Bad(400, f"media.{extra} 路径不对")
                item[extra] = m[extra]
        for dim in ("w", "h"):
            if isinstance(m.get(dim), int):
                item[dim] = m[dim]
        if m.get("role") == "src":
            item["role"] = "src"  # 已拆成 plan 的原图：不当示意图排，只在「看原图」里打开
        spec["media"].append(item)
    opts = raw.get("options") or []
    if not opts:
        opts = [{"id": "ok", "label": "通过", "style": "good"},
                {"id": "redo", "label": "返修", "style": "bad"}]
    if not isinstance(opts, list) or len(opts) > 12:
        raise Bad(400, "options 至多 12 个")
    seen = set()
    spec["options"] = []
    for o in opts:
        if not isinstance(o, dict):
            raise Bad(400, "options 每项是对象")
        oid = o.get("id")
        if not isinstance(oid, str) or not OPT_ID_RE.match(oid) or oid in seen:
            raise Bad(400, f"option id 不合法或重复：{oid}")
        seen.add(oid)
        style = o.get("style") or ""
        if style not in STYLES:
            raise Bad(400, "option style 只能是 good / bad / 空")
        item = {"id": oid, "label": s(o.get("label"), 80, "option.label") or oid,
                "desc": s(o.get("desc"), 400, "option.desc"), "style": style}
        if isinstance(o.get("media"), int) and 0 <= o["media"] < len(spec["media"]):
            item["media"] = o["media"]
        if o.get("rec"):
            item["rec"] = True
        if o.get("gen"):
            item["gen"] = True  # 后台正在出这个选项的配图，网页先画占位
        pic = clean_pic(o.get("pic"))
        if pic:
            item["pic"] = pic
        spec["options"].append(item)
    plan = clean_plan(raw.get("plan"))
    if plan:
        spec["plan"] = plan
    links = clean_links(raw.get("links"))
    if links:
        spec["links"] = links
    return spec


def ensure_project(slug, name=None):
    r = db.execute("SELECT slug FROM projects WHERE slug=?", (slug,)).fetchone()
    now = time.time()
    if r:
        if name:
            db.execute("UPDATE projects SET name=?, updated=? WHERE slug=?", (name, now, slug))
        else:
            db.execute("UPDATE projects SET updated=? WHERE slug=?", (now, slug))
        return
    color = PALETTE[sum(slug.encode()) % len(PALETTE)]
    db.execute("INSERT INTO projects(slug,name,color,created,updated) VALUES(?,?,?,?,?)",
               (slug, name or slug, color, now, now))


def card_json(r):
    d = json.loads(r["spec"])
    d.update({"id": r["id"], "project": r["project"], "status": r["status"],
              "created": r["created"], "updated": r["updated"], "rev": r["rev"],
              "source": r["source"], "batch": r["batch"], "seq": r["seq"],
              "session": r["session"], "acked": bool(r["acked"]),
              "answer": json.loads(r["answer"]) if r["answer"] else None})
    return d


def project_rows():
    out = []
    for p in db.execute("SELECT * FROM projects ORDER BY updated DESC"):
        c = db.execute("SELECT SUM(status='pending') AS pending, SUM(status='answered') AS answered,"
                       " MAX(updated) AS last FROM cards WHERE project=?", (p["slug"],)).fetchone()
        out.append({"slug": p["slug"], "name": p["name"], "color": p["color"], "note": p["note"],
                    "archived": bool(p["archived"]), "created": p["created"],
                    "pending": c["pending"] or 0, "answered": c["answered"] or 0,
                    "last": c["last"] or p["updated"]})
    return out


def get_card(cid):
    r = db.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    if not r:
        raise Bad(404, "没有这张卡")
    return r


# ---------------- 派活入口（10-20）：网页上交文字 + 附件，转给分拣台 ----------------
INTAKE_CONF = os.environ.get("DECIDE_INTAKE_CONF", os.path.join(HOME, "intake.json"))
INTAKE_UP = os.environ.get("DECIDE_INTAKE_UP", os.path.join(HOME, "intake-up"))
INTAKE_MAX = 95 * 1024 * 1024  # 浏览器经 Cloudflare 上传，免费档单个请求上限 100 MB
INTAKE_FILES = 30
FID_RE = re.compile(r"^u[0-9a-f]{16}$")
IK_RE = re.compile(r"^[0-9a-f]{16}$")


def intake_conf():
    """每次现读，改配置不用重启。两种去处：
    {"gateway": "http://…", "token": "…", "capability": "route.intake"}：交给任务网关（POST /v1/files 传附件、POST /v1/jobs 交件，见 README）；
    {"dir": "/path"}：直接写进本机目录，每件一个子目录（item.json + files/）。都没有就不开放派活入口。"""
    try:
        with open(INTAKE_CONF) as f:
            c = json.load(f)
    except (OSError, ValueError):
        return None
    if (c.get("gateway") and c.get("token")) or c.get("dir"):
        return c
    return None


def safe_name(name, i=0):
    b = os.path.basename(str(name or "")).strip()
    b = re.sub(r"[\x00-\x1f/\\]", "_", b).lstrip(".")[:120]
    return b or f"file_{i + 1}"


def intake_json(r):
    return {"id": r["id"], "created": r["created"], "updated": r["updated"], "status": r["status"],
            "text": r["text"][:300], "chat": r["chat"], "note": r["note"],
            "files": [{"name": f["name"], "size": f["size"]} for f in json.loads(r["files"] or "[]")],
            "ref": r["ref"], "error": r["error"]}


def sweep_intake_up():
    """浏览器传上来、两天还没交出去的附件清掉。"""
    try:
        cut = time.time() - 2 * 86400
        for n in os.listdir(INTAKE_UP):
            fp = os.path.join(INTAKE_UP, n)
            if os.path.getmtime(fp) < cut:
                os.remove(fp)
    except OSError:
        pass


def gw_call(c, method, path, body=None, data=None, headers=None, timeout=60):
    h = {"Authorization": "Bearer " + c["token"], "User-Agent": "decide-intake/1"}
    h.update(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(c["gateway"].rstrip("/") + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error") or str(e)
        except Exception:  # noqa: BLE001
            msg = str(e)
        raise RuntimeError(f"网关返回 {e.code}：{msg}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"连不上网关：{e}")


def intake_forward(c, row):
    """返回 (status, ref)：queued = 已进分拣台收件箱（ref 是件号）；submitted = 网关受理了还没跑完（ref 是任务号）。失败抛 RuntimeError。"""
    files = json.loads(row["files"] or "[]")
    params = {k: row[k] for k in ("text", "chat", "note") if row[k]}
    if c.get("gateway"):
        fids = []
        for f in files:
            fp = os.path.join(INTAKE_UP, f["id"])
            if not os.path.exists(fp):
                raise RuntimeError(f"附件 {f['name']} 已过期，请重新添加")
            with open(fp, "rb") as fh:
                r = gw_call(c, "POST", "/v1/files", data=fh, timeout=600,
                            headers={"X-Filename": quote(f["name"]), "Content-Type": "application/octet-stream",
                                     "Content-Length": str(os.path.getsize(fp))})
            fids.append(r["file_id"])
        job = gw_call(c, "POST", "/v1/jobs", {"capability": c.get("capability") or "route.intake", "params": params,
                                              "inputs": {"files": fids} if fids else {},
                                              "idempotency_key": "decide-" + row["id"]})
        jid = job.get("job_id") or ""
        end = time.time() + 40
        while True:
            st = job.get("status")
            if st == "done":
                res = job.get("result") or {}
                return "queued", str(res.get("inbox_id") or jid)
            if st in ("failed", "rejected", "expired", "cancelled", "interrupted"):
                raise RuntimeError(f"网关任务{st}：{job.get('error') or job.get('hint') or ''}".strip("："))
            if time.time() > end or not jid:
                return "submitted", jid
            job = gw_call(c, "GET", f"/v1/jobs/{jid}?wait=15", timeout=30)
    d = c["dir"]
    os.makedirs(d, exist_ok=True)
    name = time.strftime("%Y%m%dT%H%M%S") + "-" + row["id"]
    tmp, dst = os.path.join(d, ".tmp-" + name), os.path.join(d, name)
    os.makedirs(os.path.join(tmp, "files"), exist_ok=True)
    names = []
    for i, f in enumerate(files):
        fp = os.path.join(INTAKE_UP, f["id"])
        if not os.path.exists(fp):
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"附件 {f['name']} 已过期，请重新添加")
        n = f"{i + 1:02d}-{safe_name(f['name'], i)}"
        shutil.copyfile(fp, os.path.join(tmp, "files", n))
        names.append(n)
    with open(os.path.join(tmp, "item.json"), "w", encoding="utf-8") as fh:
        json.dump({"via": "decide", "job": row["id"], "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "chat": row["chat"],
                   "text": row["text"], "note": row["note"], "files": names}, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)  # 整件写完再改名，收件方不会读到半截
    return "queued", name


def intake_run(c, iid):
    """后台线程里转发一件；附件只在交出去以后才删，失败留着好重试。"""
    with lock:
        row = db.execute("SELECT * FROM intakes WHERE id=?", (iid,)).fetchone()
    try:
        status, ref = intake_forward(c, row)
        err = ""
    except Exception as e:  # noqa: BLE001
        status, ref, err = "failed", "", str(e)[:300]
    with lock:
        db.execute("UPDATE intakes SET status=?, ref=?, error=?, updated=? WHERE id=?", (status, ref, err, time.time(), iid))
    if status != "failed":
        for f in json.loads(row["files"] or "[]"):
            for ext in ("", ".json"):
                try:
                    os.remove(os.path.join(INTAKE_UP, f["id"] + ext))
                except OSError:
                    pass


INTAKE_CHECKING = set()


def intake_check(c, iid, jid):
    try:
        job = gw_call(c, "GET", f"/v1/jobs/{jid}", timeout=15)
        st = job.get("status")
        if st == "done":
            upd = ("queued", str((job.get("result") or {}).get("inbox_id") or jid), "")
        elif st in ("failed", "rejected", "expired", "cancelled", "interrupted"):
            upd = ("failed", jid, f"网关任务{st}：{job.get('error') or ''}".rstrip("："))
        else:
            upd = None
        if upd:
            with lock:
                db.execute("UPDATE intakes SET status=?, ref=?, error=?, updated=? WHERE id=? AND status='submitted'",
                           upd + (time.time(), iid))
    except Exception:  # noqa: BLE001
        pass
    finally:
        INTAKE_CHECKING.discard(iid)


def intake_refresh(c):
    """看列表时顺手收尾：网关还在跑的去问一下；服务重启丢了线程、一直卡在「交件中」的标成失败。"""
    now = time.time()
    with lock:
        db.execute("UPDATE intakes SET status='failed', error='交件中断（服务重启），请重试', updated=? "
                   "WHERE status='sending' AND updated<?", (now, now - 900))
        rows = db.execute("SELECT id, ref FROM intakes WHERE status='submitted' AND ref!='' AND created>?",
                          (now - 7 * 86400,)).fetchall()
    if not (c and c.get("gateway")):
        return
    for r in rows:
        if r["id"] not in INTAKE_CHECKING:
            INTAKE_CHECKING.add(r["id"])
            threading.Thread(target=intake_check, args=(c, r["id"], r["ref"]), daemon=True).start()


def sweep_expired():
    """钩子被强杀时卡片没人撤：过了 expires 的待定卡自动撤销。调用方持锁。"""
    now = time.time()
    hit = False
    for r in db.execute("SELECT id, spec FROM cards WHERE status='pending' AND spec LIKE '%\"expires\"%'").fetchall():
        exp = json.loads(r["spec"]).get("expires")
        if isinstance(exp, (int, float)) and exp < now:
            db.execute("UPDATE cards SET status='cancelled', updated=?, rev=rev+1, acked=1 WHERE id=?", (now, r["id"]))
            hit = True
    if hit:
        bump()


class H(BaseHTTPRequestHandler):
    server_version = "decide/1"

    def log_message(self, fmt, *args):
        if "/api/poll" in self.path or "/api/listen" in self.path:
            return
        sys.stderr.write("%s %s\n" % (self.ip(), fmt % args))

    def ip(self):
        if TRUST_PROXY:
            fwd = self.headers.get("CF-Connecting-IP") or (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if fwd:
                return fwd
        return self.client_address[0]

    # ---------- 工具 ----------
    def send(self, code, obj=None, headers=None):
        body = json.dumps(obj if obj is not None else {}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2 * 1024 * 1024:
            raise Bad(413, "请求太大")
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise Bad(400, "不是合法 JSON")

    def authed(self):
        k = self.headers.get("X-Decide-Key") or ""
        if k and hmac.compare_digest(k, KEY):
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, val = part.strip().partition("=")
            if name == COOKIE and hmac.compare_digest(val, KEY):
                return True
        return False

    def need_auth(self):
        if not self.authed():
            raise Bad(401, "未登录")

    def q(self):
        return {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}

    def route(self, method):
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            self.dispatch(method, path)
        except Bad as e:
            self.send(e.code, {"error": e.msg})
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"ERR {method} {path}: {e!r}\n")
            self.send(500, {"error": "服务内部错误"})

    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("HEAD")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    # ---------- 路由 ----------
    def dispatch(self, m, path):
        if path == "/api/health":
            return self.send(200, {"ok": True, "ver": VER[0]})
        if path == "/login" and m == "POST":
            return self.login()
        if SERVE_STATIC and m in ("GET", "HEAD") and path != "/api" and not path.startswith("/api/"):
            return self.static(path)
        self.need_auth()
        parts = path.split("/")[1:]  # ['api', ...]
        if parts[:1] != ["api"]:
            raise Bad(404, "没有这个地址")
        p = parts[1:]
        if m == "GET" and p == ["state"]:
            return self.state()
        if m == "GET" and p == ["poll"]:
            return self.poll()
        if m == "GET" and p == ["listen"]:
            return self.listen()
        if m == "POST" and p == ["chat"]:
            return self.to_chat()
        if m == "POST" and p == ["ack"]:
            return self.ack()
        if m == "GET" and p == ["session"]:
            return self.session_state()
        if p == ["intake"] and m in ("GET", "POST"):
            return self.intake_info() if m == "GET" else self.intake_submit()
        if m == "POST" and p == ["intake", "file"]:
            return self.intake_file()
        if m == "POST" and len(p) == 3 and p[0] == "intake" and IK_RE.match(p[1]) and p[2] == "retry":
            return self.intake_retry(p[1])
        if p == ["cards"]:
            return self.list_cards() if m == "GET" else self.create_card()
        if len(p) >= 2 and p[0] == "cards" and ID_RE.match(p[1]):
            cid = p[1]
            if len(p) == 2 and m == "GET":
                with lock:
                    return self.send(200, card_json(get_card(cid)))
            if len(p) == 3 and m == "POST" and p[2] in ("answer", "reopen", "cancel", "redo"):
                return self.card_action(cid, p[2])
            if len(p) == 3 and m == "POST" and p[2] == "pic":
                return self.set_pic(cid)
            if len(p) == 3 and m == "POST" and p[2] == "plan":
                return self.set_plan(cid)
            if len(p) == 3 and m == "POST" and p[2] == "links":
                return self.set_links(cid)
        if p == ["projects"]:
            if m == "GET":
                with lock:
                    return self.send(200, {"projects": project_rows()})
            return self.upsert_project()
        if len(p) == 4 and p[0] == "media" and m == "PUT":
            return self.upload(p[1], p[2], p[3])
        raise Bad(404, "没有这个地址")

    # ---------- 网页与媒体（DECIDE_SERVE_STATIC=1 时） ----------
    def static(self, path):
        pub = os.path.join(WEB_ROOT, "public")
        if path in PUBLIC_FILES:
            return self.send_file(os.path.join(pub, path[1:]), cache="public, max-age=3600")
        if not self.authed():
            return self.send_file(os.path.join(pub, "locked.html"), cache="no-store")
        if path.startswith("/media/"):
            f = safe_join(MEDIA_ROOT, unquote(path[len("/media/"):]))
            if not f or not os.path.isfile(f):
                raise Bad(404, "没有这个文件")
            return self.send_file(f, cache="private, max-age=31536000, immutable", media=True)
        app = os.path.join(WEB_ROOT, "app")
        rel = unquote(path.lstrip("/"))
        for cand in (rel, rel + ".html"):
            f = safe_join(app, cand) if cand and cand != ".html" else None
            if f and os.path.isfile(f):
                return self.send_file(f, cache="no-cache")
        return self.send_file(os.path.join(app, "index.html"), cache="no-cache")

    def send_file(self, f, cache, media=False):
        size = os.path.getsize(f)
        ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("image/svg+xml", "application/manifest+json"):
            ctype += "; charset=utf-8"
        start, end, code = 0, size - 1, 200
        m = media and re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        if media:
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
                                                        "font-src data:; media-src 'self'; sandbox")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(f, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(left, 1 << 20))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def login(self):
        ip = self.ip()
        now = time.time()
        with lock:
            for k_ in list(FAILS):
                FAILS[k_] = [t for t in FAILS[k_] if now - t < FAIL_WINDOW]
                if not FAILS[k_]:
                    del FAILS[k_]
            if len(FAILS.get(ip, [])) >= FAIL_PER_IP or sum(map(len, FAILS.values())) >= FAIL_GLOBAL:
                raise Bad(429, "试错太多次了，10 分钟后再试")
        raw = self.body_json()
        raw = raw if isinstance(raw, dict) else {}
        pw, k = raw.get("p"), raw.get("k")
        ok = (isinstance(pw, str) and PASSWORD and hmac.compare_digest(pw.strip(), PASSWORD)) or \
             (isinstance(k, str) and hmac.compare_digest(k.strip(), KEY))
        if not ok:
            with lock:
                FAILS.setdefault(ip, []).append(now)
            time.sleep(1.5)
            raise Bad(403, "密码不对")
        with lock:
            FAILS.pop(ip, None)
        cookie = f"{COOKIE}={KEY}; Path=/; Max-Age={COOKIE_MAX_AGE}; HttpOnly; SameSite=Lax" + ("; Secure" if COOKIE_SECURE else "")
        self.send(200, {"ok": True}, {"Set-Cookie": cookie})

    def state(self):
        with lock:
            sweep_expired()
            pending = db.execute("SELECT * FROM cards WHERE status='pending' ORDER BY created").fetchall()
            done = db.execute("SELECT * FROM cards WHERE status!='pending' ORDER BY updated DESC LIMIT 400").fetchall()
            self.send(200, {"ver": VER[0], "now": time.time(), "projects": project_rows(),
                            "listeners": dict(LISTENERS), "intake": bool(intake_conf()),
                            "cards": [card_json(r) for r in pending + done]})

    def poll(self):
        q = self.q()
        since = int(q.get("since") or 0)
        timeout = min(max(float(q.get("timeout") or 25), 1), 55)
        end = time.time() + timeout
        with cond:
            while VER[0] <= since:
                rem = end - time.time()
                if rem <= 0:
                    break
                cond.wait(rem)
            v = VER[0]
        self.send(200, {"ver": v})

    def listen(self):
        """Claude 会话的后台监听：本会话推的卡片里，有定了但还没交给 Claude 的就立刻返回。"""
        q = self.q()
        sess = q.get("session") or ""
        if not sess:
            raise Bad(400, "缺 session")
        timeout = min(max(float(q.get("timeout") or 50), 1), 55)
        end = time.time() + timeout
        sql = "SELECT * FROM cards WHERE session=? AND status='answered' AND acked=0 ORDER BY seq"
        with cond:
            if sess not in LISTENERS:
                LISTENERS[sess] = time.time()
                bump()
            while True:
                LISTENERS[sess] = time.time()
                rows = db.execute(sql, (sess,)).fetchall()
                rem = end - time.time()
                if rows or rem <= 0:
                    break
                cond.wait(min(rem, 20))
            self.send(200, {"ver": VER[0], "cards": [card_json(r) for r in rows]})

    def ack(self):
        raw = self.body_json()
        ids = [i for i in (raw.get("ids") or []) if isinstance(i, str) and ID_RE.match(i)][:200]
        if ids:
            with lock:
                db.execute("UPDATE cards SET acked=1, acked_at=? WHERE status!='pending' AND acked=0 AND id IN (%s)" % ",".join("?" * len(ids)),
                           [time.time()] + ids)
                bump()
        self.send(200, {"ok": True, "n": len(ids)})

    def session_state(self):
        """钩子用（10-19）：本会话还有哪些待定卡、哪些答了还没送到会话、有没有监听在跑。
        会话说「在等你定」时其实已经答了，多半是监听死了或刚答完；钩子据此补送、提醒重挂。"""
        sess = self.q().get("session") or ""
        if not sess:
            raise Bad(400, "缺 session")
        now = time.time()
        with lock:
            sweep_expired()
            rows = db.execute("SELECT * FROM cards WHERE session=? AND (status='pending' OR (status='answered' AND acked=0))"
                              " ORDER BY seq", (sess,)).fetchall()
            listening = now - LISTENERS.get(sess, 0) < LIVE
            out = {"now": now, "listening": listening, "pending": [], "unacked": []}
            for r in rows:
                c = card_json(r)
                if c.get("kind") == "perm":  # 授权卡由权限钩子自己收，不归会话
                    continue
                if r["status"] == "pending":
                    out["pending"].append({"id": c["id"], "seq": c["seq"], "title": c.get("title", ""), "project": c["project"],
                                           "created": c["created"], "watched": listening or now - WAITERS.get(c["id"], 0) < LIVE})
                else:
                    out["unacked"].append(c)
            self.send(200, out)

    def list_cards(self):
        q = self.q()
        sql, args = "SELECT * FROM cards WHERE 1=1", []
        if q.get("ids"):
            ids = [i for i in q["ids"].split(",") if ID_RE.match(i)][:200]
            sql += " AND id IN (%s)" % ",".join("?" * len(ids))
            args += ids
            if q.get("watch"):  # decide wait 在等这几张：会话停下时钩子据此判断「有人在听」
                now = time.time()
                for i in ids:
                    WAITERS[i] = now
        if q.get("project"):
            sql += " AND project=?"
            args.append(q["project"])
        if q.get("seq"):
            sql += " AND seq=?"
            args.append(int(q["seq"]))
        st = q.get("status") or "all"
        if st != "all":
            sql += " AND status=?"
            args.append(st)
        sql += " ORDER BY updated DESC LIMIT ?"
        args.append(min(int(q.get("limit") or 50), 500))
        with lock:
            sweep_expired()
            rows = db.execute(sql, args).fetchall()
            self.send(200, {"ver": VER[0], "cards": [card_json(r) for r in rows]})

    def create_card(self):
        raw = self.body_json()
        slug = raw.get("project")
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            raise Bad(400, "project 只能是小写字母数字和连字符")
        cid = raw.get("id") or secrets.token_hex(4)
        if not isinstance(cid, str) or not ID_RE.match(cid):
            raise Bad(400, "id 只能是 4–16 位小写字母数字")
        spec = clean_spec(raw)
        now = time.time()
        with lock:
            if db.execute("SELECT 1 FROM cards WHERE id=?", (cid,)).fetchone():
                raise Bad(409, "id 已存在")
            pname = raw.get("project_name")
            ensure_project(slug, pname if isinstance(pname, str) and pname.strip() else None)
            r = db.execute("SELECT v FROM meta WHERE k='seq'").fetchone()
            seq = (int(r["v"]) if r else 0) + 1
            db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('seq',?)", (str(seq),))
            db.execute("INSERT INTO cards(id,project,status,created,updated,rev,spec,source,batch,seq,session)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (cid, slug, "pending", now, now, 1, json.dumps(spec, ensure_ascii=False),
                        s(raw.get("source"), 80, "source"), s(raw.get("batch"), 80, "batch"), seq,
                        s(raw.get("session"), 80, "session")))
            bump()
        self.send(201, {"id": cid, "seq": seq, "url": f"{PUBLIC_BASE}/p/{slug}#c-{cid}"})

    def card_action(self, cid, act):
        raw = self.body_json()
        with lock:
            r = get_card(cid)
            spec = json.loads(r["spec"])
            now = time.time()
            if act == "answer":
                if r["status"] != "pending":
                    raise Bad(409, "这张卡已经定过或撤销了")
                choice = raw.get("choice") or []
                if isinstance(choice, str):
                    choice = [choice]
                valid = {o["id"] for o in spec["options"]}
                note_only = choice == ["note"] and "note" not in valid  # 不选选项、直接在板上写全部意见
                if note_only and not (raw.get("note") or "").strip():
                    raise Bad(400, "意见是空的")
                if not note_only and (not choice or any(c not in valid for c in choice)):
                    raise Bad(400, "选项不对")
                if not spec.get("multi") and len(choice) != 1:
                    raise Bad(400, "这张卡只能选一个")
                ans = {"choice": choice,
                       "labels": ["写了意见"] if note_only else [o["label"] for o in spec["options"] if o["id"] in choice],
                       "note": s(raw.get("note"), 4000, "note"),
                       "at": now, "device": s(raw.get("device"), 80, "device")}
                db.execute("UPDATE cards SET status='answered', answer=?, updated=?, rev=rev+1, acked=? WHERE id=?",
                           (json.dumps(ans, ensure_ascii=False), now, 1 if raw.get("ack") else 0, cid))
            elif act == "reopen":
                if r["status"] == "pending":
                    raise Bad(409, "这张卡本来就在待定")
                if spec.get("kind") == "perm":
                    raise Bad(409, "授权卡答完 Claude 就继续了，撤回不了")
                db.execute("UPDATE cards SET status='pending', answer=NULL, updated=?, rev=rev+1, acked=0 WHERE id=?",
                           (now, cid))
            elif act == "redo":
                # 已定历史里点「返修」（10-21）：卡记成「返修 + 意见」，原答案留底，重新交给推卡的会话
                if r["status"] == "pending":
                    raise Bad(409, "这张卡还在待定，直接在板上定")
                if spec.get("kind") == "perm":
                    raise Bad(409, "授权卡答完 Claude 就继续了，返修不了")
                note = s(raw.get("note"), 4000, "note").strip()
                if not note:
                    raise Bad(400, "返修要写哪里要改")
                old = json.loads(r["answer"]) if r["answer"] else {}
                # 只留上一次的答案，不套娃：连着返修两次，prev 是上一次返修
                prev = {k: old[k] for k in ("choice", "labels", "note", "at") if k in old} or None
                ans = {"choice": ["redo"], "labels": ["返修"], "note": note, "at": now,
                       "device": s(raw.get("device"), 80, "device"), "redo": True,
                       "prev": prev, "prev_status": r["status"]}
                db.execute("UPDATE cards SET status='answered', answer=?, updated=?, rev=rev+1, acked=0 WHERE id=?",
                           (json.dumps(ans, ensure_ascii=False), now, cid))
            else:
                if r["status"] != "pending":
                    raise Bad(409, "只能撤销待定的卡")
                db.execute("UPDATE cards SET status='cancelled', updated=?, rev=rev+1, acked=1 WHERE id=?", (now, cid))
            db.execute("UPDATE projects SET updated=? WHERE slug=?", (now, r["project"]))
            bump()
            out = card_json(get_card(cid))
            out["listening"] = bool(r["session"]) and time.time() - LISTENERS.get(r["session"], 0) < 75
            self.send(200, out)

    def set_pic(self, cid):
        """后台出图补进选项卡：{oid, pic:{src,thumb,w,h}}；出图失败传 {oid, failed:true} 去掉占位。"""
        raw = self.body_json()
        with lock:
            r = get_card(cid)
            spec = json.loads(r["spec"])
            secs = (spec.get("plan") or {}).get("secs") or []
            if isinstance(raw.get("sec"), int) and 0 <= raw["sec"] < len(secs):
                x = secs[raw["sec"]]  # 方案小节的配图
            else:
                x = next((o for o in spec["options"] if o["id"] == raw.get("oid")), None)
            if not x:
                raise Bad(400, "没有这个选项")
            pic = clean_pic(raw.get("pic"))
            if not pic and not raw.get("failed"):
                raise Bad(400, "pic.src 必须是 /media/ 开头的路径")
            x.pop("gen", None)
            if pic:
                x["pic"] = pic
            now = time.time()
            db.execute("UPDATE cards SET spec=?, updated=?, rev=rev+1 WHERE id=?",
                       (json.dumps(spec, ensure_ascii=False), now, cid))
            bump()
            self.send(200, card_json(get_card(cid)))

    def set_plan(self, cid):
        """已经上板的卡补一份 plan（decide replan 用）：{plan, src: 原图在 media 里的下标}。"""
        raw = self.body_json()
        plan = clean_plan(raw.get("plan"))
        if not plan:
            raise Bad(400, "plan 是空的")
        with lock:
            r = get_card(cid)
            spec = json.loads(r["spec"])
            spec["plan"] = plan
            i = raw.get("src")
            if isinstance(i, int) and 0 <= i < len(spec["media"]):
                spec["media"][i]["role"] = "src"
            now = time.time()
            db.execute("UPDATE cards SET spec=?, updated=?, rev=rev+1 WHERE id=?",
                       (json.dumps(spec, ensure_ascii=False), now, cid))
            bump()
            self.send(200, card_json(get_card(cid)))

    def set_links(self, cid):
        """已经上板的卡补 / 换链接（decide link 用）：{links: [{t, url}]}，空列表清掉。"""
        links = clean_links(self.body_json().get("links"))
        with lock:
            r = get_card(cid)
            spec = json.loads(r["spec"])
            if links:
                spec["links"] = links
            else:
                spec.pop("links", None)
            now = time.time()
            db.execute("UPDATE cards SET spec=?, updated=?, rev=rev+1 WHERE id=?",
                       (json.dumps(spec, ensure_ascii=False), now, cid))
            bump()
            self.send(200, card_json(get_card(cid)))

    def to_chat(self):
        """用户说「这些我在对话里说」：把指定卡片（或某会话全部待定）标成已定、选项为「在对话里说」。"""
        raw = self.body_json()
        ids = [i for i in (raw.get("ids") or []) if isinstance(i, str) and ID_RE.match(i)]
        sess = raw.get("session") if isinstance(raw.get("session"), str) else ""
        now = time.time()
        with lock:
            if ids:
                rows = db.execute("SELECT * FROM cards WHERE status='pending' AND id IN (%s)" % ",".join("?" * len(ids)), ids).fetchall()
            elif sess:
                rows = db.execute("SELECT * FROM cards WHERE status='pending' AND session=? ORDER BY seq", (sess,)).fetchall()
            else:
                raise Bad(400, "要给 ids 或 session")
            ans = json.dumps({"choice": ["chat"], "labels": ["在对话里说"], "note": "", "at": now,
                              "device": s(raw.get("device"), 80, "device"), "chat": True}, ensure_ascii=False)
            for r in rows:
                db.execute("UPDATE cards SET status='answered', answer=?, updated=?, rev=rev+1, acked=? WHERE id=?",
                           (ans, now, 1 if raw.get("ack") else 0, r["id"]))
            if rows:
                bump()
            sessions = {r["session"] for r in rows if r["session"]}
            self.send(200, {"ids": [r["id"] for r in rows], "seqs": [r["seq"] for r in rows],
                            "listening": any(now - LISTENERS.get(x, 0) < 75 for x in sessions)})

    def upsert_project(self):
        raw = self.body_json()
        slug = raw.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            raise Bad(400, "slug 只能是小写字母数字和连字符")
        with lock:
            name = raw.get("name")
            ensure_project(slug, name.strip() if isinstance(name, str) and name.strip() else None)
            if isinstance(raw.get("color"), str) and re.match(r"^#[0-9a-fA-F]{6}$", raw["color"]):
                db.execute("UPDATE projects SET color=? WHERE slug=?", (raw["color"], slug))
            if "note" in raw:
                db.execute("UPDATE projects SET note=? WHERE slug=?", (s(raw["note"], 400, "note"), slug))
            if "archived" in raw:
                db.execute("UPDATE projects SET archived=? WHERE slug=?", (1 if raw["archived"] else 0, slug))
            bump()
            self.send(200, {"projects": project_rows()})

    def upload(self, slug, cid, name):
        if not SLUG_RE.match(slug) or not ID_RE.match(cid) or not NAME_RE.match(name):
            raise Bad(400, "上传路径不合法")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            raise Bad(413, "文件为空或太大")
        d = os.path.join(MEDIA_ROOT, slug, cid)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f".{name}.part")
        left = n
        with open(tmp, "wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    raise Bad(400, "上传中断")
                f.write(chunk)
                left -= len(chunk)
        os.replace(tmp, os.path.join(d, name))
        self.send(201, {"src": f"/media/{slug}/{cid}/{name}"})

    # ---------- 派活入口 ----------
    def intake_info(self):
        c = intake_conf()
        intake_refresh(c)
        with lock:
            rows = db.execute("SELECT * FROM intakes ORDER BY created DESC LIMIT 20").fetchall()
        self.send(200, {"enabled": bool(c), "via": ("gateway" if c.get("gateway") else "dir") if c else "",
                        "max_bytes": INTAKE_MAX, "max_files": INTAKE_FILES, "recent": [intake_json(r) for r in rows]})

    def intake_file(self):
        if not intake_conf():
            raise Bad(404, "派活入口没开")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise Bad(400, "文件是空的")
        if n > INTAKE_MAX:
            raise Bad(413, f"单个文件不能超过 {INTAKE_MAX >> 20} MB")
        name = safe_name(unquote(self.headers.get("X-Filename") or ""))
        os.makedirs(INTAKE_UP, exist_ok=True)
        sweep_intake_up()
        fid = "u" + secrets.token_hex(8)
        fp = os.path.join(INTAKE_UP, fid)
        left = n
        with open(fp + ".part", "wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    raise Bad(400, "上传中断")
                f.write(chunk)
                left -= len(chunk)
        os.replace(fp + ".part", fp)
        with open(fp + ".json", "w", encoding="utf-8") as f:
            json.dump({"name": name, "size": n}, f, ensure_ascii=False)
        self.send(201, {"id": fid, "name": name, "size": n})

    def intake_submit(self):
        c = intake_conf()
        if not c:
            raise Bad(404, "派活入口没开")
        raw = self.body_json()
        key = str(raw.get("key") or "")
        iid = key if IK_RE.match(key) else secrets.token_hex(8)
        with lock:
            old = db.execute("SELECT * FROM intakes WHERE id=?", (iid,)).fetchone()
        if old and old["status"] != "failed":  # 同一次提交重发：原样返回，不重复交件
            return self.send(200, intake_json(old))
        text, chat, note = (str(raw.get(k) or "").strip()[:n] for k, n in (("text", 20000), ("chat", 120), ("note", 2000)))
        files = []
        for fid in (raw.get("files") or [])[:INTAKE_FILES]:
            if not isinstance(fid, str) or not FID_RE.match(fid):
                raise Bad(400, "附件编号不对")
            try:
                with open(os.path.join(INTAKE_UP, fid + ".json"), encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                raise Bad(400, "有附件已过期，请删掉重新添加")
            files.append({"id": fid, "name": meta["name"], "size": meta["size"]})
        if not text and not files:
            raise Bad(400, "文字和附件至少给一样")
        now = time.time()
        with lock:
            db.execute("INSERT OR REPLACE INTO intakes(id,created,updated,status,text,chat,note,files) VALUES(?,?,?,?,?,?,?,?)",
                       (iid, now, now, "sending", text, chat, note, json.dumps(files, ensure_ascii=False)))
            row = db.execute("SELECT * FROM intakes WHERE id=?", (iid,)).fetchone()
        threading.Thread(target=intake_run, args=(c, iid), daemon=True).start()
        self.send(200, intake_json(row))

    def intake_retry(self, iid):
        c = intake_conf()
        if not c:
            raise Bad(404, "派活入口没开")
        with lock:
            r = db.execute("SELECT * FROM intakes WHERE id=?", (iid,)).fetchone()
            if not r:
                raise Bad(404, "没有这一件")
            if r["status"] != "failed":
                return self.send(200, intake_json(r))
            db.execute("UPDATE intakes SET status='sending', error='', updated=? WHERE id=?", (time.time(), iid))
            row = db.execute("SELECT * FROM intakes WHERE id=?", (iid,)).fetchone()
        threading.Thread(target=intake_run, args=(c, iid), daemon=True).start()
        self.send(200, intake_json(row))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    os.makedirs(MEDIA_ROOT, exist_ok=True)
    srv = Server((HOST, PORT), H)
    sys.stderr.write(f"decide 已启动 {PUBLIC_BASE}（监听 {HOST}:{PORT}，数据在 {HOME}，ver={VER[0]}）\n")
    if KEY_CREATED:
        sys.stderr.write(f"第一次启动，已生成密钥 {KEY_FILE}；同一台机器上的 bin/decide 会自动读它\n")
    if sys.stderr.isatty():
        sys.stderr.write(f"浏览器打开（自动登录）：{PUBLIC_BASE}/#k={KEY}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
