#!/usr/bin/env python3
"""arxiv_sweep.py -- every new arXiv paper in your categories, scored against the lanes you run.

Why: arXiv
posts ~20,000 papers a month; a person cannot read them and a model reading all of them would learn
nothing. This tool reads the METADATA of every new paper in the categories you care about (arXiv's
own API, free, no key), scores each against the lanes in a JSON file, keeps a seen-ledger so a paper
is scored once, and hands a short ranked list to a reader with judgment. Volume by machine, judgment
by the frontier model, verification by you.

  py scripts/arxiv_sweep.py --days 7                 sweep, score, print the ranked list, write the files
  py scripts/arxiv_sweep.py --days 30 --top 60       first pass over a month
  py scripts/arxiv_sweep.py --cats cs.AI,cs.SE        override categories
  py scripts/arxiv_sweep.py --selftest               scoring on a fixture, no network

Outputs: arxiv/sweep_<date>.json (every paper seen with score + reasons) and
arxiv/SWEEP_<date>.md (the ranked list a reader opens). Seen-ledger:
knowledge/arxiv_seen.json (id -> first-seen date), so weekly runs show only what is new.
Config: knowledge/arxiv_lanes.json -- written from DEFAULT_LANES on first run, then grow the file,
not the code (arxiv_lanes.example.json in the public repo is that default). Exit 0 ok / 1 API error.
Politeness: arXiv asks for one request per 3 s; this tool sleeps 3.2 s between pages.
"""
import argparse, json, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "arxiv"
SEEN = ROOT / "knowledge" / "arxiv_seen.json"
LANES = ROOT / "knowledge" / "arxiv_lanes.json"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
API = "http://export.arxiv.org/api/query"
UA = {"User-Agent": "axion-readers/arxiv_sweep (+https://github.com/endrezsoltdios-sketch/axion-readers)"}

DEFAULT_LANES = {
    "about": "Terms are scored on title+abstract. weight = how much a hit matters to a lane you run; a paper's score is the sum of distinct term hits (each term counts once). Grow this file, not the code.",
    "categories": ["cs.AI", "cs.CL", "cs.SE", "cs.CR", "cs.MA", "cs.IR", "cs.CY", "cs.DL"],
    "lanes": {
        "agents-and-the-machine-web (tool use, harnesses, signed agents, crawling, citation)": {
            "weight": 3,
            "terms": ["agent harness", "tool use", "multi-agent", "orchestrat", "coding agent", "prompt injection", "agent security", "sandbox",
                      "mcp server", "model context protocol", "tool discovery", "web bot auth", "http message signature", "signed agent", "verified bot",
                      "crawler", "web crawl", "pay per crawl", "citation", "provenance", "attribution", "llms.txt", "robots.txt"]
        },
        "retrieval-and-answers (RAG, grounding, freshness, verification)": {
            "weight": 2,
            "terms": ["retrieval-augmented", "rag", "grounding", "hallucination", "citation accuracy", "source attribution", "answer engine",
                      "generative search", "search engine", "freshness", "knowledge cutoff", "fact verification", "claim verification"]
        },
        "efficiency (small models, cost, evaluation)": {
            "weight": 1,
            "terms": ["small language model", "local model", "cost", "token efficiency", "inference cost", "open-weight", "distillation", "quantization",
                      "benchmark", "evaluation", "measurement"]
        }
    },
    "noise": ["diffusion model", "image generation", "protein", "molecule", "quantum", "robot arm", "autonomous driving", "speech recognition", "video generation",
              "3d reconstruction", "medical imaging", "segmentation", "point cloud", "reinforcement learning from human feedback for games", "federated learning"]
}


def lanes():
    if not LANES.exists():
        LANES.write_text(json.dumps(DEFAULT_LANES, indent=1), encoding="utf-8")
    return json.loads(LANES.read_text(encoding="utf-8"))


def fetch(cats, days, max_pages=40):
    """Every paper submitted/updated in the window, newest first, across the categories."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = " OR ".join("cat:%s" % c for c in cats)
    out, start, per = {}, 0, 200
    for _ in range(max_pages):
        u = "%s?search_query=%s&sortBy=submittedDate&sortOrder=descending&start=%d&max_results=%d" % (API, urllib.parse.quote(q), start, per)
        try:
            body = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read()
        except Exception as e:
            print("[arxiv api] %s at start=%d -- stopping with %d papers" % (e, start, len(out)), file=sys.stderr)
            break
        root = ET.fromstring(body)
        entries = root.findall("a:entry", NS)
        if not entries:
            break
        oldest = None
        for e in entries:
            pid = e.findtext("a:id", namespaces=NS).split("/abs/")[-1]
            pub = e.findtext("a:published", namespaces=NS) or ""
            upd = e.findtext("a:updated", namespaces=NS) or pub
            when = datetime.fromisoformat(upd.replace("Z", "+00:00"))
            oldest = when if oldest is None or when < oldest else oldest
            if when < since:
                continue
            out[pid.split("v")[0]] = {
                "id": pid, "title": re.sub(r"\s+", " ", e.findtext("a:title", namespaces=NS) or "").strip(),
                "abstract": re.sub(r"\s+", " ", e.findtext("a:summary", namespaces=NS) or "").strip(),
                "published": pub[:10], "updated": upd[:10],
                "authors": [a.findtext("a:name", namespaces=NS) for a in e.findall("a:author", NS)][:6],
                "cats": [c.get("term") for c in e.findall("a:category", NS)],
                "url": "https://arxiv.org/abs/" + pid.split("v")[0],
            }
        start += per
        if oldest is not None and oldest < since:
            break
        time.sleep(3.2)
    return out


_TERM_RE = {}


def _hit(term, text):
    """Whole-word match: 'fine' must not hit 'fine-tuning', 'cost' must not hit 'costly' (6 Sep 2026, first run)."""
    rx = _TERM_RE.get(term)
    if rx is None:
        rx = _TERM_RE[term] = re.compile(r"(?<![a-z0-9-])" + re.escape(term) + r"(?![a-z0-9-])")
    return rx.search(text) is not None


def score(paper, cfg):
    text = (paper["title"] + " " + paper["abstract"]).lower()
    hits, total = {}, 0
    for lane, spec in cfg["lanes"].items():
        found = sorted({t for t in spec["terms"] if _hit(t, text)})
        if found:
            hits[lane] = found
            total += spec["weight"] * len(found)
    noise = [n for n in cfg.get("noise", []) if _hit(n, text)]
    if noise and total < 6:
        total = max(0, total - 3 * len(noise))
    return total, hits, noise


def selftest(cfg):
    fx = {"title": "Web Bot Auth for verified crawlers with HTTP message signatures", "abstract": "We propose signed agent requests and a citation receipt for pay per crawl."}
    s, h, _ = score(fx, cfg)
    ok1 = s >= 9 and next(iter(cfg["lanes"])) in h   # the first lane in the config is the one the fixture targets
    fx2 = {"title": "Diffusion model for protein structure", "abstract": "image generation of molecules"}
    s2, _, n2 = score(fx2, cfg)
    ok2 = s2 == 0 and len(n2) >= 2
    fx3 = {"title": "A benchmark", "abstract": "cost evaluation"}
    s3, _, _ = score(fx3, cfg)
    ok3 = 0 < s3 < 6
    print(json.dumps({"selftest": "PASS" if ok1 and ok2 and ok3 else "FAIL", "door_paper": s, "noise_paper": s2, "weak_paper": s3}))
    return 0 if ok1 and ok2 and ok3 else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--cats")
    ap.add_argument("--min-score", type=int, default=4)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:   # never writes the lanes file: a selftest leaves no trace
        sys.exit(selftest(json.loads(LANES.read_text(encoding="utf-8")) if LANES.exists() else DEFAULT_LANES))
    cfg = lanes()
    cats = a.cats.split(",") if a.cats else cfg["categories"]
    papers = fetch(cats, a.days)
    if not papers:
        print("[arxiv api] no papers returned"); sys.exit(1)
    seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
    today = date.today().isoformat()
    rows = []
    for pid, p in papers.items():
        s, hits, noise = score(p, cfg)
        p.update({"score": s, "lanes": hits, "noise": noise, "new": pid not in seen})
        rows.append(p)
        seen.setdefault(pid, today)
    rows.sort(key=lambda r: (-r["score"], r["updated"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("sweep_%s.json" % today)).write_text(json.dumps({"generated": today, "days": a.days, "categories": cats, "papers": len(rows), "rows": rows}, indent=1), encoding="utf-8")
    SEEN.write_text(json.dumps(seen, indent=0), encoding="utf-8")
    kept = [r for r in rows if r["score"] >= a.min_score][: a.top]
    new_kept = sum(1 for r in kept if r["new"])
    L = ["# arXiv sweep -- %s (last %d days, %s)" % (today, a.days, ", ".join(cats)), "",
         "%s papers read by metadata; %d scored >= %d against knowledge/arxiv_lanes.json; %d of those not seen before. "
         "Scores are term hits weighted by lane -- a shortlist for a reader, not a verdict. Every URL below is arXiv's own; nothing is invented." % (format(len(rows), ","), len([r for r in rows if r["score"] >= a.min_score]), a.min_score, new_kept), "",
         "| # | score | new | id | title | lanes hit | date |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(kept, 1):
        lanes_hit = "; ".join("%s: %s" % (k.split(" (")[0], ", ".join(v[:4])) for k, v in r["lanes"].items())
        L.append("| %d | %d | %s | [%s](%s) | %s | %s | %s |" % (i, r["score"], "yes" if r["new"] else "", r["id"], r["url"], r["title"].replace("|", "/"), lanes_hit.replace("|", "/"), r["updated"]))
    by_lane = {}
    for r in rows:
        for k in r["lanes"]:
            by_lane[k] = by_lane.get(k, 0) + 1
    L += ["", "## Coverage by lane (papers with at least one hit)", ""] + ["- %s: %d" % (k, v) for k, v in sorted(by_lane.items(), key=lambda x: -x[1])]
    L += ["", "## What a reader does with this", "", "Open the top rows' abstracts, apply the adopt/skip filter: "
          "each adopted paper becomes a test in your own estate with a falsifier and a date; each skipped one is named so it is not re-litigated. Never summarise."]
    (OUT / ("SWEEP_%s.md" % today)).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:6 + min(len(kept), 25)]))
    print("\nwrote %s and %s | %d papers, %d kept, %d new" % ((OUT / ("SWEEP_%s.md" % today)).relative_to(ROOT), (OUT / ("sweep_%s.json" % today)).relative_to(ROOT), len(rows), len(kept), new_kept))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
