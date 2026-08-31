#!/usr/bin/env python3
"""search.py — read internet search results as clean text. One job: query in,
ranked results out. Generalises scripts/reddit_serp.py (31 Aug 2026).

Engine: our DataForSEO account (SERP live advanced, ~$0.002/query). Measured
results, no scraping, no ToS games.

Usage:
  py scripts/search.py "parking eye appeal"            # top 10, UK
  py scripts/search.py "query" --num 20 --us           # top 20, United States
  py scripts/search.py "query" --site reddit.com       # restrict to a site
  py scripts/search.py "query" --read 1                # also fetch+extract result #1 (via web.py)
  py scripts/search.py "query" --out research_out/x.md

Credentials: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD from .env (never printed).
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def serp(query, num, location_code):
    login = os.environ.get("DATAFORSEO_LOGIN")
    password = os.environ.get("DATAFORSEO_PASSWORD")
    if not (login and password):
        sys.exit("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set (.env)")
    payload = json.dumps([{
        "keyword": query, "location_code": location_code,
        "language_code": "en", "depth": max(10, min(num, 100)),
    }]).encode()
    req = urllib.request.Request(
        "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        data=payload,
        headers={"Authorization": "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode(),
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    task = data["tasks"][0]
    if task["status_code"] != 20000:
        sys.exit(f"DataForSEO error {task['status_code']}: {task['status_message']}")
    items = (task["result"] or [{}])[0].get("items") or []
    rows = []
    for it in items:
        if it.get("type") != "organic":
            continue
        rows.append({"rank": it.get("rank_absolute"), "title": it.get("title", ""),
                     "url": it.get("url", ""), "snippet": it.get("description", ""),
                     "seen": (it.get("timestamp") or "")[:10]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--num", type=int, default=10)
    ap.add_argument("--us", action="store_true", help="United States (default UK)")
    ap.add_argument("--site", help="restrict to a site, e.g. reddit.com")
    ap.add_argument("--read", type=int, help="also fetch+extract result #N via web.py")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()

    load_env()
    q = (f"site:{a.site} " if a.site else "") + a.query
    rows = serp(q, a.num, 2840 if a.us else 2826)[: a.num]

    if a.json:
        body = json.dumps(rows, indent=1)
    else:
        lines = [f"# search: {q} — {date.today().isoformat()} — "
                 f"{'US' if a.us else 'UK'} — {len(rows)} organic results", ""]
        for r in rows:
            seen = f" · seen {r['seen']}" if r["seen"] else ""
            lines.append(f"{r['rank']}. {r['title']}{seen}\n   {r['url']}")
            if r["snippet"]:
                lines.append(f"   > {r['snippet']}")
        body = "\n".join(lines)

    if a.read and 1 <= a.read <= len(rows):
        target = rows[a.read - 1]["url"]
        p = subprocess.run([sys.executable, str(ROOT / "web.py"), target],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        body += f"\n\n---- READ result #{a.read}: {target} ----\n" + p.stdout

    if a.out:
        p = ROOT / a.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        print(f"wrote {p}")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(body)


if __name__ == "__main__":
    main()
