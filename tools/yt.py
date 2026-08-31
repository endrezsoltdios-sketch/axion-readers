#!/usr/bin/env python3
"""yt.py — read a YouTube video as clean text. One job: URL in, transcript out.

Built 31 Aug 2026 (user order: own tools, token-friendly). Wraps yt-dlp;
auto-captions are deduped (they repeat every line — dedupe halves tokens).

Usage:
  py scripts/yt.py <url>                 # header + deduped transcript to stdout
  py scripts/yt.py <url> --deictic       # also list seconds where speaker points at screen
  py scripts/yt.py <url> --json          # structured: meta + lines[]
  py scripts/yt.py <url> --out FILE      # write transcript to FILE instead
  py scripts/yt.py <url> --meta          # metadata only, no captions (fast)

Cost: 0 tokens, 0 API keys. Needs yt-dlp (py -m yt_dlp) and, for future-proof
YouTube extraction, deno (auto-detected from winget path or PATH).
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

DEICTIC = [
    "look at", "you can see", "as you can see", "watch this", "see here",
    "see this", "right here", "this page", "this site", "on screen",
    "showing you", "here is", "screenshot", "pull up", "pulled up",
    "this chart", "this graph", "this code",
]


def find_deno():
    p = shutil.which("deno")
    if p:
        return p
    for c in (
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\DenoLand.Deno_Microsoft.Winget.Source_8wekyb3d8bbwe\deno.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\deno.exe"),
        os.path.expandvars(r"%USERPROFILE%\.deno\bin\deno.exe"),
    ):
        if os.path.exists(c):
            return c
    return None


def run_ytdlp(args_list):
    cmd = [sys.executable, "-m", "yt_dlp"]
    deno = find_deno()
    if deno:
        cmd += ["--js-runtimes", f"deno:{deno}"]
    cmd += args_list
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r


def dedupe_srt(path):
    """SRT -> [(seconds, '[MM:SS] text')]. YouTube rolling captions emit each
    line as partial then full states; drop exact consecutive repeats AND any
    block whose text is contained in its neighbour (halves tokens again)."""
    content = open(path, encoding="utf-8", errors="replace").read()
    raw = []
    for block in content.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)", lines[1])
        if not m:
            continue
        total = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        text = re.sub(r"<[^>]+>", "", " ".join(lines[2:]).strip())
        text = " ".join(text.split())
        if text:
            raw.append((total, text))
    out = []
    for i, (t, text) in enumerate(raw):
        prev_kept = out[-1][1] if out else ""
        nxt = raw[i + 1][1] if i + 1 < len(raw) else ""
        if text in prev_kept:          # partial repeat of what we already have
            continue
        if text != nxt and text in nxt:  # partial state of the next fuller line
            continue
        if text == prev_kept:
            continue
        out.append((t, text))
    # Merge rolling overlaps into continuous prose: consecutive kept lines
    # share their tail/head ("...A B" -> "B C..."); append only the new suffix.
    # A [MM:SS] marker is inserted roughly every 20 seconds of speech.
    merged, buf, buf_t, last_mark = [], "", None, -999
    for t, text in out:
        k = 0
        for j in range(min(len(buf), len(text)), 0, -1):
            if buf.endswith(text[:j]):
                k = j
                break
        addition = text[k:].strip()
        if not addition:
            continue
        if t - last_mark >= 20:
            if buf:
                merged.append((buf_t, buf.strip()))
            buf, buf_t, last_mark = addition, t, t
        else:
            buf += " " + addition
    if buf:
        merged.append((buf_t, buf.strip()))
    return [(t, f"[{t // 60:02d}:{t % 60:02d}] {x}") for t, x in merged]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--deictic", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--meta", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--lang", default="en")
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="yt_")
    try:
        args = ["--skip-download", "--print", "title", "--print", "channel",
                "--print", "duration_string", "--print", "view_count",
                "--print", "upload_date", "--no-simulate"]
        if not a.meta:
            args += ["--write-auto-sub", "--write-sub", "--sub-lang", a.lang,
                     "--convert-subs", "srt", "--output", os.path.join(tmp, "v.%(ext)s")]
        r = run_ytdlp(args + [a.url])
        printed = [l for l in r.stdout.strip().splitlines() if l.strip()]
        if len(printed) < 3:
            sys.exit(f"yt-dlp failed: {r.stderr.strip()[-400:]}")
        meta = {"title": printed[0], "channel": printed[1], "duration": printed[2],
                "views": printed[3] if len(printed) > 3 else "?",
                "uploaded": printed[4] if len(printed) > 4 else "?"}

        lines = []
        if not a.meta:
            srts = glob.glob(os.path.join(tmp, "*.srt"))
            if srts:
                lines = dedupe_srt(srts[0])
            else:
                meta["note"] = "no captions available"

        deictic = [t for t, l in lines if any(p in l.lower() for p in DEICTIC)]

        if a.json:
            body = json.dumps({"meta": meta, "deictic_seconds": deictic,
                               "lines": [l for _, l in lines]}, indent=1)
        else:
            head = (f"# {meta['title']}\n{meta['channel']} · {meta['duration']} · "
                    f"{meta['views']} views · uploaded {meta['uploaded']}")
            if a.deictic and deictic:
                head += f"\ndeictic seconds (speaker points at screen): {','.join(map(str, deictic))}"
            if meta.get("note"):
                head += f"\nNOTE: {meta['note']}"
            body = head + "\n\n" + "\n".join(l for _, l in lines)

        if a.out:
            open(a.out, "w", encoding="utf-8").write(body)
            print(f"wrote {a.out} ({len(lines)} transcript lines)")
        else:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            print(body)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
