#!/usr/bin/env python3
"""radar.py — university & lab AI research radar. One job: what did the
research world publish this week? Built 31 Aug 2026 (user order: monitor
MIT/Stanford/Yale/US university AI departments weekly — "that is the tomorrow").

Sources live in knowledge/research_radar_sources.txt (one per line:
NAME | rss <url>   or   NAME | arxiv <category>) — add a university = add a line.

Usage:
  py scripts/radar.py                 # items from the last 8 days, all sources
  py scripts/radar.py --days 14
  py scripts/radar.py --out research_out/radar.md

Cost: 0 tokens. Pipe the output through digest.py (--ask) for a free-tier
shortlist; the weekly scheduled task does the judging.
"""
import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "radar_sources.txt"
UA = {"User-Agent": "AxionReader/1.0 (+https://getaxionlabs.com; research radar)"}

DEFAULT_SOURCES = """\
# Axion research radar sources — one per line: NAME | rss <url>  OR  NAME | arxiv <cat>
# Add a university/lab = add a line. Comments start with #.
MIT News AI | rss https://news.mit.edu/rss/topic/artificial-intelligence2
Berkeley BAIR | rss https://bair.berkeley.edu/blog/feed.xml
CMU ML Blog | rss https://blog.ml.cmu.edu/feed/
Stanford HAI | rss https://hai.stanford.edu/rss.xml
arXiv AI | arxiv cs.AI
arXiv Software Eng | arxiv cs.SE
arXiv Computation+Language | arxiv cs.CL
"""


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_rss(name, url, since):
    items = []
    try:
        raw = fetch(url)
        root = ET.fromstring(raw.encode("utf-8", "replace"))
        # RSS 2.0 and Atom
        entries = root.findall(".//item") or root.findall(
            ".//{http://www.w3.org/2005/Atom}entry")
        for e in entries:
            def g(tag, atom):
                el = e.find(tag)
                if el is None:
                    el = e.find("{http://www.w3.org/2005/Atom}" + atom)
                return (el.text or "").strip() if el is not None and el.text else ""
            title = g("title", "title")
            link = g("link", "id")
            atom_link = e.find("{http://www.w3.org/2005/Atom}link")
            if not link and atom_link is not None:
                link = atom_link.get("href", "")
            d = g("pubDate", "updated")
            dt = None
            if d:
                try:
                    dt = parsedate_to_datetime(d)
                except (TypeError, ValueError):
                    try:
                        dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
                    except ValueError:
                        pass
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if title and (dt is None or dt >= since):
                items.append((dt, name, title, link))
    except Exception as ex:
        items.append((None, name, f"[SOURCE ERROR: {type(ex).__name__}: {ex}]", url))
    return items


def parse_arxiv(name, cat, since, max_n=40):
    url = ("http://export.arxiv.org/api/query?search_query=cat:" + cat +
           "&sortBy=submittedDate&sortOrder=descending&max_results=" + str(max_n))
    items = []
    try:
        raw = fetch(url)
        root = ET.fromstring(raw.encode("utf-8", "replace"))
        ns = "{http://www.w3.org/2005/Atom}"
        for e in root.findall(ns + "entry"):
            title = re.sub(r"\s+", " ", (e.findtext(ns + "title") or "")).strip()
            link = (e.findtext(ns + "id") or "").strip()
            d = (e.findtext(ns + "published") or "").strip()
            try:
                dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
            except ValueError:
                dt = None
            if title and (dt is None or dt >= since):
                items.append((dt, name, title, link))
    except Exception as ex:
        items.append((None, name, f"[SOURCE ERROR: {type(ex).__name__}: {ex}]", url))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--out")
    a = ap.parse_args()

    if not SOURCES.exists():
        SOURCES.parent.mkdir(parents=True, exist_ok=True)
        SOURCES.write_text(DEFAULT_SOURCES, encoding="utf-8")

    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    all_items = []
    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        name, spec = [p.strip() for p in line.split("|", 1)]
        kind, _, arg = spec.partition(" ")
        if kind == "rss":
            all_items += parse_rss(name, arg.strip(), since)
        elif kind == "arxiv":
            all_items += parse_arxiv(name, arg.strip(), since)

    all_items.sort(key=lambda x: (x[0] is None, -(x[0].timestamp() if x[0] else 0)))
    lines = [f"# Research radar — last {a.days} days — generated "
             f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — "
             f"{len(all_items)} items"]
    cur = None
    for dt, name, title, link in all_items:
        if name != cur:
            lines.append(f"\n## {name}")
            cur = name
        stamp = dt.strftime("%d %b") if dt else "??"
        lines.append(f"- [{stamp}] {title}\n  {link}")
    body = "\n".join(lines)

    if a.out:
        p = ROOT / a.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        print(f"wrote {p} ({len(all_items)} items)")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(body)


if __name__ == "__main__":
    main()
