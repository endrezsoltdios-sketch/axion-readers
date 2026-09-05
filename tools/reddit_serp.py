#!/usr/bin/env python3
"""reddit_serp.py — Reddit sentiment via Google's index (DataForSEO SERP API).

Fallback after Reddit declined our Data API registration (31 Aug 2026): we read
what Google indexed from Reddit — thread titles, URLs, snippets — never touching
Reddit's servers. Legitimate, measured, thread-level (no comment bodies).

Usage:
  py scripts/reddit_serp.py "parking eye appeal" [--num 20] [--out research_out/x.md]
  py scripts/reddit_serp.py "popla appeal" --sub LegalAdviceUK

Credentials: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD from .env (never printed).
Cost: ~$0.002 per query (SERP live advanced).
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def serp(query: str, num: int) -> list[dict]:
    login = os.environ.get("DATAFORSEO_LOGIN")
    password = os.environ.get("DATAFORSEO_PASSWORD")
    if not (login and password):
        sys.exit("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set (.env)")
    payload = json.dumps([{
        "keyword": query,
        "location_code": 2826,  # United Kingdom
        "language_code": "en",
        "depth": max(10, min(num, 100)),
    }]).encode()
    req = urllib.request.Request(
        "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        data=payload,
        headers={
            "Authorization": "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode(),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    task = data["tasks"][0]
    if task["status_code"] != 20000:
        sys.exit(f"DataForSEO error {task['status_code']}: {task['status_message']}")
    items = (task["result"] or [{}])[0].get("items") or []
    out = []
    for it in items:
        if it.get("type") != "organic":
            continue
        out.append({
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "snippet": it.get("description", ""),
            "timestamp": it.get("timestamp"),  # Google's crawl-seen date when present
            "rank": it.get("rank_absolute"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--num", type=int, default=20)
    ap.add_argument("--sub", help="restrict to one subreddit, e.g. LegalAdviceUK")
    ap.add_argument("--out", help="write markdown here instead of stdout")
    args = ap.parse_args()

    load_env()
    scope = f"site:reddit.com/r/{args.sub}" if args.sub else "site:reddit.com"
    q = f"{scope} {args.query}"
    rows = serp(q, args.num)

    lines = [f"# Reddit-via-SERP — \"{args.query}\" — {date.today().isoformat()}",
             f"Query: `{q}` · UK · {len(rows)} organic results · source: Google index "
             f"(DataForSEO), NOT Reddit API — thread-level only, snippets may be stale.", ""]
    for r in rows:
        ts = f" · seen {r['timestamp'][:10]}" if r.get("timestamp") else ""
        lines.append(f"**{r['rank']}. {r['title']}**{ts}")
        lines.append(f"<{r['url']}>")
        if r["snippet"]:
            lines.append(f"> {r['snippet']}")
        lines.append("")
    text = "\n".join(lines)

    if args.out:
        p = ROOT / args.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p} ({len(rows)} results)")
    else:
        print(text)


if __name__ == "__main__":
    main()
