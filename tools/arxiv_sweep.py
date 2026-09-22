#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
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

HTTP 406 repair, 22 Sep 2026 (radar verdicts ADOPT 6). This tool used to send a long User-Agent that
carries a URL and an @ address, no Accept, no Accept-Encoding, and no second door, and the arXiv
gateway answered 406 Not Acceptable to every page, so the sweep returned 0 papers while scripts/radar.py
read 200 from the same API. The request shape here is now radar.py's: the short product token
"Mozilla/5.0 axion-radar/1.0" with no URL and no @ in it, Accept: application/atom+xml, and
Accept-Encoding: gzip, deflate inflated on the way in. Measured the same evening: the gateway also
answers 406 in runs, to urllib only, while curl with the same headers reads 200 through the whole run,
so a refused page is retried with backoff and then handed to the curl on the box, the same second door
radar.arxiv() uses. Every request prints the status it saw; a 0-paper run can no longer look silent.
"""
import argparse, gzip, json, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, zlib
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "arxiv"
SEEN = ROOT / "knowledge" / "arxiv_seen.json"
LANES = ROOT / "knowledge" / "arxiv_lanes.json"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
API = "https://export.arxiv.org/api/query"
# arXiv answers 406 to a bare product token, to a URL or an @ inside the agent string, and to any request
# without gzip accepted (measured 21 Sep 2026 in radar.py, reproduced here 22 Sep 2026); this exact string passes.
UA = "Mozilla/5.0 axion-readers-arxiv/1.0"
ACCEPT = "application/atom+xml, */*"
BACKOFF = (4.0, 12.0, 30.0)  # seconds between retries of a refused page; arXiv asks for one request per 3 s

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


def page_url(cats, start, per):
    """One page of the window query. urlencode, not quote: radar.py's shape, so the API sees the same string."""
    q = " OR ".join("cat:%s" % c for c in cats)
    return API + "?" + urllib.parse.urlencode({"search_query": q, "start": start, "max_results": per,
                                               "sortBy": "submittedDate", "sortOrder": "descending"})


def build_request(url, accept=ACCEPT):
    """The exact request shape radar.py:get() uses. The gzip header is the one that stops the 406."""
    return urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Encoding": "gzip, deflate"})


def inflate(raw, enc):
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if enc == "deflate":
        return zlib.decompress(raw, -zlib.MAX_WBITS) if raw[:1] != b"x" else zlib.decompress(raw)
    return raw


def get_curl(url):
    """Second door: the curl on the box (Windows 10+, macOS, Linux). Measured 22 Sep 2026: curl reads 200
    from this API through a whole run while urllib is being answered 406 with identical headers."""
    r = subprocess.run(["curl", "-sS", "-L", "--max-time", "60", "-A", UA, "-H", "Accept: " + ACCEPT, url], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("curl exit %d: %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[:120]))
    return r.stdout.decode("utf-8", "replace"), "curl 200"


_PREFER_CURL = [False]  # latched once urllib has been refused in this run: the backoff is paid once, not per page


def get(url, tries=BACKOFF, sleep=time.sleep):
    """Returns (text, status_note). urllib first with the gzip shape, retried with backoff on 406/429/5xx,
    then curl. Raises only when both doors are shut, so the caller can name the status it saw."""
    if _PREFER_CURL[0]:
        return get_curl(url)
    last = ""
    for i in range(len(tries) + 1):
        try:
            with urllib.request.urlopen(build_request(url), timeout=60) as r:
                raw, enc, code = r.read(), (r.headers.get("Content-Encoding") or "").lower(), r.status
            return inflate(raw, enc).decode("utf-8", "replace"), "urllib %d" % code
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code not in (406, 429, 500, 502, 503, 504) or i == len(tries):
                break
        except Exception as e:  # DNS, TLS, timeout: one retry each, same backoff
            last = type(e).__name__
            if i == len(tries):
                break
        sleep(tries[i])
    text, note = get_curl(url)
    _PREFER_CURL[0] = True
    return text, "urllib %s then %s" % (last, note)


def parse_entries(text, since=None):
    """Atom -> {bare id: paper}. Returns (papers, oldest_seen). No network, so the selftest can call it."""
    root = ET.fromstring(text)
    out, oldest = {}, None
    for e in root.findall("a:entry", NS):
        pid = (e.findtext("a:id", namespaces=NS) or "").split("/abs/")[-1]
        if not pid:
            continue
        pub = e.findtext("a:published", namespaces=NS) or ""
        upd = e.findtext("a:updated", namespaces=NS) or pub
        when = datetime.fromisoformat(upd.replace("Z", "+00:00"))
        oldest = when if oldest is None or when < oldest else oldest
        if since is not None and when < since:
            continue
        out[pid.split("v")[0]] = {
            "id": pid, "title": re.sub(r"\s+", " ", e.findtext("a:title", namespaces=NS) or "").strip(),
            "abstract": re.sub(r"\s+", " ", e.findtext("a:summary", namespaces=NS) or "").strip(),
            "published": pub[:10], "updated": upd[:10],
            "authors": [a.findtext("a:name", namespaces=NS) for a in e.findall("a:author", NS)][:6],
            "cats": [c.get("term") for c in e.findall("a:category", NS)],
            "url": "https://arxiv.org/abs/" + pid.split("v")[0],
        }
    return out, oldest


def fetch(cats, days, max_pages=40):
    """Every paper submitted/updated in the window, newest first, across the categories."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out, start, per = {}, 0, 200
    for _ in range(max_pages):
        u = page_url(cats, start, per)
        try:
            body, note = get(u)
        except Exception as e:
            print("[arxiv api] %s at start=%d -- stopping with %d papers" % (e, start, len(out)), file=sys.stderr)
            break
        try:
            page, oldest = parse_entries(body, since)
        except ET.ParseError as e:
            print("[arxiv api] unparseable body at start=%d (%s, %s) -- stopping with %d papers" % (start, note, e, len(out)), file=sys.stderr)
            break
        print("[arxiv api] start=%d %s, %d kept in window (%d so far)" % (start, note, len(page), len(out) + len(page)), file=sys.stderr)
        if oldest is None:
            break
        out.update(page)
        start += per
        if oldest < since:
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


FIXTURE_ATOM = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2609.11152v2</id>
    <published>2026-09-14T10:00:00Z</published>
    <updated>2026-09-19T10:00:00Z</updated>
    <title>Machine-readable
      access terms</title>
    <summary> Web Bot Auth and HTTP message signature receipts for pay per crawl. </summary>
    <author><name>A One</name></author><author><name>B Two</name></author>
    <category term="cs.CR"/><category term="cs.AI"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <published>2026-01-02T10:00:00Z</published>
    <updated>2026-01-02T10:00:00Z</updated>
    <title>Old paper</title><summary>Out of the window.</summary>
    <author><name>C Three</name></author><category term="cs.CY"/>
  </entry>
</feed>"""


def selftest(cfg):
    checks, fails = [], []
    _PREFER_CURL[0] = False

    def ck(name, ok):
        checks.append(name)
        if not ok:
            fails.append(name)

    # 1-3, the scorer, unchanged from the 6 Sep build
    fx = {"title": "Web Bot Auth for verified crawlers with HTTP message signatures", "abstract": "We propose signed agent requests and a citation receipt for pay per crawl."}
    s, h, _ = score(fx, cfg)
    ck("door_paper_scores", s >= 9 and next(iter(cfg["lanes"])) in h)   # the first lane in the config is the one the fixture targets
    fx2 = {"title": "Diffusion model for protein structure", "abstract": "image generation of molecules"}
    s2, _, n2 = score(fx2, cfg)
    ck("noise_paper_zero", s2 == 0 and len(n2) >= 2)
    fx3 = {"title": "A benchmark", "abstract": "cost evaluation"}
    s3, _, _ = score(fx3, cfg)
    ck("weak_paper_between", 0 < s3 < 6)

    # 4, the 406 catch: the request must accept gzip, or the API refuses the page (22 Sep 2026)
    req = build_request("https://export.arxiv.org/api/query?search_query=cat%3Acs.AI")
    ck("request_accepts_gzip", "gzip" in (req.get_header("Accept-encoding") or ""))
    # 5, and the agent string must stay a short product token with no URL and no @ in it
    ck("agent_token_clean", "@" not in UA and "http" not in UA.lower() and len(UA) < 60 and req.get_header("User-agent") == UA)
    # 6, Accept is the feed type the API serves
    ck("accept_is_atom", "atom+xml" in (req.get_header("Accept") or ""))
    # 7, the page URL is https and carries the window sort the sweep depends on
    u = page_url(["cs.AI", "cs.CR"], 200, 100)
    ck("page_url_shape", u.startswith("https://export.arxiv.org/api/query?") and "sortBy=submittedDate" in u
       and "sortOrder=descending" in u and "start=200" in u and "max_results=100" in u and "cat%3Acs.AI" in u)
    # 8, a gzipped body is inflated, which is what accepting gzip commits us to
    ck("inflates_gzip", inflate(gzip.compress(b"<feed/>"), "gzip") == b"<feed/>" and inflate(b"<feed/>", "") == b"<feed/>")
    # 9, the Atom parser, on a canned feed, with the window filter applied
    papers, oldest = parse_entries(FIXTURE_ATOM, datetime(2026, 9, 12, tzinfo=timezone.utc))
    p = papers.get("2609.11152")
    ck("parses_atom_window", len(papers) == 1 and p and p["title"] == "Machine-readable access terms"
       and p["id"] == "2609.11152v2" and p["updated"] == "2026-09-19" and p["published"] == "2026-09-14"
       and p["authors"] == ["A One", "B Two"] and p["cats"] == ["cs.CR", "cs.AI"]
       and p["url"] == "https://arxiv.org/abs/2609.11152" and oldest.year == 2026 and oldest.month == 1)
    # 10, no window filter means both entries, and an empty feed reports oldest None so fetch() stops
    ck("parses_atom_unfiltered", len(parse_entries(FIXTURE_ATOM)[0]) == 2
       and parse_entries('<feed xmlns="http://www.w3.org/2005/Atom"/>') == ({}, None))
    # 11, a 406 is retried with backoff and then handed to the second door, never returned as 0 papers in silence
    slept = []

    def fake_open(req_, timeout=None):
        raise urllib.error.HTTPError(req_.full_url, 406, "Not Acceptable", {}, None)

    real_open, real_curl = urllib.request.urlopen, globals()["get_curl"]
    urllib.request.urlopen = fake_open
    globals()["get_curl"] = lambda url: ("<feed/>", "curl 200")
    try:
        text, note = get("https://export.arxiv.org/api/query?x=1", sleep=slept.append)
    finally:
        urllib.request.urlopen, globals()["get_curl"] = real_open, real_curl
    ck("406_retries_then_curl", text == "<feed/>" and note == "urllib HTTP 406 then curl 200"
       and slept == list(BACKOFF) and all(b > 3.0 for b in BACKOFF))
    # 12, once refused, the run latches onto the second door so the backoff is paid once and not on every page
    ck("curl_latches_after_refusal", _PREFER_CURL[0] is True)
    _PREFER_CURL[0] = False
    # 13, a 404 is not a throttle: no retries, straight to the second door
    slept2 = []
    urllib.request.urlopen = lambda req_, timeout=None: (_ for _ in ()).throw(urllib.error.HTTPError(req_.full_url, 404, "Not Found", {}, None))
    globals()["get_curl"] = lambda url: ("<feed/>", "curl 200")
    try:
        _, note2 = get("https://export.arxiv.org/api/query?x=1", sleep=slept2.append)
    finally:
        urllib.request.urlopen, globals()["get_curl"] = real_open, real_curl
    ck("404_does_not_retry", note2 == "urllib HTTP 404 then curl 200" and slept2 == [])

    print(json.dumps({"selftest": "PASS" if not fails else "FAIL", "checks": "%d/%d" % (len(checks) - len(fails), len(checks)),
                      "failed": fails, "door_paper": s, "noise_paper": s2, "weak_paper": s3}))
    return 0 if not fails else 1


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
