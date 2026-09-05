#!/usr/bin/env python3
"""tiktok_read — 0-token reader for TikTok's PUBLIC rehydration payload.

Built 5 Sep 2026 to reproduce (and then keep watching) the indexing defect found by
the demand-map team: `indexEnabled` absent on 7/7 of our posts, true on 30/30
competitors; `textExtra` hashtags empty on 7/7 of ours vs a competitor median of 5.
See business/research/demand_map_2026-09-05/tiktok/FINDINGS.md and the diagnosis in
business/content/tiktok/INDEXING_2026-09-05.md.

  py scripts/tiktok_read.py https://www.tiktok.com/@who/video/123   # one video
  py scripts/tiktok_read.py @somecreator                          # profile + last N posts
  py scripts/tiktok_read.py @a @b --json                            # machine output
  py scripts/tiktok_read.py --gate @somecreator                   # exit 1 if any post is broken

No login, no API key, no tokens. The instrument is the same one the research team
used: TikTok server-renders `__UNIVERSAL_DATA_FOR_REHYDRATION__` into the page HTML,
`__DEFAULT_SCOPE__["webapp.video-detail"].itemInfo.itemStruct` for a video and
`["webapp.user-detail"].userInfo` for a profile. A plain urllib GET is enough for
video pages. Where it is not (TikTok sometimes serves a JS-only shell), we fall back
to the browser door and wait ~6s for the payload to SETTLE — measured by the team:
at 2s EVERY video reads as zero hashtags, which is exactly the artifact that nearly
shipped as "nobody uses hashtags". Never read this payload without the settle wait.

A profile page does NOT server-render its video list, so `@handle` resolves post ids
from (1) business/content/shorts/POSTED.jsonl — our own factory's log, door-free —
and (2) the browser door, if it happens to be up. `--gate` therefore works as an
unattended canary with no browser running.

MASKING: nothing personal is printed. Bio text is passed through mask_personal()
which redacts emails and phone-shaped runs before anything reaches stdout or a file.

GATE semantics (exit codes):
  0  every checked post has indexEnabled true AND >=1 parsed hashtag
  1  at least one post fails (LOUD: the offending ids are printed)
  2  could not check (no ids resolvable, or every fetch failed) — not a pass
"""
import argparse, json, os, re, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTED = ROOT / "business" / "content" / "shorts" / "POSTED.jsonl"
TIKTOK_PORT = 8770
SETTLE_SECS = 6  # measured 5 Sep 2026: below ~6s textExtra reads empty on EVERY video
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

PAYLOAD_RE = re.compile(
    r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)
VIDEO_URL_RE = re.compile(r'tiktok\.com/@([\w.\-]+)/video/(\d+)')
BARE_ID_RE = re.compile(r'^\d{6,}$')

# ── masking ──────────────────────────────────────────────────────────────────
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
PHONE_RE = re.compile(r'(?<!\d)(?:\+\d[\d ()\-]{7,}\d)(?!\d)')


def mask_personal(text, limit=160):
    """Redact anything person-shaped before it reaches stdout or a file."""
    if not text:
        return ""
    t = EMAIL_RE.sub("[email]", str(text))
    t = PHONE_RE.sub("[phone]", t)
    t = " ".join(t.split())
    return t[:limit] + ("..." if len(t) > limit else "")


# ── fetch ────────────────────────────────────────────────────────────────────
def _plain_get(url, timeout=45):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _door(op, *args, port=TIKTOK_PORT, timeout=120):
    cmd = [sys.executable, str(ROOT / "scripts" / "browserd.py"), op,
           *[str(x) for x in args], "--port", str(port)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT),
                           timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"browserd {op} exceeded {timeout}s"}
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": ((r.stdout or "") + (r.stderr or ""))[-300:]}


def _scope_from_html(html):
    m = PAYLOAD_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1)).get("__DEFAULT_SCOPE__") or None
    except Exception:
        return None


def fetch_scope(url, settle=SETTLE_SECS, port=TIKTOK_PORT, allow_door=True):
    """Return (scope_dict, how). Plain GET first; browser door with a settle wait
    only when the plain read comes back without a usable payload."""
    try:
        scope = _scope_from_html(_plain_get(url))
    except Exception as e:
        scope = None
        err = f"{type(e).__name__}: {e}"
    else:
        err = "payload absent from plain HTML"
    if scope and ("webapp.video-detail" in scope or "webapp.user-detail" in scope):
        return scope, "plain"
    if not allow_door:
        return None, f"plain-failed ({err}); door not tried"
    st = _door("status", port=port, timeout=30)
    if not st.get("ok"):
        return None, f"plain-failed ({err}); door down on {port}"
    g = _door("goto", url, port=port, timeout=120)
    if not g.get("ok"):
        return None, f"plain-failed ({err}); door goto failed: {g.get('error')}"
    time.sleep(settle)  # SETTLE — see module docstring; do not shorten
    r = _door("eval", "document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__')"
                      "?.textContent || ''", port=port, timeout=120)
    raw = r.get("result") or ""
    try:
        scope = json.loads(raw).get("__DEFAULT_SCOPE__")
    except Exception:
        scope = None
    return (scope, f"door(settle={settle}s)") if scope else (None, "door payload empty")


# ── parse ────────────────────────────────────────────────────────────────────
def read_video(url, settle=SETTLE_SECS, port=TIKTOK_PORT, allow_door=True):
    scope, how = fetch_scope(url, settle, port, allow_door)
    m = VIDEO_URL_RE.search(url)
    out = {"kind": "video", "url": url, "handle": m.group(1) if m else None,
           "id": m.group(2) if m else None, "read_via": how, "ok": False}
    det = (scope or {}).get("webapp.video-detail") or {}
    it = (det.get("itemInfo") or {}).get("itemStruct")
    if not it:
        out["error"] = det.get("statusMsg") or "no itemStruct in payload"
        out["status_code"] = det.get("statusCode")
        return out
    stats = it.get("stats") or {}
    tags = [t.get("hashtagName") for t in (it.get("textExtra") or [])
            if t.get("hashtagName")]
    subs = (it.get("video") or {}).get("subtitleInfos") or []
    out.update({
        "ok": True,
        "id": it.get("id") or out["id"],
        "handle": (it.get("author") or {}).get("uniqueId") or out["handle"],
        "created": it.get("createTime"),
        "duration_s": (it.get("video") or {}).get("duration"),
        "playCount": stats.get("playCount"),
        "diggCount": stats.get("diggCount"),
        # ABSENT is the finding. Never coerce a missing key to False — the whole
        # diagnosis rests on absent-vs-true, and False would hide which it was.
        "indexEnabled": it.get("indexEnabled", "ABSENT"),
        "hashtags": tags,
        "hashtag_count": len(tags),
        "caption_hash_marks": (it.get("desc") or "").count("#"),
        "IsAigc": it.get("IsAigc", "ABSENT"),
        "ShowAIGC": it.get("ShowAIGC", "ABSENT"),
        "has_asr": bool(subs),
        "asr_langs": sorted({s.get("LanguageCodeName") for s in subs if s.get("LanguageCodeName")}),
        "penalty_keys": sorted((it.get("penaltyContext") or {}).keys()),
        "penaltyContext": it.get("penaltyContext") or {},
        # 5 Sep 2026: the field that actually separates our posts from the
        # controls. diversificationId is the class TikTok's recommender assigns
        # a video; it is null on 7/7 of ours and set on every control, including
        # ones whose penaltyContext is byte-identical to ours. No classification
        # means no distribution, which is upstream of indexing.
        "diversificationId": it.get("diversificationId"),
        "diversificationLabels": it.get("diversificationLabels") or [],
        "suggestedWords": it.get("suggestedWords") or [],
        "isReviewing": it.get("isReviewing"),
        "privateItem": it.get("privateItem"),
        "desc": mask_personal(it.get("desc")),
    })
    return out


def read_profile_meta(handle, settle=SETTLE_SECS, port=TIKTOK_PORT, allow_door=True):
    h = handle.lstrip("@")
    url = f"https://www.tiktok.com/@{h}"
    scope, how = fetch_scope(url, settle, port, allow_door)
    out = {"kind": "profile", "handle": h, "url": url, "read_via": how, "ok": False}
    ui = ((scope or {}).get("webapp.user-detail") or {}).get("userInfo") or {}
    user, st = ui.get("user") or {}, ui.get("stats") or {}
    if not user:
        out["error"] = "no userInfo in payload"
        return out
    bl = user.get("bioLink") or {}
    out.update({"ok": True,
                "bioLink": (bl.get("link") if isinstance(bl, dict) else bl) or None,
                "signature": mask_personal(user.get("signature")),
                "followers": st.get("followerCount"), "videos": st.get("videoCount"),
                "private": user.get("privateAccount"), "verified": user.get("verified")})
    return out


def ids_from_posted_log(handle):
    """Our own factory's log — door-free, so the gate runs unattended."""
    h, seen, urls = handle.lstrip("@"), set(), []
    if not POSTED.exists():
        return urls
    for line in POSTED.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        u = rec.get("url") or ""
        if rec.get("rail") != "tiktok" or not rec.get("ok"):
            continue
        m = VIDEO_URL_RE.search(u)
        if m and m.group(1).lower() == h.lower() and m.group(2) not in seen:
            seen.add(m.group(2))
            urls.append(f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}")
    urls.reverse()  # newest first
    return urls


def ids_from_door(handle, settle=SETTLE_SECS, port=TIKTOK_PORT):
    h = handle.lstrip("@")
    if not _door("status", port=port, timeout=30).get("ok"):
        return []
    if not _door("goto", f"https://www.tiktok.com/@{h}", port=port, timeout=120).get("ok"):
        return []
    time.sleep(settle)
    r = _door("eval", "Array.from(document.querySelectorAll('a[href*=\"/video/\"]'))"
                      ".map(a=>a.href)", port=port, timeout=120)
    out, seen = [], set()
    for u in (r.get("result") or []):
        m = VIDEO_URL_RE.search(u)
        if m and m.group(1).lower() == h.lower() and m.group(2) not in seen:
            seen.add(m.group(2))
            out.append(f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}")
    return out


def resolve_posts(handle, limit, settle, port, allow_door=True):
    urls = ids_from_door(handle, settle, port) if allow_door else []
    src = "door"
    if not urls:
        urls, src = ids_from_posted_log(handle), "POSTED.jsonl"
    return urls[:limit], src


# ── report ───────────────────────────────────────────────────────────────────
def line_for(v):
    if not v.get("ok"):
        return f"  ! {v.get('id') or v.get('url')}  READ FAILED: {v.get('error')} [{v['read_via']}]"
    idx = v["indexEnabled"]
    flag = "OK " if idx is True else "!! "
    tags = ",".join(v["hashtags"]) if v["hashtags"] else "(none)"
    return (f"  {flag}{v['id']}  plays={v['playCount']:<8} indexEnabled={idx!s:<7} "
            f"tags={v['hashtag_count']} in-caption#={v['caption_hash_marks']} "
            f"aigc={v['IsAigc']} asr={'y' if v['has_asr'] else 'n'} [{tags}]")


def check_post(v):
    """The two defects, as a pure predicate. Returns list of failure reasons."""
    bad = []
    if not v.get("ok"):
        return ["unreadable"]
    if v["indexEnabled"] is not True:
        bad.append(f"indexEnabled={v['indexEnabled']}")
    if v["hashtag_count"] == 0:
        bad.append(f"0 parsed hashtags (caption carries {v['caption_hash_marks']} '#')")
    return bad


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("targets", nargs="+", help="video URL(s) and/or @handle(s)")
    ap.add_argument("--limit", type=int, default=10, help="posts per @handle (default 10)")
    ap.add_argument("--settle", type=float, default=SETTLE_SECS)
    ap.add_argument("--port", type=int, default=TIKTOK_PORT)
    ap.add_argument("--no-door", action="store_true", help="plain fetch only")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="write the full JSON result here as well")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 if any checked post lacks indexEnabled or has 0 parsed hashtags")
    a = ap.parse_args()
    allow_door = not a.no_door

    report, videos = {"read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "targets": a.targets, "profiles": [], "videos": []}, []
    for t in a.targets:
        t = t.strip()
        if t.startswith("@") or (not t.startswith("http") and not BARE_ID_RE.match(t)):
            prof = read_profile_meta(t, a.settle, a.port, allow_door)
            urls, src = resolve_posts(t, a.limit, a.settle, a.port, allow_door)
            prof["post_source"], prof["posts_found"] = src, len(urls)
            report["profiles"].append(prof)
            for u in urls:
                videos.append(read_video(u, a.settle, a.port, allow_door))
        else:
            videos.append(read_video(t, a.settle, a.port, allow_door))
    report["videos"] = videos

    failures = {}
    for v in videos:
        bad = check_post(v)
        if bad:
            failures[v.get("id") or v.get("url")] = bad
    report["checked"] = len(videos)
    report["failures"] = failures

    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    if a.json:
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        for p in report["profiles"]:
            if p.get("ok"):
                print(f"@{p['handle']}  followers={p['followers']} videos={p['videos']} "
                      f"bioLink={p['bioLink'] or 'NULL'}  [{p['read_via']}]")
                print(f"    bio: {p['signature']}")
                print(f"    posts resolved: {p['posts_found']} via {p['post_source']}")
            else:
                print(f"@{p['handle']}  PROFILE READ FAILED: {p.get('error')} [{p['read_via']}]")
        for v in videos:
            print(line_for(v))
        print(f"\nchecked={len(videos)} failing={len(failures)}")
        for k, why in failures.items():
            print(f"  FAIL {k}: {'; '.join(why)}")

    if a.gate:
        if not videos:
            print("GATE UNKNOWN: no posts resolved — not a pass", file=sys.stderr)
            return 2
        if all(not v.get("ok") for v in videos):
            print("GATE UNKNOWN: every read failed — not a pass", file=sys.stderr)
            return 2
        if failures:
            print(f"GATE FAIL: {len(failures)}/{len(videos)} posts broken — "
                  f"{json.dumps(failures)}", file=sys.stderr)
            return 1
        print(f"GATE PASS: {len(videos)} posts all indexEnabled=true with parsed hashtags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
