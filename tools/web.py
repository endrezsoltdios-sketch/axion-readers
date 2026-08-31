#!/usr/bin/env python3
"""web.py — read any web page as clean text. One job: URL in, readable text out.

Built 31 Aug 2026 (user order: own tools, token-friendly). Stdlib only — no
dependencies, nothing to break. Announces itself honestly as our own reader.

Usage:
  py scripts/web.py <url>                # main text, capped at 15k chars
  py scripts/web.py <url> --full         # no cap
  py scripts/web.py <url> --links        # list links (text -> href) instead
  py scripts/web.py <url> --raw          # raw body, no extraction
  py scripts/web.py <url> --out FILE     # write to FILE

Token-friendly: strips script/style/nav/svg, collapses whitespace, drops
boilerplate runs. JSON and plain-text responses pass through pretty-printed.
Cost: 0 tokens, 0 keys.
"""
import argparse
import gzip
import json
import re
import sys
import urllib.request
from html import unescape
from html.parser import HTMLParser

UA = "AxionReader/1.0 (+https://getaxionlabs.com; polite; contact hello@getaxionlabs.com)"
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
BLOCK_TAGS = {"p", "div", "section", "article", "li", "tr", "br", "h1", "h2",
              "h3", "h4", "h5", "h6", "blockquote", "pre", "td", "th",
              "header", "footer", "nav", "main", "aside", "figcaption"}
HEAD_TAGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### "}


class Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links = [], []
        self.skip_depth = 0
        self.title = ""
        self.in_title = False
        self.prefix = ""
        self.href = None
        self.link_text = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in HEAD_TAGS:
            self.parts.append("\n\n" + HEAD_TAGS[tag])
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.link_text = []

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False
        if tag in HEAD_TAGS or tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a" and self.href:
            t = " ".join("".join(self.link_text).split())
            if t:
                self.links.append((t[:80], self.href))
            self.href = None

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.in_title:
            self.title += data
        else:
            self.parts.append(data)
            if self.href is not None:
                self.link_text.append(data)


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip",
        "Accept": "text/html,application/json,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        return body.decode("utf-8", errors="replace"), ctype, r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--links", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--cap", type=int, default=15000)
    ap.add_argument("--out")
    a = ap.parse_args()
    url = a.url if a.url.startswith("http") else "https://" + a.url

    try:
        text, ctype, status = fetch(url)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"[{e.code}] {url}\n{body}")
    except urllib.error.URLError as e:
        sys.exit(f"[unreachable] {url} — {e.reason}")

    if a.raw:
        body = text
    elif ctype == "application/json" or text.lstrip()[:1] in "[{":
        try:
            body = json.dumps(json.loads(text), indent=1)
        except Exception:
            body = text
    elif "html" not in ctype and "<html" not in text[:500].lower():
        body = text  # llms.txt, robots, plain text: pass through
    else:
        ex = Extractor()
        ex.feed(text)
        if a.links:
            body = "\n".join(f"{t} -> {h}" for t, h in ex.links)
        else:
            raw = unescape("".join(ex.parts))
            lines = [" ".join(l.split()) for l in raw.splitlines()]
            out, blank = [], 0
            for l in lines:
                if l:
                    out.append(l)
                    blank = 0
                else:
                    blank += 1
                    if blank == 1:
                        out.append("")
            body = (f"[{status}] {' '.join(ex.title.split())}\n{url}\n\n"
                    + "\n".join(out).strip())

    if not a.full and len(body) > a.cap:
        body = body[:a.cap] + f"\n\n[capped at {a.cap} chars of {len(body)} — use --full]"

    if a.out:
        open(a.out, "w", encoding="utf-8").write(body)
        print(f"wrote {a.out} ({len(body)} chars)")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(body)


if __name__ == "__main__":
    main()
