#!/usr/bin/env python3
"""arxiv_fetch.py -- pull the full text of the papers a sweep kept, for a cheap reader to read locally.

  py scripts/arxiv_fetch.py arxiv/sweep_2026-09-06.json [--min-score 4] [--max 80]
  py scripts/arxiv_fetch.py --selftest          # header + manifest shape on a fake reader, no network

For each kept paper: try arXiv's HTML rendering (https://arxiv.org/html/<id>) through tools/web.py
(clean text, 15k cap raised to 60k here), fall back to the abstract page when there is no HTML build
(older LaTeX-only submissions), write arxiv/papers/<id>.txt with a one-line header
(id | title | date | source: html|abs), and print a manifest. Paces 3.2 s between papers -- arXiv's
own ask. Never invents text: a paper without HTML says so in its header.
"""
import argparse, json, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "arxiv" / "papers"
WEB = ROOT / "tools" / "web.py"


def read(url, cap):
    r = subprocess.run([sys.executable, str(WEB), url, "--cap", str(cap), "--signed"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    txt = r.stdout or ""
    ok = r.returncode == 0 and "[200]" in txt[:80] and len(txt) > 1500
    return ok, txt


def run(rows, papers, reader=read, pause=3.2):
    """rows: kept sweep rows -> <papers>/<id>.txt each with a one-line header; returns the manifest list."""
    papers.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, r in enumerate(rows, 1):
        pid = r["id"].split("v")[0]
        out = papers / (pid.replace("/", "_") + ".txt")
        if out.exists() and out.stat().st_size > 2000:
            manifest.append({"id": pid, "file": str(out), "source": "cached"}); continue
        ok, txt = reader("https://arxiv.org/html/" + r["id"], 60000)
        src = "html"
        if not ok:
            time.sleep(pause)
            ok, txt = reader("https://arxiv.org/abs/" + pid, 15000); src = "abs" if ok else "none"
        header = "%s | %s | %s | source: %s | score %d | lanes: %s\n\n" % (pid, r["title"], r["updated"], src, r["score"], "; ".join(r["lanes"].keys()))
        out.write_text(header + (txt if ok else "(no text retrievable from arXiv today -- abstract only follows)\n\n" + r["abstract"]), encoding="utf-8")
        manifest.append({"id": pid, "file": str(out), "source": src, "chars": len(txt) if ok else len(r["abstract"])})
        print("%3d/%d %s %-6s %s" % (i, len(rows), pid, src, r["title"][:70]))
        time.sleep(pause)
    return manifest


def selftest():
    row = {"id": "2609.00001v2", "title": "T", "updated": "2026-09-06", "score": 7, "lanes": {"x": []}, "abstract": "ABS"}
    calls = []

    def fake(url, cap):
        calls.append(url)
        return ("/html/" in url, "[200] " + "y" * 2000) if "/html/" in url else (False, "")

    def refuse(url, cap):
        raise AssertionError("must not fetch a cached paper")

    with tempfile.TemporaryDirectory() as d:
        m = run([row, dict(row, id="2609.00002")], Path(d), reader=lambda u, c: fake(u, c) if "00001" in u else (False, ""), pause=0)
        t1 = (Path(d) / "2609.00001.txt").read_text(encoding="utf-8")
        t2 = (Path(d) / "2609.00002.txt").read_text(encoding="utf-8")
        checks = [("html kept", m[0]["source"] == "html" and t1.startswith("2609.00001 | T | 2026-09-06 | source: html | score 7")),
                  ("no text = honest header + abstract", m[1]["source"] == "none" and "no text retrievable" in t2 and t2.endswith("ABS")),
                  ("html tried before abs", calls == ["https://arxiv.org/html/2609.00001v2"]),
                  ("cache skip", run([row], Path(d), reader=refuse, pause=0)[0]["source"] == "cached")]
    bad = [n for n, ok in checks if not ok]
    print(json.dumps({"selftest": "PASS" if not bad else "FAIL", "checks": len(checks), "failed": bad}))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sweep", nargs="?", help="sweep_<date>.json written by arxiv_sweep.py")
    ap.add_argument("--min-score", type=int, default=4)
    ap.add_argument("--max", type=int, default=80)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if not a.sweep:
        ap.error("sweep file required")
    sweep = json.loads(Path(a.sweep).read_text(encoding="utf-8"))
    rows = [r for r in sweep["rows"] if r["score"] >= a.min_score][: a.max]
    manifest = run(rows, PAPERS)
    for m in manifest:
        m["file"] = str(Path(m["file"]).relative_to(ROOT))
    (PAPERS / "manifest.json").write_text(json.dumps({"sweep": a.sweep, "papers": manifest}, indent=1), encoding="utf-8")
    print("fetched %d papers -> %s" % (len(manifest), (PAPERS / "manifest.json").relative_to(ROOT)))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
