#!/usr/bin/env python3
"""reddit_api.py -- Reddit's OFFICIAL API rail (Fig Tree primitive, 6 Sep 2026).

Why: Reddit refuses logins in automation Chrome, so scripts/reddit.py stays read-only over
a browser. Reddit's own OAuth API is the door Reddit built for exactly this; a "script"
app on the user's account, created once by the user, gives read + write on that account
with Reddit's knowledge and rate limits (60 req/min). No evasion, no alt accounts.

ONE-TIME USER STEP (for anyone whose account Reddit has approved): reddit.com/prefs/apps -> "create another
app" -> type: script -> name: Axion door -> redirect uri: http://localhost:8080 ->
create. Then add four lines to the credentials file:
  REDDIT_CLIENT_ID=<the 14-char id under the app name>
  REDDIT_CLIENT_SECRET=<secret>
  REDDIT_USERNAME=<account>
  REDDIT_PASSWORD=<password>      (Reddit's password grant for script apps; 2FA must be off)

  py scripts/reddit_api.py whoami                          account, karma, created, is_suspended
  py scripts/reddit_api.py banned LegalAdviceUK AskUK     user_is_banned per sub (the rules-first fact)
  py scripts/reddit_api.py rules LegalAdviceUK
  py scripts/reddit_api.py search "parking charge appeal" [--sub LegalAdviceUK] [--limit 25]
  py scripts/reddit_api.py thread <id-or-url> [--max 60]
  py scripts/reddit_api.py comment <thing_id> --text-file draft.md --human-fired "<user's words>"
  py scripts/reddit_api.py post <sub> --title "..." --text-file body.md --human-fired "<user's words>"
Every write is refused without --human-fired and is appended to business/content/POSTS.ledger.jsonl.
Reddit CHARTER + rules-first law apply: recon dossier first, ban check first, one sub per day.
Exit 0 ok / 1 API error / 2 configured-out or refused.
"""
import argparse, base64, json, os, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "business" / "content" / "POSTS.ledger.jsonl"
UA = "AxionDoor/1.0 by u/{user} (+https://getaxionlabs.com)"


def env_key(name):
    v = os.environ.get(name)
    if v:
        return v
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import sales
        return sales.env_key(name)
    except Exception:
        return None


def creds():
    c = {k: env_key("REDDIT_" + k) for k in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD")}
    missing = [k for k, v in c.items() if not v]
    if missing:
        print("[configured-out] REDDIT_%s absent -- see the ONE-TIME USER STEP in scripts/reddit_api.py" % ", REDDIT_".join(missing))
        sys.exit(2)
    return c


def token(c):
    body = urllib.parse.urlencode({"grant_type": "password", "username": c["USERNAME"], "password": c["PASSWORD"]}).encode()
    auth = base64.b64encode(("%s:%s" % (c["CLIENT_ID"], c["CLIENT_SECRET"])).encode()).decode()
    req = urllib.request.Request("https://www.reddit.com/api/v1/access_token", data=body,
                                 headers={"Authorization": "Basic " + auth, "User-Agent": UA.format(user=c["USERNAME"])})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("[auth %s] %s" % (e.code, e.read()[:200])); sys.exit(1)
    if "access_token" not in d:
        print("[auth] %s" % d.get("error", d)); sys.exit(1)
    return d["access_token"]


class Door:
    def __init__(self):
        self.c = creds()
        self.H = {"Authorization": "bearer " + token(self.c), "User-Agent": UA.format(user=self.c["USERNAME"])}
        self._last = 0.0

    def call(self, path, params=None, data=None):
        gap = 1.1 - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)
        url = "https://oauth.reddit.com" + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None, headers=self.H)
        self._last = time.time()
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            print("[api %s] %s %s" % (e.code, path, e.read()[:200])); sys.exit(1)


def thing_id(s):
    if "/comments/" in s:
        return s.split("/comments/")[1].split("/")[0]
    return s.replace("t3_", "")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami")
    b = sub.add_parser("banned"); b.add_argument("subs", nargs="+")
    r = sub.add_parser("rules"); r.add_argument("sub")
    s = sub.add_parser("search"); s.add_argument("q"); s.add_argument("--sub"); s.add_argument("--limit", type=int, default=25); s.add_argument("--time", default="year")
    t = sub.add_parser("thread"); t.add_argument("id"); t.add_argument("--max", type=int, default=60)
    c = sub.add_parser("comment"); c.add_argument("thing"); c.add_argument("--text-file", required=True); c.add_argument("--human-fired", default="")
    po = sub.add_parser("post"); po.add_argument("sub"); po.add_argument("--title", required=True); po.add_argument("--text-file", required=True); po.add_argument("--human-fired", default="")
    a = p.parse_args()
    d = Door()

    if a.cmd == "whoami":
        me = d.call("/api/v1/me")
        print(json.dumps({k: me.get(k) for k in ("name", "total_karma", "comment_karma", "link_karma", "created_utc", "is_suspended", "verified")}))
    elif a.cmd == "banned":
        out = {}
        for sname in a.subs:
            ab = d.call("/r/%s/about" % sname).get("data", {})
            out[sname] = {"user_is_banned": ab.get("user_is_banned"), "user_is_subscriber": ab.get("user_is_subscriber"), "subscribers": ab.get("subscribers"), "restrict_posting": ab.get("restrict_posting")}
        print(json.dumps(out, indent=1))
    elif a.cmd == "rules":
        rules = d.call("/r/%s/about/rules" % a.sub).get("rules", [])
        for i, ru in enumerate(rules, 1):
            print("%d. %s -- %s" % (i, ru.get("short_name"), (ru.get("description") or "").replace("\n", " ")[:300]))
    elif a.cmd == "search":
        params = {"q": a.q, "limit": a.limit, "sort": "relevance", "t": a.time, "restrict_sr": "on" if a.sub else "off"}
        res = d.call("/r/%s/search" % a.sub if a.sub else "/search", params)
        for ch in res.get("data", {}).get("children", []):
            x = ch["data"]
            print(json.dumps({"id": x["id"], "sub": x["subreddit"], "score": x["score"], "comments": x["num_comments"], "created": time.strftime("%Y-%m-%d", time.gmtime(x["created_utc"])), "title": x["title"], "url": "https://www.reddit.com" + x["permalink"]}))
    elif a.cmd == "thread":
        res = d.call("/comments/%s" % thing_id(a.id), {"limit": a.max, "depth": 2})
        post = res[0]["data"]["children"][0]["data"]
        print(json.dumps({"title": post["title"], "sub": post["subreddit"], "score": post["score"], "selftext": post.get("selftext", "")[:2000]}))
        for ch in res[1]["data"]["children"][: a.max]:
            x = ch.get("data", {})
            if "body" in x:
                print(json.dumps({"id": "t1_" + x["id"], "score": x["score"], "author": x.get("author"), "body": x["body"][:600]}))
    else:
        if not a.human_fired or len(a.human_fired.strip()) < 8:
            print("[refused] writes run only with --human-fired \"<the user's own words>\""); sys.exit(2)
        text = Path(a.text_file).read_text(encoding="utf-8")
        if a.cmd == "comment":
            th = a.thing if a.thing.startswith(("t1_", "t3_")) else "t3_" + thing_id(a.thing)
            res = d.call("/api/comment", data={"api_type": "json", "thing_id": th, "text": text})
        else:
            res = d.call("/api/submit", data={"api_type": "json", "sr": a.sub, "kind": "self", "title": a.title, "text": text, "sendreplies": "true"})
        errs = res.get("json", {}).get("errors")
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "door": "reddit", "cmd": a.cmd, "target": a.thing if a.cmd == "comment" else a.sub, "human_fired": a.human_fired, "errors": errs, "result": res.get("json", {}).get("data")}
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print(json.dumps(rec))
        sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
