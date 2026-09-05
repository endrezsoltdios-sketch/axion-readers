"""yt_reply.py -- post YouTube comment replies from a fire sheet. Human-fired only.

Built 6 Sep 2026 when the user asked "can't you paste the answers to them?" about
business/content/youtube_replies/2026-09-05_batch.md. Rules it enforces, from that file:
  - one reply per target; the target is found by its VERBATIM text, never by position
  - nothing posts without --post (dry run prints what WOULD go out)
  - the channel that speaks is printed first (channels?mine=true) and must match --as
  - a target already replied to by this channel is skipped (idempotent re-runs)
  - every send is appended to the ledger JSONL next to the fire sheet

Usage:
  py scripts/yt_reply.py QUEUE.json                    # dry run
  py scripts/yt_reply.py QUEUE.json --post --as "<your channel name>"
  py scripts/yt_reply.py QUEUE.json --only R1,R3       # subset

QUEUE.json: [{"id":"R1","video":"<videoId>","needle":"<text inside the target comment>",
              "reply":"<exact text to post>"}, ...]
Token: youtube_token.json (scope youtube.force-ssl) unless --token names another.
"""
import json, sys, time, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://www.googleapis.com/youtube/v3/"


def http(url, body=None, headers=None):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def token(tf):
    d = json.loads((ROOT / tf).read_text(encoding="utf-8"))
    body = urllib.parse.urlencode({"client_id": d["client_id"], "client_secret": d["client_secret"],
                                   "refresh_token": d["refresh_token"], "grant_type": "refresh_token"}).encode()
    st, txt = http(d.get("token_uri", "https://oauth2.googleapis.com/token"), body,
                   {"Content-Type": "application/x-www-form-urlencoded"})
    if st != 200:
        sys.exit("token refresh failed for %s: HTTP %s" % (tf, st))
    return json.loads(txt)["access_token"]


def get(H, path, **q):
    st, txt = http(API + path + "?" + urllib.parse.urlencode(q), headers=H)
    if st != 200:
        sys.exit("GET %s -> %s %s" % (path, st, txt[:300]))
    return json.loads(txt)


def find_target(H, video, needle):
    """Top-level comment whose text contains the needle. Returns (id, author, likes, date, text)."""
    n = needle.lower()
    for order in ("relevance", "time"):
        page = None
        seen = 0
        while True:
            q = dict(part="snippet", maxResults=100, order=order, videoId=video)
            if page:
                q["pageToken"] = page
            d = get(H, "commentThreads", **q)
            for it in d.get("items", []):
                s = it["snippet"]["topLevelComment"]["snippet"]
                if n in s["textOriginal"].lower():
                    return (it["snippet"]["topLevelComment"]["id"], s["authorDisplayName"],
                            s.get("likeCount", 0), s["publishedAt"][:10], s["textOriginal"])
            seen += len(d.get("items", []))
            page = d.get("nextPageToken")
            if not page or seen > 3000:
                break
    return None


def already_replied(H, parent_id, my_channel_id):
    d = get(H, "comments", part="snippet", parentId=parent_id, maxResults=100)
    for it in d.get("items", []):
        if it["snippet"].get("authorChannelId", {}).get("value") == my_channel_id:
            return it["snippet"]["textOriginal"][:80]
    return None


def post_reply(H, parent_id, text):
    body = json.dumps({"snippet": {"parentId": parent_id, "textOriginal": text}}).encode()
    st, txt = http(API + "comments?part=snippet", body, {**H, "Content-Type": "application/json"})
    return st, txt


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    queue_path = Path(args[0])
    post = "--post" in args
    tf = args[args.index("--token") + 1] if "--token" in args else "youtube_token.json"
    want_as = args[args.index("--as") + 1] if "--as" in args else None
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    ledger = queue_path.with_suffix(".ledger.jsonl")

    H = {"Authorization": "Bearer " + token(tf)}
    me = get(H, "channels", part="snippet", mine="true")["items"][0]
    my_id, my_title = me["id"], me["snippet"]["title"]
    print("speaking as: %s (%s) via %s" % (my_title, my_id, tf))
    if post and (not want_as or want_as.lower() not in my_title.lower()):
        sys.exit("refusing to post: --as %r does not match the token's channel %r" % (want_as, my_title))

    fired = 0
    for item in queue:
        if only and item["id"] not in only:
            continue
        hit = find_target(H, item["video"], item["needle"])
        if not hit:
            print("[%s] TARGET NOT FOUND on %s for needle %r" % (item["id"], item["video"], item["needle"][:50]))
            continue
        pid, author, likes, date, text = hit
        dup = already_replied(H, pid, my_id)
        line = "[%s] %s | %s | %s likes | %s" % (item["id"], item["video"], author, likes, date)
        if dup:
            print(line + " | ALREADY REPLIED: %r" % dup)
            continue
        if not post:
            print(line + " | WOULD POST %d chars" % len(item["reply"]))
            continue
        st, txt = post_reply(H, pid, item["reply"])
        ok = st in (200, 201)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": item["id"], "video": item["video"],
               "parent": pid, "author": author, "status": st, "ok": ok, "channel": my_title}
        if ok:
            rec["comment_id"] = json.loads(txt).get("id")
        else:
            rec["error"] = txt[:300]
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print(line + (" | POSTED %s" % rec.get("comment_id") if ok else " | FAILED %s %s" % (st, txt[:200])))
        fired += ok
        time.sleep(4)
    print("fired: %d (%s)" % (fired, "LIVE" if post else "dry run"))


if __name__ == "__main__":
    main()
