#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""cf_readiness.py: read Cloudflare's Agent Readiness diagnostic for every zone you hold, through a Chrome
started with --remote-debugging-port=9226 and logged into the dashboard, and write one record you can diff.

Why (22 Sep 2026): Cloudflare scores every zone on 21 named doors in four
levels (Quick Wins, Technical Groundwork, Advanced Integration, Commerce). The dashboard has no API for it and
the page is a click-per-item accordion, so a person reading ten zones by hand is an hour of clicking and no
record. This reads the same page the same way, once per zone, and keeps the answer in a file.

What it reads, per zone: the pass mark on each of the 21 items (the icon class Cloudflare paints: badge-green =
pass, subtle = not found), and the one-line Status Cloudflare prints under the item once it is opened (the
reason, e.g. "auth.md exists but OAuth Protected Resource Metadata was not found"). Nothing is clicked except
the accordion rows and the Rescan button when --rescan is given; nothing is typed; no setting changes.

  python tools/cf_readiness.py                 all zones the API token can see (names only from the API)
  python tools/cf_readiness.py example.com example.org
  python tools/cf_readiness.py --rescan        press Rescan first, wait, then read (slower)
  python tools/cf_readiness.py --selftest

Needs: CLOUDFLARE_ACCOUNT_ID, a Chrome on --remote-debugging-port=9226 already logged into the dashboard, and
playwright. CLOUDFLARE_API_TOKEN (Zone Read) only when you name no zones and want them listed for you.
Writes cf_readiness_latest.json (machine) and cf_readiness_<date>.md (a zone-by-item table with
every Status line), and prints one row per zone. Exit 2 when the Chrome is not up or shows the login wall.
"""
import argparse, datetime, json, os, pathlib, re, sys, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASH = "https://dash.cloudflare.com"


def account():
    """The Cloudflare account id from your dashboard URL. No default: a wrong id reads another account."""
    a = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not a:
        sys.exit("set CLOUDFLARE_ACCOUNT_ID (the id in your https://dash.cloudflare.com/<id>/ URL)")
    return a
LEVELS = {
    "Quick Wins": ["robots.txt", "Sitemap", "AI Crawler Rules", "Content Signals", "Markdown Negotiation"],
    "Technical Groundwork": ["Auth.md", "API Catalog", "Link Headers"],
    "Advanced Integration": ["OAuth Discovery", "OAuth Protected Resource", "A2A Agent Card", "Web Bot Auth",
                             "DNS-AID", "Skills Index", "MCP Server Card", "WebMCP"],
    "Commerce": ["ACP", "AP2 Protocol", "MPP", "UCP", "x402 Protocol"],
}
ITEMS = [(lvl, it) for lvl, its in LEVELS.items() for it in its]

JS_MARKS = """(names)=>{const leaves=[...document.querySelectorAll('*')].filter(e=>e.children.length===0&&!e.closest('nav')&&!e.closest('a[href]'));
return names.map(nm=>{const el=leaves.find(e=>e.textContent.trim()===nm); if(!el) return [nm,'MISSING'];
let n=el; for(let i=0;i<6;i++){n=n.parentElement; if(n.querySelector('svg')) break;}
const s=n.querySelector('svg'); return [nm, s?(s.getAttribute('class')||''):'nosvg']})}"""
JS_EXPAND = """()=>{const b=[...document.querySelectorAll('[aria-expanded="false"]')]; b.forEach(x=>x.click()); return b.length}"""
JS_OPEN = """(nm)=>{const leaves=[...document.querySelectorAll('*')].filter(e=>e.children.length===0&&!e.closest('nav')&&!e.closest('a[href]'));
const el=leaves.find(e=>e.textContent.trim()===nm); if(!el) return false; let n=el, hit=null;
/* the item ROW is the nearest clickable ancestor whose own text is short; the level header above it is long */
for(let i=0;i<8;i++){n=n.parentElement; if(!n) break; if(n.innerText.length>300) break;
if(n.matches('button,[role=button],summary,[aria-expanded]')) {hit=n; break;}}
(hit||el).click(); return !!hit}"""


def classify(cls):
    """Cloudflare's pass mark is the icon colour class; anything else is 'not found' or unknown."""
    if cls == "MISSING":
        return "absent"
    if "badge-green" in cls:
        return "pass"
    if "subtle" in cls:
        return "fail"
    return "unknown"


def status_line(text, name):
    """The one-line Status under an opened item: 'Status\\n<line>' after the item name."""
    i = text.find("\n" + name + "\n")
    if i < 0:
        return ""
    j = text.find("\nStatus\n", i)
    if j < 0 or j - i > 1200:
        return ""
    k = j + len("\nStatus\n")
    return text[k:text.find("\n", k)].strip()


def counts(items):
    out = {}
    for lvl, its in LEVELS.items():
        rows = [x for x in items if x["level"] == lvl]
        out[lvl] = {"pass": sum(1 for x in rows if x["pass"] == "pass"), "of": len(its)}
    return out


def zone_names():
    """Zone names from Cloudflare's API, so a run over everything you hold needs no typed list."""
    tok = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not tok:
        sys.exit("name the zones on the command line, or set CLOUDFLARE_API_TOKEN (Zone Read) to list them")
    req = urllib.request.Request("https://api.cloudflare.com/client/v4/zones?per_page=50",
                                 headers={"authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        z = json.loads(r.read().decode("utf-8"))
    return sorted(r["name"] for r in z.get("result", []))


def read_zone(page, zone, rescan=False):
    url = "%s/%s/%s/agent-readiness/diagnostics" % (DASH, account(), zone)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    for _ in range(30):
        time.sleep(1)
        t = page.evaluate("document.body.innerText")
        if "Improve your domain" in t:
            break
    else:
        # a zone switch passes through /login/google for a second or two; only a wall that STAYS is a wall
        if "/login" in page.url or "Manage Your Account" in page.title():
            return {"zone": zone, "error": "login wall"}
        return {"zone": zone, "error": "diagnostics did not render", "url": page.url, "text_head": t[:200]}
    if rescan:
        try:
            page.get_by_role("button", name="Rescan").first.click(timeout=5000)
            time.sleep(20)
        except Exception:
            pass
    page.evaluate(JS_EXPAND)
    for _ in range(20):   # the item rows render after the level headers
        time.sleep(1)
        if "\nrobots.txt\n" in page.evaluate("document.body.innerText"):
            break
    items = []
    for lvl, it in ITEMS:
        if not page.url.endswith("/agent-readiness/diagnostics"):   # a stray click can navigate; come back
            page.goto(url, wait_until="domcontentloaded", timeout=30000); time.sleep(4); page.evaluate(JS_EXPAND); time.sleep(1.5)
        page.evaluate(JS_OPEN, it); time.sleep(0.8)
        mark = dict(page.evaluate(JS_MARKS, [it])).get(it, "MISSING")
        if mark == "MISSING":   # a level folded; open every level again and retry once
            page.evaluate(JS_EXPAND); time.sleep(1.2); page.evaluate(JS_OPEN, it); time.sleep(0.8)
            mark = dict(page.evaluate(JS_MARKS, [it])).get(it, "MISSING")
        t = page.evaluate("document.body.innerText")
        items.append({"level": lvl, "name": it, "pass": classify(mark), "mark": mark, "status": status_line(t, it)})
    m = re.search(r"Last scanned ([^\n]+)", page.evaluate("document.body.innerText"))
    return {"zone": zone, "url": url, "last_scanned": m.group(1).strip() if m else "", "counts": counts(items), "items": items}


def selftest():
    n = 0
    assert classify("shrink-0 text-kumo-badge-green") == "pass"; n += 1
    assert classify("text-kumo-subtle shrink-0") == "fail"; n += 1
    assert classify("MISSING") == "absent" and classify("nosvg") == "unknown"; n += 1
    t = "x\nAuth.md\nReadiness\nA markdown file.\nStatus\nauth.md exists but OAuth was not found\nCopy Agent Prompt\n"
    assert status_line(t, "Auth.md") == "auth.md exists but OAuth was not found"; n += 1
    assert status_line(t, "Sitemap") == ""; n += 1
    assert status_line("x\nMPP\nno status here\n" + "y\n" * 700 + "\nStatus\nfar away\n", "MPP") == ""; n += 1
    items = [{"level": "Commerce", "name": it, "pass": "fail"} for it in LEVELS["Commerce"]]
    items += [{"level": "Quick Wins", "name": it, "pass": "pass"} for it in LEVELS["Quick Wins"]]
    c = counts(items); assert c["Commerce"] == {"pass": 0, "of": 5} and c["Quick Wins"] == {"pass": 5, "of": 5}; n += 1
    assert len(ITEMS) == 21; n += 1
    print("selftest OK: %d checks" % n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zones", nargs="*")
    ap.add_argument("--cdp", default="http://127.0.0.1:9226")
    ap.add_argument("--rescan", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    from playwright.sync_api import sync_playwright
    zones = a.zones or zone_names()
    day = datetime.date.today().isoformat()
    rec = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "account": account(), "zones": {}}
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(a.cdp, timeout=8000)
        except Exception as e:
            print("no Chrome at %s (%s); start Chrome with --remote-debugging-port=9226 and log into the dashboard" % (a.cdp, str(e)[:80])); sys.exit(2)
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "dash.cloudflare.com" in pg.url), None) or ctx.new_page()
        for z in zones:
            r = read_zone(page, z, rescan=a.rescan)
            rec["zones"][z] = r
            if "error" in r:
                print("%-28s %s" % (z, r["error"]))
                if r["error"] == "login wall":
                    sys.exit(2)
                continue
            c = r["counts"]
            print("%-28s quick %d/%d  groundwork %d/%d  advanced %d/%d  commerce %d/%d  (scanned %s)" % (
                z, c["Quick Wins"]["pass"], 5, c["Technical Groundwork"]["pass"], 3, c["Advanced Integration"]["pass"], 8,
                c["Commerce"]["pass"], 5, r["last_scanned"]))
    (ROOT / "cf_readiness_latest.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    lines = ["# Cloudflare Agent Readiness, every zone, read %s through the dashboard (tools/cf_readiness.py)" % day, "",
             "pass = Cloudflare paints the green mark; fail = its grey one; the Status column is Cloudflare's own line under the item.", ""]
    for z, r in rec["zones"].items():
        lines.append("## %s" % z)
        if "error" in r:
            lines += ["", "ERROR: " + r["error"], ""]; continue
        c = r["counts"]
        lines += ["", "Quick Wins %d/5, Technical Groundwork %d/3, Advanced Integration %d/8, Commerce %d/5 (optional). Last scanned %s." % (
            c["Quick Wins"]["pass"], c["Technical Groundwork"]["pass"], c["Advanced Integration"]["pass"], c["Commerce"]["pass"], r["last_scanned"]),
            "", "| Level | Item | Mark | Status |", "|---|---|---|---|"]
        for it in r["items"]:
            lines.append("| %s | %s | %s | %s |" % (it["level"], it["name"], it["pass"], it["status"].replace("|", "/")))
        lines.append("")
    (ROOT / ("cf_readiness_%s.md" % day)).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("wrote cf_readiness_latest.json and cf_readiness_%s.md" % day)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
