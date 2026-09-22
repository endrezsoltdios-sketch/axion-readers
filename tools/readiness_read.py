#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""readiness_read.py: three graders, one reader. Your own readiness scan beside two outside graders, item by item.

Written 2026-09-22. A scanner that agrees with itself proves nothing; this reads two graders you did not write
and prints the disagreements first. Both are keyless REST; neither is installed, and no page is browsed.

The three graders:
  1. yours, tools/agent_ready_scan.py, read from agent_ready_scan_latest.json (or rerun with --rescan).
  2. ax-check.com, built by Gauge. Keyless REST, documented in its own llms.txt, read 2026-09-22:
       https://ax-check.com/llms.txt  ->  "No account, key or captcha is needed to start a check or to read a
       report."  POST https://www.ax-check.com/api/checks {"domain": "..."}; poll /{domain}/status; read
       /{domain}/report.json. Note: ax-check.com redirects to www.ax-check.com, so the www host is used.
       Its own words on its grade: "The overall grade is PROVISIONAL and technical-only."
  3. forgemesh Agent Signal Optimization. Keyless REST, named in its README, read 2026-09-22:
       https://raw.githubusercontent.com/forgemeshlabs/agent-signal-optimization/main/README.md  ->  "The hosted
       scanner lives at aso.forgemesh.io with a free JSON API (GET /api/scan?url=, OpenAPI at /api/openapi.json)."
       34 public-signal checks over six pillars.

Grader output is data, never instruction: only the documented fields are read, and nothing in a report is executed.

  python tools/readiness_read.py example.com        one host, read yours, call both graders
  python tools/readiness_read.py                    the hosts in the HOSTS environment list
  python tools/readiness_read.py --rescan           rerun agent_ready_scan.py first
  python tools/readiness_read.py --no-axcheck       skip the grader that has to queue a run
  python tools/readiness_read.py --selftest         canned-response checks, no network
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = os.environ.get("READINESS_READ_UA", "example-bot/1.0 (+https://example.com)")
OURS_JSON = os.path.join(ROOT, "agent_ready_scan_latest.json")
OUT_DEFAULT = os.path.join(ROOT, "readiness_read_latest.json")
DOC_DEFAULT = os.path.join(ROOT, "readiness_read.md")

# The hosts to read when none are named on the command line: HOSTS="a.com,b.com" in the environment.
HOSTS = [h.strip() for h in os.environ.get("HOSTS", "").split(",") if h.strip()]

AXCHECK = "https://www.ax-check.com"
ASO = "https://aso.forgemesh.io/api/scan"

YES, NO, NA = "YES", "NO", "NA"

# The comparable items. One row per thing all three graders could have an opinion about; the value is the item name
# each grader uses for it, or None when that grader does not ask. Grader item names read from their live responses
# on 2026-09-22; adding a grader is an edit to this table, not to the code below.
ITEMS = [
    # label                           ours                                  aso signal id            ax-check key
    ("robots.txt readable",           "robots_txt",                         None,                    None),
    ("AI crawler named in robots",    "ai_user_agent_named",                "ai-crawler-rules",      None),
    ("sitemap.xml",                   "sitemap",                            "sitemap",               None),
    ("llms.txt",                      "llms_txt",                           "llms-txt",
     "llms-txt-provides-an-actionable-documentation-index"),
    ("markdown for agents",           "markdown_negotiation",               "llm-docs",
     "homepage-answers-markdown-requests"),
    ("Link headers on the HTML",      "link_headers",                       None,                    None),
    ("DNS-AID agent records",         "dns_aid",                            None,                    None),
    ("content signals",               "content_signal",                     None,                    None),
    ("API catalogue",                 "api_catalog",                        None,                    None),
    ("OpenAPI document",              None,                                 "openapi",
     "an-api-reference-or-openapi-spec-is-reachable"),
    ("agent manifest at well-known",  "agent_card",                         "well-known-agent-endpoint", None),
    ("agent.json identity",           None,                                 "agent-json",            None),
    ("OAuth metadata",                "oauth_authorization_server",         None,                    None),
    ("protected-resource metadata",   "oauth_protected_resource",           None,                    None),
    ("auth documentation",            "auth_md",                            "auth-docs",
     "prerequisites-and-auth-boundaries-are-explicit"),
    ("signature key directory",       "http_message_signatures_directory",  None,                    None),
    ("MCP server card",               "mcp_server_card",                    None,
     "an-mcp-server-is-documented-and-well-formed"),
    ("structured data (JSON-LD)",     None,                                 "json-ld",               None),
    ("HTTPS",                         None,                                 "https",                 None),
    ("public status endpoint",        None,                                 "status",                None),
    ("machine-readable pricing",      None,                                 "pricing",
     "prices-are-stated-not-gated"),
    ("payment manifest",              "mpp",                                "payment-manifest",      None),
    ("purchase path",                 "x402",                               "purchase-path",         None),
    ("governance / terms",            None,                                 "governance",            None),
    ("directory listings",            None,                                 "directory-listings",    None),
    ("consistent signals",            None,                                 "consistent-signals",    None),
    ("versioning",                    None,                                 "versioning",            None),
    ("agent-readable docs, links hold", None,                               None,
     "install-and-next-step-links-resolve"),
    ("compact guide fits a budget",   None,                                 None,
     "equivalent-instructions-fit-a-token-budget"),
    ("agent skills published",        "skills_index",                       None,
     "agent-skills-are-published"),
    ("persistent identity",           None,                                 "persistent-identity",   None),
]


# ---------------------------------------------------------------- transport


def get_json(url, data=None, timeout=60):
    hdrs = {"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "identity"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw), ""
            except ValueError:
                return r.status, None, "body is not JSON (%d bytes)" % len(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return e.code, None, "HTTP %d %s" % (e.code, body)
    except Exception as e:
        return 0, None, repr(e)[:200]


# ---------------------------------------------------------------- our scanner


def read_ours(path=OURS_JSON):
    if not os.path.exists(path):
        return {}, "missing %s (run python tools/agent_ready_scan.py --json %s)" % (path, path)
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    out = {}
    for h in doc.get("hosts", []):
        out[h.get("host", "")] = {
            "level": h.get("level"),
            "level_name": h.get("level_name", ""),
            "score": h.get("score"),
            "scored_items": h.get("scored_items"),
            "items": {i["item"]: i for i in h.get("items", [])},
        }
    return out, ""


def rescan(hosts, path=OURS_JSON):
    cmd = [sys.executable, os.path.join(ROOT, "tools", "agent_ready_scan.py"), "--json", path] + list(hosts)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    return p.returncode, (p.stdout or "")[-400:]


def ours_verdict(item):
    if item is None:
        return NA, ""
    s = (item.get("status") or "").lower()
    ev = item.get("evidence", "")[:120]
    if s == "pass":
        return YES, ev
    if s == "fail":
        return NO, ev
    return NA, ev                                               # neutral: not claimed, so not measured


# ---------------------------------------------------------------- ASO grader


def aso_scan(host):
    status, doc, err = get_json("%s?url=%s" % (ASO, urllib.parse.quote("https://" + host, safe="")))
    if doc is None:
        return {"available": False, "error": err or ("HTTP %d" % status), "signals": {}}
    return parse_aso(doc)


def parse_aso(doc):
    signals = {}
    for pillar in doc.get("pillars", []) or []:
        for s in pillar.get("signals", []) or []:
            if s.get("id"):
                signals[s["id"]] = {"status": s.get("status", ""), "points": s.get("points"),
                                    "max": s.get("maxPoints"), "pillar": pillar.get("pillar", "")}
    lvl = doc.get("level") or {}
    return {"available": True, "error": "", "score": doc.get("asoScore"), "max": doc.get("asoMax"),
            "level": ("%s %s" % (lvl.get("id", ""), lvl.get("name", ""))).strip(),
            "readiness": doc.get("agentReadiness", ""), "signals": signals}


def aso_verdict(sig):
    if sig is None:
        return NA, ""
    s = (sig.get("status") or "").lower()
    ev = "%s %s/%s" % (s, sig.get("points"), sig.get("max"))
    if s == "pass":
        return YES, ev
    if s in ("fail", "partial"):
        return NO, ev
    return NA, ev                                               # manual / unassessed: not measured by the grader


# ---------------------------------------------------------------- ax-check grader


def axcheck_start(host):
    return get_json("%s/api/checks" % AXCHECK, data=json.dumps({"domain": host}).encode("utf-8"))


def axcheck_report(host, wait=0, poll=15):
    """Start a check if needed, poll status while it runs, then read report.json."""
    status, doc, err = axcheck_start(host)
    if doc is None:
        return {"available": False, "error": err or ("HTTP %d" % status), "items": {}}
    waited = 0
    while waited <= wait:
        s, st, e = get_json("%s/%s/status" % (AXCHECK, host))
        state = (st or {}).get("status", "")
        if state in ("complete", "failed", ""):
            break
        time.sleep(poll)
        waited += poll
    s, rep, err = get_json("%s/%s/report.json" % (AXCHECK, host))
    if rep is None:
        return {"available": False, "error": err or ("HTTP %d" % s), "items": {},
                "state": (st or {}).get("status", "") if "st" in dir() else ""}
    return parse_axcheck(rep)


def slugify(text):
    out = []
    for ch in str(text).lower():
        out.append(ch if ch.isalnum() else "-")
    return re.sub(r"-+", "-", "".join(out)).strip("-")


def parse_axcheck(doc):
    """Read the documented fields only.

    Measured on the live report 2026-09-22: its checklist entries carry `label`, `status` and `evidence` and no id,
    so the key is the slug of the label. `checks[]` groups them under Clarity, Onboarding, Pricing and Activation.
    """
    items = {}

    def walk(node):
        if isinstance(node, dict):
            key = node.get("id") or node.get("key") or node.get("slug") or node.get("label")
            st = node.get("status") or node.get("result") or node.get("state")
            if key and isinstance(st, str):
                items[slugify(key)] = {"status": st, "label": str(node.get("label") or node.get("name") or "")[:90],
                                       "evidence": str(node.get("evidence") or "")[:160]}
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    grade = doc.get("grade") or (doc.get("summary") or {}).get("grade") if isinstance(doc.get("summary"), dict) \
        else doc.get("grade")
    score = doc.get("score")
    if score is None and isinstance(doc.get("summary"), dict):
        score = doc["summary"].get("score")
    return {"available": True, "error": "", "grade": grade, "score": score,
            "provisional": bool(doc.get("provisional")),
            "status_line": str(doc.get("status") or "")[:140],
            "totals": doc.get("checklistTotals") or {}, "items": items}


def axcheck_verdict(items, key):
    if not key:
        return NA, ""
    it = items.get(key)
    if it is None:
        return NA, ""
    s = (it.get("status") or "").lower()
    ev = "%s: %s" % (s, it.get("evidence", ""))
    if s in ("pass", "ok", "present", "true"):
        return YES, ev
    if s in ("fail", "missing", "false", "attention"):
        return NO, ev
    return NA, ev                                               # unassessed: "never a failure" by its own words


# ---------------------------------------------------------------- comparison


def compare_host(host, ours, aso, ax):
    rows = []
    for label, our_key, aso_key, ax_key in ITEMS:
        o_v, o_ev = ours_verdict((ours.get("items") or {}).get(our_key) if our_key else None)
        a_v, a_ev = aso_verdict(aso.get("signals", {}).get(aso_key) if aso_key else None)
        x_v, x_ev = axcheck_verdict(ax.get("items", {}), ax_key)
        graded = [v for v in (o_v, a_v, x_v) if v != NA]
        disagree = len(set(graded)) > 1
        rows.append({"item": label, "ours": o_v, "aso": a_v, "axcheck": x_v, "disagree": disagree,
                     "ours_evidence": o_ev, "aso_evidence": a_ev, "axcheck_evidence": x_ev})
    rows.sort(key=lambda r: (not r["disagree"], r["item"]))
    return rows


# ---------------------------------------------------------------- selftest

CANNED_ASO = {
    "asoScore": 65, "asoMax": 100, "agentReadiness": "Ready",
    "level": {"id": "ASO-3", "name": "Invocable"},
    "pillars": [
        {"pillar": "Discoverability", "signals": [
            {"id": "ai-crawler-rules", "status": "pass", "points": 4, "maxPoints": 4},
            {"id": "sitemap", "status": "pass", "points": 4, "maxPoints": 4}]},
        {"pillar": "Commerce", "signals": [
            {"id": "pricing", "status": "partial", "points": 2.5, "maxPoints": 5},
            {"id": "payment-manifest", "status": "fail", "points": 0, "maxPoints": 5}]},
        {"pillar": "Reputation", "signals": [
            {"id": "citations", "status": "manual", "points": 0, "maxPoints": 4}]},
    ],
}
# The live report shape, measured 2026-09-22: checks[] groups, items carry label + status + evidence, no id.
CANNED_AX = {"domain": "x.test", "grade": "B", "score": 67, "provisional": True,
             "status": "Provisional score from 15 of 22 technical checks.",
             "checklistTotals": {"pass": 15, "attention": 1, "unassessed": 7},
             "checks": [
                 {"name": "Clarity", "items": [
                     {"label": "Homepage answers Markdown requests", "status": "pass",
                      "evidence": "text/markdown with 965 tokens"},
                     {"label": "llms.txt provides an actionable documentation index", "status": "pass",
                      "evidence": "llms.txt lists all pages"}]},
                 {"name": "Pricing", "items": [
                     {"label": "Agents identify pricing and its assumptions", "status": "attention",
                      "evidence": "1 of 3 sessions fell short"}]},
                 {"name": "Activation", "items": [
                     {"label": "An MCP server is documented and well-formed", "status": "unassessed",
                      "evidence": "no MCP documentation was fetched"}]}]}
CANNED_AX_TRUNCATED = '{"domain": "x.test", "grade":'
CANNED_OURS = {"hosts": [{"host": "x.test", "level": 5, "level_name": "5 Agent-native", "score": 19,
                          "items": [{"group": "content", "item": "llms_txt", "status": "pass", "evidence": "ok"},
                                    {"group": "commerce", "item": "mpp", "status": "fail", "evidence": "absent"},
                                    {"group": "commerce", "item": "ucp", "status": "neutral",
                                     "evidence": "not claimed"}]}]}


def selftest():
    n = [0]
    bad = []

    def eq(got, want, label):
        n[0] += 1
        if got != want:
            bad.append("%s: got %r want %r" % (label, got, want))

    a = parse_aso(CANNED_ASO)
    eq(a["available"], True, "aso parsed")
    eq(a["score"], 65, "aso score")
    eq(a["level"], "ASO-3 Invocable", "aso level")
    eq(len(a["signals"]), 5, "aso signal count")
    eq(aso_verdict(a["signals"]["ai-crawler-rules"])[0], YES, "aso pass is YES")
    eq(aso_verdict(a["signals"]["payment-manifest"])[0], NO, "aso fail is NO")
    eq(aso_verdict(a["signals"]["pricing"])[0], NO, "aso partial is NO, never a pass")
    eq(aso_verdict(a["signals"]["citations"])[0], NA, "aso manual is NA, never a pass")
    eq(aso_verdict(None)[0], NA, "aso item the grader does not ask about")

    x = parse_axcheck(CANNED_AX)
    eq(x["grade"], "B", "axcheck grade")
    eq(x["provisional"], True, "axcheck provisional flag kept")
    eq(x["totals"], {"pass": 15, "attention": 1, "unassessed": 7}, "axcheck totals kept")
    eq(slugify("An MCP server is documented and well-formed"),
       "an-mcp-server-is-documented-and-well-formed", "label slug")
    eq(slugify("llms.txt provides an actionable documentation index"),
       "llms-txt-provides-an-actionable-documentation-index", "label slug with a dot")
    eq(axcheck_verdict(x["items"], "homepage-answers-markdown-requests")[0], YES, "axcheck pass is YES")
    eq(axcheck_verdict(x["items"], "agents-identify-pricing-and-its-assumptions")[0], NO,
       "axcheck attention is NO, never a pass")
    eq(axcheck_verdict(x["items"], "an-mcp-server-is-documented-and-well-formed")[0], NA,
       "axcheck unassessed is NA, never a pass")
    eq(axcheck_verdict(x["items"], "nothing-here")[0], NA, "axcheck unknown key")

    try:
        json.loads(CANNED_AX_TRUNCATED)
        truncated_ok = True
    except ValueError:
        truncated_ok = False
    eq(truncated_ok, False, "a truncated body does not parse")
    eq(parse_axcheck({})["items"], {}, "empty report yields no items")
    eq(parse_axcheck({})["grade"], None, "empty report has no grade")

    tmp = os.path.join(ROOT, "ops", "_readiness_selftest.json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(CANNED_OURS, fh)
    ours, err = read_ours(tmp)
    os.remove(tmp)
    eq(err, "", "our scan read without error")
    eq(ours["x.test"]["level"], 5, "our level read")
    eq(ours_verdict(ours["x.test"]["items"]["llms_txt"])[0], YES, "our pass is YES")
    eq(ours_verdict(ours["x.test"]["items"]["mpp"])[0], NO, "our fail is NO")
    eq(ours_verdict(ours["x.test"]["items"]["ucp"])[0], NA, "our neutral is NA, never a pass")
    eq(read_ours(os.path.join(ROOT, "ops", "_does_not_exist.json"))[0], {}, "missing scan file")

    rows = compare_host("x.test", ours["x.test"], a, x)
    by = {r["item"]: r for r in rows}
    eq(by["llms.txt"]["disagree"], False, "three graders agreeing is not a disagreement")
    eq(by["payment manifest"]["ours"], NO, "payment manifest ours")
    eq(rows[0]["disagree"] or not any(r["disagree"] for r in rows), True, "disagreements sort first")

    print("selftest: %d checks, %d failed" % (n[0], len(bad)))
    for b in bad:
        print("  FAIL", b)
    return 1 if bad else 0


# ---------------------------------------------------------------- output


def write_doc(report, path):
    L = ["# Three graders, one reader: agent readiness\n"]
    L.append("Tool: `tools/readiness_read.py`, read-only. Record: `readiness_read_latest.json`.\n")
    L.append("Graders, all read 22 Sep 2026:\n")
    L.append("- yours, `tools/agent_ready_scan.py` via `agent_ready_scan_latest.json`")
    L.append("- ax-check.com, keyless REST documented at <https://ax-check.com/llms.txt>; its own words: the grade")
    L.append("  \"is PROVISIONAL and technical-only\" and `unassessed` \"is never a failure\"")
    L.append("- forgemesh ASO, keyless `GET https://aso.forgemesh.io/api/scan?url=`, named in")
    L.append("  <https://github.com/forgemeshlabs/agent-signal-optimization> README\n")
    L.append("YES means the grader saw it; NO means the grader looked and did not; NA means that grader does not")
    L.append("ask, or marked the item unassessed or manual. Disagreements are the rows where two graders that both")
    L.append("have an opinion give different ones.\n")
    L.append("## Headline\n")
    L.append("| host | yours | ax-check | forgemesh ASO | rows where they disagree |")
    L.append("|---|---|---|---|---|")
    for d in report["doors"]:
        ax = d["axcheck"]
        aso = d["aso"]
        L.append("| %s | %s (%s/%s) | %s | %s | %d |" % (
            d["host"],
            d["ours"].get("level_name", "not read"), d["ours"].get("score"), d["ours"].get("scored_items"),
            ("%s %s%s" % (ax.get("grade"), ax.get("score"), " provisional" if ax.get("provisional") else ""))
            if ax.get("available") else "UNREADABLE: " + str(ax.get("error"))[:50],
            ("%s %s/%s" % (aso.get("level"), aso.get("score"), aso.get("max")))
            if aso.get("available") else "UNREADABLE: " + str(aso.get("error"))[:50],
            sum(1 for r in d["rows"] if r["disagree"])))
    L.append("")
    for d in report["doors"]:
        L.append("### %s\n" % d["host"])
        L.append("| item | ours | ax-check | ASO | evidence (ours / ASO) |")
        L.append("|---|---|---|---|---|")
        for r in d["rows"]:
            mark = "**" if r["disagree"] else ""
            L.append("| %s%s%s | %s | %s | %s | %s |" % (
                mark, r["item"], mark, r["ours"], r["axcheck"], r["aso"],
                ("%s / %s" % (r["ours_evidence"][:70], r["aso_evidence"]))
                .replace("|", "/").strip(" /")))
        L.append("")
    L.append("## What each outside grader could and could not read\n")
    for d in report["doors"]:
        ax = d["axcheck"]
        if ax.get("available"):
            t = ax.get("totals") or {}
            L.append("- **%s**, ax-check: %s pass, %s attention, %s unassessed. Its own line: %s"
                     % (d["host"], t.get("pass"), t.get("attention"), t.get("unassessed"),
                        ax.get("status_line") or "none"))
        else:
            L.append("- **%s**, ax-check UNREADABLE: %s" % (d["host"], str(ax.get("error"))[:160]))
        aso = d["aso"]
        if not aso.get("available"):
            L.append("  - forgemesh ASO UNREADABLE: %s" % str(aso.get("error"))[:200])
    L.append("")
    L.append("\nEvery UNREADABLE above is recorded as NA, never as a pass and never as a failure.\n")
    L.append("Not verified: neither grader's method was audited, and neither was run twice, so a disagreement is a")
    L.append("difference between two readings on one day, not a proven defect in either. isitagentready.com, the")
    L.append("scanner the Level 5 claim rests on, has no keyless API and was not read here.")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="our readiness scan beside two outside graders, item by item")
    ap.add_argument("hosts", nargs="*", help="door hosts (default: the five)")
    ap.add_argument("--rescan", action="store_true", help="rerun agent_ready_scan.py before comparing")
    ap.add_argument("--no-axcheck", action="store_true", help="skip ax-check")
    ap.add_argument("--no-aso", action="store_true", help="skip forgemesh ASO")
    ap.add_argument("--wait", type=int, default=0, help="seconds to poll an ax-check run before reading its report")
    ap.add_argument("--json", dest="json_path", default=OUT_DEFAULT)
    ap.add_argument("--doc", dest="doc_path", default=DOC_DEFAULT)
    ap.add_argument("--selftest", action="store_true", help="canned-response checks, no network")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    hosts = a.hosts or HOSTS
    if not hosts:
        sys.exit("name one or more hosts, or set HOSTS to a comma-separated list")
    if a.rescan:
        rc, tail = rescan(hosts)
        print("agent_ready_scan.py exit %d\n%s" % (rc, tail))
    ours_all, err = read_ours()
    if err:
        print("our scan: %s" % err)

    report = {"tool": "readiness_read.py",
              "read_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "graders": {
                  "ours": {"tool": "agent_ready_scan.py", "source": OURS_JSON},
                  "axcheck": {"docs": "https://ax-check.com/llms.txt", "api": AXCHECK + "/api/checks",
                              "read_date": "2026-09-22", "keyless": True},
                  "aso": {"docs": "https://github.com/forgemeshlabs/agent-signal-optimization",
                          "api": ASO, "read_date": "2026-09-22", "keyless": True}},
              "doors": []}

    for host in hosts:
        ours = ours_all.get(host, {})
        aso = {"available": False, "error": "skipped", "signals": {}} if a.no_aso else aso_scan(host)
        ax = {"available": False, "error": "skipped", "items": {}} if a.no_axcheck \
            else axcheck_report(host, wait=a.wait)
        rows = compare_host(host, ours, aso, ax)
        report["doors"].append({"host": host, "ours": ours, "aso": aso, "axcheck": ax, "rows": rows})
        dis = [r for r in rows if r["disagree"]]
        print("\n%-22s ours %-18s ax-check %-24s ASO %s" % (
            host, ours.get("level_name", "not read"),
            ("%s %s" % (ax.get("grade"), ax.get("score"))) if ax.get("available")
            else "UNREADABLE " + str(ax.get("error"))[:40],
            ("%s %s/%s" % (aso.get("level"), aso.get("score"), aso.get("max"))) if aso.get("available")
            else "UNREADABLE " + str(aso.get("error"))[:40]))
        print("  %d disagreement(s) of %d items" % (len(dis), len(rows)))
        for r in dis:
            print("    %-30s ours=%-3s axcheck=%-3s aso=%-3s  %s" % (r["item"], r["ours"], r["axcheck"],
                                                                     r["aso"], r["aso_evidence"]))

    if os.path.dirname(a.json_path):
        os.makedirs(os.path.dirname(a.json_path), exist_ok=True)
    with open(a.json_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    write_doc(report, a.doc_path)
    print("\nwrote %s and %s" % (a.json_path, a.doc_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
