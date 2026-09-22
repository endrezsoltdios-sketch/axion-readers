#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""vendor_news.py: what did the AI vendors actually ship since we last looked, in one command.

Why (22 Sep 2026): a vendor news sweep run by research sub-agents cost about 493,000 tokens to answer a question
that is mechanical, namely "list every dated entry each vendor published in the last N days, with its URL".
Nothing in that half needs a model: code costs zero tokens and never invents a date. Measured the same day, this
found an entry all four agents missed.

What it is not: `diffwatch.py` snapshots one page and prints a text diff, which is the right tool for a pricing
page and the wrong one here, because it cannot say which entry is new, when it was published or where it lives.
This reads entry level, keeps a per source seen list and prints dated rows with one URL each.

Sources are declared in SOURCES below, each one a feed we have opened by hand at least once:
  rss    an RSS 2.0 or Atom feed, parsed with xml.etree; date from pubDate, published or updated
  ghrel  the GitHub releases API for one repository; date from published_at, body trimmed to one line

  python tools/vendor_news.py                       # last 14 days, every source, human table
  python tools/vendor_news.py --days 7 --new-only   # only entries not in the state file
  python tools/vendor_news.py --source anthropic-code,cloudflare-blog --days 30
  python tools/vendor_news.py --days 14 --commit --md vendor_news.md
  python tools/vendor_news.py --list
  python tools/vendor_news.py --selftest

State: vendor_news_state.json beside the repo root, written only with --commit; a dry run is the default.
Exit codes: 0 when at least one source answered, 1 when every source failed, 2 on a usage error.
"""
import argparse
import datetime as dt
import email.utils
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "vendor_news_state.json"
UA = os.environ.get("VENDOR_NEWS_UA", "example-bot/1.0 (+https://example.com/bot)")
TIMEOUT = 40
MAX_BYTES = 16 * 1024 * 1024

# key, vendor, kind, url. Every URL below returned HTTP 200 to curl on 2026-09-22.
SOURCES = [
    ("anthropic-code", "Anthropic", "ghrel", "https://api.github.com/repos/anthropics/claude-agent-sdk-python/releases?per_page=30"),
    ("anthropic-sdk-ts", "Anthropic", "ghrel", "https://api.github.com/repos/anthropics/claude-agent-sdk-typescript/releases?per_page=30"),
    ("openai-news", "OpenAI", "rss", "https://openai.com/news/rss.xml"),
    ("openai-agents", "OpenAI", "ghrel", "https://api.github.com/repos/openai/openai-agents-python/releases?per_page=20"),
    ("deepmind", "Google DeepMind", "rss", "https://blog.google/technology/google-deepmind/rss"),
    ("google-blog", "Google", "rss", "https://blog.google/rss/"),
    ("cloudflare-blog", "Cloudflare", "rss", "https://blog.cloudflare.com/rss/"),
    ("cloudflare-changelog", "Cloudflare", "rss", "https://developers.cloudflare.com/changelog/rss.xml"),
    ("cloudflare-agents", "Cloudflare", "ghrel", "https://api.github.com/repos/cloudflare/agents/releases?per_page=20"),
    ("mcp-spec", "MCP", "ghrel", "https://api.github.com/repos/modelcontextprotocol/modelcontextprotocol/releases?per_page=20"),
    ("a2a-spec", "A2A", "ghrel", "https://api.github.com/repos/a2aproject/A2A/releases?per_page=20"),
    ("microsoft-agents", "Microsoft", "ghrel", "https://api.github.com/repos/microsoft/agent-framework/releases?per_page=10"),
    ("simonw", "Independent", "rss", "https://simonwillison.net/atom/everything/"),
]
KEYS = [s[0] for s in SOURCES]


def fetch(url):
    """Return (bytes, None) or (None, one line of failure). A failure is a line, not a traceback."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read(MAX_BYTES), None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:
        return None, type(e).__name__


def parse_date(raw):
    """RFC 822 or ISO 8601 to a date. Returns None when the string is not a date we know."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        return email.utils.parsedate_to_datetime(raw).date()
    except Exception:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def strip_tags(text, limit=180):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_rss(blob):
    """RSS 2.0 and Atom to a list of dicts. Returns [] when the bytes are not XML we can read."""
    try:
        root = ET.fromstring(blob)
    except Exception:
        return []
    out = []
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        rec = {"title": "", "url": "", "date": None, "summary": ""}
        for child in item:
            ctag = child.tag.split("}")[-1]
            if ctag == "title" and not rec["title"]:
                rec["title"] = strip_tags(child.text or "", 200)
            elif ctag == "link":
                href = child.get("href") or (child.text or "")
                rel = child.get("rel") or "alternate"
                if href and rel == "alternate" and not rec["url"]:
                    rec["url"] = href.strip()
            elif ctag in ("pubDate", "published", "updated", "date") and rec["date"] is None:
                rec["date"] = parse_date(child.text or "")
            elif ctag in ("description", "summary", "content") and not rec["summary"]:
                rec["summary"] = strip_tags(child.text or "")
        if rec["title"] or rec["url"]:
            out.append(rec)
    return out


def parse_ghrel(blob):
    """GitHub releases JSON to the same shape. A draft release is skipped."""
    try:
        data = json.loads(blob.decode("utf-8", "replace"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for rel in data:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        body = (rel.get("body") or "").replace("\r", "\n")
        first = ""
        for line in body.split("\n"):
            line = line.strip().lstrip("*-# ").strip()
            if line and not line.startswith("<"):
                first = line[:180]
                break
        out.append({
            "title": (rel.get("name") or rel.get("tag_name") or "").strip()[:200],
            "url": rel.get("html_url") or "",
            "date": parse_date(rel.get("published_at") or rel.get("created_at") or ""),
            "summary": strip_tags(first),
        })
    return out


PARSERS = {"rss": parse_rss, "ghrel": parse_ghrel}


def load_state(path=STATE):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def collect(keys, days, state, today=None, fetcher=fetch):
    """Read every named source, keep entries inside the window, mark the unseen ones."""
    today = today or dt.date.today()
    cutoff = today - dt.timedelta(days=days)
    rows, failures = [], []
    for key, vendor, kind, url in SOURCES:
        if key not in keys:
            continue
        blob, err = fetcher(url)
        if blob is None:
            failures.append((key, err))
            continue
        items = PARSERS[kind](blob)
        if not items:
            failures.append((key, "0 entries parsed"))
            continue
        seen = set(state.get(key, []))
        kept = 0
        for it in items:
            if it["date"] is None or it["date"] < cutoff or it["date"] > today + dt.timedelta(days=1):
                continue
            rows.append({
                "source": key, "vendor": vendor, "date": it["date"].isoformat(),
                "title": it["title"], "url": it["url"], "summary": it["summary"],
                "new": it["url"] not in seen,
            })
            kept += 1
        if kept == 0:
            failures.append((key, "0 entries inside the window"))
    rows.sort(key=lambda r: (r["date"], r["source"]), reverse=True)
    return rows, failures


def to_markdown(rows, days, today):
    out = ["# Vendor news, last %d days, read %s" % (days, today.isoformat()), "",
           "| date | vendor | source | what it is | url |", "|---|---|---|---|---|"]
    for r in rows:
        title = r["title"].replace("|", "/")
        out.append("| %s | %s | %s | %s | %s |" % (r["date"], r["vendor"], r["source"], title, r["url"]))
    out.append("")
    out.append("%d entries. Generated by tools/vendor_news.py, not hand edited." % len(rows))
    return "\n".join(out)


def selftest():
    """Every check runs in process against fixtures. No network, no state file written."""
    checks, fails = [], []

    def ck(name, cond):
        checks.append((name, bool(cond)))
        if not cond:
            fails.append(name)

    ck("1 rfc822 date parses", parse_date("Mon, 15 Sep 2026 10:00:00 GMT") == dt.date(2026, 9, 15))
    ck("2 iso date parses", parse_date("2026-09-18T12:30:00Z") == dt.date(2026, 9, 18))
    ck("3 junk date is None, not a guess", parse_date("last tuesday") is None)
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title>A thing shipped</title><link>https://example.com/a</link>
      <pubDate>Tue, 16 Sep 2026 09:00:00 GMT</pubDate>
      <description>&lt;p&gt;Body  text&lt;/p&gt;</description></item></channel></rss>"""
    got = parse_rss(rss)
    ck("4 rss item parsed", len(got) == 1 and got[0]["url"] == "https://example.com/a")
    ck("5 rss html stripped from summary", got and got[0]["summary"] == "Body text")
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Atom entry</title><link rel="alternate" href="https://example.com/b"/>
      <updated>2026-09-19T00:00:00Z</updated></entry></feed>"""
    a = parse_rss(atom)
    ck("6 atom entry parsed with href link", len(a) == 1 and a[0]["url"] == "https://example.com/b")
    ck("7 non xml bytes give [] and never raise", parse_rss(b"<html>not a feed") == [])
    gh = json.dumps([
        {"name": "v0.2.1", "html_url": "https://example.com/r1", "published_at": "2026-09-14T00:00:00Z",
         "body": "## What changed\n- added a flag\n", "draft": False},
        {"name": "draft", "html_url": "https://example.com/r2", "published_at": "2026-09-14T00:00:00Z",
         "body": "x", "draft": True},
    ]).encode()
    g = parse_ghrel(gh)
    ck("8 github release parsed", len(g) == 1 and g[0]["title"] == "v0.2.1")
    ck("9 draft release skipped", all(r["title"] != "draft" for r in g))
    ck("10 release body trimmed to one line", g and g[0]["summary"] == "What changed")
    ck("11 bad json gives [] and never raises", parse_ghrel(b"{not json") == [])

    today = dt.date(2026, 9, 22)
    fixtures = {
        "https://openai.com/news/rss.xml": rss,
        "https://api.github.com/repos/anthropics/claude-agent-sdk-python/releases?per_page=30": gh,
    }

    def fake(url):
        if url in fixtures:
            return fixtures[url], None
        return None, "HTTP 404"

    rows, failures = collect(["openai-news", "anthropic-code"], 14, {}, today=today, fetcher=fake)
    ck("12 window keeps both in date entries", len(rows) == 2)
    ck("13 rows sorted newest first", rows[0]["date"] == "2026-09-16")
    ck("14 unseen entry marked new", all(r["new"] for r in rows))
    rows2, _ = collect(["openai-news"], 14, {"openai-news": ["https://example.com/a"]}, today=today, fetcher=fake)
    ck("15 seen entry is not new", rows2 and rows2[0]["new"] is False)
    rows3, _ = collect(["openai-news"], 2, {}, today=today, fetcher=fake)
    ck("16 entry outside the window is dropped", rows3 == [])
    _, f4 = collect(["deepmind"], 14, {}, today=today, fetcher=fake)
    ck("17 a dead source is a line, not a crash", f4 and f4[0][1] == "HTTP 404")
    md = to_markdown(rows, 14, today)
    ck("18 markdown carries a row per entry", md.count("\n|") >= 3)
    ck("19 every source key is unique", len(KEYS) == len(set(KEYS)))
    ck("20 every source kind has a parser", all(s[2] in PARSERS for s in SOURCES))

    for name, ok in checks:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    print("\n%d checks, %d passed, %d failed" % (len(checks), len(checks) - len(fails), len(fails)))
    return 0 if not fails else 1


def main():
    p = argparse.ArgumentParser(description="dated vendor entries since we last looked, one command")
    p.add_argument("--days", type=int, default=14, help="window in days (default 14)")
    p.add_argument("--source", help="comma separated source keys (default all)")
    p.add_argument("--new-only", action="store_true", help="only entries not in the state file")
    p.add_argument("--commit", action="store_true", help="write the state file (dry run is the default)")
    p.add_argument("--json", dest="as_json", action="store_true")
    p.add_argument("--md", help="also write a markdown table to this path")
    p.add_argument("--list", dest="do_list", action="store_true", help="list the source keys and exit")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.do_list:
        for key, vendor, kind, url in SOURCES:
            print("%-22s %-16s %-6s %s" % (key, vendor, kind, url))
        return 0

    keys = KEYS if not a.source else [k.strip() for k in a.source.split(",") if k.strip()]
    unknown = [k for k in keys if k not in KEYS]
    if unknown:
        print("unknown source: %s (see --list)" % ", ".join(unknown), file=sys.stderr)
        return 2

    today = dt.date.today()
    state = load_state()
    rows, failures = collect(keys, a.days, state, today=today)
    shown = [r for r in rows if r["new"]] if a.new_only else rows

    if a.as_json:
        print(json.dumps({"read": today.isoformat(), "days": a.days, "rows": shown,
                          "failures": [{"source": k, "why": w} for k, w in failures]}, indent=2))
    else:
        print("vendor_news  %s  last %d days  %d sources  %d entries%s"
              % (today.isoformat(), a.days, len(keys), len(shown), "  (new only)" if a.new_only else ""))
        for r in shown:
            print("  %s  %-10s %-22s %s" % (r["date"], r["vendor"][:10], r["source"], r["title"]))
            print("      %s" % r["url"])
        for k, why in failures:
            print("  no rows: %-22s %s" % (k, why))

    if a.md:
        path = pathlib.Path(a.md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_markdown(shown, a.days, today), encoding="utf-8")
        print("wrote %s" % path)

    if a.commit:
        for r in rows:
            state.setdefault(r["source"], [])
            if r["url"] and r["url"] not in state[r["source"]]:
                state[r["source"]].append(r["url"])
        for k in state:
            state[k] = state[k][-400:]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        print("state written: %s (%d sources)" % (STATE, len(state)))
    elif not a.as_json:
        print("dry run: state not written. Add --commit to remember these entries.")

    return 0 if len(failures) < len(keys) else 1


if __name__ == "__main__":
    sys.exit(main())
