#!/usr/bin/env python3
"""reddit.py — Axion's own READ-ONLY Reddit door (Fig Tree primitive, 4 Sep 2026; feed rung 6 Sep 2026).

Why: the Claude-in-Chrome extension waits for document_idle and Reddit never idles;
old.reddit now demands a login for anything; the in-app pane blocks reddit by policy;
Reddit's API rail needs an app it has approved (reddit_api.py). What Reddit
DOES still publish to anyone, no login and no key, is its Atom feeds: /r/<sub>/search.rss,
/r/<sub>/new.rss and <thread>.rss (post + comments, author + date + text, no scores).
Measured 6 Sep 2026 (16:35-16:45, VPS): about ONE anonymous feed request per 30 s per IP; shorter gaps
draw 429s. The feed rung paces itself at FEED_PAUSE and HALTS on 429 instead of retrying.

TWO RUNGS, honest about which answered:
  feed      stdlib Atom (default) — search, new, thread text, voices. No scores, top-level
            comments only (Reddit's feed shape), needs nothing running.
  browser   browserd.py (real Chrome, persistent `reddit` profile, port 8766) rendering
            Reddit's JSON — scores, nested replies, rules, about, user_is_banned. Used
            for rules/about/recon/whoami and for `--deep`. Self-heals a stale daemon.

HARD RULES: GET only — there is no post, comment, vote
or message op here and none may be added. Halts on a login wall, a 429 or a block page.

  py scripts/reddit.py voices "parking charge" --subs LegalAdviceUK AskUK --num 8     # -> reddit/voices/<slug>_<date>.md
  py scripts/reddit.py search "parking charge notice appeal" [--sub LegalAdviceUK] [--num 25] [--time year] [--deep]
  py scripts/reddit.py new LegalAdviceUK [--num 25]
  py scripts/reddit.py thread https://www.reddit.com/r/.../comments/abc123/...  [--deep --max-comments 80]
  py scripts/reddit.py rules  LegalAdviceUK          # browser rung
  py scripts/reddit.py about  LegalAdviceUK          # browser rung: subscribers, user_is_banned (if logged in)
  py scripts/reddit.py recon  LegalAdviceUK AskUK    # one dossier per sub -> reddit/dossiers/<sub>.md
  py scripts/reddit.py whoami | start | selftest
Every command prints JSON (or markdown for recon/voices). Exit 0 ok, 1 error, 2 daemon down, 3 rate-limited (partial output kept).
"""
import argparse, json, os, sys, time, subprocess, urllib.parse, urllib.request, urllib.error, datetime, pathlib, re, html
import xml.etree.ElementTree as ET

PORT = int(os.environ.get("AXION_REDDIT_PORT", "8766"))   # override when the login lives in another browserd profile
ROOT = pathlib.Path(__file__).resolve().parents[1]
BROWSERD = ROOT / "tools" / "browserd.py"
DOSSIERS = ROOT / "reddit" / "dossiers"
VOICES = ROOT / "reddit" / "voices"
PAUSE = 2.0          # browser rung
FEED_PAUSE = 30.0    # feed rung — re-measured 6 Sep 2026 16:35-16:45 from the VPS: 30/60/90 s gaps = 200 every time; 8-20 s gaps = 429s (and a 429 spends budget too). 4 s was the 05:00 reading and is stale.
FEED_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
           "Accept": "application/atom+xml, application/xml, text/xml, */*"}
NS = {"a": "http://www.w3.org/2005/Atom"}
_last = [0.0]
_feed_last = [0.0]


class RateLimited(RuntimeError):
    pass


# ---------------------------------------------------------------- browser rung
def _kill_port(port):
    """Windows: find the PID listening on 127.0.0.1:<port> and kill it. Returns the pids it killed."""
    killed = []
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=30).stdout
        for line in out.splitlines():
            if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                pid = line.split()[-1]
                if pid.isdigit() and pid != "0":
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=30)
                    killed.append(pid)
    except Exception:
        pass
    return killed


def start_daemon():
    r = subprocess.run([sys.executable, str(BROWSERD), "start", "--profile", "reddit", "--port", str(PORT)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = (r.stdout.strip().splitlines() or [r.stderr[-300:]])[-1]
    try:
        j = json.loads(line)
    except Exception:
        return {"ok": False, "error": line[:200]}
    if not j.get("ok") and "browser is gone" in (j.get("error") or ""):
        # the failure mode of 6 Sep 2026: a dead listener still holding the port. Heal it once.
        killed = _kill_port(PORT)
        time.sleep(2)
        r = subprocess.run([sys.executable, str(BROWSERD), "start", "--profile", "reddit", "--port", str(PORT)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        line = (r.stdout.strip().splitlines() or [r.stderr[-300:]])[-1]
        try:
            j = json.loads(line); j["healed"] = {"killed_pids": killed}
        except Exception:
            j = {"ok": False, "error": line[:200], "healed": {"killed_pids": killed}}
    return j


def bd(*args, timeout=90):
    """Run one browserd op and return its JSON."""
    r = subprocess.run([sys.executable, str(BROWSERD), *args, "--port", str(PORT)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    line = (r.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        return json.loads(line)
    except Exception:
        return {"ok": False, "error": f"browserd returned non-JSON: {line[:200]}", "code": 1}


def get_json(url, _healed=False):
    """Render a Reddit JSON endpoint in the real browser and parse the page text."""
    wait = PAUSE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    g = bd("goto", url)
    _last[0] = time.time()
    if not g.get("ok"):
        err = str(g.get("error"))
        if not _healed and ("goto failed" in err or "None" in err or "closed" in err.lower() or "daemon" in err.lower()):
            h = start_daemon()
            if h.get("ok"):
                return get_json(url, _healed=True)
        raise RuntimeError(f"goto failed: {g.get('error')}")
    landed = g.get("url", "")
    if "/login" in landed or "reason=lor" in landed:
        raise RuntimeError("LOGIN WALL: Reddit redirected to login. Log in once in the browserd window, then retry.")
    t = bd("text", "--max", "800000")   # browserd truncates at 20k by default; threads run long
    if not t.get("ok"):
        raise RuntimeError(f"text failed: {t.get('error')}")
    txt = t.get("text", "").strip()
    if txt.startswith("{") or txt.startswith("["):
        try:
            return json.loads(txt)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"JSON cut short at {e.pos} of {len(txt)} chars — raise --max in bd('text')")
    if "rate limit" in txt.lower() or "too many requests" in txt.lower():
        raise RateLimited("RATE LIMITED by Reddit — stop, wait, do not retry in a loop.")
    if "blocked" in txt.lower() and "network" in txt.lower():
        raise RuntimeError("BLOCKED: Reddit's network-security page. Stop; do not fight it.")
    raise RuntimeError(f"unexpected page (not JSON): {txt[:160]!r}")


# ---------------------------------------------------------------- feed rung
def _strip(h):
    t = re.sub(r"<br\s*/?>|</p>|</li>|</blockquote>", "\n", h or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" +([.,;:!?])", r"\1", t)          # "<b>today</b>." leaves "today ." otherwise
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n\n+", "\n\n", t)
    return t.strip()


_backed_off = [False]


def feed(url, _retry=True):
    """Fetch one Atom feed anonymously; paced; ONE back-off per run on 429 (Retry-After or 60 s), then HALT."""
    wait = FEED_PAUSE - (time.time() - _feed_last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers=FEED_UA)
    _feed_last[0] = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            if _retry and not _backed_off[0]:
                _backed_off[0] = True
                try:
                    delay = max(60, int(e.headers.get("Retry-After", "60")))
                except ValueError:
                    delay = 60
                print(json.dumps({"note": f"429 from Reddit's feed — backing off once for {delay}s"}), file=sys.stderr)
                time.sleep(delay)
                return feed(url, _retry=False)
            raise RateLimited("429 from Reddit's feed twice — stop; the tool does not loop. Wait a few minutes before the next run.")
        if e.code == 403:
            raise RuntimeError("403 on the feed — Reddit is refusing this fetch; do not fight it (try the browser rung with --deep).")
        raise RuntimeError(f"feed HTTP {e.code} for {url}")
    except Exception as e:
        raise RuntimeError(f"feed unreachable: {e}")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise RuntimeError("feed returned non-XML (login or block page?) — halt")
    out = []
    for e in root.findall("a:entry", NS):
        a = e.find("a:author/a:name", NS)
        link = e.find("a:link", NS)
        cat = e.find("a:category", NS)
        out.append({"author": (a.text if a is not None else "") or "", "url": link.get("href") if link is not None else "",
                    "title": (e.findtext("a:title", default="", namespaces=NS) or "").strip(),
                    "published": (e.findtext("a:published", default="", namespaces=NS) or e.findtext("a:updated", default="", namespaces=NS) or "")[:10],
                    "sub": cat.get("term") if cat is not None else None,
                    "text": _strip(e.findtext("a:content", default="", namespaces=NS))})
    return out


def parse_atom(body_bytes):
    """Pure parser for --selftest (no network)."""
    root = ET.fromstring(body_bytes)
    return [{"author": (e.findtext("a:author/a:name", default="", namespaces=NS) or ""),
             "text": _strip(e.findtext("a:content", default="", namespaces=NS))} for e in root.findall("a:entry", NS)]


def feed_search(q, sub=None, num=25, sort="relevance", t="year"):
    base = f"https://www.reddit.com/r/{sub}/search.rss?restrict_sr=1&" if sub else "https://www.reddit.com/search.rss?"
    rows = feed(base + urllib.parse.urlencode({"q": q, "sort": sort, "t": t, "limit": min(num, 100)}))
    return [{"sub": r["sub"], "title": r["title"], "author": r["author"], "created": r["published"], "url": r["url"],
             "self": r["text"][:400], "rung": "feed"} for r in rows[:num]]


def feed_new(sub, num=25):
    rows = feed(f"https://www.reddit.com/r/{sub}/new.rss?limit={min(num, 100)}")
    return [{"sub": sub, "title": r["title"], "author": r["author"], "created": r["published"], "url": r["url"], "self": r["text"][:400], "rung": "feed"} for r in rows[:num]]


def feed_thread(url, max_comments=80):
    rows = feed(url.split("?")[0].rstrip("/") + ".rss?limit=100")
    if not rows:
        raise RuntimeError("thread feed empty (removed, private, or quarantined?)")
    post, comments = rows[0], rows[1:]
    body = re.sub(r"\s*submitted by\s+/u/\S+.*$", "", post["text"], flags=re.S).strip()   # the feed's trailer, not the poster's words
    return {"title": post["title"], "sub": post["sub"], "author": post["author"], "created": post["published"], "url": url,
            "body": body[:3000], "rung": "feed", "note": "feed rung: top-level comments, no scores — add --deep for the browser rung",
            "comments": [{"author": c["author"], "created": c["published"], "body": c["text"][:1200], "url": c["url"]}
                         for c in comments[:max_comments] if not _noise(c["author"], c["text"])]}


def _noise(author, body):
    """Deleted/removed comments, AutoModerator and mod-team removal notices are not voices."""
    a = (author or "").lower(); b = (body or "").strip().lower()
    return (not a) or a.endswith("-modteam") or a == "/u/automoderator" or a == "automoderator" or b in ("[deleted]", "[removed]", "") \
        or b.startswith("unfortunately, your comment has been removed") or b.startswith("unfortunately, your submission has been removed")


# ---------------------------------------------------------------- browser-rung readers (unchanged)
def sub_rules(sub):
    j = get_json(f"https://www.reddit.com/r/{sub}/about/rules.json")
    return [{"n": i + 1, "name": r.get("short_name"), "kind": r.get("kind"),
             "text": (r.get("description") or "").strip()} for i, r in enumerate(j.get("rules", []))]


def sub_about(sub):
    j = get_json(f"https://www.reddit.com/r/{sub}/about.json").get("data", {})
    keys = ["display_name", "title", "subscribers", "active_user_count", "public_description",
            "submission_type", "allow_images", "restrict_posting", "user_is_banned", "user_is_muted",
            "user_is_subscriber", "over18", "created_utc", "subreddit_type"]
    out = {k: j.get(k) for k in keys}
    out["description_md"] = (j.get("description") or "")[:4000]
    return out


def search(q, sub=None, num=25, t="year"):
    base = f"https://www.reddit.com/r/{sub}/search.json?restrict_sr=1&" if sub else "https://www.reddit.com/search.json?"
    j = get_json(base + urllib.parse.urlencode({"q": q, "sort": "relevance", "t": t, "limit": min(num, 100)}))
    rows = []
    for c in j.get("data", {}).get("children", []):
        d = c.get("data", {})
        rows.append({"sub": d.get("subreddit"), "title": d.get("title"), "score": d.get("score"),
                     "comments": d.get("num_comments"), "created": datetime.datetime.fromtimestamp(d.get("created_utc", 0), datetime.timezone.utc).date().isoformat(),
                     "url": "https://www.reddit.com" + d.get("permalink", ""), "flair": d.get("link_flair_text"),
                     "self": (d.get("selftext") or "")[:400], "rung": "browser"})
    return rows


def thread(url, max_comments=80):
    u = url.split("?")[0].rstrip("/") + ".json?limit=200"
    j = get_json(u)
    post = j[0]["data"]["children"][0]["data"]
    out = {"title": post.get("title"), "sub": post.get("subreddit"), "author": "/u/" + str(post.get("author") or "?"),
           # measured 6 Sep 2026: anonymous rendering returns score 0 on a 52-comment post — scores are only trustworthy logged in
           "score": post.get("score") if post.get("score") else None,
           "created": datetime.datetime.fromtimestamp(post.get("created_utc", 0), datetime.timezone.utc).date().isoformat(),
           "url": url, "body": (post.get("selftext") or "")[:3000], "rung": "browser", "comments": []}
    def walk(children, depth=0):
        for c in children:
            if len(out["comments"]) >= max_comments:
                return
            d = c.get("data", {})
            if c.get("kind") != "t1":
                continue
            if not _noise(d.get("author"), d.get("body")):
                out["comments"].append({"depth": depth, "score": d.get("score"), "author": d.get("author"),
                                        "created": datetime.datetime.fromtimestamp(d.get("created_utc", 0), datetime.timezone.utc).date().isoformat(),
                                        "body": (d.get("body") or "")[:1200]})
            rep = d.get("replies")
            if isinstance(rep, dict):
                walk(rep.get("data", {}).get("children", []), depth + 1)
    walk(j[1]["data"]["children"])
    return out


def whoami():
    try:
        j = get_json("https://www.reddit.com/api/me.json")
    except RuntimeError as e:
        return {"logged_in": False, "error": str(e)}
    d = j.get("data", {}) if isinstance(j, dict) else {}
    return {"logged_in": bool(d.get("name")), "name": d.get("name"), "comment_karma": d.get("comment_karma"),
            "link_karma": d.get("link_karma")}


def recon(subs):
    DOSSIERS.mkdir(parents=True, exist_ok=True)
    me = whoami()
    written = []
    for sub in subs:
        try:
            a = sub_about(sub)
            r = sub_rules(sub)
            top = search("*", sub=sub, num=10, t="month")
        except RuntimeError as e:
            print(json.dumps({"sub": sub, "ok": False, "error": str(e)}))
            continue
        lines = [f"# r/{sub} — recon dossier", f"Pulled {datetime.datetime.utcnow().isoformat(timespec='minutes')}Z via scripts/reddit.py (read-only). "
                 f"Account context: {'logged in as u/' + str(me.get('name')) if me.get('logged_in') else 'NOT logged in — user_is_banned unknown'}.", "",
                 "## Standing", f"- subscribers: {a.get('subscribers')} · active now: {a.get('active_user_count')} · type: {a.get('subreddit_type')} · posts allowed: {a.get('submission_type')}",
                 f"- **user_is_banned: {a.get('user_is_banned')}** · muted: {a.get('user_is_muted')} · subscribed: {a.get('user_is_subscriber')}",
                 f"- blurb: {a.get('public_description')}", "", "## Rules (verbatim, numbered as the sub numbers them)"]
        for x in r:
            lines.append(f"{x['n']}. **{x['name']}** ({x['kind']}) — {x['text']}")
        lines += ["", "## Sidebar / wiki text (first 4000 chars, verbatim)", "", a.get("description_md") or "(none)", "",
                  "## What survives — top posts this month (title · score · comments · flair)"]
        for p in top:
            lines.append(f"- {p['title']} · {p['score']} · {p['comments']} · {p['flair'] or '-'} · {p['url']}")
        lines += ["", "## Verdict (to be filled by the reviewer, not by this script)",
                  "- self-promo / links allowed? (quote the rule number):", "- AutoMod gates seen (min karma/age, flair required):",
                  "- value-first angle for us (which page's facts answer which recurring question):", "- GO / NO-GO:", ""]
        p = DOSSIERS / f"{sub}.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        written.append(str(p.relative_to(ROOT)))
        print(json.dumps({"sub": sub, "ok": True, "banned": a.get("user_is_banned"), "rules": len(r), "file": written[-1]}))
    return written


# ---------------------------------------------------------------- voices: what people actually say
def _browser_up():
    return bd("status", timeout=20).get("ok", False)


def voices(topic, subs, num=8, t="year", max_comments=40, out_path=None, deep=False):
    """Topic + subs -> the posts and comments people wrote, verbatim, with author/date/permalink.
    Rung: feed by default (needs nothing running; measured 6 Sep 2026: ~10 anonymous feed requests then
    429 for over a minute). When the feed rate-limits and the browser daemon is up, the rest of the
    run moves to the browser rung (scores + nested replies). --deep starts on the browser rung.
    Writes a partial file and exits 3 only if BOTH rungs are shut."""
    VOICES.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40]
    day = datetime.date.today().isoformat()
    out = pathlib.Path(out_path) if out_path else VOICES / f"{slug}_{day}.md"
    rung = ["browser" if deep else "feed"]
    L = [f"# Reddit voices — “{topic}”", f"Pulled {day} by `py scripts/reddit.py voices` (read-only). "
         f"Subs: {', '.join('r/' + s for s in subs)}. Quotes are VERBATIM; treat as evidence of language and pain, not as fact. "
         "Rung per thread is marked: feed = Reddit's public Atom (no scores, top-level comments); browser = rendered JSON (scores, replies).", ""]
    stats = {"threads": 0, "comments": 0, "authors": set(), "halted": None, "switched": False}
    seen = set()

    def do_search(sub):
        if rung[0] == "feed":
            try:
                return feed_search(topic, sub=sub, num=num, sort="relevance", t=t)
            except RateLimited:
                if _browser_up():
                    rung[0] = "browser"; stats["switched"] = True
                else:
                    raise
        rows = search(topic, sub=sub, num=num, t=t)
        return rows

    def do_thread(url):
        if rung[0] == "feed":
            try:
                return feed_thread(url, max_comments=max_comments)
            except RateLimited:
                if _browser_up():
                    rung[0] = "browser"; stats["switched"] = True
                else:
                    raise
        th = thread(url, max_comments)
        th["author"] = th.get("author") or ""
        for c in th["comments"]:
            c.setdefault("created", ""); c.setdefault("author", "")
        return th

    try:
        for sub in subs:
            L += [f"## r/{sub}", ""]
            try:
                posts = do_search(sub)
            except RateLimited:
                raise
            except RuntimeError as e:
                L += [f"_(search failed: {e})_", ""]; continue
            if not posts:
                L += ["_(no posts matched)_", ""]; continue
            for p in posts:
                if p["url"] in seen:
                    continue
                seen.add(p["url"])
                try:
                    th = do_thread(p["url"])
                except RateLimited:
                    raise
                except RuntimeError as e:
                    L += [f"### {p['title']}", f"{p.get('author', '')} · {p['created']} · {p['url']}", f"_(thread unreadable: {e})_", ""]; continue
                stats["threads"] += 1; stats["authors"].add(th.get("author") or p.get("author") or "")
                score = f" · score {th['score']}" if th.get("score") is not None else ""
                L += [f"### {th['title']}", f"{th.get('author') or p.get('author', '')} · {th['created']}{score} · {th['url']} · rung {th['rung']}", ""]
                if th.get("body"):
                    L += ["> " + th["body"][:1500].replace("\n", "\n> "), ""]
                for c in th["comments"]:
                    stats["comments"] += 1; stats["authors"].add(c.get("author") or "")
                    sc = f" · {c['score']}" if c.get("score") is not None else ""
                    ind = "  " * c.get("depth", 0)
                    L += [f"{ind}- **{c.get('author') or '?'}**{sc} ({c.get('created') or ''}): " + (c["body"] or "")[:900].replace("\n", " ")]
                L += [""]
    except RateLimited as e:
        stats["halted"] = str(e)
        L += ["", f"**HALTED: {e}** — everything above is complete; rerun later for the rest.", ""]
    L += ["---", f"Threads {stats['threads']} · comments {stats['comments']} · distinct authors {len(stats['authors'])}"
          + (" · switched feed→browser mid-run" if stats["switched"] else "") + (" · HALTED on 429" if stats["halted"] else "")]
    out.write_text("\n".join(L), encoding="utf-8")
    return {"ok": stats["halted"] is None, "file": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out),
            "threads": stats["threads"], "comments": stats["comments"], "authors": len(stats["authors"]),
            "rung_end": rung[0], "switched": stats["switched"], "halted": stats["halted"]}


# ---------------------------------------------------------------- selftest (no network)
SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/alice</name></author><category term="LegalAdviceUK"/><content type="html">&lt;div&gt;&lt;p&gt;Got a &amp;pound;100 charge &lt;b&gt;today&lt;/b&gt;.&lt;/p&gt;&lt;p&gt;Advice?&lt;/p&gt;&lt;/div&gt;</content>
<id>t3_a</id><link href="https://www.reddit.com/r/LegalAdviceUK/comments/a/x/"/><updated>2026-09-05T11:15:27+00:00</updated><published>2026-09-05T11:15:27+00:00</published><title>Parking charge</title></entry>
<entry><author><name>/u/AutoModerator</name></author><content type="html">Welcome</content><id>t1_b</id><link href="https://www.reddit.com/r/LegalAdviceUK/comments/a/x/b/"/><updated>2026-09-05T11:15:28+00:00</updated><title>c</title></entry>
</feed>"""


def selftest():
    rows = parse_atom(SAMPLE)
    checks = [
        ("two entries parsed", len(rows) == 2),
        ("author kept", rows[0]["author"] == "/u/alice"),
        ("html stripped + entity decoded", rows[0]["text"] == "Got a £100 charge today.\n\nAdvice?" or (
            "£100" in rows[0]["text"] and "<" not in rows[0]["text"] and rows[0]["text"].endswith("Advice?"))),
        ("slug shape", re.sub(r"[^a-z0-9]+", "-", "Parking Charge!! notice".lower()).strip("-") == "parking-charge-notice"),
        ("no write ops exist", not any(hasattr(sys.modules[__name__], n) for n in ("post", "comment", "vote", "submit"))),
    ]
    bad = [n for n, ok in checks if not ok]
    print(json.dumps({"selftest": "PASS" if not bad else "FAIL", "checks": len(checks), "failed": bad}))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("op")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--sub")
    ap.add_argument("--subs", nargs="*")
    ap.add_argument("--num", type=int, default=25)
    ap.add_argument("--time", default="year")
    ap.add_argument("--max-comments", type=int, default=80)
    ap.add_argument("--deep", action="store_true", help="use the browser rung (scores, nested replies)")
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        if a.op == "start":
            print(json.dumps(start_daemon())); return
        if a.op == "selftest":
            sys.exit(selftest())
        if a.op == "rules": out = sub_rules(a.args[0])
        elif a.op == "about": out = sub_about(a.args[0])
        elif a.op == "search":
            q = " ".join(a.args)
            out = search(q, sub=a.sub, num=a.num, t=a.time) if a.deep else feed_search(q, sub=a.sub, num=a.num, t=a.time)
        elif a.op == "new": out = feed_new(a.args[0], num=a.num)
        elif a.op == "thread": out = thread(a.args[0], a.max_comments) if a.deep else feed_thread(a.args[0], a.max_comments)
        elif a.op == "whoami": out = whoami()
        elif a.op == "recon": out = recon(a.args); print(json.dumps({"ok": True, "written": out})); return
        elif a.op == "voices":
            r = voices(" ".join(a.args), a.subs or ["LegalAdviceUK"], num=min(a.num, 25) if a.num != 25 else 8, t=a.time,
                       max_comments=min(a.max_comments, 80), out_path=a.out, deep=a.deep)
            print(json.dumps(r)); sys.exit(0 if r["ok"] else 3)
        else: sys.exit(f"unknown op {a.op}")
        print(json.dumps(out, ensure_ascii=False, indent=1))
    except RateLimited as e:
        print(json.dumps({"ok": False, "error": str(e)})); sys.exit(3)
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": str(e)})); sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
