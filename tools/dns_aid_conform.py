#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""dns_aid_conform.py: read every DNS-AID item the reference implementation documents, per zone, over DNS-over-HTTPS.

Written 2026-09-22 against the reference's own README; nothing from it is installed, imported or run. The record
shapes it documents are cited below.

Reference read 2026-09-22:
  https://raw.githubusercontent.com/dns-aid/dns-aid-core/main/README.md   (dns-aid/dns-aid-core, Apache-2.0,
  reference implementation of IETF draft-mozleywilliams-dnsop-dnsaid-02)

What the reference documents, and what this tool therefore reads:
  1. A ServiceMode SVCB record per agent, RFC 9460, carrying alpn and port:
         chat.example.com. 3600 IN SVCB 1 chat.example.com. alpn="a2a" port=443 mandatory="alpn,port"
     The underscore form the Cloudflare Agent Readiness diagnostic and isitagentready.com look for is
     _a2a._agents.<zone> and _mcp._agents.<zone> (tools/dns_aid.py publishes it).
  2. An index record at _index._agents.<zone>. The reference publishes it as TXT:
         _index._agents.example.com. TXT "agents=chat:mcp,billing:a2a,support:https"
     "DNS-AID v0.3.0 automatically maintains an index record at _index._agents.{domain} for efficient discovery.
      Benefits: single DNS query discovers all agents at a domain; crawlers can efficiently index domains; explicit
      list of published agents (no guessing)."  Its discovery flow reads the TXT index as the DNS fallback to the
     HTTP index. The same label is also used as an SVCB pointer when a zone advertises an ARD ai-catalog
     ("dns-aid index publish-catalog" publishes _catalog._agents + _index._agents SVCB to the catalog host), so
     both types are read here and either one present satisfies the item.
  3. Private-use SVCB parameters (v0.4.8+), carried natively on the SVCB record on Cloudflare DNS:
     cap, cap-sha256, bap, policy, realm, written as key65400 to key65409. OPTIONAL in the reference.
  4. DNSSEC. OPTIONAL: the reference's hardening is "SDK/CLI/MCP; all default off", and llms.txt/DNS-AID interop
     is described as "optional DNSSEC-validated trust". The AD flag is therefore reported, never failed on. The
     requirement belongs to the checkers, not to the draft.
  5. That the SVCB target answers on the advertised port (the record is a pointer; a pointer to nothing is a FAIL).

Read-only. It never writes DNS and never touches the dashboard: tools/dns_aid.py publishes, this one reads.

  python tools/dns_aid_conform.py example.com            one zone
  python tools/dns_aid_conform.py                        the zones in the ZONES environment list
  python tools/dns_aid_conform.py --json x.json          write the record elsewhere
  python tools/dns_aid_conform.py --no-port-probe        DNS only, no HTTPS reach test
  python tools/dns_aid_conform.py --selftest             offline parser checks, no network

Exit code 1 when any item FAILs on any zone.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOH = "https://cloudflare-dns.com/dns-query"
UA = os.environ.get("DNS_AID_CONFORM_UA", "example-bot/1.0 (+https://example.com)")
OUT_DEFAULT = os.path.join(ROOT, "dns_aid_conform_latest.json")

# The zones to read when none are named on the command line: ZONES="a.com,b.com" in the environment.
ZONES = [z.strip() for z in os.environ.get("ZONES", "").split(",") if z.strip()]

# The agent records a zone publishes, and the alpn each one must carry (tools/dns_aid.py writes them).
AGENT_RECORDS = [("_a2a._agents", "a2a"), ("_mcp._agents", "mcp")]

# Reference private-use parameters, v0.4.8+. Optional; reported, never failed on.
PRIVATE_PARAMS = ["cap", "cap-sha256", "bap", "policy", "realm"] + ["key%d" % k for k in range(65400, 65410)]

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"


# ---------------------------------------------------------------- parsing


def parse_doh(doc):
    """Read a Cloudflare DoH JSON answer into answered / ad / records."""
    if not isinstance(doc, dict):
        return {"answered": False, "ad": False, "status": -1, "records": []}
    answers = doc.get("Answer") or []
    records = [a.get("data", "") for a in answers if isinstance(a, dict)]
    return {
        "answered": bool(records),
        "ad": bool(doc.get("AD")),
        "status": doc.get("Status", -1),
        "records": records,
    }


def parse_svcb(data):
    """Presentation-format SVCB rdata -> {priority, target, params{}}.

    Cloudflare's DoH returns e.g. '1 example.com. alpn=a2a port=443' or the quoted form alpn="a2a".
    """
    out = {"priority": None, "target": None, "params": {}}
    if not isinstance(data, str) or not data.strip():
        return out
    parts = data.strip().split()
    if len(parts) < 2:
        return out
    try:
        out["priority"] = int(parts[0])
    except ValueError:
        return out
    out["target"] = parts[1].rstrip(".") or "."
    for tok in parts[2:]:
        if "=" not in tok:
            out["params"][tok.lower()] = ""
            continue
        k, v = tok.split("=", 1)
        out["params"][k.lower()] = v.strip('"')
    return out


def parse_index_txt(records):
    """The reference index record: TXT "agents=chat:mcp,billing:a2a". -> [(name, protocol), ...]"""
    agents = []
    for r in records or []:
        s = r.strip().strip('"') if isinstance(r, str) else ""
        if not s.lower().startswith("agents="):
            continue
        for item in s[len("agents="):].split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                n, p = item.split(":", 1)
                agents.append((n.strip(), p.strip()))
            else:
                agents.append((item, ""))
    return agents


# ---------------------------------------------------------------- network


def doh_query(name, rtype):
    url = "%s?name=%s&type=%s" % (DOH, urllib.parse.quote(name), rtype)
    req = urllib.request.Request(url, headers={"accept": "application/dns-json", "User-Agent": UA,
                                               "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8", "replace")), url
    except Exception as exc:                                     # a failed read is not an answer
        return {"_error": repr(exc)[:200]}, url


def port_answers(host, port):
    """Does the SVCB target answer on the advertised port? Any HTTP status counts; a transport error does not."""
    url = "https://%s:%d/" % (host, int(port))
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return True, "HTTP %d" % r.status, url
    except urllib.error.HTTPError as e:
        return True, "HTTP %d" % e.code, url
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        return False, repr(e)[:140], url


# ---------------------------------------------------------------- one zone


def check_zone(zone, port_probe=True):
    items = []

    def add(item, verdict, evidence, source):
        items.append({"item": item, "verdict": verdict, "evidence": evidence, "source": source})

    ad_flags = []

    for label, alpn in AGENT_RECORDS:
        fqdn = "%s.%s" % (label, zone)
        doc, url = doh_query(fqdn, "SVCB")
        p = parse_doh(doc)
        ad_flags.append(p["ad"])
        if not p["answered"]:
            add("%s SVCB present" % label, FAIL,
                "NO ANSWER (DoH Status %s)" % p["status"], url)
            add("%s alpn" % label, FAIL, "no record to read", url)
            add("%s port" % label, FAIL, "no record to read", url)
            add("%s target answers on port" % label, FAIL, "no record to read", url)
            continue
        svcb = parse_svcb(p["records"][0])
        add("%s SVCB present" % label, PASS, p["records"][0], url)
        got_alpn = svcb["params"].get("alpn", "")
        add("%s alpn" % label, PASS if got_alpn == alpn else FAIL,
            "alpn=%s (reference requires alpn on every agent record)" % (got_alpn or "absent"), url)
        got_port = svcb["params"].get("port", "")
        add("%s port" % label, PASS if got_port.isdigit() else FAIL,
            "port=%s" % (got_port or "absent"), url)
        present = sorted(k for k in svcb["params"] if k in PRIVATE_PARAMS)
        add("%s private-use params (optional)" % label, INFO,
            "present: %s" % (", ".join(present) if present else "none of cap, cap-sha256, bap, policy, realm"), url)
        if port_probe and svcb["target"] and got_port.isdigit():
            ok, ev, purl = port_answers(svcb["target"], got_port)
            add("%s target answers on port" % label, PASS if ok else FAIL,
                "%s -> %s" % (svcb["target"], ev), purl)
        else:
            add("%s target answers on port" % label, INFO, "not probed", url)

    # The index record. TXT is the reference's own shape; SVCB is its ARD catalog pointer. Either satisfies it.
    txt_doc, txt_url = doh_query("_index._agents.%s" % zone, "TXT")
    txt = parse_doh(txt_doc)
    svcb_doc, svcb_url = doh_query("_index._agents.%s" % zone, "SVCB")
    idx_svcb = parse_doh(svcb_doc)
    ad_flags.extend([txt["ad"], idx_svcb["ad"]])
    if txt["answered"]:
        agents = parse_index_txt(txt["records"])
        add("_index._agents record", PASS if agents else FAIL,
            "TXT %s -> %d agent(s)" % (txt["records"][0][:120], len(agents)), txt_url)
    elif idx_svcb["answered"]:
        add("_index._agents record", PASS, "SVCB %s (ARD catalog pointer form)" % idx_svcb["records"][0][:120],
            svcb_url)
    else:
        add("_index._agents record", FAIL,
            "NO ANSWER on TXT and on SVCB (DoH Status %s/%s). The reference maintains an index record here "
            "holding the published agent list, agents=<name>:<protocol> comma separated, so one query "
            "discovers every agent at the zone." % (txt["status"], idx_svcb["status"]), txt_url)

    add("DNSSEC AD flag (optional in the reference)", INFO,
        "AD=%s on %d of %d queries" % (all(ad_flags) if ad_flags else False, sum(1 for a in ad_flags if a),
                                       len(ad_flags)), DOH)

    fails = [i for i in items if i["verdict"] == FAIL]
    return {"zone": zone, "items": items, "pass": sum(1 for i in items if i["verdict"] == PASS),
            "fail": len(fails), "info": sum(1 for i in items if i["verdict"] == INFO)}


# ---------------------------------------------------------------- selftest

CANNED_SVCB = {"Status": 0, "AD": True,
               "Answer": [{"name": "_a2a._agents.example.com", "type": 64, "TTL": 300,
                           "data": '1 example.com. alpn="a2a" port=443'}]}
CANNED_NXANSWER = {"Status": 0, "AD": True, "Authority": [{"name": "example.com", "type": 6}]}
CANNED_INDEX_TXT = {"Status": 0, "AD": False,
                    "Answer": [{"name": "_index._agents.example.com", "type": 16,
                                "data": '"agents=chat:mcp,billing:a2a,support:https"'}]}


def selftest():
    n = [0]
    bad = []

    def eq(got, want, label):
        n[0] += 1
        if got != want:
            bad.append("%s: got %r want %r" % (label, got, want))

    p = parse_doh(CANNED_SVCB)
    eq(p["answered"], True, "svcb answered")
    eq(p["ad"], True, "svcb AD true")
    eq(len(p["records"]), 1, "svcb record count")
    e = parse_doh(CANNED_NXANSWER)
    eq(e["answered"], False, "no-answer is not an answer")
    eq(parse_doh(None)["answered"], False, "garbage doc is not an answer")
    eq(parse_doh(None)["status"], -1, "garbage doc status")

    s = parse_svcb('1 example.com. alpn="a2a" port=443')
    eq(s["priority"], 1, "svcb priority")
    eq(s["target"], "example.com", "svcb target, trailing dot stripped")
    eq(s["params"]["alpn"], "a2a", "svcb alpn unquoted")
    eq(s["params"]["port"], "443", "svcb port")
    u = parse_svcb("1 mcp.example.org. alpn=mcp port=443")
    eq(u["params"]["alpn"], "mcp", "svcb alpn unquoted form")
    c = parse_svcb('1 x.example. alpn=mcp port=443 cap="https://x/cap.json" realm=prod')
    eq(sorted(k for k in c["params"] if k in PRIVATE_PARAMS), ["cap", "realm"], "private-use params read")
    eq(parse_svcb("0 .")["params"], {}, "AliasMode record carries no params")
    eq(parse_svcb("")["target"], None, "empty rdata")
    eq(parse_svcb("junk")["priority"], None, "unparseable rdata")

    a = parse_index_txt(parse_doh(CANNED_INDEX_TXT)["records"])
    eq(a, [("chat", "mcp"), ("billing", "a2a"), ("support", "https")], "index TXT parsed")
    eq(parse_index_txt(['"version=1"']), [], "a TXT that is not an index yields no agents")
    eq(parse_index_txt([]), [], "no TXT yields no agents")

    print("selftest: %d checks, %d failed" % (n[0], len(bad)))
    for b in bad:
        print("  FAIL", b)
    return 1 if bad else 0


# ---------------------------------------------------------------- output


def table(report):
    width = max(len(i["item"]) for z in report["zones"] for i in z["items"])
    for z in report["zones"]:
        print("\n%s   %d PASS  %d FAIL  %d INFO" % (z["zone"], z["pass"], z["fail"], z["info"]))
        print("-" * (width + 60))
        for i in z["items"]:
            print("  %-4s %-*s %s" % (i["verdict"], width, i["item"], i["evidence"][:110]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="DNS-AID conformance read against the reference implementation")
    ap.add_argument("zones", nargs="*", help="zones, e.g. example.com (default: the ZONES environment list)")
    ap.add_argument("--json", dest="json_path", default=OUT_DEFAULT, help="where to write the record")
    ap.add_argument("--no-port-probe", action="store_true", help="skip the HTTPS reach test on each SVCB target")
    ap.add_argument("--selftest", action="store_true", help="offline parser checks, no network")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    zones = a.zones or ZONES
    if not zones:
        sys.exit("name one or more zones, or set ZONES to a comma-separated list")
    report = {
        "tool": "dns_aid_conform.py",
        "read_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resolver": DOH,
        "reference": {
            "repo": "dns-aid/dns-aid-core",
            "readme": "https://raw.githubusercontent.com/dns-aid/dns-aid-core/main/README.md",
            "read_date": "2026-09-22",
            "draft": "draft-mozleywilliams-dnsop-dnsaid-02",
            "dnssec": "optional in the reference; the AD flag is reported, never failed on",
        },
        "zones": [check_zone(z, port_probe=not a.no_port_probe) for z in zones],
    }
    report["fail_total"] = sum(z["fail"] for z in report["zones"])
    table(report)

    if os.path.dirname(a.json_path):
        os.makedirs(os.path.dirname(a.json_path), exist_ok=True)
    with open(a.json_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    print("\nwrote %s   %d FAIL across %d zone(s)" % (a.json_path, report["fail_total"], len(zones)))
    return 1 if report["fail_total"] else 0


if __name__ == "__main__":
    sys.exit(main())
