#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""library.py - meaning search over our own idea corpus: BM25 over paragraphs of the markdown folders you name (LIBRARY_ROOTS),
plus the Claude Code memory folder when one exists. No embeddings, no API keys, no network, no third-party packages.
  py scripts/library.py "bedtime fear"                 # top 10 passages: score, file:line, 200-char snippet
  py scripts/library.py "POPLA 28 days" --json --top 5
  py scripts/library.py --hooks uk_parking --top 3     # the 3 strongest outliers for a lane from hooks_latest.json (read-only)
  py scripts/library.py --selftest                     # fixture dir, planted line, rebuild-on-change; exits 0 on PASS
Index cached in ops/library_index.json; rebuilt when any corpus file changed (mtime or size), appeared or vanished.
Read-only against the corpus. Failure is a line, not a traceback."""
import argparse, json, math, os, re, sys, tempfile, time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "ops" / "library_index.json"
HOOKS = ROOT / "hooks_latest.json"
MAX_FILE_BYTES = 2_000_000       # anything bigger is a data dump, not an idea
MAX_PASSAGE_CHARS = 2000         # a paragraph longer than this is a table or a log; keep the head
PER_FILE_CAP = 3                 # one long file must not fill the whole top-10
K1, B = 1.5, 0.75
STOP = set("""a an and are as at be been but by can could did do does for from had has have he her his how i if in into is it its
me my no nor not of on or our own so than that the their them then there these they this those to too was we were what when where
which who why will with would you your yes also just very than very""".split())
TOKEN = re.compile(r"[a-z0-9][a-z0-9'_-]*[a-z0-9]|[a-z0-9]", re.I)


def memory_dir(root):
    """The Claude Code memory folder for this repo: <drive>:/a/b/repo -> ~/.claude/projects/<drive>--a-b-repo/memory."""
    s = str(root).replace("\\", "/").rstrip("/")
    parts = [p for p in s.split("/") if p]
    if not parts:
        return None
    drive = parts[0].rstrip(":")
    return Path.home() / ".claude" / "projects" / (drive + "--" + "-".join(parts[1:])) / "memory"


# The corpus: (display-prefix, path relative to the repo root, glob or None for a single file). LIBRARY_ROOTS="a,b/c,NOTES.md"
# in the environment replaces this list (a .md path is a file, anything else a directory of **/*.md).
DEFAULT_SOURCES = [("docs", "docs", "**/*.md"),
                   ("notes", "notes", "**/*.md"),
                   ("README.md", "README.md", None)]


def sources(root, spec=None):
    """(display-prefix, directory-or-file, glob) triples; the Claude Code memory folder is added read-only when it exists."""
    if spec is None:
        env = os.environ.get("LIBRARY_ROOTS", "").strip()
        spec = [(x.strip(), x.strip(), None if x.strip().endswith(".md") else "**/*.md") for x in env.split(",") if x.strip()]             if env else DEFAULT_SOURCES
    out = [(show, root / rel, pattern) for show, rel, pattern in spec]
    mem = memory_dir(root)
    if mem and mem.is_dir():
        out.append(("memory", mem, "*.md"))
    return out


def stem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text):
    return [stem(t.lower()) for t in TOKEN.findall(text) if t.lower() not in STOP and len(t) > 1]


def scan_files(root, spec=None):
    """{abs_path: [show_path, mtime, size]} for every corpus file, cheap (stat only)."""
    found = {}
    for show, base, pattern in sources(root, spec):
        if pattern is None:
            paths = [base] if base.is_file() else []
        else:
            paths = base.glob(pattern) if base.is_dir() else []
        for p in paths:
            sp = str(p).replace("\\", "/")
            if "/node_modules/" in sp or "/.git/" in sp:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES or st.st_size == 0:
                continue
            rel = p.name if pattern is None or pattern == "*.md" else str(p.relative_to(base)).replace("\\", "/")
            disp = show if pattern is None else show + "/" + rel
            found[str(p)] = [disp, round(st.st_mtime, 3), st.st_size]
    return found


def passages_of(path):
    """Yield (start_line, text) per blank-line-separated block."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    buf, start = [], None
    for i, line in enumerate(lines, 1):
        if line.strip():
            if start is None:
                start = i
            buf.append(line)
        elif buf:
            yield start, "\n".join(buf)
            buf, start = [], None
    if buf:
        yield start, "\n".join(buf)


def build(root, files):
    docs = []
    for abs_path, (show, _m, _s) in sorted(files.items()):
        for line, text in passages_of(abs_path):
            if len(tokens(text)) < 4:
                continue
            docs.append([show, line, text[:MAX_PASSAGE_CHARS]])
    return {"schema": "axion.library/1", "built": time.strftime("%Y-%m-%dT%H:%M:%S"), "root": str(root),
            "files": files, "passages": docs}


def load_index(root, index_path, force=False, note=print, spec=None):
    files = scan_files(root, spec)
    idx = None
    if not force and index_path.is_file():
        try:
            idx = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            idx = None
    stale = idx is None or idx.get("schema") != "axion.library/1" or idx.get("files") != files
    if stale:
        t0 = time.time()
        idx = build(root, files)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
        note(f"[index rebuilt: {len(files)} files, {len(idx['passages'])} passages, {time.time() - t0:.1f}s -> {index_path}]")
    return idx


class BM25:
    def __init__(self, passages):
        self.p = passages
        self.tf, self.len, self.post = [], [], defaultdict(list)
        for i, (_f, _l, text) in enumerate(passages):
            c = Counter(tokens(text))
            self.tf.append(c)
            self.len.append(sum(c.values()))
            for t in c:
                self.post[t].append(i)
        self.n = max(len(passages), 1)
        self.avg = (sum(self.len) / self.n) if self.n else 1.0

    def search(self, query, top=10, per_file=PER_FILE_CAP):
        q = tokens(query)
        if not q:
            return []
        scores = defaultdict(float)
        for t in set(q):
            ids = self.post.get(t)
            if not ids:
                continue
            idf = math.log(1 + (self.n - len(ids) + 0.5) / (len(ids) + 0.5))
            for i in ids:
                f = self.tf[i][t]
                scores[i] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * self.len[i] / self.avg))
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        out, seen = [], Counter()
        for i, s in ranked:
            f = self.p[i][0]
            if seen[f] >= per_file:
                continue
            seen[f] += 1
            out.append({"score": round(s, 2), "file": f, "line": self.p[i][1],
                        "snippet": re.sub(r"\s+", " ", self.p[i][2])[:200]})
            if len(out) >= top:
                break
        return out


def resolve_lane(lanes, want):
    want = (want or "").lower().strip()
    if want in lanes:
        return want, None
    hits = [k for k in lanes if want and want in k.lower()]
    if len(hits) == 1:
        return hits[0], None
    return None, f"lane '{want}' -> {'ambiguous: ' + ', '.join(hits) if hits else 'unknown'}; lanes: {', '.join(lanes)}"


def hooks_top(lane, top, as_json):
    if not HOOKS.is_file():
        print(f"[no hooks file: {HOOKS}; run scripts/hook_miner.py first]")
        return 2
    d = json.loads(HOOKS.read_text(encoding="utf-8"))
    lanes = [k for k in d.get("lanes", {}) if not k.startswith("_")]
    key, err = resolve_lane(lanes, lane)
    if err:
        print("[" + err + "]")
        return 2
    L = d["lanes"][key]
    ev = sorted(L.get("evidence", []), key=lambda e: -float(e.get("outlier_score") or 0))[:top]
    tt = sorted(L.get("tiktok_evidence", []), key=lambda e: e.get("rank") or 99)[:top]
    out = {"lane": key, "label": L.get("label"), "generated": d.get("generated"), "params": d.get("params"),
           "outliers": [{k: e.get(k) for k in ("platform", "title", "url", "views", "channel", "channel_median",
                                                 "outlier_score", "published", "seconds", "is_short")} for e in ev],
           "tiktok_ranking": [{k: e.get(k) for k in ("title", "url", "query", "rank")} for e in tt],
           "shapes": L.get("shapes", [])[:3], "templates": L.get("templates", [])}
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(f"lane {key} ({out['label']}) hooks generated {out['generated']}; outlier_min {d.get('params', {}).get('outlier_min')}")
    for e in out["outliers"]:
        print(f"  {e['outlier_score']:>7}x  {e['views']:>9} views  {e['published']}  {'short' if e['is_short'] else 'long '}  "
              f"{e['channel']} (median {e['channel_median']})\n           {e['title']}\n           {e['url']}")
    if out["tiktok_ranking"]:
        print("  tiktok ranking (SERP, no view counts):")
        for e in out["tiktok_ranking"]:
            print(f"    #{e['rank']} for '{e['query']}': {e['title']}  {e['url']}")
    if out["shapes"]:
        print("  shapes by lift: " + ", ".join(f"{s['shape']} {s['lift']}x" for s in out["shapes"]))
    return 0


def selftest():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "knowledge").mkdir()
        (root / "business" / "content").mkdir(parents=True)
        (root / "knowledge" / "a.md").write_text("# Parking\n\nA private parking charge is an invoice, not a fine.\n\n"
                                                  "The 14 day discount window is not a deadline to pay.\n", encoding="utf-8")
        planted = root / "knowledge" / "b.md"
        planted.write_text("# Notes\n\nline two\n\nline four\n\nline six\n\nThe zorblax tadpole plays the trumpet at dusk, "
                           "every single evening.\n\nunrelated closing block about tea and biscuits and rain\n", encoding="utf-8")
        (root / "business" / "content" / "c.md").write_text("Bedtime fear: the dark hallway, the shape on the chair, "
                                                             "the noise the house makes at night.\n", encoding="utf-8")
        (root / "DECISIONS.md").write_text("- 2026-09-16 decision: build library.py, no embeddings, no keys.\n", encoding="utf-8")
        idx_path = root / "ops" / "library_index.json"
        spec = [("knowledge", "knowledge", "**/*.md"), ("business/content", "business/content", "**/*.md"),
                ("DECISIONS.md", "DECISIONS.md", None)]
        notes = []
        idx = load_index(root, idx_path, note=notes.append, spec=spec)
        if not idx_path.is_file() or not notes:
            fails.append("index not written on first load")
        eng = BM25(idx["passages"])
        top = eng.search("zorblax trumpet", top=3)
        if not top or top[0]["file"] != "knowledge/b.md" or top[0]["line"] != 9:
            fails.append(f"planted line not top-1 at knowledge/b.md:9 -> {top[:1]}")
        top = eng.search("bedtime fear", top=3)
        if not top or top[0]["file"] != "business/content/c.md":
            fails.append(f"bedtime query missed business/content/c.md -> {top[:1]}")
        top = eng.search("embeddings keys", top=3)
        if not top or top[0]["file"] != "DECISIONS.md":
            fails.append(f"DECISIONS.md not indexed -> {top[:1]}")
        notes.clear()
        load_index(root, idx_path, note=notes.append, spec=spec)
        if notes:
            fails.append("index rebuilt although nothing changed")
        time.sleep(0.01)
        planted.write_text(planted.read_text(encoding="utf-8") + "\nNew paragraph about quorbat lanterns.\n", encoding="utf-8")
        os.utime(planted, (time.time() + 2, time.time() + 2))
        notes.clear()
        idx = load_index(root, idx_path, note=notes.append, spec=spec)
        if not notes:
            fails.append("index not rebuilt after a file changed")
        if not BM25(idx["passages"]).search("quorbat lanterns", top=1):
            fails.append("new paragraph not searchable after rebuild")
        planted.unlink()
        notes.clear()
        idx = load_index(root, idx_path, note=notes.append, spec=spec)
        if not notes or any(p[0] == "knowledge/b.md" for p in idx["passages"]):
            fails.append("deleted file still in index")
        if tokens("Deadlines — the 28-day POPLA window") != ["deadline", "28-day", "popla", "window"]:
            fails.append(f"tokenizer drift: {tokens('Deadlines — the 28-day POPLA window')}")
    for f in fails:
        print("FAIL " + f)
    print("selftest " + ("PASS (7 checks)" if not fails else f"FAIL ({len(fails)})"))
    return 1 if fails else 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="BM25 search over our own idea corpus; no keys, no network")
    ap.add_argument("query", nargs="?", help="what you are looking for, in plain words")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="ignore the cached index")
    ap.add_argument("--hooks", metavar="LANE", help="print the top outliers for a hook_miner lane instead of searching")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.hooks:
        return hooks_top(a.hooks, a.top, a.json)
    if not a.query:
        ap.print_usage()
        return 2
    idx = load_index(ROOT, INDEX, force=a.rebuild, note=(lambda s: None) if a.json else print)
    hits = BM25(idx["passages"]).search(a.query, top=a.top)
    if a.json:
        print(json.dumps({"query": a.query, "built": idx["built"], "hits": hits}, ensure_ascii=False, indent=1))
        return 0
    if not hits:
        print(f"[no passage matches '{a.query}' across {len(idx['files'])} files]")
        return 1
    for h in hits:
        print(f"{h['score']:>6}  {h['file']}:{h['line']}\n        {h['snippet']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
