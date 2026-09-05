#!/usr/bin/env python3
"""worker_sizes.py — measure each fact-site Worker's UNCOMPRESSED bundle size
against Cloudflare's 64 MiB limit. Zero tokens, zero keys, no deploy.

Why this exists: Cloudflare removed the compressed-bundle ceiling (3 MB Free /
10 MB Paid) on 4 Sep 2026; only the uncompressed 64 MiB limit is now checked, on
all plans. [PRIMARY: https://developers.cloudflare.com/changelog/ 4 Sep 2026,
recorded in business/research/landscape_2026-09-05.md ADOPT item 3]

Method: `npx wrangler deploy --dry-run --outdir <tmp>` per site (DRY-RUN ONLY —
this never deploys), then sum the emitted bundle files excluding sourcemaps and
wrangler's own README. That sum equals wrangler's own "Total Upload" figure to
the byte, which is the number checked against the limit.

Usage:
  py scripts/worker_sizes.py            # measure all, rewrite ops/worker_sizes.json
  py scripts/worker_sizes.py --print    # re-print the last saved reading, measure nothing
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ops" / "worker_sizes.json"
LIMIT_BYTES = 64 * 1024 * 1024  # 64 MiB, uncompressed — the only ceiling as of 4 Sep 2026
FLAG_PCT = 50.0                 # flag any site past half the limit

SITES = [
    "business/appeal_engine/site",
    "business/denial_facts/site",
    "business/nyc_ticket_facts/site",
    "business/mtd_answers/site",
    "business/screening_facts/site",
]
# Emitted by wrangler into --outdir but NOT part of the uploaded bundle.
NOT_UPLOADED = {"README.md"}


def bundle_bytes(outdir: Path) -> int:
    total = 0
    for p in outdir.rglob("*"):
        if not p.is_file():
            continue
        if p.name in NOT_UPLOADED or p.suffix == ".map":
            continue
        total += p.stat().st_size
    return total


def measure(site: str):
    d = ROOT / site
    if not (d / "wrangler.jsonc").exists() and not (d / "wrangler.toml").exists():
        return {"site": site, "bytes": None, "error": "no wrangler config"}
    tmp = Path(tempfile.mkdtemp(prefix="wsz_"))
    try:
        r = subprocess.run(
            ["npx", "wrangler", "deploy", "--dry-run", "--outdir", str(tmp)],
            cwd=str(d), capture_output=True, text=True, timeout=900,
            # wrangler prints emoji; the Windows console codepage cannot decode them
            encoding="utf-8", errors="replace",
            shell=(os.name == "nt"),
        )
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
            return {"site": site, "bytes": None, "error": " | ".join(tail)[:300]}
        return {"site": site, "bytes": bundle_bytes(tmp)}
    except subprocess.TimeoutExpired:
        return {"site": site, "bytes": None, "error": "wrangler dry-run timed out"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def report(doc):
    print(f"limit {doc['limit_bytes']:,} bytes (64 MiB, uncompressed)   measured {doc['measured_at']}")
    for s in doc["sites"]:
        if s.get("bytes") is None:
            print(f"  {s['site']:<40} FAILED  {s.get('error','')}")
            continue
        mark = "  <-- OVER 50% OF LIMIT" if s["used_pct"] >= FLAG_PCT else ""
        print(f"  {s['site']:<40} {s['bytes']:>10,} B  "
              f"{s['used_pct']:>6.2f}% used  {s['headroom_pct']:>6.2f}% headroom{mark}")
    flagged = [s["site"] for s in doc["sites"] if s.get("used_pct", 0) >= FLAG_PCT]
    print(f"flagged (>= {FLAG_PCT:.0f}% of limit): " + (", ".join(flagged) if flagged else "none"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="show", action="store_true",
                    help="re-print ops/worker_sizes.json without measuring")
    a = ap.parse_args()
    if a.show:
        if not OUT.exists():
            sys.exit(f"{OUT} does not exist — run without --print first")
        report(json.loads(OUT.read_text(encoding="utf-8")))
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for site in SITES:
        print(f"[dry-run] {site} ...", flush=True)
        row = measure(site)
        row["measured_at"] = now
        row["limit_bytes"] = LIMIT_BYTES
        if row.get("bytes") is not None:
            row["used_pct"] = round(row["bytes"] / LIMIT_BYTES * 100, 4)
            row["headroom_pct"] = round(100 - row["used_pct"], 4)
            row["headroom_bytes"] = LIMIT_BYTES - row["bytes"]
        rows.append(row)
    doc = {
        "measured_at": now,
        "limit_bytes": LIMIT_BYTES,
        "limit_note": "64 MiB uncompressed; the 3 MB/10 MB compressed ceiling was removed 4 Sep 2026 "
                      "(developers.cloudflare.com/changelog/)",
        "method": "npx wrangler deploy --dry-run --outdir <tmp>; sum of emitted files "
                  "excluding *.map and README.md; equals wrangler's own 'Total Upload'",
        "flag_threshold_pct": FLAG_PCT,
        "sites": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    report(doc)
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
