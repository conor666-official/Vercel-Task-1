#!/usr/bin/env python3
# ig_server.py - Instagram lookup on localhost. Single file, live only.
#
#   pip install flask
#   python ig_server.py --open
#
#   GET /health
#   GET /search?ig=<username>[&n=6]     profile + recent posts
#
# Add &pretty=1 for indented JSON. Binds 127.0.0.1 only.
# Credit: @Techno_Telex

import os, sys, io, re, json, gzip, time, html, argparse, webbrowser, platform
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta

try:
    from flask import Flask, request, Response
except ImportError:
    sys.exit("Flask not installed. Run:  pip install flask")

CREDIT  = "@Techno_Telex"
VERSION = "2.0"
TIMEOUT = 8
IST     = timezone(timedelta(hours=5, minutes=30))
START   = time.time()

UAS = [
    ("googlebot", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    ("facebook",  "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"),
    ("android",   "Mozilla/5.0 (Linux; Android 11; Redmi 8A) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
    ("desktop",   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
]
PROXY = "https://r.jina.ai/https://www.instagram.com/%s/"

app = Flask(__name__)

# ---------------------------------------------------------------- fetching ---
def fetch(url, ua, want_json=False, extra=None):
    hdrs = {
        "User-Agent": ua,
        "Accept": "application/json, */*;q=0.8" if want_json else "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip",
    }
    if extra:
        hdrs.update(extra)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                try:
                    data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
                except Exception:
                    pass
            return r.status, data.decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return e.code, body.decode("utf-8", "replace"), "HTTP %s" % e.code
    except Exception as e:
        return 0, "", str(e)


def unescape(s):
    try:
        return json.loads('"%s"' % s.replace('"', '\\"'))
    except Exception:
        return s.replace("\\n", "\n").replace("\\/", "/")


def brace_block(text, key, limit=40000):
    i = text.find('"%s"' % key)
    if i < 0:
        return None
    j = text.find("{", i)
    if j < 0:
        return None
    depth = 0; instr = False; esc = False
    for k in range(j, min(len(text), j + limit)):
        c = text[k]
        if instr:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': instr = False
            continue
        if c == '"': instr = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
    return None


def page_state(html):
    if not html:
        return "empty"
    if "not-logged-in" in html and len(html) < 60000:
        return "login_wall"
    if "httpErrorPage" in html and "follower_count" not in html:
        return "error_shell"
    if "follower_count" in html or "xig_user_by_igid_v2" in html:
        return "has_data"
    return "shell_no_data"


def parse_profile(page):
    p = {}
    # 1. the structured block
    blk = brace_block(page, "xig_user_by_igid_v2")
    if blk:
        try:
            d = json.loads(blk)
            p["_block"] = d
            for k in ("username", "full_name", "biography", "follower_count",
                      "following_count", "is_verified", "is_private", "pk", "id",
                      "profile_pic_url", "pronouns", "account_badges",
                      "has_any_clips", "latest_reel_media", "is_coppa_enforced",
                      "business_category", "category_name", "external_url"):
                if d.get(k) not in (None, "", [], {}):
                    p[k] = d[k]
        except Exception:
            pass

    # 2. meta tags (entities decoded)
    metas = {}
    for m in re.finditer(r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', page):
        metas.setdefault(m.group(1), html.unescape(m.group(2)))

    if metas.get("og:description"):
        p["og_description"] = metas["og:description"]
    if metas.get("og:title"):
        p["og_title"] = metas["og:title"]
    if metas.get("og:url"):
        p["profile_url"] = metas["og:url"]
    if "profile_pic_url" not in p and metas.get("og:image"):
        p["profile_pic_url"] = metas["og:image"]

    def g(rx):
        m = re.search(rx, page)
        return m.group(1) if m else None

    # 3. plain JSON regexes
    for key, rx, cast in (
        ("follower_count",  r'"follower_count":(\d+)',  int),
        ("following_count", r'"following_count":(\d+)', int),
        ("username",        r'"username":"([^"]+)"',     None),
        ("pk",              r'"pk":"(\d+)"',            None),
        ("profile_id",      r'"profile_id":"(\d+)"',    None),
    ):
        if key not in p:
            v = g(rx)
            if v is not None:
                p[key] = cast(v) if cast else v

    for key, rx, tf in (
        ("is_private",  r'"is_private":(true|false)',  True),
        ("is_verified", r'"is_verified":(true|false)', True),
    ):
        if key not in p:
            v = g(rx)
            if v is not None:
                p[key] = (v == "true")

    for key, rx in (("full_name", r'"full_name":"((?:[^"\\]|\\.)*)"'),
                    ("biography", r'"biography":"((?:[^"\\]|\\.)*)"')):
        if key not in p:
            v = g(rx)
            if v is not None:
                p[key] = unescape(v)

    # 4. counts out of og:description
    if p.get("og_description"):
        m = re.search(r'([\d,\.]+[KMB]?)\s+followers?,\s+([\d,\.]+[KMB]?)\s+following,'
                      r'\s+([\d,\.]+[KMB]?)\s+posts?', p["og_description"])
        if m:
            p.setdefault("follower_count", m.group(1))
            p.setdefault("following_count", m.group(2))
            p["media_count"] = m.group(3)

    # 5. post shortcodes, if the page happens to carry any
    codes = []
    for c in re.findall(r'"shortcode":"([A-Za-z0-9_-]{5,})"', page):
        if c not in codes:
            codes.append(c)
    if codes:
        p["recent_shortcodes"] = codes[:12]
    return p


def clean_caption(raw):
    keep = [l.strip() for l in (raw or "").splitlines()
            if l.strip() and not set(l.strip()) <= set(".\u2026 -_")]
    t = " ".join(" ".join(keep).split())
    return (t[:140] + "...") if len(t) > 140 else t


def _attempt_plan():
    """On Vercel the direct request comes from a datacenter IP that Instagram
    blocks, so try the reader proxy first there. Locally, direct first."""
    direct = [(label, ua) for label, ua in UAS]
    proxy = ("reader-proxy", None)
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return [proxy] + direct
    return direct + [proxy]


def lookup_profile(user):
    url = "https://www.instagram.com/%s/" % user
    attempts = []
    for label, ua in _attempt_plan():
        try:
            if ua is None:
                st, page, err = fetch(PROXY % user, UAS[3][1],
                                      extra={"x-respond-with": "html"})
            else:
                st, page, err = fetch(url, ua)
        except Exception as e:
            attempts.append({"ua": label, "status": 0, "bytes": 0,
                             "page": "error", "error": str(e)})
            continue
        state = page_state(page)
        attempts.append({"ua": label, "status": st, "bytes": len(page),
                         "page": state, "error": err})
        if st == 200 and len(page) > 2000:
            prof = parse_profile(page)
            if prof.get("follower_count") is not None or prof.get("username"):
                return {"success": True, "profile": prof, "ua_used": label,
                        "attempts": attempts}
    return {"success": False, "error": "no page contained profile data",
            "attempts": attempts}


def lookup_post(code):
    m = re.search(r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)', code or "")
    if m:
        code = m.group(1)
    url = "https://www.instagram.com/api/v1/oembed/?url=" + urllib.parse.quote(
        "https://www.instagram.com/p/%s/" % code, safe="")
    st, body, err = fetch(url, UAS[3][1], want_json=True)
    if st == 200:
        try:
            d = json.loads(body)
            return {"success": True, "code": code,
                    "author_username": d.get("author_name"),
                    "author_id": d.get("author_id"),
                    "caption": clean_caption(d.get("title")),
                    "thumbnail_url": d.get("thumbnail_url"),
                    "permalink": "https://www.instagram.com/p/%s/" % code}
        except Exception as e:
            return {"success": False, "code": code, "error": "bad json: %s" % e}
    return {"success": False, "code": code, "status": st, "error": err}


def lookup_all(user, n=6):
    base = lookup_profile(user)
    if not base.get("success"):
        return base
    codes = (base["profile"].get("recent_shortcodes") or [])[:max(0, n)]
    posts = []
    for c in codes:
        r = lookup_post(c)
        if r.get("success"):
            posts.append({k: r.get(k) for k in
                          ("code", "caption", "thumbnail_url", "permalink")})
        time.sleep(0.2)
    base["posts"] = posts
    base["posts_fetched"] = len(posts)
    return base

# ----------------------------------------------------------------- output ----
def now_ist():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def took(ms):
    return ("%dms" % ms) if ms < 1000 else ("%.2fs" % (ms / 1000.0))


def uptime():
    s = int(time.time() - START)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    parts = []
    if h: parts.append("%dh" % h)
    if m: parts.append("%dm" % m)
    parts.append("%ds" % sec)
    return " ".join(parts)


def num(v):
    try:
        n = int(str(v).replace(",", ""))
        if n >= 10_000_000: return "%.1fCr" % (n / 10_000_000)
        if n >= 100_000:    return "%.1fL" % (n / 100_000)
        if n >= 1_000:      return "{:,}".format(n)
        return n
    except Exception:
        return v


def human(n):
    """Return '68.1Cr' style plus the exact number when they differ."""
    try:
        v = int(str(n).replace(",", ""))
    except Exception:
        return n, None
    if v >= 10_000_000:
        return "%.1fCr" % (v / 10_000_000), v
    if v >= 100_000:
        return "%.1fL" % (v / 100_000), v
    if v >= 1_000:
        return "{:,}".format(v), v
    return v, None


def fmt_profile(p):
    o = {}
    if p.get("username"):          o["🏷️ username"] = "@" + str(p["username"]).lstrip("@")
    if p.get("full_name"):         o["📛 name"] = p["full_name"]
    if p.get("pk"):                o["🆔 id"] = p["pk"]
    if p.get("id"):                o["🆔 fb_id"] = p["id"]
    if p.get("profile_id") and str(p.get("profile_id")) != str(p.get("pk")):
        o["🆔 profile_id"] = p["profile_id"]

    for label, key in (("👥 followers", "follower_count"),
                       ("➡️ following", "following_count"),
                       ("🖼️ posts", "media_count")):
        if p.get(key) is not None:
            short, exact = human(p[key])
            o[label] = short
            if exact is not None:
                o[label + "_exact"] = exact

    if p.get("category_name") or p.get("business_category"):
        o["📂 category"] = p.get("category_name") or p.get("business_category")
    if p.get("is_private") is not None:  o["🔒 private"] = bool(p["is_private"])
    if p.get("is_verified") is not None: o["☑️ verified"] = bool(p["is_verified"])
    if p.get("is_coppa_enforced"):       o["👶 coppa"] = True
    if p.get("biography"):         o["📝 bio"] = p["biography"]
    if p.get("external_url"):      o["🔗 link"] = p["external_url"]
    if p.get("pronouns"):          o["🗣️ pronouns"] = p["pronouns"]
    if p.get("account_badges"):    o["🎖️ badges"] = p["account_badges"]
    if p.get("has_any_clips"):     o["🎬 reels"] = True
    if p.get("latest_reel_media"): o["📺 latest_reel"] = p["latest_reel_media"]
    if p.get("city_name"):         o["📍 city"] = p["city_name"]
    if p.get("profile_pic_url"):   o["📸 dp"] = p["profile_pic_url"]
    if p.get("profile_url"):       o["🌐 url"] = p["profile_url"]
    if p.get("og_description"):    o["📊 summary"] = p["og_description"]
    return o


def fmt_posts(posts):
    arr = []
    for po in posts or []:
        e = {}
        if po.get("code"):     e["🔖 code"] = po["code"]
        if po.get("permalink"):e["🔗 link"] = po["permalink"]
        if po.get("caption"):  e["💬 caption"] = po["caption"]
        if po.get("thumbnail_url"): e["🖼️ thumb"] = po["thumbnail_url"]
        if e:
            arr.append(e)
    return arr


def envelope(kind, t0, body):
    out = {"🎯 search": kind, "⏱️ time_taken": took(int((time.time() - t0) * 1000)),
           "📅 time": now_ist()}
    out.update(body)
    out["🔧 credit"] = CREDIT
    return out


def respond(obj, code=200):
    pretty = request.args.get("pretty") in ("1", "true", "yes")
    body = json.dumps(obj, indent=2 if pretty else None, ensure_ascii=False,
                      separators=None if pretty else (",", ":"))
    return Response(body, status=code, mimetype="application/json; charset=utf-8")

# ------------------------------------------------------------------- page ----
PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IG Lookup</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--tx:#e6e8ee;--dim:#8b93a7;--acc:#4da3ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 -apple-system,Roboto,system-ui,sans-serif;padding:14px}
h1{font-size:17px;margin:0 0 12px;font-weight:600}
h1 span{color:var(--dim);font-weight:400;font-size:12px}
.row{display:flex;gap:8px;margin-bottom:9px}
input,button{font:inherit;border-radius:9px;border:1px solid var(--line);background:var(--card);color:var(--tx);padding:11px 12px}
input{flex:1;min-width:0}
button{background:var(--acc);border-color:var(--acc);color:#06121f;font-weight:600;cursor:pointer}
button.ghost{background:var(--card);color:var(--tx);border-color:var(--line);font-weight:500}
button:disabled{opacity:.4}
.bar{display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
.bar button{padding:7px 11px;font-size:13px;border-radius:8px}
.stat{color:var(--dim);font-size:12px;margin-left:auto}
pre{margin:0;background:#0b0d11;border:1px solid var(--line);border-radius:10px;padding:12px;
    overflow:auto;font:12.5px/1.6 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;
    word-break:break-word;min-height:140px;max-height:calc(100vh - 200px);color:#cfd6e4}
</style></head><body>
<h1>IG Lookup <span>localhost &middot; @Techno_Telex</span></h1>
<div class="row">
  <input id="q" placeholder="username" autocomplete="off" autocapitalize="off" spellcheck="false">
  <button id="go">Search</button>
</div>
<div class="bar">
  <button class="ghost" id="pretty" disabled>Pretty Print</button>
  <button class="ghost" id="copy" disabled>Copy</button>
  <button class="ghost" id="health">Health</button>
  <span class="stat" id="stat"></span>
</div>
<pre id="out">no response yet</pre>
<script>
let rawText='', prettyOn=false;
const $=s=>document.querySelector(s);
function show(t,s,n){rawText=t;prettyOn=false;$('#pretty').textContent='Pretty Print';
  $('#out').textContent=t;$('#pretty').disabled=!t;$('#copy').disabled=!t;
  $('#stat').textContent=s?`HTTP ${s} - ${n} bytes`:'';}
async function run(){
  const q=$('#q').value.trim().replace(/^@/,'');
  if(!q) return;
  show('working...',0,0);
  try{const r=await fetch(`/search?ig=${encodeURIComponent(q)}&n=6`);
      const t=await r.text();show(t,r.status,t.length);}
  catch(e){show('request failed: '+e.message,0,0);}
}
$('#pretty').onclick=()=>{ if(!rawText)return;
  if(!prettyOn){try{$('#out').textContent=JSON.stringify(JSON.parse(rawText),null,2);
      prettyOn=true;$('#pretty').textContent='Compact';}
    catch(e){$('#out').textContent='not valid JSON:\\n\\n'+rawText;}}
  else{$('#out').textContent=rawText;prettyOn=false;$('#pretty').textContent='Pretty Print';}};
$('#copy').onclick=async()=>{try{await navigator.clipboard.writeText($('#out').textContent);
  $('#copy').textContent='Copied';}catch(e){$('#copy').textContent='Copy failed';}
  setTimeout(()=>$('#copy').textContent='Copy',1200);};
$('#health').onclick=async()=>{show('working...',0,0);
  const r=await fetch('/health');const t=await r.text();show(t,r.status,t.length);};
$('#go').onclick=run;
$('#q').addEventListener('keydown',e=>{if(e.key==='Enter')run();});
</script></body></html>"""

# ----------------------------------------------------------------- routes ----
@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/api")
def api_hint():
    return respond({
        "🔧 credit": CREDIT,
        "📡 endpoints": {
            "🩺 health": "/health",
            "🔍 search": "/search?ig=<username>",
            "🖼️ post": "/search?post=<shortcode>",
        },
        "💡 tip": "add &pretty=1 for indented json",
    })


@app.route("/health")
def health():
    t0 = time.time()
    return respond({
        "🟢 status": "online",
        "⏱️ uptime": uptime(),
        "📅 time": now_ist(),
        "🔖 version": VERSION,
        "🐍 python": platform.python_version(),
        "🖥️ host": "127.0.0.1",
        "📡 endpoints": {
            "🩺 health": "/health",
            "🔍 search": "/search?ig=<username>",
            "🖼️ post": "/search?post=<shortcode>",
        },
        "🔧 credit": CREDIT,
        "⏱️ time_taken": took(int((time.time() - t0) * 1000)),
    })


@app.route("/search")
def search():
    t0 = time.time()
    post = (request.args.get("post") or "").strip()
    if post:
        pr = lookup_post(post)
        if pr.get("success"):
            return respond(envelope(post, t0, {
                "✅ success": True,
                "🖼️ post": {"🔖 code": pr.get("code"),
                             "🔗 link": pr.get("permalink"),
                             "🏷️ author": "@" + str(pr.get("author_username") or "").lstrip("@"),
                             "🆔 author_id": pr.get("author_id"),
                             "💬 caption": pr.get("caption"),
                             "🖼️ thumb": pr.get("thumbnail_url")},
            }))
        return respond(envelope(post, t0, {
            "✅ success": False,
            "❌ error": pr.get("error") or "post not found",
        }), 404)

    user = (request.args.get("ig") or request.args.get("u") or "").strip().lstrip("@")
    if not user:
        return respond(envelope("none", t0, {
            "✅ success": False,
            "❌ error": "missing ?ig=<username>",
            "💡 example": "/search?ig=cristiano   or   /search?post=DefC9GfAVcD",
        }), 400)

    try:
        n = max(0, min(12, int(request.args.get("n", 0))))
    except ValueError:
        n = 6

    r = lookup_all(user, n) if n else lookup_profile(user)

    if r.get("success"):
        body = {
            "✅ success": True,
            "👤 profile": fmt_profile(r.get("profile") or {}),
            "🖼️ recent_posts": fmt_posts(r.get("posts") or []),
            "🧮 posts_fetched": r.get("posts_fetched", len(r.get("posts") or [])),
        }
        if r.get("ua_used"):
            body["🌐 source"] = "public page via %s" % r["ua_used"]
        if not body["🖼️ recent_posts"]:
            body["ℹ️ note"] = ("post list needs a login; the public page carries no "
                               "shortcodes. Use /search?post=<code> for one post.")
        return respond(envelope(user, t0, body))

    return respond(envelope(user, t0, {
        "✅ success": False,
        "❌ error": r.get("error", "no data"),
        "💡 hint": "Instagram refused this network. Wait a few minutes and retry, "
                   "or switch between Wi-Fi and mobile data.",
        "🔎 attempts": [{"🌐 ua": a.get("ua"), "📶 status": a.get("status"),
                         "📄 page": a.get("page"), "📏 bytes": a.get("bytes")}
                        for a in (r.get("attempts") or [])],
    }))


# Vercel / WSGI entrypoints
handler = app
application = app
