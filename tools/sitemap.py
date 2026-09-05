#!/usr/bin/env python3
"""sitemap.py — read any site's sitemap. One job: the full URL inventory of a
site, with lastmod dates, without crawling it.

Built 1 Sep 2026 (tool factory wave 2). Discovery order: the URL you gave →
/sitemap.xml → robots.txt Sitemap: lines → a short list of common paths.
Sitemap indexes are followed one level (--depth for more).

Usage:
  py scripts/sitemap.py example.com               # every URL + lastmod
  py scripts/sitemap.py popla.co.uk --count             # summary by path prefix
  py scripts/sitemap.py example.com --since 2026-08-01
  py scripts/sitemap.py example.com/sitemap_index.xml --urls-only --out u.txt
  py scripts/sitemap.py example.com --grep parking      # filter by URL substring

Cost: 0 tokens, 0 keys.
"""
import argparse
import gzip
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

UA = "AxionReader/1.0 (+https://getaxionlabs.com; sitemap reader)"
COMMON = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
          "/sitemap.xml.gz", "/wp-sitemap.xml", "/sitemap/sitemap.xml"]
NS = re.compile(r"\{[^}]*\}")


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip",
        # some WAFs 406 a request with no Accept header
        "Accept": "application/xml,text/xml,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
        return body.decode("utf-8", errors="replace")


def try_fetch(url):
    try:
        return fetch(url), None
    except urllib.error.HTTPError as e:
        return None, f"[{e.code}]"
    except urllib.error.URLError as e:
        return None, f"[unreachable] {e.reason}"
    except (TimeoutError, OSError) as e:
        return None, f"[unreachable] {type(e).__name__}"


def tag(el):
    return NS.sub("", el.tag)


def parse(xml):
    """-> (kind, [(loc, lastmod), ...]); kind is 'index', 'urlset' or None."""
    try:
        root = ET.fromstring(xml.encode("utf-8", "replace"))
    except ET.ParseError:
        return None, []
    kind = "index" if tag(root) == "sitemapindex" else (
        "urlset" if tag(root) == "urlset" else None)
    rows = []
    for child in root:
        loc = mod = ""
        for f in child:
            if tag(f) == "loc":
                loc = (f.text or "").strip()
            elif tag(f) == "lastmod":
                mod = (f.text or "").strip()[:10]
        if loc:
            rows.append((loc, mod))
    return kind, rows


def discover(target):
    """Return candidate sitemap URLs in priority order, with a note per source."""
    if not target.startswith("http"):
        target = "https://" + target
    p = urllib.parse.urlparse(target)
    base = f"{p.scheme}://{p.netloc}"
    if p.path and p.path not in ("/",):
        return base, [(target, "given")]
    bases = [base]
    if not p.netloc.startswith("www."):
        bases.append(f"{p.scheme}://www.{p.netloc}")   # many hosts only serve www
    cands = []
    for b in bases:
        robots, _err = try_fetch(b + "/robots.txt")
        if robots:
            for line in robots.splitlines():
                if line.lower().startswith("sitemap:"):
                    cands.append((line.split(":", 1)[1].strip(), f"robots.txt @ {b}"))
    for b in bases:
        for c in COMMON:
            if not any(u == b + c for u, _ in cands):
                cands.append((b + c, "guess"))
    return base, cands


def collect(url, depth, seen, notes):
    """Recursively expand sitemap indexes into (loc, lastmod) URL rows."""
    if url in seen or depth < 0:
        return []
    seen.add(url)
    xml, err = try_fetch(url)
    if err:
        notes.append(f"{err} {url}")
        return []
    kind, rows = parse(xml)
    if kind is None:
        notes.append(f"[not a sitemap] {url}")
        return []
    if kind == "urlset":
        notes.append(f"[ok] {len(rows):>5} urls  {url}")
        return rows
    notes.append(f"[index] {len(rows):>4} children {url}")
    out = []
    for loc, _mod in rows:
        out += collect(loc, depth - 1, seen, notes)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="domain, or a direct sitemap URL")
    ap.add_argument("--since", help="only lastmod >= YYYY-MM-DD (drops undated)")
    ap.add_argument("--grep", help="only URLs containing this substring")
    ap.add_argument("--count", action="store_true",
                    help="summarise by first path segment instead of listing")
    ap.add_argument("--urls-only", action="store_true")
    ap.add_argument("--depth", type=int, default=2, help="index nesting to follow")
    ap.add_argument("--out")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    base, cands = discover(a.target)
    notes, seen, rows = [], set(), []
    for url, src in cands:
        rows = collect(url, a.depth, seen, notes)
        if rows:
            notes.append(f"       (found via {src})")
            break
    if not rows:
        print("\n".join(notes))
        print(f"[no sitemap] nothing usable for {a.target} — "
              f"tried {len(cands)} candidate(s). The site may have none, or may "
              f"be blocking non-browser clients.")
        sys.exit(1)

    # de-duplicate, keeping the newest lastmod seen for each URL
    best = {}
    for loc, mod in rows:
        if loc not in best or mod > best[loc]:
            best[loc] = mod
    total = len(best)
    items = sorted(best.items())
    if a.grep:
        items = [(u, m) for u, m in items if a.grep.lower() in u.lower()]
    undated = 0
    if a.since:
        kept = []
        for u, m in items:
            if not m:
                undated += 1
            elif m >= a.since:
                kept.append((u, m))
        items = kept

    lines = [f"# sitemap — {a.target} — {total} url(s), {len(items)} shown"]
    lines += ["# " + n for n in notes]
    if a.since:
        lines.append(f"# --since {a.since}: {undated} url(s) dropped for having "
                     f"no lastmod (absence of a date is not a date)")
    lines.append("")
    if a.count:
        c = Counter()
        for u, _ in items:
            path = urllib.parse.urlparse(u).path.strip("/")
            c[("/" + path.split("/")[0]) if path else "/ (root)"] += 1
        w = max((len(k) for k in c), default=1)
        for k, n in c.most_common():
            lines.append(f"{k:<{w}}  {n}")
    elif a.urls_only:
        lines += [u for u, _ in items]
    else:
        for u, m in items:
            lines.append(f"{m or '    -     '}  {u}")
    body = "\n".join(lines)

    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        print(f"wrote {p} ({len(items)} urls)")
    else:
        print(body)


if __name__ == "__main__":
    main()
