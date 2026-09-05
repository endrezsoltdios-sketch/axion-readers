#!/usr/bin/env python3
"""diffwatch.py — page change watcher. One job: tell me what changed on a page
since I last looked, and nothing when nothing changed.

Built 1 Sep 2026 (tool factory wave 2). Free competitor/price/policy monitoring:
no service, no subscription, no tokens. Text is extracted with web.py's own
extractor, so a diff is a diff in *readable* text, not in markup churn.

Usage:
  py scripts/diffwatch.py add popla-faq https://www.popla.co.uk/faqs
  py scripts/diffwatch.py add rival-pricing https://example.com/pricing --css-strip
  py scripts/diffwatch.py check                 # diff changed pages only (silent if none)
  py scripts/diffwatch.py check --commit        # ...and accept the new snapshots
  py scripts/diffwatch.py check --only popla    # name substring filter
  py scripts/diffwatch.py list                  # watched pages + last-change dates
  py scripts/diffwatch.py drop popla-faq

Store: knowledge/diffwatch/ (index.json + one .txt snapshot per page).
Cost: 0 tokens, 0 keys.
"""
import argparse
import difflib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import web  # noqa: E402  — same extraction as `py scripts/web.py`

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "knowledge" / "diffwatch"
INDEX = STORE / "index.json"
# Lines that change on every load and mean nothing: cookie banners, session ids,
# view counters, timestamps. --css-strip drops the noisiest of them.
NOISE = re.compile(
    r"(cookie|consent|csrf|session|nonce|\b\d{1,2}:\d{2}(:\d{2})?\b|"
    r"last updated|copyright\s*(©|\(c\))?\s*\d{4}|\b\d+\s+views?\b)", re.I)


def load_index():
    if not INDEX.exists():
        return {}
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.exit(f"[corrupt] {INDEX} is not valid JSON — fix or delete it")


def save_index(idx):
    STORE.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding="utf-8")


def snap_path(name):
    return STORE / f"{name}.txt"


def clean(text, css_strip):
    lines = [l for l in text.splitlines() if l.strip()]
    if css_strip:
        lines = [l for l in lines if not NOISE.search(l)]
    return "\n".join(lines) + "\n"


# A watch whose snapshot is empty is worse than no watch: it reports "no change"
# forever and reads exactly like "the policy did not move". Measured 5 Sep 2026 -
# tiktok.com/community-guidelines returns HTTP 200 with ZERO extractable text to
# the stdlib fetcher, so both TikTok watches first registered as empty files and
# would have been permanently, silently green. Anything under this is BLOCKED,
# not watched.
MIN_SNAPSHOT_CHARS = 200


def grab(url, css_strip):
    """(text, None) on success, (None, honest one-line reason) on failure.

    STDLIB FIRST, ALWAYS. The browser rung is reached only when the stdlib
    fetcher returns too little to diff, and that order is load-bearing, not a
    performance choice: browserd returns `document.body.innerText` while the
    stdlib path returns web.py's own structured extraction, so the SAME page read
    by the two rungs produces two different texts. Escalating a page that stdlib
    can already read would print one enormous fake diff, once, on a watcher whose
    entire job is to be trusted when it says something changed. Measured 5 Sep
    2026 on developers.google.com's changelog: 2,300+ diff lines, none of them a
    real change.

    So: stdlib, then browserd only for pages stdlib cannot read at all, then
    BLOCKED. A page that reports fewer than MIN_SNAPSHOT_CHARS on both rungs is
    not watched, it is recorded as unread."""
    import urllib.error
    try:
        body = ""
        try:
            _title, raw = web.page_text(url)
            body = clean(raw, css_strip)
        except urllib.error.HTTPError as e:
            if e.code not in (403, 404, 429, 503):
                raise
        if len(body.strip()) >= MIN_SNAPSHOT_CHARS:
            return body, None

        r = web.ladder_fetch(url)
        if not r.get("ok"):
            return None, f"[blocked:{r.get('rung')}] {url} - {r.get('note')}"
        body = clean(r.get("text") or "", css_strip)
        if len(body.strip()) < MIN_SNAPSHOT_CHARS:
            return None, (f"[blocked:empty] {url} - {len(body.strip())} chars of "
                          f"readable text via the {r.get('rung')} rung; a snapshot "
                          f"this small would report 'no change' forever")
        return body, None
    except urllib.error.HTTPError as e:
        return None, f"[{e.code}] {url}"
    except urllib.error.URLError as e:
        return None, f"[unreachable] {url} — {e.reason}"
    except (TimeoutError, OSError) as e:
        return None, f"[unreachable] {url} — {type(e).__name__}: {e}"


def cmd_add(a):
    idx = load_index()
    if a.name in idx and not a.force:
        sys.exit(f"'{a.name}' is already watched ({idx[a.name]['url']}) — "
                 f"use --force to re-snapshot")
    text, err = grab(a.url, a.css_strip)
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    if err and not (a.register_blocked and err.startswith("[blocked")):
        sys.exit(err)
    STORE.mkdir(parents=True, exist_ok=True)
    rec = {"url": a.url, "css_strip": a.css_strip,
           "added": today, "last_check": today, "last_change": today, "changes": 0}
    if err:
        # Registered on purpose, with the reason stored. `check` reports it as
        # BLOCKED on every run: a target we cannot read is a finding we must keep
        # seeing, not one that quietly drops off the list before the deadline.
        rec["blocked"] = err
        idx[a.name] = rec
        save_index(idx)
        print(f"REGISTERED BLOCKED '{a.name}' - {a.url}")
        print(f"  {err}")
        print("  no snapshot written; `check` reports BLOCKED, never 'no change'")
        return
    snap_path(a.name).write_text(text, encoding="utf-8")
    idx[a.name] = rec
    save_index(idx)
    print(f"watching '{a.name}' — {a.url}\nsnapshot: {snap_path(a.name)} "
          f"({len(text.splitlines())} lines)")


def cmd_drop(a):
    idx = load_index()
    if a.name not in idx:
        sys.exit(f"'{a.name}' is not watched")
    idx.pop(a.name)
    save_index(idx)
    snap_path(a.name).unlink(missing_ok=True)
    print(f"dropped '{a.name}'")


def cmd_check(a):
    idx = load_index()
    if not idx:
        sys.exit("nothing watched yet — diffwatch.py add NAME URL")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    changed = failed = checked = unblocked = 0
    for name, rec in sorted(idx.items()):
        if a.only and a.only.lower() not in name.lower():
            continue
        checked += 1
        new, err = grab(rec["url"], rec.get("css_strip", False))
        rec["last_check"] = now
        if err:
            failed += 1
            rec["blocked"] = err
            print(f"{name}: BLOCKED {err}")
            continue
        if rec.pop("blocked", None):
            # It was unreadable and now it is not. The first readable snapshot is
            # a baseline, not a diff: printing a whole page as "changed" would
            # bury the real change when it finally comes.
            snap_path(name).write_text(new, encoding="utf-8")
            rec["last_change"] = now
            unblocked += 1
            print(f"{name}: UNBLOCKED - first readable snapshot taken "
                  f"({len(new.splitlines())} lines), baseline not diffed")
            continue
        p = snap_path(name)
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        if new == old:
            continue
        changed += 1
        diff = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=f"{name} @ {rec.get('last_change','?')}",
            tofile=f"{name} @ {now}", lineterm="", n=a.context))
        if len(diff) > a.max_lines:
            diff = diff[:a.max_lines] + [
                f"... [{len(diff) - a.max_lines} more diff lines — "
                f"raise --max-lines to see them]"]
        print(f"\n=== CHANGED: {name} — {rec['url']} ===")
        print("\n".join(diff))
        # Only an accepted snapshot advances the ledger — otherwise the same
        # change would be counted again on the next uncommitted check.
        if a.commit:
            p.write_text(new, encoding="utf-8")
            rec["last_change"] = now
            rec["changes"] = rec.get("changes", 0) + 1
    save_index(idx)
    if changed and not a.commit:
        print(f"\n[{changed} changed] snapshots NOT updated — re-run with "
              f"--commit to accept, or the same diff prints again next check.")
    if failed:
        print(f"[{failed} unreachable]")
    if not checked:
        print(f"no watched page matches --only '{a.only}'", file=sys.stderr)
    elif not changed and not failed and not unblocked and not a.quiet:
        print(f"no change ({checked} page(s) checked {now})", file=sys.stderr)
    elif unblocked and not changed and not failed and not a.quiet:
        print(f"[{unblocked} unblocked, {checked - unblocked} unchanged] "
              f"({checked} page(s) checked {now})", file=sys.stderr)


def cmd_list(a):
    idx = load_index()
    if not idx:
        print("nothing watched yet")
        return
    print(f"{'name':22} {'last change':17} {'chg':>4}  url")
    for name, rec in sorted(idx.items()):
        print(f"{name:22} {rec.get('last_change','?'):17} "
              f"{rec.get('changes',0):>4}  {rec['url']}"
              + ("  [css-strip]" if rec.get("css_strip") else "")
              + ("  [BLOCKED]" if rec.get("blocked") else ""))
    blocked = {n: r["blocked"] for n, r in idx.items() if r.get("blocked")}
    if blocked:
        print("")
        print("BLOCKED - registered but UNREAD; these cannot report a change:")
        for n, why in sorted(blocked.items()):
            print(f"  {n}: {why}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("url")
    p.add_argument("--css-strip", action="store_true",
                   help="drop cookie/session/timestamp/counter lines before diffing")
    p.add_argument("--force", action="store_true")
    p.add_argument("--register-blocked", action="store_true",
                   help="register a page we cannot currently read, storing the "
                        "reason, so it stays on the list and reports BLOCKED "
                        "instead of vanishing from it")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("check")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--only", help="name substring filter")
    p.add_argument("--context", type=int, default=2)
    p.add_argument("--max-lines", type=int, default=200)
    p.add_argument("--quiet", action="store_true",
                   help="print nothing at all when nothing changed")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("list")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("drop")
    p.add_argument("name")
    p.set_defaults(fn=cmd_drop)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a.fn(a)


if __name__ == "__main__":
    main()
