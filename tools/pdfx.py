#!/usr/bin/env python3
"""pdfx.py — PDF in, text or markdown out. One job: read a PDF without opening it.

Built 1 Sep 2026 (tool factory wave 2). The one wave-2 tool with a dependency:
pypdf (pure python, no binaries) — install with `py -m pip install pypdf` if the
import fails. Honest about scanned pages: it says so rather than returning empty
text and letting you think the page was blank.

Usage:
  py scripts/pdfx.py products/blueprint/10K_Trader_Blueprint_Axion_Labs.pdf
  py scripts/pdfx.py doc.pdf --pages 1-5
  py scripts/pdfx.py doc.pdf --pages 3,7,10-12 --out doc.txt
  py scripts/pdfx.py doc.pdf --md                # headings + page markers
  py scripts/pdfx.py doc.pdf --layout            # keep column spacing (tables)
  py scripts/pdfx.py doc.pdf --meta              # page count, title, sizes only

Cost: 0 tokens, 0 keys. Pipe into digest.py or ask.py --file for a free-tier
first pass over a long document.
"""
import argparse
import re
import sys
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    sys.exit("[missing dependency] pypdf — install it with:\n"
             "  py -m pip install pypdf")

# A heading in extracted text: short, no terminal full stop, mostly title/upper.
HEAD = re.compile(r"^(?=.{2,70}$)(?![^\w]*$).*[^.:;,]$")


def parse_pages(spec, n):
    """'1-5,8,11-' -> a sorted list of 0-based indexes."""
    if not spec:
        return list(range(n))
    want = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            try:
                a = int(lo) if lo.strip() else 1
                b = int(hi) if hi.strip() else n
            except ValueError:
                sys.exit(f"[bad --pages] cannot read '{part}' — use 1-5,8,11-")
            want |= set(range(a, b + 1))
        else:
            try:
                want.add(int(part))
            except ValueError:
                sys.exit(f"[bad --pages] cannot read '{part}' — use 1-5,8,11-")
    idx = sorted(i - 1 for i in want if 1 <= i <= n)
    if not idx:
        sys.exit(f"[bad --pages] '{spec}' selects no page — the file has {n}")
    return idx


def looks_like_heading(line):
    if not HEAD.match(line):
        return False
    letters = [c for c in line if c.isalpha()]
    if len(letters) < 2:
        return False
    upper = sum(c.isupper() for c in letters) / len(letters)
    return upper > 0.6 or (line.istitle() and len(line) < 60)


def as_markdown(text):
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            out.append("")
        elif looks_like_heading(line):
            out.append(f"### {line}")
        else:
            out.append(line)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="e.g. 1-5 or 3,7,10-12 (1-based, inclusive)")
    ap.add_argument("--md", action="store_true", help="mark likely headings")
    ap.add_argument("--layout", action="store_true",
                    help="preserve column spacing — best-effort tables")
    ap.add_argument("--meta", action="store_true", help="metadata only, no text")
    ap.add_argument("--no-page-marks", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    p = Path(a.pdf)
    if not p.is_file():
        sys.exit(f"[no such file] {a.pdf}")
    try:
        reader = PdfReader(str(p))
        if reader.is_encrypted:
            try:
                reader.decrypt("")          # many PDFs are "encrypted" with no password
            except Exception:
                sys.exit(f"[encrypted] {p.name} needs a password — pdfx.py will "
                         f"not guess one")
        n = len(reader.pages)
    except Exception as e:
        sys.exit(f"[unreadable pdf] {p.name} — {type(e).__name__}: {e}")

    if a.meta:
        info = reader.metadata or {}
        print(f"{p.name} — {n} page(s), {p.stat().st_size/1024:.0f} KB")
        for k in ("/Title", "/Author", "/Subject", "/Creator", "/Producer",
                  "/CreationDate"):
            if info.get(k):
                print(f"{k[1:]:14} {info[k]}")
        try:
            box = reader.pages[0].mediabox
            print(f"{'PageSize':14} {float(box.width):.0f} x "
                  f"{float(box.height):.0f} pt "
                  f"({float(box.width)/72:.2f} x {float(box.height)/72:.2f} in)")
        except Exception:
            pass
        return

    idx = parse_pages(a.pages, n)
    mode = {"extraction_mode": "layout"} if a.layout else {}
    parts, empty = [], 0
    for i in idx:
        try:
            text = reader.pages[i].extract_text(**mode) or ""
        except Exception as e:
            text = ""
            parts.append(f"[page {i+1}: extraction failed — "
                         f"{type(e).__name__}: {e}]")
            continue
        text = text.strip()
        if not text:
            empty += 1
            parts.append(f"[page {i+1}: no extractable text — likely a scanned "
                         f"image. OCR it (or use the Read tool on a PNG of the "
                         f"page) if the content matters.]")
            continue
        if a.md:
            text = as_markdown(text)
        if not a.no_page_marks:
            parts.append(f"\n--- page {i+1} ---\n" if not a.md
                         else f"\n## page {i+1}\n")
        parts.append(text)

    head = (f"# {p.name} — {n} page(s), {len(idx)} extracted"
            + (f", {empty} with no extractable text" if empty else ""))
    body = head + "\n" + "\n".join(parts)

    if a.out:
        o = Path(a.out)
        o.parent.mkdir(parents=True, exist_ok=True)
        o.write_text(body, encoding="utf-8")
        print(f"wrote {o} ({len(body)} chars, {len(idx)} page(s))")
    else:
        print(body)


if __name__ == "__main__":
    main()
