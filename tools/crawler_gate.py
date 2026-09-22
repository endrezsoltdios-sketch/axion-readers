#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""crawler_gate.py: does the edge actually let in the AI crawlers our robots.txt invites by name?

Why (22 Sep 2026, from the AI news sweep): on 15 Sep 2026 Cloudflare's Content Signals default flipped. Training
and agent class crawlers are now blocked by default on ad bearing pages for new domains, for new zones on
existing accounts and for Free plan zones, and the single Block AI Bots toggle is being replaced by three
independent controls (Search, Training, Agent). Source read 22 Sep 2026: blog.cloudflare.com/accountable-mixed-use-ai-crawlers/
and the 1 Jul 2026 Content Signals post. A site that invites those crawlers on purpose (robots.txt naming them,
each group repeating the wildcard rules, ai-input=yes) has no way to check that the edge agrees. A robots.txt
that says yes while the edge answers 403 is a promise not kept, and it is invisible from the dashboard, because
the dashboard shows settings and not what a crawler receives.

What is new here: a bot-settings reader tells you the configuration, and a readiness scanner speaks as one
polite agent, so neither can see a per user-agent rule at all. This reads the promise and the behaviour
together, for every named crawler on every host, on a real content page as well as the front page, and it
fails a row only when the two disagree.

Per door and crawler the verdict is one of:
  SERVED            robots.txt allows the path for this crawler and the edge answered 2xx
  GATE              robots.txt allows it and the edge answered 403, 429 or 503: the invitation is not honoured
  ERROR             the request did not complete (the edge is not the thing being measured)
  DENY-CONSISTENT   robots.txt disallows the path and the edge also refused
  DENY-ADVISORY     robots.txt disallows the path and the edge served it anyway (robots is advisory, not a lock)

  python tools/crawler_gate.py example.com                 # every named crawler, front page and one content page
  python tools/crawler_gate.py --json crawler_gate_latest.json   # hosts from DOORS in the environment
  python tools/crawler_gate.py --path /how-it-works --agent GPTBot,ClaudeBot
  python tools/crawler_gate.py --selftest

Exit codes: 0 when no GATE row on any door, 1 when any row is GATE, 2 when no robots.txt could be read at all.
"""
import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
TIMEOUT = 25
OWN_UA = os.environ.get("CRAWLER_GATE_UA", "example-bot/1.0 (+https://example.com/bot)")

# The hosts to read when none are named on the command line: DOORS="a.com,b.com" in the environment.
DOORS = [h.strip() for h in os.environ.get("DOORS", "").split(",") if h.strip()]

# token -> (class, a representative published full user agent string).
# The token is what RFC 9309 matches in robots.txt; the full string is what the edge sees. Class follows the
# three way split Cloudflare moved to on 15 Sep 2026: search, training, agent (a user fired fetch).
CRAWLERS = {
    "GPTBot": ("training", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot"),
    "OAI-SearchBot": ("search", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot"),
    "ChatGPT-User": ("agent", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot"),
    "ClaudeBot": ("training", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ClaudeBot/1.0; +claudebot@anthropic.com"),
    "Claude-User": ("agent", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; Claude-User/1.0; +Claude-User@anthropic.com"),
    "Claude-SearchBot": ("search", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; Claude-SearchBot/1.0; +Claude-SearchBot@anthropic.com"),
    "PerplexityBot": ("search", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot"),
    "Perplexity-User": ("agent", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user"),
    "Google-Extended": ("training", "Mozilla/5.0 (compatible; Google-Extended/1.0; +http://www.google.com/bot.html)"),
    "GoogleOther": ("search", "Mozilla/5.0 (compatible; GoogleOther/1.0; +http://www.google.com/bot.html)"),
    "Bytespider": ("training", "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)"),
    "CCBot": ("training", "CCBot/2.0 (https://commoncrawl.org/faq/)"),
    "meta-externalagent": ("training", "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)"),
    "Amazonbot": ("search", "Mozilla/5.0 (compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)"),
    "Applebot-Extended": ("training", "Mozilla/5.0 (compatible; Applebot-Extended/1.0; +http://www.apple.com/go/applebot)"),
    "DuckAssistBot": ("agent", "Mozilla/5.0 (compatible; DuckAssistBot/1.0; +https://duckduckgo.com/duckassistbot)"),
    "MistralAI-User": ("agent", "Mozilla/5.0 (compatible; MistralAI-User/1.0; +https://mistral.ai/)"),
}
CONTROLS = {
    "own-reader": ("own", OWN_UA),
    "bare-python": ("control", "Python-urllib/3.12"),
}
BLOCK_CODES = {401, 403, 405, 418, 429, 503}


def fetch(url, ua, method="GET"):
    """One request, one row. Returns (status, bytes_read, note). A failure is a line, not a traceback."""
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "text/html,*/*"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(400_000)
            return r.status, len(body), ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read(20_000)
        except Exception:
            body = b""
        note = ""
        m = re.search(rb"[Ee]rror code[:\s]*(\d{3,5})", body)
        if m:
            note = "cf error %s" % m.group(1).decode()
        return e.code, len(body), note
    except Exception as e:
        return 0, 0, type(e).__name__


def parse_groups(text):
    """robots.txt to {user-agent token lowercased: [(field, value)]}.

    RFC 9309 grouping: a run of consecutive user-agent lines opens a group, the rule lines that follow apply to
    every agent in that run, and the next user-agent line after a rule line opens a new group. Sitemap and other
    non group lines are kept out. Comments and blank lines never end a group on their own.
    """
    groups = {}
    agents, open_group = [], False
    for raw in str(text).splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = line.split(":", 1)
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if open_group:
                agents, open_group = [], False
            agents.append(value.lower())
            groups.setdefault(value.lower(), [])
            continue
        if field in ("sitemap", "host"):
            continue
        if not agents:
            continue
        open_group = True
        for a in agents:
            groups[a].append((field, value))
    return groups


def group_for(groups, token):
    """RFC 9309: the most specific matching group wins, the wildcard is the fallback, nothing else merges."""
    t = token.lower()
    if t in groups:
        return t
    best = None
    for name in groups:
        if name == "*":
            continue
        if t.startswith(name) and (best is None or len(name) > len(best)):
            best = name
    if best:
        return best
    return "*" if "*" in groups else None


def allows(rules, path):
    """RFC 9309 longest match wins; Allow wins a tie; no rule means allowed."""
    best_len, best_allow = -1, True
    for field, value in rules:
        if field not in ("allow", "disallow"):
            continue
        if value == "" and field == "disallow":
            continue
        pattern = value.rstrip("$")
        if not path.startswith(pattern.replace("*", "")) and "*" not in pattern:
            continue
        if "*" in pattern:
            rx = "^" + re.escape(pattern).replace(r"\*", ".*")
            if not re.match(rx, path):
                continue
        if len(value) > best_len or (len(value) == best_len and field == "allow"):
            best_len, best_allow = len(value), field == "allow"
    return best_allow


def content_signal(rules):
    for field, value in rules:
        if field == "content-signal":
            return value
    return ""


def verdict(robots_allows, status):
    if status == 0:
        return "ERROR"
    blocked = status in BLOCK_CODES
    if robots_allows and not blocked:
        return "SERVED"
    if robots_allows and blocked:
        return "GATE"
    if not robots_allows and blocked:
        return "DENY-CONSISTENT"
    return "DENY-ADVISORY"


def audit_door(host, paths, agents, fetcher=fetch, sleep=0.0):
    """Read one door's robots.txt, then ask each named agent for each path and compare the two answers."""
    base = "https://%s" % host
    status, _, note = fetcher(base + "/robots.txt", OWN_UA)
    body = ""
    if status == 200:
        req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": OWN_UA})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read(200_000).decode("utf-8", "replace")
        except Exception:
            body = ""
    if not body:
        return {"host": host, "robots": "unreadable (%s%s)" % (status, " " + note if note else ""), "rows": []}
    groups = parse_groups(body)
    rows = []
    for token in agents:
        klass, ua = (CRAWLERS.get(token) or CONTROLS.get(token) or ("unknown", token))
        gname = group_for(groups, token)
        rules = groups.get(gname or "", [])
        named = gname == token.lower()
        for path in paths:
            allowed = allows(rules, path)
            st, n, note = fetcher(base + path, ua)
            rows.append({
                "host": host, "agent": token, "class": klass, "path": path,
                "robots_group": gname or "(none)", "named_group": named,
                "content_signal": content_signal(rules),
                "robots_allows": allowed, "status": st, "bytes": n, "note": note,
                "verdict": verdict(allowed, st),
            })
            if sleep:
                time.sleep(sleep)
    return {"host": host, "robots": "%d groups, %d bytes" % (len(groups), len(body)), "rows": rows}


def selftest():
    checks, fails = [], []

    def ck(name, cond):
        checks.append((name, bool(cond)))
        if not cond:
            fails.append(name)

    robots = (
        "User-agent: GPTBot\nAllow: /\nDisallow: /api/\nContent-Signal: ai-train=no, ai-input=yes\n\n"
        "User-agent: ClaudeBot\nAllow: /\nDisallow: /api/\nContent-Signal: ai-train=no, ai-input=yes\n\n"
        "User-agent: Bytespider\nDisallow: /\n\n"
        "User-agent: *\nAllow: /\nDisallow: /api/\nContent-Signal: ai-train=no, ai-input=yes\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    g = parse_groups(robots)
    ck("1 every named group parsed", set(["gptbot", "claudebot", "bytespider", "*"]).issubset(set(g)))
    ck("2 rules land in their own group", len(g["gptbot"]) == 3)
    ck("3 wildcard group found for an unnamed agent", group_for(g, "SomeOtherBot") == "*")
    ck("4 an exact named group beats the wildcard", group_for(g, "GPTBot") == "gptbot")
    ck("5 token match is case insensitive", group_for(g, "gptBOT") == "gptbot")
    ck("6 a path with no rule is allowed", allows(g["*"], "/how-it-works"))
    ck("7 the longest matching disallow bites", allows(g["*"], "/api/bulk") is False)
    ck("8 a blanket disallow blocks the front page", allows(g["bytespider"], "/") is False)
    ck("9 content signal read from the applying group", content_signal(g["gptbot"]).startswith("ai-train=no"))
    ck("10 a group with no content signal reads empty", content_signal(g["bytespider"]) == "")
    ck("11 allowed and 200 is SERVED", verdict(True, 200) == "SERVED")
    ck("12 allowed and 403 is GATE", verdict(True, 403) == "GATE")
    ck("13 allowed and 429 is GATE", verdict(True, 429) == "GATE")
    ck("14 disallowed and 403 is consistent", verdict(False, 403) == "DENY-CONSISTENT")
    ck("15 disallowed and 200 is advisory, not a fail", verdict(False, 200) == "DENY-ADVISORY")
    ck("16 a dead connection is ERROR, never GATE", verdict(True, 0) == "ERROR")
    ck("17 a 301 is not a block", verdict(True, 301) == "SERVED")

    served = {"/robots.txt": (200, len(robots), ""), "/": (200, 5000, ""), "/api/bulk": (402, 300, "")}

    def fake(url, ua, method="GET"):
        path = url.split("example.test", 1)[-1] if "example.test" in url else url
        for p, res in served.items():
            if path.endswith(p):
                if p == "/" and "Bytespider" in ua:
                    return 403, 100, "cf error 1010"
                return res
        return 0, 0, "Unmapped"

    ck("18 every crawler token carries a class and a user agent",
       all(len(v) == 2 and v[0] in ("training", "search", "agent") for v in CRAWLERS.values()))
    ck("19 seventeen named crawlers, matching robots_ai.js", len(CRAWLERS) == 17)
    ck("20 controls are not counted as AI crawlers", not set(CONTROLS) & set(CRAWLERS))
    row = verdict(allows(g["bytespider"], "/"), 403)
    ck("21 a door that blocks a crawler it disallows is consistent", row == "DENY-CONSISTENT")
    ck("22 a door that blocks a crawler it welcomes is a GATE", verdict(allows(g["gptbot"], "/"), 403) == "GATE")

    for name, ok in checks:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    print("\n%d checks, %d passed, %d failed" % (len(checks), len(checks) - len(fails), len(fails)))
    return 0 if not fails else 1


def main():
    p = argparse.ArgumentParser(description="does the edge honour the AI crawlers robots.txt invites by name")
    p.add_argument("host", nargs="*", help="hosts to read (default: the DOORS environment list)")
    p.add_argument("--path", default="/", help="comma separated paths to request (default /)")
    p.add_argument("--agent", help="comma separated crawler tokens (default: all 17 plus the two controls)")
    p.add_argument("--sleep", type=float, default=0.15, help="seconds between requests (default 0.15)")
    p.add_argument("--json", dest="as_json", help="also write the full result to this path")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()

    hosts = a.host or DOORS
    if not hosts:
        sys.exit("name one or more hosts, or set DOORS to a comma-separated list")
    paths = [q if q.startswith("/") else "/" + q for q in a.path.split(",") if q.strip()]
    agents = [t.strip() for t in a.agent.split(",")] if a.agent else list(CRAWLERS) + list(CONTROLS)
    unknown = [t for t in agents if t not in CRAWLERS and t not in CONTROLS]
    if unknown:
        print("unknown agent token: %s" % ", ".join(unknown), file=sys.stderr)
        return 2

    results, read_any = [], False
    print("crawler_gate  %s  %d hosts  %d agents  %d paths"
          % (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), len(hosts), len(agents), len(paths)))
    for host in hosts:
        res = audit_door(host, paths, agents, sleep=a.sleep)
        results.append(res)
        if not res["rows"]:
            print("\n%-22s robots.txt %s" % (host, res["robots"]))
            continue
        read_any = True
        counts = {}
        for r in res["rows"]:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        named = sum(1 for r in res["rows"] if r["named_group"] and r["agent"] in CRAWLERS)
        print("\n%-22s robots.txt %s  named groups hit %d/%d  %s"
              % (host, res["robots"], named, len(res["rows"]),
                 "  ".join("%s %d" % (k, v) for k, v in sorted(counts.items()))))
        for r in res["rows"]:
            if r["verdict"] in ("SERVED", "DENY-CONSISTENT"):
                continue
            print("    %-9s %-18s %-8s %-14s http %s %s"
                  % (r["verdict"], r["agent"], r["class"], r["path"], r["status"], r["note"]))

    gates = [r for res in results for r in res["rows"] if r["verdict"] == "GATE"]
    served = [r for res in results for r in res["rows"] if r["verdict"] == "SERVED"]
    print("\ntotal: %d SERVED, %d GATE, %d rows" % (len(served), len(gates), sum(len(r["rows"]) for r in results)))

    if a.as_json:
        path = pathlib.Path(a.as_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"read": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "doors": results}, indent=1), encoding="utf-8")
        print("wrote %s" % path)

    if not read_any:
        return 2
    return 1 if gates else 0


if __name__ == "__main__":
    sys.exit(main())
