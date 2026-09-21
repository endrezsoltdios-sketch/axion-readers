#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""bot_ranges.py: fetch the IP ranges the big crawler operators publish for their own bots, into one JSON file the
edge traffic classifier bundles (edge/data/bot_ranges.json).

Why (22 Sep 2026): a user-agent string is a claim. "Googlebot" from a Hetzner box is not Google; "GPTBot" from a
residential line is not OpenAI. Every operator below publishes the address blocks its bot really uses, so the
classifier can mark each declared bot VERIFIED (address inside the operator's own list), IMPERSONATED (declared
operator, address outside its list) or UNLISTED (operator publishes no list). Cloudflare's own verified-bot flag
in request.cf costs an Enterprise plan; this is the same fact from the operators' own front doors, free.

Sources (each is the operator's own published file; a dead URL is reported, never guessed around):
  google        googlebot.json, special-crawlers.json, user-triggered-fetchers.json, user-triggered-fetchers-google.json
  bing          bing.com/toolbox/bingbot.json
  openai        gptbot.json, searchbot.json, chatgpt-user.json
  perplexity    perplexitybot.json, perplexity-user.json
  apple         search.developer.apple.com/applebot.json

  python tools/bot_ranges.py            fetch, write the data file, print one row per source
  python tools/bot_ranges.py --check    read the data file, print age and counts, exit 1 when older than 14 days
  python tools/bot_ranges.py --selftest

Needs: nothing but network access. BOT_RANGES_UA sets the user-agent this reader identifies itself with.
Exit 0 when every source answered; 1 when any source failed (the file is still written from the ones that did,
with the failed ones carried over from the previous file so a bot never turns "impersonated" because a list was down).
"""
import argparse, datetime, ipaddress, json, os, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "edge" / "data" / "bot_ranges.json"
UA = os.environ.get("BOT_RANGES_UA", "example-bot/1.0 (+https://example.com)")

SOURCES = {
    "google": [
        "https://developers.google.com/static/search/apis/ipranges/googlebot.json",
        "https://developers.google.com/static/search/apis/ipranges/special-crawlers.json",
        "https://developers.google.com/static/search/apis/ipranges/user-triggered-fetchers.json",
        "https://developers.google.com/static/search/apis/ipranges/user-triggered-fetchers-google.json",
    ],
    "bing": ["https://www.bing.com/toolbox/bingbot.json"],
    "openai": ["https://openai.com/gptbot.json", "https://openai.com/searchbot.json", "https://openai.com/chatgpt-user.json"],
    "perplexity": ["https://www.perplexity.com/perplexitybot.json", "https://www.perplexity.com/perplexity-user.json"],
    "apple": ["https://search.developer.apple.com/applebot.json"],
}

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"user-agent": UA, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def prefixes(doc):
    """Every operator uses Google's shape: {"prefixes": [{"ipv4Prefix": ...} | {"ipv6Prefix": ...}]}."""
    out = []
    for p in doc.get("prefixes", []):
        v = p.get("ipv4Prefix") or p.get("ipv6Prefix")
        if v:
            out.append(v.strip())
    return out

def clean_prefixes(rows):
    """Keep only what the edge parser can also hold (edge/src/traffic_class.js): a real network, in
    canonical form, never /0. One odd row is not a cosmetic problem: a /0 would verify every address on earth as
    that operator's bot, and a row the JavaScript cannot parse is a row that silently shrinks the list. Returns
    (kept, dropped)."""
    kept, dropped = [], []
    for r in rows:
        s = str(r).strip()
        try:
            net = ipaddress.ip_network(s, strict=False)
        except ValueError:
            dropped.append(s or "(empty)")
            continue
        if net.prefixlen == 0:
            dropped.append(s)
            continue
        kept.append(str(net))
    return kept, dropped

def build(previous=None):
    previous = previous or {}
    ops, failed = {}, []
    for op, urls in SOURCES.items():
        cidrs, ok = [], True
        for u in urls:
            try:
                got, dropped = clean_prefixes(prefixes(fetch(u)))
                if dropped:
                    print(f"  {op:11s} DROPPED {len(dropped)} unusable prefix(es): {', '.join(dropped[:5])}")
                if not got:
                    raise ValueError("no usable prefixes in document")
                cidrs.extend(got)
                print(f"  {op:11s} {len(got):5d} prefixes  {u}")
            except Exception as e:
                ok = False
                failed.append(u)
                print(f"  {op:11s} FAILED           {u}  ({str(e)[:80]})")
        if ok:
            ops[op] = sorted(set(cidrs))
        elif op in previous.get("operators", {}):
            ops[op] = previous["operators"][op]
            print(f"  {op:11s} carried over {len(ops[op])} prefixes from the previous file")
    return {
        "fetched": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Address blocks each operator publishes for its own bots; read by edge/src/traffic_class.js. Regenerate with tools/bot_ranges.py, never hand edit.",
        "sources": SOURCES,
        "operators": ops,
    }, failed

OUT_JS = OUT.with_suffix(".js")

def emit_module(doc):
    """The classifier imports a JavaScript module, not the JSON: Node refuses a JSON import without an attribute,
    Wrangler bundles either, and a plain module needs no loader hook in any suite (caught 22 Sep 2026)."""
    body = json.dumps({"fetched": doc["fetched"], "operators": doc["operators"]}, separators=(",", ":"))
    OUT_JS.write_text("/* GENERATED by tools/bot_ranges.py on %s; never hand edit. Address blocks the operators publish for their own bots. */\nexport default %s;\n" % (doc["fetched"], body), encoding="utf-8")

def check(max_age_days=14):
    if not OUT.exists():
        print("MISSING", OUT); return 1
    d = json.loads(OUT.read_text(encoding="utf-8"))
    age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.strptime(d["fetched"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)).days
    bad = 0
    for op, c in d["operators"].items():
        kept, dropped = clean_prefixes(c)
        bad += len(dropped) + (1 if not kept else 0)
        note = "" if not dropped else f"  UNUSABLE: {', '.join(dropped[:5])}"
        print(f"  {op:11s} {len(c):5d} prefixes{note}")
    # An operator missing from the file is not an error here: the classifier reads "n", never "impersonated".
    for op in SOURCES:
        if op not in d["operators"]:
            print(f"  {op:11s}     0 prefixes  (declared bots for this operator will read n or c, never i)")
    print(f"fetched {d['fetched']} ({age} days ago); {'STALE' if age > max_age_days else 'fresh'}")
    if bad:
        print(f"{bad} unusable row(s) in the file; regenerate with: python tools/bot_ranges.py")
    return 1 if (age > max_age_days or bad) else 0

def selftest():
    assert prefixes({"prefixes": [{"ipv4Prefix": "1.2.3.0/24"}, {"ipv6Prefix": "2001:db8::/32"}, {"x": 1}]}) == ["1.2.3.0/24", "2001:db8::/32"]
    assert set(SOURCES) == {"google", "bing", "openai", "perplexity", "apple"}
    assert sum(len(v) for v in SOURCES.values()) == 11
    assert OUT_JS.name == "bot_ranges.js"
    # nothing unusable may reach the edge table
    kept, dropped = clean_prefixes(["66.249.64.0/19", "2001:4860:4801:10::/64", "0.0.0.0/0", "::/0", "1.2.3.4/", "", "nonsense", "1.2.3.4/33"])
    assert kept == ["66.249.64.0/19", "2001:4860:4801:10::/64"], kept
    assert len(dropped) == 6, dropped
    # a host address keeps its meaning, and a sloppy network is written back canonically
    assert clean_prefixes(["8.8.8.8", "66.249.66.5/19"])[0] == ["8.8.8.8/32", "66.249.64.0/19"]
    # the file that ships must itself be clean
    if OUT.exists():
        doc = json.loads(OUT.read_text(encoding="utf-8"))
        for op, c in doc["operators"].items():
            assert c and not clean_prefixes(c)[1], f"{op} carries unusable prefixes"
    print("selftest OK: 8 checks")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.check:
        sys.exit(check())
    previous = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    doc, failed = build(previous)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    emit_module(doc)
    total = sum(len(v) for v in doc["operators"].values())
    print(f"wrote {OUT.relative_to(ROOT)}: {len(doc['operators'])} operators, {total} prefixes; {len(failed)} source(s) failed")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
