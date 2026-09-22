#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""dns_aid.py: publish the DNS for AI Discovery (DNS-AID) entry points for a zone, through the logged-in Cloudflare
dashboard (tools/cfdash.py over a Chrome on port 9226), and prove each one back through DNS-over-HTTPS.

Why (22 Sep 2026): a Cloudflare API token with zone read but no DNS write cannot add the records the Agent
Readiness diagnostic and isitagentready.com look for (_a2a._agents.<zone>, _mcp._agents.<zone>, SVCB or HTTPS) .
The dashboard form is four fields and a Save; this drives it once per record, skips a record that
DNS already answers, and never touches any other record.

What it publishes, per zone, and why each is true:
  _a2a._agents.<zone>   SVCB 1 <zone>.               alpn="a2a" port=443   the door's own /a2a JSON-RPC endpoint
  _mcp._agents.<zone>   SVCB 1 $MCP_HOST.           alpn="mcp" port=443   the MCP gateway your own server card names
  _index._agents.<zone> TXT   "agents=a2a:a2a,mcp:mcp"                     the index the reference maintains, --index only

  python tools/dns_aid.py example.com example.org        add what is missing, verify, print one row per record
  python tools/dns_aid.py --index example.com                   add the _index._agents TXT record only (the one DNS-AID
                                                                   conformance FAIL on every zone, knowledge/dns_aid_conform_2026-09-22.md);
                                                                   idempotent: present = skip, a different value = printed, never overwritten
  python tools/dns_aid.py --index --check example.com           DoH read of the index record only, no dashboard
  python tools/dns_aid.py --check example.com                   DoH read only, no dashboard
  python tools/dns_aid.py --dnssec example.com                  press Enable DNSSEC on the zone's DNS settings page (the
                                                                   draft requires signed zones; on a Cloudflare Registrar
                                                                   domain the DS record is added by Cloudflare itself)
  python tools/dns_aid.py --selftest

Needs: CLOUDFLARE_ACCOUNT_ID, and a Chrome started with --remote-debugging-port=9226 already logged into the
dashboard (tools/cfdash.py drives it). MCP_HOST sets the _mcp target; it defaults to mcp.example.com.
Exit 0 when every wanted record answers over DoH afterwards; 1 otherwise; 2 when the Chrome is not up.
"""
import argparse, json, os, pathlib, re, subprocess, sys, tempfile, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFDASH = [sys.executable, str(ROOT / "tools" / "cfdash.py"), "--cdp", "9226"]
MCP_HOST = os.environ.get("MCP_HOST", "mcp.example.com")


def account():
    """The Cloudflare account id from your dashboard URL. No default: a wrong id writes on another zone."""
    a = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not a:
        sys.exit("set CLOUDFLARE_ACCOUNT_ID (the id in your https://dash.cloudflare.com/<id>/ URL)")
    return a
DOH = "https://cloudflare-dns.com/dns-query"

def wanted(zone):
    return [
        {"name": "_a2a._agents", "fqdn": f"_a2a._agents.{zone}", "priority": "1", "target": zone, "value": 'alpn="a2a" port=443'},
        {"name": "_mcp._agents", "fqdn": f"_mcp._agents.{zone}", "priority": "1", "target": MCP_HOST, "value": 'alpn="mcp" port=443'},
    ]

def doh(fqdn, rtype="SVCB"):
    req = urllib.request.Request(f"{DOH}?name={fqdn}&type={rtype}", headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    return [a.get("data", "") for a in d.get("Answer", []) if a.get("type") == 64]

def expected_data(rec):
    return f"1 {rec['target']}. " + rec["value"].replace('"', "")

# ---- the index record (--index). The reference keeps one TXT per zone listing the agents that zone publishes,
# agents=<name>:<protocol> comma separated, so one query discovers all of them. Ours publishes two: a2a and mcp.
INDEX_NAME = "_index._agents"
INDEX_VALUE = "agents=a2a:a2a,mcp:mcp"

def index_rec(zone):
    return {"name": INDEX_NAME, "fqdn": f"{INDEX_NAME}.{zone}", "content": INDEX_VALUE, "ttl": "300"}

def norm_txt(data):
    """Presentation-format TXT rdata -> the string it carries. '"a" "b"' -> 'ab'; an unquoted form is returned as is."""
    s = (data or "").strip()
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', s)
    if parts:
        return "".join(p.replace('\\"', '"') for p in parts)
    return s

def txt_answers(doc):
    """TXT strings out of a Cloudflare DoH JSON document. Type 16 only, so a CNAME in the chain is never read as one."""
    if not isinstance(doc, dict):
        return []
    return [norm_txt(a.get("data", "")) for a in (doc.get("Answer") or []) if isinstance(a, dict) and a.get("type") == 16]

def doh_txt(fqdn):
    req = urllib.request.Request(f"{DOH}?name={fqdn}&type=TXT", headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return txt_answers(json.loads(r.read().decode("utf-8")))

def classify_index(values, want=INDEX_VALUE):
    """present = the wanted value already answers; other = the name answers with something else, never overwritten."""
    if any(v == want for v in values):
        return "present"
    return "other" if values else "missing"

def cf(*args):
    p = subprocess.run(CFDASH + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = (p.stdout.strip().splitlines() or [""])[-1]
    try:
        return json.loads(line)
    except Exception:
        return {"ok": False, "error": (p.stderr or p.stdout)[-300:]}

def evaljs(js):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js); path = f.name
    try:
        return cf("evalfile", path)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)

OPEN_TYPE = """(() => { const dlg = document.querySelector('[role="dialog"]'); if (!dlg) return "no dialog";
  const b = [...dlg.querySelectorAll('[role="combobox"]')][0]; if (!b) return "no type combobox"; b.click(); return "opened"; })()"""
PICK_SVCB = """(() => { const o = [...document.querySelectorAll('[role="option"],[role="menuitem"],li')].find(e => (e.innerText||'').trim() === 'SVCB');
  if (!o) return "no SVCB option"; o.click(); return "picked"; })()"""
DIALOG_GONE = """(() => !document.querySelector('[role="dialog"]'))()"""

def add_record(zone, rec):
    url = f"https://dash.cloudflare.com/{account()}/{zone}/dns/records"
    g = cf("goto", url)
    if not g.get("ok"):
        return "chrome/login: " + str(g)[:120]
    time.sleep(3)
    cf("click", "Got it")   # the new-DNS welcome card, when it shows; harmless otherwise
    r = cf("click", "Add record")
    if not r.get("ok"):
        return "no Add record button: " + str(r)[:120]
    time.sleep(2)
    a = evaljs(OPEN_TYPE); time.sleep(1)
    b = evaljs(PICK_SVCB); time.sleep(1.5)
    if b.get("result") != "picked":
        return f"type picker: {a.get('result')} / {b.get('result')}"
    for label, value in [("Use @ for root", rec["name"]), ("Priority", rec["priority"]), ("Target", rec["target"]), ("Value", rec["value"])]:
        f = cf("fill", label, value)
        if not f.get("ok"):
            return f"fill {label}: {str(f)[:120]}"
    s = cf("click", "Save")
    if not s.get("ok"):
        return "save: " + str(s)[:120]
    for _ in range(10):
        time.sleep(1)
        if evaljs(DIALOG_GONE).get("result") is True:
            break
    return "saved"

PICK_TXT = """(() => { const o = [...document.querySelectorAll('[role="option"],[role="menuitem"],li')].find(e => (e.innerText||'').trim() === 'TXT');
  if (!o) return "no TXT option"; o.click(); return "picked"; })()"""

# Trap 22 Sep 2026: cfdash fill "Content" matches the DNS table's "Sort by Content" button, not the dialog's textarea,
# because get_by_label searches the whole page. The content field is scoped by name inside the dialog instead, and set
# through the native setter so the React form sees the change (same pattern as scripts/cf_bot_submit.py).
FILL_CONTENT = """(() => { const dlg = document.querySelector('[role="dialog"]'); if (!dlg) return "no dialog";
  const el = dlg.querySelector('textarea[name="content"], input[name="content"]'); if (!el) return "no content field";
  const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, %s);
  el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
  return el.value === %s ? "filled" : "value is " + el.value; })()"""

def add_txt_record(zone, rec):
    """Same dashboard flow the SVCB records use: Add record, pick the type, fill the fields, Save. TTL is left on Auto."""
    url = f"https://dash.cloudflare.com/{account()}/{zone}/dns/records"
    g = cf("goto", url)
    if not g.get("ok"):
        return "chrome/login: " + str(g)[:120]
    time.sleep(3)
    cf("click", "Got it")   # the new-DNS welcome card, when it shows; harmless otherwise
    r = cf("click", "Add record")
    if not r.get("ok"):
        return "no Add record button: " + str(r)[:120]
    time.sleep(2)
    a = evaljs(OPEN_TYPE); time.sleep(1)
    b = evaljs(PICK_TXT); time.sleep(1.5)
    if b.get("result") != "picked":
        return f"type picker: {a.get('result')} / {b.get('result')}"
    f = cf("fill", "Use @ for root", rec["name"])
    if not f.get("ok"):
        return f"fill name: {str(f)[:120]}"
    q = json.dumps(rec["content"])
    c = evaljs(FILL_CONTENT % (q, q)); time.sleep(1)
    if c.get("result") != "filled":
        return f"fill content: {c.get('result')}"
    s = cf("click", "Save")
    if not s.get("ok"):
        return "save: " + str(s)[:120]
    for _ in range(10):
        time.sleep(1)
        if evaljs(DIALOG_GONE).get("result") is True:
            break
    return "saved"

def prove_index(fqdn, want=INDEX_VALUE, seconds=90, step=5):
    """Wait for the new TXT to answer over DoH, up to `seconds`. A read that errors is not an answer; it retries."""
    values, waited = [], 0
    while True:
        try:
            values = doh_txt(fqdn)
        except Exception:
            values = []
        if any(v == want for v in values) or waited >= seconds:
            return values, waited
        time.sleep(step); waited += step

def do_index(zones, check_only=False):
    bad = 0
    for zone in zones:
        rec = index_rec(zone)
        before = doh_txt(rec["fqdn"])
        state = classify_index(before)
        if state == "present":
            print(f"{rec['fqdn']:44s} present   TXT \"{rec['content']}\""); continue
        if state == "other":
            print(f"{rec['fqdn']:44s} OTHER     answers with {before}; not overwritten"); bad += 1; continue
        if check_only:
            print(f"{rec['fqdn']:44s} MISSING   (no TXT answer)"); bad += 1; continue
        status = add_txt_record(zone, rec)
        after, waited = prove_index(rec["fqdn"])
        ok = any(v == rec["content"] for v in after)
        bad += 0 if ok else 1
        print(f"{rec['fqdn']:44s} {'ADDED' if ok else 'FAILED':8s}  dashboard: {status}; DoH after {waited}s: {after or 'none'}")
    sys.exit(1 if bad else 0)

DNSSEC_STATE = """(() => { const t = document.body.innerText; const i = t.indexOf('DNSSEC'); return t.slice(i, i + 400); })()"""

def enable_dnssec(zone):
    g = cf("goto", f"https://dash.cloudflare.com/{account()}/{zone}/dns/settings")
    if not g.get("ok"):
        return "chrome/login: " + str(g)[:120]
    time.sleep(5)
    before = evaljs(DNSSEC_STATE).get("result", "")
    if "Enable DNSSEC" not in before:
        return "already: " + " ".join(before.split())[:160]
    r = cf("click", "Enable DNSSEC")
    if not r.get("ok"):
        return "click failed: " + str(r)[:120]
    time.sleep(6)
    after = evaljs(DNSSEC_STATE).get("result", "")
    return "pressed: " + " ".join(after.split())[:160]

CANNED_INDEX_TXT = {"Status": 0, "AD": True, "Answer": [
    {"name": "cname.example.org", "type": 5, "data": "_index._agents.example.org."},
    {"name": "_index._agents.example.org", "type": 16, "TTL": 300, "data": '"agents=a2a:a2a,mcp:mcp"'}]}

def selftest():
    r = wanted("example.org")
    assert r[0]["fqdn"] == "_a2a._agents.example.org" and r[1]["target"] == MCP_HOST
    assert expected_data(r[0]) == "1 example.org. alpn=a2a port=443"
    assert expected_data(r[1]) == "1 %s. alpn=mcp port=443" % MCP_HOST
    # the index record: value shape
    i = index_rec("example.org")
    assert i["fqdn"] == "_index._agents.example.org" and i["name"] == "_index._agents"
    assert i["content"] == "agents=a2a:a2a,mcp:mcp"
    pairs = [p.split(":") for p in i["content"][len("agents="):].split(",")]
    assert pairs == [["a2a", "a2a"], ["mcp", "mcp"]] and i["content"].startswith("agents=")
    # TXT rdata normalisation
    assert norm_txt('"agents=a2a:a2a,mcp:mcp"') == "agents=a2a:a2a,mcp:mcp"
    assert norm_txt('"agents=a2a:a2a," "mcp:mcp"') == "agents=a2a:a2a,mcp:mcp"
    assert norm_txt("agents=a2a:a2a,mcp:mcp") == "agents=a2a:a2a,mcp:mcp"
    assert norm_txt("") == "" and norm_txt(None) == ""
    # DoH parse: type 16 only, so a CNAME in the chain is never read as a TXT answer
    assert txt_answers(CANNED_INDEX_TXT) == ["agents=a2a:a2a,mcp:mcp"]
    assert txt_answers({"Status": 0, "Authority": [{"name": "example.org", "type": 6}]}) == []
    assert txt_answers(None) == []
    # the idempotence branch
    assert classify_index(["agents=a2a:a2a,mcp:mcp"]) == "present"
    assert classify_index(["v=spf1 -all", "agents=a2a:a2a,mcp:mcp"]) == "present"
    assert classify_index(["agents=chat:mcp"]) == "other"
    assert classify_index([]) == "missing"
    print("selftest OK: 17 checks (3 SVCB, 14 index)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zones", nargs="*")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dnssec", action="store_true")
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    bad = 0
    if a.index:
        return do_index(a.zones, check_only=a.check)
    if a.dnssec:
        for zone in a.zones:
            print(f"{zone:44s} DNSSEC    {enable_dnssec(zone)}")
        return
    for zone in a.zones:
        for rec in wanted(zone):
            before = doh(rec["fqdn"])
            if any(expected_data(rec) == d for d in before):
                print(f"{rec['fqdn']:44s} present   {before[0]}"); continue
            if a.check:
                print(f"{rec['fqdn']:44s} MISSING   (answers: {before or 'none'})"); bad += 1; continue
            status = add_record(zone, rec)
            after = []
            for _ in range(6):
                time.sleep(3)
                after = doh(rec["fqdn"])
                if any(expected_data(rec) == d for d in after):
                    break
            ok = any(expected_data(rec) == d for d in after)
            bad += 0 if ok else 1
            print(f"{rec['fqdn']:44s} {'ADDED' if ok else 'FAILED':8s}  dashboard: {status}; DoH now: {after or 'none'}")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
