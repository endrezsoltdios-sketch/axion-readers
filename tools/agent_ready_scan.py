#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""agent_ready_scan.py: your own agent readiness scanner, one command against any host, no third party in the loop.

Why (22 Sep 2026): Cloudflare's Agent Readiness diagnostic and isitagentready.com each grade a site, and both of
those graders are someone else's. They can change a rule, rate limit you, go away, or simply be wrong about a
host, and you would not know. This reads the same public surfaces itself, from the outside, over plain HTTPS and
DNS-over-HTTPS, and prints what it saw with the URL and the HTTP status next to every verdict. It runs against
any host, yours or a stranger's, so a comparison row is measured, not quoted.

What it checks, one function per item, each returning pass, fail or neutral plus a one line evidence string:
  Discoverability: robots.txt valid; sitemap present and parseable (the Sitemap: line in robots.txt is followed
    too); Link headers on the home page carrying api-catalog, service-desc and describedby; DNS-AID SVCB records
    at _a2a._agents.<host> and _mcp._agents.<host>, read over DNS-over-HTTPS, plus whether the answer was DNSSEC
    validated (AD=true), reported as its own line and not counted towards the level.
  Content: markdown negotiation (GET / with Accept: text/markdown, or <path>.md); llms.txt.
  Bot access: robots.txt naming at least one AI user agent explicitly; a Content-Signal line in robots.txt.
  Discovery: /.well-known/api-catalog; RFC 8414 /.well-known/oauth-authorization-server (issuer and
    token_endpoint present); RFC 9728 /.well-known/oauth-protected-resource; /auth.md with the H1 "# auth.md";
    /.well-known/agent-card.json (A2A: name, url or supportedInterfaces, skills); the http-message-signatures
    directory as a JWKS with keys; /.well-known/mcp/server-card.json; a skills index; /.well-known/ard.
  Commerce: x402 (a 402 carrying an accepts array on /api/v1 or on a path the openapi marks priced); MPP
    (openapi operations carrying x-payment-info); UCP, ACP and AP2, reported "not claimed" and never failed.

Scoring, ours and stated here so nobody mistakes it for the public checkers' exact rule: each item scores 1 for
pass, 0 for fail, and neutral items are not counted. The level is the highest rung whose group and every group
below it passed in full: 1 Discoverable (robots, sitemap, link headers, DNS-AID), 2 Readable (plus markdown
negotiation and llms.txt), 3 Bot-aware (plus the AI user agent line and Content-Signal), 4 Agent-integrated
(plus api-catalog, both OAuth documents and auth.md), 5 Agent-native (plus the agent card and the
http-message-signatures directory). Everything else is measured and printed but does not gate a level.

  python tools/agent_ready_scan.py example.com
  python tools/agent_ready_scan.py example.com example.org --json agent_ready_scan_latest.json
  python tools/agent_ready_scan.py --selftest

Exit codes: 0 when every requested host reached level 5; 1 when any host did not; 2 when the very first fetch of
the run failed at the network (nothing was measured, so no verdict is honest).
"""
import argparse
import json
import os
import pathlib
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = os.environ.get("READY_SCAN_UA", "example-bot/1.0 (+https://example.com)")
TIMEOUT = 20
DOH = "https://cloudflare-dns.com/dns-query"
AI_AGENTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "CCBot", "Bytespider"]
PRICED_CANDIDATES = ["/api/v1", "/api/bulk", "/api/v1/bulk"]

PASS, FAIL, NEUTRAL = "pass", "fail", "neutral"

GROUPS = {
    "discoverability": ["robots_txt", "sitemap", "link_headers", "dns_aid"],
    "content": ["markdown_negotiation", "llms_txt"],
    "bot_access": ["ai_user_agent_named", "content_signal"],
    "integration": ["api_catalog", "oauth_authorization_server", "oauth_protected_resource", "auth_md"],
    "native": ["agent_card", "http_message_signatures_directory"],
}
GROUP_ORDER = ["discoverability", "content", "bot_access", "integration", "native"]
LEVEL_NAMES = {
    0: "0 Not discoverable",
    1: "1 Discoverable",
    2: "2 Readable",
    3: "3 Bot-aware",
    4: "4 Agent-integrated",
    5: "5 Agent-native",
}


# ---------------------------------------------------------------- transport

class Resp:
    def __init__(self, url, status=0, headers=None, body="", error=""):
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.error = error

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return None

    def ctype(self):
        return self.headers.get("content-type", "")


class Fetcher:
    """One host, one cache. Every check asks through here so a page is fetched once."""

    def __init__(self, host, timeout=TIMEOUT):
        self.host = host
        self.timeout = timeout
        self.cache = {}
        self.network_error = ""
        self.first_done = False

    def get(self, path_or_url, accept=None):
        key = (path_or_url, accept)
        if key in self.cache:
            return self.cache[key]
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = "https://" + self.host + path_or_url
        headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
        headers["Accept"] = accept or "*/*"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read(600000)
                hdrs = {k.lower(): v for k, v in r.headers.items()}
                link_all = r.headers.get_all("Link") or []
                if link_all:
                    hdrs["link"] = ", ".join(link_all)
                resp = Resp(url, r.status, hdrs, raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read(600000)
            except Exception:
                pass
            hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
            resp = Resp(url, e.code, hdrs, raw.decode("utf-8", "replace"))
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            resp = Resp(url, 0, {}, "", str(getattr(e, "reason", e)))
            if not self.first_done:
                self.network_error = "%s: %s" % (url, resp.error)
        self.first_done = True
        self.cache[key] = resp
        return resp


# ---------------------------------------------------------------- parsers

def parse_robots(text):
    """Return what a robots.txt says: user agents named, sitemaps listed, content signal lines, validity."""
    out = {"user_agents": [], "sitemaps": [], "content_signals": [], "valid": False, "bad_lines": []}
    seen_ua = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            out["bad_lines"].append(line[:60])
            continue
        field, value = line.split(":", 1)
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            seen_ua = True
            out["user_agents"].append(value)
        elif field == "sitemap":
            out["sitemaps"].append(value)
        elif field == "content-signal":
            out["content_signals"].append(value)
    out["valid"] = seen_ua and not out["bad_lines"]
    return out


def parse_link_header(value):
    """Return {rel: url} from a Link header, one entry per rel token."""
    rels = {}
    if not value:
        return rels
    for part in re.split(r",(?=\s*<)", value):
        m = re.match(r"\s*<([^>]*)>\s*(.*)", part)
        if not m:
            continue
        url, params = m.group(1), m.group(2)
        rm = re.search(r'rel\s*=\s*"?([^";]+)"?', params)
        if not rm:
            continue
        for rel in rm.group(1).split():
            rels.setdefault(rel.strip().lower(), url)
    return rels


def check_rfc8414(doc):
    """RFC 8414 authorization server metadata: issuer and token_endpoint must be present."""
    if not isinstance(doc, dict):
        return False, "not a JSON object"
    missing = [k for k in ("issuer", "token_endpoint") if not doc.get(k)]
    if missing:
        return False, "missing " + ",".join(missing)
    return True, "issuer=%s token_endpoint present" % str(doc.get("issuer"))[:60]


def parse_doh(doc):
    """Read a Cloudflare DoH JSON answer: did it answer, was it DNSSEC validated, how many records."""
    if not isinstance(doc, dict):
        return {"answered": False, "ad": False, "count": 0, "status": -1}
    answers = doc.get("Answer") or []
    return {
        "answered": len(answers) > 0,
        "ad": bool(doc.get("AD")),
        "count": len(answers),
        "status": doc.get("Status", -1),
    }


def parse_sitemap(text):
    """Count <loc> entries in a sitemap or sitemap index; None when the XML does not look like one."""
    if "<urlset" not in text and "<sitemapindex" not in text:
        return None
    return len(re.findall(r"<loc>", text))


def level_for(statuses):
    """The level rule stated in the docstring, applied to a dict of item -> status."""
    level = 0
    for i, group in enumerate(GROUP_ORDER, start=1):
        if all(statuses.get(k) == PASS for k in GROUPS[group]):
            level = i
        else:
            break
    return level


# ---------------------------------------------------------------- checks

def _ev(resp, note=""):
    if resp.status == 0:
        return "%s network error: %s" % (resp.url, resp.error[:70])
    return "%s HTTP %d%s" % (resp.url, resp.status, (" " + note) if note else "")


def check_robots_txt(f):
    r = f.get("/robots.txt")
    if not r.ok:
        return FAIL, _ev(r, "no robots.txt")
    p = parse_robots(r.body)
    if not p["valid"]:
        return FAIL, _ev(r, "unparseable: %d bad lines, %d user-agent groups" % (len(p["bad_lines"]), len(p["user_agents"])))
    return PASS, _ev(r, "%d user-agent groups, %d sitemap lines" % (len(p["user_agents"]), len(p["sitemaps"])))


def check_sitemap(f):
    robots = f.get("/robots.txt")
    listed = parse_robots(robots.body)["sitemaps"] if robots.ok else []
    tried = []
    for url in listed[:3] + ["/sitemap.xml", "/sitemap_index.xml"]:
        r = f.get(url)
        tried.append(r)
        if r.ok:
            n = parse_sitemap(r.body)
            if n is not None:
                return PASS, _ev(r, "%d <loc> entries" % n)
    last = tried[-1] if tried else Resp("https://%s/sitemap.xml" % f.host)
    return FAIL, _ev(last, "no parseable sitemap (%d candidates tried)" % len(tried))


def check_link_headers(f):
    r = f.get("/", accept="text/html")
    rels = parse_link_header(r.headers.get("link", ""))
    want = ["api-catalog", "service-desc", "describedby"]
    have = [w for w in want if w in rels]
    if len(have) == len(want):
        return PASS, _ev(r, "Link rels " + ",".join(have))
    return FAIL, _ev(r, "Link rels present %s, missing %s" % (",".join(have) or "none", ",".join(w for w in want if w not in have)))


def _doh(f, name):
    q = "%s?name=%s&type=SVCB" % (DOH, urllib.parse.quote(name))
    r = f.get(q, accept="application/dns-json")
    return r, parse_doh(r.json())


def check_dns_aid(f):
    names = ["_a2a._agents.%s" % f.host, "_mcp._agents.%s" % f.host]
    results = []
    for n in names:
        r, p = _doh(f, n)
        results.append((n, r, p))
    answered = [n for n, _, p in results if p["answered"]]
    ev = "; ".join("%s %s" % (n.split(".")[0], "SVCB x%d" % p["count"] if p["answered"] else "no record") for n, _, p in results)
    status = PASS if len(answered) == len(names) else FAIL
    return status, "%s (DoH %s) %s" % (DOH, "HTTP %d" % results[0][1].status, ev)


def check_dns_aid_dnssec(f):
    names = ["_a2a._agents.%s" % f.host, "_mcp._agents.%s" % f.host]
    ads, answered_any = [], False
    for n in names:
        _, p = _doh(f, n)
        ads.append(p["ad"])
        answered_any = answered_any or p["answered"]
    if not answered_any:
        return NEUTRAL, "%s no DNS-AID records to validate" % DOH
    if all(ads):
        return PASS, "%s AD=true on both records (DNSSEC validated)" % DOH
    return FAIL, "%s AD=false on %d of %d records (DNSSEC not validated)" % (DOH, ads.count(False), len(ads))


def check_markdown_negotiation(f):
    r = f.get("/", accept="text/markdown")
    if r.ok and "text/markdown" in r.ctype():
        return PASS, _ev(r, "Accept: text/markdown answered %s" % r.ctype().split(";")[0])
    alt = f.get("/index.md")
    if alt.ok and ("markdown" in alt.ctype() or alt.body.lstrip().startswith("#")):
        return PASS, _ev(alt, "path twin answered %s" % (alt.ctype().split(";")[0] or "no content-type"))
    return FAIL, _ev(r, "Accept: text/markdown answered %s; /index.md HTTP %d" % (r.ctype().split(";")[0] or "nothing", alt.status))


def check_llms_txt(f):
    r = f.get("/llms.txt")
    if r.ok and r.body.strip():
        return PASS, _ev(r, "%d bytes" % len(r.body))
    return FAIL, _ev(r, "absent")


def check_ai_user_agent_named(f):
    r = f.get("/robots.txt")
    if not r.ok:
        return FAIL, _ev(r, "no robots.txt")
    named = [a for a in AI_AGENTS if a.lower() in [u.lower() for u in parse_robots(r.body)["user_agents"]]]
    if named:
        return PASS, _ev(r, "names " + ",".join(named))
    return FAIL, _ev(r, "no AI user-agent named explicitly")


def check_content_signal(f):
    r = f.get("/robots.txt")
    if not r.ok:
        return FAIL, _ev(r, "no robots.txt")
    sig = parse_robots(r.body)["content_signals"]
    if sig:
        return PASS, _ev(r, "Content-Signal: %s" % sig[0][:60])
    return FAIL, _ev(r, "no Content-Signal line")


def _json_check(f, path, required, label):
    r = f.get(path, accept="application/json")
    if not r.ok:
        return FAIL, _ev(r, "absent"), None
    doc = r.json()
    if doc is None:
        return FAIL, _ev(r, "not JSON (%s)" % (r.ctype().split(";")[0] or "no content-type")), None
    missing = [k for k in required if not (isinstance(doc, dict) and doc.get(k))]
    if missing:
        return FAIL, _ev(r, "%s missing %s" % (label, ",".join(missing))), doc
    return PASS, _ev(r, label), doc


def check_api_catalog(f):
    r = f.get("/.well-known/api-catalog", accept="application/linkset+json, application/json")
    if not r.ok:
        return FAIL, _ev(r, "absent")
    doc = r.json()
    if doc is None:
        return FAIL, _ev(r, "not JSON")
    n = len(doc.get("linkset", [])) if isinstance(doc, dict) else 0
    return PASS, _ev(r, "linkset %d entries" % n)


def check_oauth_authorization_server(f):
    r = f.get("/.well-known/oauth-authorization-server", accept="application/json")
    if not r.ok:
        return FAIL, _ev(r, "absent")
    ok, note = check_rfc8414(r.json())
    return (PASS if ok else FAIL), _ev(r, "RFC 8414 " + note)


def check_oauth_protected_resource(f):
    r = f.get("/.well-known/oauth-protected-resource", accept="application/json")
    if not r.ok:
        return FAIL, _ev(r, "absent")
    doc = r.json()
    if not isinstance(doc, dict) or not doc.get("resource"):
        return FAIL, _ev(r, "RFC 9728 missing resource")
    return PASS, _ev(r, "RFC 9728 resource present, %d authorization servers" % len(doc.get("authorization_servers") or []))


def check_auth_md(f):
    r = f.get("/auth.md", accept="text/markdown")
    if not r.ok:
        return FAIL, _ev(r, "absent")
    first = next((l.strip() for l in r.body.splitlines() if l.strip()), "")
    if first.lower() != "# auth.md":
        return FAIL, _ev(r, 'H1 is "%s", not "# auth.md"' % first[:40])
    return PASS, _ev(r, 'H1 "# auth.md", %d bytes' % len(r.body))


def check_agent_card(f):
    r = f.get("/.well-known/agent-card.json", accept="application/json")
    if not r.ok:
        return FAIL, _ev(r, "absent")
    doc = r.json()
    if not isinstance(doc, dict):
        return FAIL, _ev(r, "not JSON")
    missing = []
    if not doc.get("name"):
        missing.append("name")
    if not (doc.get("url") or doc.get("supportedInterfaces")):
        missing.append("url|supportedInterfaces")
    if not doc.get("skills"):
        missing.append("skills")
    if missing:
        return FAIL, _ev(r, "A2A card missing " + ",".join(missing))
    return PASS, _ev(r, "A2A card %s, %d skills" % (str(doc.get("name"))[:30], len(doc.get("skills") or [])))


def check_http_message_signatures_directory(f):
    r = f.get("/.well-known/http-message-signatures-directory", accept="application/http-message-signatures-directory+json, application/json")
    if not r.ok:
        return FAIL, _ev(r, "absent")
    doc = r.json()
    keys = doc.get("keys") if isinstance(doc, dict) else None
    if not keys:
        return FAIL, _ev(r, "no keys array")
    return PASS, _ev(r, "JWKS %d keys" % len(keys))


def check_mcp_server_card(f):
    r = f.get("/.well-known/mcp/server-card.json", accept="application/json")
    if not r.ok:
        return FAIL, _ev(r, "absent")
    doc = r.json()
    if not isinstance(doc, dict):
        return FAIL, _ev(r, "not JSON")
    return PASS, _ev(r, "server card %s" % str(doc.get("name") or "unnamed")[:30])


def check_skills_index(f):
    for path in ["/.well-known/skills/index.json", "/.well-known/agent-skills",
                 "/.well-known/agent-skills/index.json"]:
        r = f.get(path, accept="application/json")
        if r.ok and r.json() is not None:
            doc = r.json()
            n = len(doc) if isinstance(doc, list) else len(doc.get("skills") or []) if isinstance(doc, dict) else 0
            return PASS, _ev(r, "%d skills listed" % n)
    return FAIL, _ev(r, "no skills index at any of the three known paths")


def check_ard(f):
    r = f.get("/.well-known/ard", accept="application/json")
    if r.ok and r.json() is not None:
        return PASS, _ev(r, "ARD document present")
    cat = f.get("/.well-known/api-catalog", accept="application/json")
    if cat.ok:
        return NEUTRAL, _ev(r, "absent, api-catalog carries the entries instead")
    return NEUTRAL, _ev(r, "not claimed")


def _openapi(f):
    for path in ["/openapi.json", "/.well-known/openapi.json", "/api/openapi.json"]:
        r = f.get(path, accept="application/json")
        if r.ok and isinstance(r.json(), dict):
            return r, r.json()
    return r, None


def _priced_paths(spec):
    out = []
    if not isinstance(spec, dict):
        return out
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if isinstance(op, dict) and (op.get("x-payment-info") or op.get("x-402")):
                out.append(path)
                break
    return out


def check_x402(f):
    r, spec = _openapi(f)
    candidates = list(dict.fromkeys(_priced_paths(spec) + PRICED_CANDIDATES))
    last = None
    for path in candidates[:6]:
        probe = f.get(path, accept="application/json")
        last = probe
        if probe.status == 402:
            doc = probe.json()
            accepts = doc.get("accepts") if isinstance(doc, dict) else None
            if accepts:
                return PASS, _ev(probe, "402 with accepts[%d]" % len(accepts))
            return FAIL, _ev(probe, "402 without an accepts array")
    if last is None:
        return NEUTRAL, "no priced path to probe on %s" % f.host
    return FAIL, _ev(last, "no 402 on %d candidate paths" % len(candidates[:6]))


def check_mpp(f):
    r, spec = _openapi(f)
    if spec is None:
        return FAIL, _ev(r, "no openapi document found")
    priced = _priced_paths(spec)
    if not priced:
        return FAIL, _ev(r, "openapi has no operation carrying x-payment-info")
    return PASS, _ev(r, "x-payment-info on %d operations (%s)" % (len(priced), ",".join(priced[:3])))


def _not_claimed(name, paths):
    def fn(f):
        last = None
        for p in paths:
            r = f.get(p, accept="application/json")
            last = r
            if r.ok and r.json() is not None:
                return PASS, _ev(r, "%s document present" % name)
        return NEUTRAL, _ev(last, "%s not claimed" % name)
    return fn


CHECKS = [
    ("discoverability", "robots_txt", check_robots_txt),
    ("discoverability", "sitemap", check_sitemap),
    ("discoverability", "link_headers", check_link_headers),
    ("discoverability", "dns_aid", check_dns_aid),
    ("discoverability", "dns_aid_dnssec", check_dns_aid_dnssec),
    ("content", "markdown_negotiation", check_markdown_negotiation),
    ("content", "llms_txt", check_llms_txt),
    ("bot_access", "ai_user_agent_named", check_ai_user_agent_named),
    ("bot_access", "content_signal", check_content_signal),
    ("discovery", "api_catalog", check_api_catalog),
    ("discovery", "oauth_authorization_server", check_oauth_authorization_server),
    ("discovery", "oauth_protected_resource", check_oauth_protected_resource),
    ("discovery", "auth_md", check_auth_md),
    ("discovery", "agent_card", check_agent_card),
    ("discovery", "http_message_signatures_directory", check_http_message_signatures_directory),
    ("discovery", "mcp_server_card", check_mcp_server_card),
    ("discovery", "skills_index", check_skills_index),
    ("discovery", "ard", check_ard),
    ("commerce", "x402", check_x402),
    ("commerce", "mpp", check_mpp),
    ("commerce", "ucp", _not_claimed("UCP", ["/.well-known/ucp.json", "/.well-known/ucp"])),
    ("commerce", "acp", _not_claimed("ACP", ["/.well-known/acp.json", "/.well-known/agentic-commerce"])),
    ("commerce", "ap2", _not_claimed("AP2", ["/.well-known/ap2.json", "/.well-known/ap2"])),
]


def scan_host(host):
    f = Fetcher(host)
    items = []
    for group, name, fn in CHECKS:
        try:
            status, evidence = fn(f)
        except Exception as e:
            status, evidence = FAIL, "check raised %s: %s" % (type(e).__name__, str(e)[:80])
        items.append({"group": group, "item": name, "status": status, "evidence": evidence})
    statuses = {i["item"]: i["status"] for i in items}
    level = level_for(statuses)
    scored = [i for i in items if i["status"] in (PASS, FAIL)]
    return {
        "host": host,
        "level": level,
        "level_name": LEVEL_NAMES[level],
        "score": sum(1 for i in scored if i["status"] == PASS),
        "scored_items": len(scored),
        "neutral_items": len(items) - len(scored),
        "network_error": f.network_error,
        "items": items,
    }


# ---------------------------------------------------------------- output

def print_host(result):
    print("")
    print("== %s ==" % result["host"])
    print("%-36s %-8s %s" % ("item", "status", "evidence"))
    print("-" * 110)
    for i in result["items"]:
        print("%-36s %-8s %s" % (i["item"], i["status"], i["evidence"][:150]))
    failed = [i["item"] for i in result["items"] if i["status"] == FAIL]
    print("SUMMARY %s: level %s, score %d/%d, %d neutral%s" % (
        result["host"], result["level_name"], result["score"], result["scored_items"],
        result["neutral_items"], (", failed: " + ",".join(failed)) if failed else ""))


# ---------------------------------------------------------------- selftest

CANNED_DOH = {
    "Status": 0, "AD": True,
    "Question": [{"name": "_a2a._agents.example.com", "type": 64}],
    "Answer": [{"name": "_a2a._agents.example.com", "type": 64, "TTL": 300,
                "data": "1 . alpn=h2 port=443"}],
}
CANNED_DOH_EMPTY = {"Status": 3, "AD": False, "Question": [{"name": "_mcp._agents.example.com", "type": 64}]}

ROBOTS_GOOD = """# robots
User-agent: *
Allow: /
User-agent: GPTBot
Allow: /
Content-Signal: search=yes, ai-train=no
Sitemap: https://example.com/sitemap.xml
"""
ROBOTS_BAD = """User-agent: *
this line has no colon
"""


def selftest():
    checks = 0

    def eq(got, want, label):
        nonlocal checks
        checks += 1
        if got != want:
            raise AssertionError("%s: got %r, want %r" % (label, got, want))

    p = parse_robots(ROBOTS_GOOD)
    eq(p["valid"], True, "robots valid")
    eq(p["user_agents"], ["*", "GPTBot"], "robots user agents")
    eq(p["sitemaps"], ["https://example.com/sitemap.xml"], "robots sitemaps")
    eq(len(p["content_signals"]), 1, "robots content signal")
    b = parse_robots(ROBOTS_BAD)
    eq(b["valid"], False, "robots invalid on a line without a colon")
    eq(len(b["bad_lines"]), 1, "robots bad line counted")
    eq(parse_robots("")["valid"], False, "empty robots is not valid")

    ok, note = check_rfc8414({"issuer": "https://x.test", "token_endpoint": "https://x.test/t"})
    eq(ok, True, "rfc8414 complete")
    eq("issuer=" in note, True, "rfc8414 evidence names the issuer")
    eq(check_rfc8414({"issuer": "https://x.test"})[0], False, "rfc8414 missing token_endpoint")
    eq(check_rfc8414({"token_endpoint": "https://x.test/t"})[0], False, "rfc8414 missing issuer")
    eq(check_rfc8414("nope")[0], False, "rfc8414 on a non object")

    d = parse_doh(CANNED_DOH)
    eq(d["answered"], True, "doh answered")
    eq(d["ad"], True, "doh AD true")
    eq(d["count"], 1, "doh record count")
    e = parse_doh(CANNED_DOH_EMPTY)
    eq(e["answered"], False, "doh empty answer")
    eq(e["ad"], False, "doh AD false")
    eq(parse_doh(None)["answered"], False, "doh on garbage")

    rels = parse_link_header('</.well-known/api-catalog>; rel="api-catalog", </openapi.json>; rel="service-desc describedby"')
    eq(sorted(rels), ["api-catalog", "describedby", "service-desc"], "link header rels")
    eq(parse_link_header(""), {}, "empty link header")

    eq(parse_sitemap("<urlset><url><loc>a</loc></url><url><loc>b</loc></url></urlset>"), 2, "sitemap loc count")
    eq(parse_sitemap("<html></html>"), None, "sitemap rejects html")

    full = {k: PASS for g in GROUPS.values() for k in g}
    eq(level_for(full), 5, "level 5 when every gating item passes")
    no_card = dict(full, agent_card=FAIL)
    eq(level_for(no_card), 4, "level 4 without the agent card")
    no_auth = dict(full, auth_md=FAIL)
    eq(level_for(no_auth), 3, "level 3 without auth.md")
    no_signal = dict(full, content_signal=FAIL)
    eq(level_for(no_signal), 2, "level 2 without Content-Signal")
    no_llms = dict(full, llms_txt=FAIL)
    eq(level_for(no_llms), 1, "level 1 without llms.txt")
    no_dns = dict(full, dns_aid=FAIL)
    eq(level_for(no_dns), 0, "level 0 without DNS-AID")
    eq(level_for({}), 0, "level 0 on an empty scan")
    eq(level_for(dict(full, dns_aid_dnssec=FAIL)), 5, "DNSSEC does not gate the level")

    spec = {"paths": {"/api/bulk": {"post": {"x-payment-info": {"price": "USD 25"}}}, "/free": {"get": {}}}}
    eq(_priced_paths(spec), ["/api/bulk"], "openapi priced paths")
    eq(_priced_paths({}), [], "openapi with no paths")

    print("selftest OK: %d checks" % checks)
    return 0


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description="agent readiness scanner: one command, any host, no third party")
    ap.add_argument("hosts", nargs="*", help="hostnames, e.g. example.com")
    ap.add_argument("--json", dest="json_path", help="write the full result to this path")
    ap.add_argument("--selftest", action="store_true", help="offline parser checks, no network")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.hosts:
        ap.print_help()
        return 1

    results = []
    for n, host in enumerate(args.hosts):
        host = host.replace("https://", "").replace("http://", "").strip("/")
        r = scan_host(host)
        if n == 0 and r["network_error"] and r["score"] == 0:
            print("NETWORK FAILURE on the first fetch: %s" % r["network_error"])
            return 2
        results.append(r)
        print_host(r)

    print("")
    print("%-24s %-22s %s" % ("host", "level", "score"))
    for r in results:
        print("%-24s %-22s %d/%d" % (r["host"], r["level_name"], r["score"], r["scored_items"]))

    if args.json_path:
        out = pathlib.Path(args.json_path)
        if not out.is_absolute():
            out = pathlib.Path(__file__).resolve().parents[1] / out
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tool": "agent_ready_scan.py",
            "user_agent": UA,
            "level_rule": {g: GROUPS[g] for g in GROUP_ORDER},
            "hosts": results,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("wrote %s" % out)

    return 0 if all(r["level"] == 5 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
