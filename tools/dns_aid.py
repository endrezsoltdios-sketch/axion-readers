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

  python tools/dns_aid.py example.com example.org        add what is missing, verify, print one row per record
  python tools/dns_aid.py --check example.com                   DoH read only, no dashboard
  python tools/dns_aid.py --dnssec example.com                  press Enable DNSSEC on the zone's DNS settings page (the
                                                                   draft requires signed zones; on a Cloudflare Registrar
                                                                   domain the DS record is added by Cloudflare itself)
  python tools/dns_aid.py --selftest

Needs: CLOUDFLARE_ACCOUNT_ID, and a Chrome started with --remote-debugging-port=9226 already logged into the
dashboard (tools/cfdash.py drives it). MCP_HOST sets the _mcp target; it defaults to mcp.example.com.
Exit 0 when every wanted record answers over DoH afterwards; 1 otherwise; 2 when the Chrome is not up.
"""
import argparse, json, os, pathlib, subprocess, sys, tempfile, time, urllib.request

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

def selftest():
    r = wanted("example.org")
    assert r[0]["fqdn"] == "_a2a._agents.example.org" and r[1]["target"] == MCP_HOST
    assert expected_data(r[0]) == "1 example.org. alpn=a2a port=443"
    assert expected_data(r[1]) == "1 %s. alpn=mcp port=443" % MCP_HOST
    print("selftest OK: 3 checks")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zones", nargs="*")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dnssec", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    bad = 0
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
