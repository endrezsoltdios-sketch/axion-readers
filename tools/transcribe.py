#!/usr/bin/env python3
"""transcribe.py — audio or video in, text out. One job: turn a media file into
words we can read, grep and digest.

Built 1 Sep 2026 (tool factory wave 2). Engine: GROQ free tier whisper-large-v3
(US-hosted — allowed for public/low-sensitivity audio per the Chinese-AI data
policy; NEVER send private calls, positions or credentials through it).
Pairs with digest.py: transcribe → digest → verify.

Usage:
  py scripts/transcribe.py clip.mp3
  py scripts/transcribe.py video.mp4 --out t.txt
  py scripts/transcribe.py "https://youtu.be/XXXX" --out t.txt   # yt-dlp audio-only
  py scripts/transcribe.py clip.m4a --srt          # timestamped subtitles
  py scripts/transcribe.py clip.wav --turbo        # faster, slightly less accurate
  py scripts/transcribe.py de.mp3 --translate      # any language -> English

Needs: GROQ_API_KEY (env or HKCU). ffmpeg only for >24MB files or URL input;
yt-dlp (module or binary) only for URL input. Cost: 0 tokens, free tier.
"""
import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = "https://api.groq.com/openai/v1/audio/"
UA = "AxionReader/1.0"          # GROQ's edge 403s python-urllib's default UA
DL_UA = "AxionReader/1.0 (+https://getaxionlabs.com; transcript fetch)"
LIMIT = 24 * 1024 * 1024        # free-tier upload ceiling is 25MB; leave headroom


def get_key():
    k = os.environ.get("GROQ_API_KEY")
    if k:
        return k
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return winreg.QueryValueEx(h, "GROQ_API_KEY")[0]
    except OSError:
        sys.exit("GROQ_API_KEY not found (env or HKCU) — transcribe.py needs it")


def download(url, tmp):
    """URL -> local audio file, via yt-dlp (module or binary)."""
    out = str(Path(tmp) / "audio.%(ext)s")
    base = ["--user-agent", DL_UA,
            "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
            "--audio-quality", "5", "-o", out, url]
    # yt-dlp needs a JS runtime for YouTube since 2026; deno is its only default.
    if not shutil.which("deno") and shutil.which("node"):
        base = ["--js-runtimes", "node"] + base
    if shutil.which("yt-dlp"):
        cmd = ["yt-dlp"] + base
    else:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            sys.exit("[no downloader] URL input needs yt-dlp (binary or python "
                     "module). Download the audio yourself and pass the file path.")
        cmd = [sys.executable, "-m", "yt_dlp"] + base
    print(f"[downloading audio: {url}]", file=sys.stderr)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    files = sorted(Path(tmp).glob("audio.*"))
    if not files:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        sys.exit("[download failed] " + " / ".join(tail or ["no output from yt-dlp"]))
    return files[0]


def shrink(path, tmp):
    """Too big for the free tier: 16kHz mono FLAC, which is what whisper wants."""
    if not shutil.which("ffmpeg"):
        sys.exit(f"[too large] {path.name} is "
                 f"{path.stat().st_size/1e6:.1f}MB and the free tier caps at 25MB. "
                 f"ffmpeg is not on PATH, so it cannot be down-converted here — "
                 f"split or re-encode it first.")
    out = Path(tmp) / "small.flac"
    print(f"[{path.stat().st_size/1e6:.1f}MB — down-converting to 16kHz mono flac]",
          file=sys.stderr)
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
                        "-ar", "16000", "-ac", "1", "-map", "0:a", str(out)],
                       capture_output=True, text=True, timeout=1800)
    if not out.exists():
        sys.exit("[ffmpeg failed] " + (r.stderr or "no output").strip()[:300])
    if out.stat().st_size > LIMIT:
        sys.exit(f"[too large] still {out.stat().st_size/1e6:.1f}MB after "
                 f"down-conversion — split the file into chunks and run each.")
    return out


def stamp(sec, vtt):
    """seconds -> 00:01:02,345 (SRT) or 00:01:02.345 (VTT)."""
    ms = int(round(float(sec) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{'.' if vtt else ','}{ms:03d}"


def subtitles(payload, vtt):
    """GROQ has no srt/vtt response_format — we build them from verbose_json."""
    segs = payload.get("segments") or []
    if not segs:
        return payload.get("text", "").strip()
    out = ["WEBVTT", ""] if vtt else []
    for i, s in enumerate(segs, 1):
        if not vtt:
            out.append(str(i))
        out.append(f"{stamp(s.get('start', 0), vtt)} --> {stamp(s.get('end', 0), vtt)}")
        out.append((s.get("text") or "").strip())
        out.append("")
    return "\n".join(out).strip()


def multipart(fields, path):
    """Build a multipart/form-data body by hand — no requests dependency."""
    b = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts = []
    for k, v in fields.items():
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n"
                     f"\r\n{v}\r\n".encode())
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
                 f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="local audio/video file, or a URL")
    ap.add_argument("--out")
    ap.add_argument("--srt", action="store_true", help="timestamped SRT subtitles")
    ap.add_argument("--vtt", action="store_true")
    ap.add_argument("--turbo", action="store_true",
                    help="whisper-large-v3-turbo: faster, slightly less accurate")
    ap.add_argument("--translate", action="store_true", help="output English")
    ap.add_argument("--language", help="ISO code hint, e.g. en, hu, de")
    ap.add_argument("--prompt", help="spelling hints: names, jargon, acronyms")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    with tempfile.TemporaryDirectory() as tmp:
        if a.source.startswith(("http://", "https://")):
            path = download(a.source, tmp)
        else:
            path = Path(a.source)
            if not path.is_file():
                sys.exit(f"[no such file] {a.source}")
        if path.stat().st_size > LIMIT:
            path = shrink(path, tmp)
        if path.stat().st_size == 0:
            sys.exit(f"[empty file] {path}")

        model = "whisper-large-v3-turbo" if a.turbo else "whisper-large-v3"
        endpoint = "translations" if a.translate else "transcriptions"
        # GROQ accepts only json / text / verbose_json — timestamps come from
        # verbose_json and are formatted into SRT/VTT here.
        timed = a.srt or a.vtt
        fields = {"model": model,
                  "response_format": "verbose_json" if timed else "text"}
        if a.language and not a.translate:
            fields["language"] = a.language
        if a.prompt:
            fields["prompt"] = a.prompt

        body, ctype = multipart(fields, path)
        req = urllib.request.Request(
            API + endpoint, data=body,
            headers={"Authorization": f"Bearer {get_key()}",
                     "Content-Type": ctype, "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            try:
                detail = json.loads(detail)["error"]["message"]
            except Exception:
                pass
            sys.exit(f"[{e.code}] GROQ transcription refused — {detail}")
        except (urllib.error.URLError, TimeoutError) as e:
            sys.exit(f"[unreachable] GROQ — {getattr(e, 'reason', e)}")

    if timed:
        try:
            text = subtitles(json.loads(text), a.vtt)
        except json.JSONDecodeError:
            sys.exit("[unparseable] GROQ returned non-JSON for verbose_json")
    text = text.strip()
    if not text:
        sys.exit("[no speech] the model returned nothing — silent or music-only "
                 "audio, most likely")
    header = (f"# transcript — {Path(a.source).name} — {model}"
              f"{' (translated to English)' if a.translate else ''}\n"
              f"# machine transcription: verify names, numbers and quotes at the "
              f"source before anything rests on them.\n")
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(header + "\n" + text, encoding="utf-8")
        print(f"wrote {p} ({len(text)} chars)")
    else:
        print(header)
        print(text)


if __name__ == "__main__":
    main()
