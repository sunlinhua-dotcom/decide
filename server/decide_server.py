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
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

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
""")
cols = {r["name"] for r in db.execute("PRAGMA table_info(cards)")}
for col, ddl in (("seq", "INTEGER"), ("session", "TEXT NOT NULL DEFAULT ''"), ("acked", "INTEGER NOT NULL DEFAULT 0")):
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
        if p == ["cards"]:
            return self.list_cards() if m == "GET" else self.create_card()
        if len(p) >= 2 and p[0] == "cards" and ID_RE.match(p[1]):
            cid = p[1]
            if len(p) == 2 and m == "GET":
                with lock:
                    return self.send(200, card_json(get_card(cid)))
            if len(p) == 3 and m == "POST" and p[2] in ("answer", "reopen", "cancel"):
                return self.card_action(cid, p[2])
            if len(p) == 3 and m == "POST" and p[2] == "pic":
                return self.set_pic(cid)
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
                            "listeners": dict(LISTENERS),
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
                db.execute("UPDATE cards SET acked=1 WHERE status!='pending' AND id IN (%s)" % ",".join("?" * len(ids)), ids)
                bump()
        self.send(200, {"ok": True, "n": len(ids)})

    def list_cards(self):
        q = self.q()
        sql, args = "SELECT * FROM cards WHERE 1=1", []
        if q.get("ids"):
            ids = [i for i in q["ids"].split(",") if ID_RE.match(i)][:200]
            sql += " AND id IN (%s)" % ",".join("?" * len(ids))
            args += ids
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
                if not choice or any(c not in valid for c in choice):
                    raise Bad(400, "选项不对")
                if not spec.get("multi") and len(choice) != 1:
                    raise Bad(400, "这张卡只能选一个")
                ans = {"choice": choice,
                       "labels": [o["label"] for o in spec["options"] if o["id"] in choice],
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
