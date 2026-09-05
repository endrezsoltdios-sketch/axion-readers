#!/usr/bin/env python3
"""cf.py -- Axion's own Cloudflare connector (Fig Tree primitive, 6 Sep 2026).

Why: the Cloudflare MCP servers need an OAuth flow this session cannot run, and the
dashboard is a human door. The account token wrangler already uses answers the REST
and GraphQL APIs directly, so every number the dashboard shows is one call away.
Stdlib only. Token: CLOUDFLARE_API_TOKEN from the environment, else the credentials
file via scripts/sales.py env_key(). Never printed.

  py scripts/cf.py verify                          token status + account id
  py scripts/cf.py workers                         every Worker script on the account
  py scripts/cf.py zones                           every zone (domain) on the account
  py scripts/cf.py traffic [--days 7] [--top 20]   requests + errors per Worker (GraphQL)
  py scripts/cf.py zone-traffic [--days 7]         requests / bytes / threats per zone (GraphQL)
  py scripts/cf.py kv-list <namespace-title>       key list via wrangler (token cannot read KV: API 10000)
  py scripts/cf.py tail <script> [--seconds 20]    live log lines via wrangler tail
Add --json to any command for machine output. Exit 0 ok / 1 API error / 2 no token.

Measured on 6 Sep 2026: this token can list workers + zones and query GraphQL
analytics; it CANNOT read KV (10000 Authentication error) or create namespaces --
kv-list therefore shells out to wrangler, which uses the same token but the
namespace binding path. What the token cannot do is printed as CONFIGURED-OUT,
never as a traceback.
"""
import argparse, json, os, subprocess, sys, urllib.request, urllib.error
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.cloudflare.com/client/v4/"
_ACCOUNT = None


def account():
    """CLOUDFLARE_ACCOUNT_ID if set, else the first account the token can see."""
    global _ACCOUNT
    if _ACCOUNT:
        return _ACCOUNT
    _ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not _ACCOUNT:
        st, d = call("accounts?per_page=5")
        if st != 200 or not d.get("result"):
            fail(st, d)
        _ACCOUNT = d["result"][0]["id"]
    return _ACCOUNT


def token():
    t = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not t:
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import sales
            t = sales.env_key("CLOUDFLARE_API_TOKEN")
        except Exception:
            t = None
    if not t:
        print("[no token] CLOUDFLARE_API_TOKEN absent from environment and credentials file")
        sys.exit(2)
    return t


def call(path, body=None, tok=None):
    H = {"Authorization": "Bearer " + (tok or token()), "Content-Type": "application/json"}
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"errors": [{"message": str(e)}]}


def fail(st, d):
    msg = (d.get("errors") or [{"message": "unknown"}])[0].get("message")
    print("[api %s] %s" % (st, msg))
    sys.exit(1)


def graphql(query):
    st, d = call("graphql", {"query": query})
    if st != 200 or d.get("errors"):
        fail(st, {"errors": d.get("errors") or [{"message": "http %s" % st}]})
    return d["data"]["viewer"]["accounts"][0]


def table(rows, headers):
    if not rows:
        print("(none)"); return
    w = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(str(h).ljust(w[i]) for i, h in enumerate(headers)))
    for r in rows:
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))


def cmd_verify(a):
    st, d = call("user/tokens/verify")
    if st != 200: fail(st, d)
    out = {"status": d["result"]["status"], "account": account(), "expires_on": d["result"].get("expires_on")}
    print(json.dumps(out) if a.json else "token %s | account %s | expires %s" % (out["status"], out["account"], out["expires_on"] or "never"))


def cmd_workers(a):
    st, d = call("accounts/%s/workers/scripts" % account())
    if st != 200: fail(st, d)
    rows = [(s["id"], (s.get("modified_on") or "")[:10]) for s in d["result"]]
    rows.sort(key=lambda r: r[1], reverse=True)
    print(json.dumps(rows) if a.json else None) if a.json else table(rows, ["worker", "modified"])
    if not a.json: print("%d workers" % len(rows))


def cmd_zones(a):
    st, d = call("zones?per_page=50")
    if st != 200: fail(st, d)
    rows = [(z["name"], z["status"], z["id"]) for z in d["result"]]
    print(json.dumps(rows)) if a.json else table(rows, ["zone", "status", "zone_id"])


def cmd_traffic(a):
    since = (date.today() - timedelta(days=a.days)).isoformat()
    q = ('{ viewer { accounts(filter:{accountTag:"%s"}) { workersInvocationsAdaptive(limit:200, '
         'filter:{date_geq:"%s"}) { sum { requests errors subrequests } dimensions { scriptName } } } } }') % (account(), since)
    rows = {}
    for x in graphql(q)["workersInvocationsAdaptive"]:
        n = x["dimensions"]["scriptName"]; r = rows.setdefault(n, [0, 0, 0])
        r[0] += x["sum"]["requests"]; r[1] += x["sum"]["errors"]; r[2] += x["sum"]["subrequests"]
    out = sorted(([k] + v for k, v in rows.items()), key=lambda r: -r[1])[: a.top]
    if a.json:
        print(json.dumps({"since": since, "rows": [dict(zip(["worker", "requests", "errors", "subrequests"], r)) for r in out]}))
    else:
        table(out, ["worker", "requests", "errors", "subrequests"])
        print("since %s (%d days) | %d workers with traffic | total %s requests" % (
            since, a.days, len(rows), format(sum(v[0] for v in rows.values()), ",")))


def cmd_zone_traffic(a):
    since = (date.today() - timedelta(days=a.days)).isoformat()
    st, d = call("zones?per_page=50")
    if st != 200: fail(st, d)
    rows = []
    for z in d["result"]:
        q = ('{ viewer { zones(filter:{zoneTag:"%s"}) { httpRequests1dGroups(limit:60, filter:{date_geq:"%s"}) '
             '{ sum { requests bytes threats cachedRequests } } } } }') % (z["id"], since)
        st2, d2 = call("graphql", {"query": q})
        if st2 != 200 or d2.get("errors"):
            msg = (d2.get("errors") or [{"message": "http %s" % st2}])[0]["message"]
            msg = "token lacks Zone Analytics:Read" if "not authorized" in msg or "Actor" in msg else msg[:60]
            rows.append((z["name"], "CONFIGURED-OUT", msg, "", "")); continue
        g = d2["data"]["viewer"]["zones"][0]["httpRequests1dGroups"]
        s = [sum(x["sum"][k] for x in g) for k in ("requests", "bytes", "threats", "cachedRequests")]
        rows.append((z["name"], s[0], "%.1f MB" % (s[1] / 1e6), s[2], s[3]))
    rows.sort(key=lambda r: -(r[1] if isinstance(r[1], int) else -1))
    print(json.dumps(rows)) if a.json else table(rows, ["zone", "requests", "bytes", "threats", "cached"])
    if not a.json: print("since %s (%d days)" % (since, a.days))


def wrangler(args, cwd=None, timeout=90):
    exe = "npx.cmd" if os.name == "nt" else "npx"
    return subprocess.run([exe, "wrangler", *args], cwd=cwd or ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def cmd_kv_list(a):
    r = wrangler(["kv", "namespace", "list"])
    try:
        ns = json.loads(r.stdout[r.stdout.find("["):])
    except Exception:
        print("[wrangler] could not list namespaces: %s" % (r.stderr or r.stdout)[-300:]); sys.exit(1)
    match = [n for n in ns if n["title"].lower() == a.namespace.lower() or a.namespace in n["title"]]
    if not match:
        print("[no namespace] %r; have: %s" % (a.namespace, ", ".join(n["title"] for n in ns))); sys.exit(1)
    r = wrangler(["kv", "key", "list", "--namespace-id", match[0]["id"], "--prefix", a.prefix or ""])
    try:
        keys = json.loads(r.stdout[r.stdout.find("["):])
    except Exception:
        print("[wrangler] %s" % (r.stderr or r.stdout)[-300:]); sys.exit(1)
    print(json.dumps(keys) if a.json else "\n".join(k["name"] for k in keys) + "\n%d keys in %s" % (len(keys), match[0]["title"]))


def cmd_tail(a):
    try:
        r = wrangler(["tail", a.script, "--format", "json"], timeout=a.seconds)
        out = r.stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
    lines = [l for l in out.splitlines() if l.startswith("{")]
    for l in lines[-200:]:
        try:
            ev = json.loads(l); print(ev.get("event", {}).get("request", {}).get("url", ""), ev.get("outcome"), [x.get("message") for x in ev.get("logs", [])])
        except Exception:
            print(l[:200])
    print("%d events in %ds" % (len(lines), a.seconds))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify"); sub.add_parser("workers"); sub.add_parser("zones")
    t = sub.add_parser("traffic"); t.add_argument("--days", type=int, default=7); t.add_argument("--top", type=int, default=50)
    z = sub.add_parser("zone-traffic"); z.add_argument("--days", type=int, default=7)
    k = sub.add_parser("kv-list"); k.add_argument("namespace"); k.add_argument("--prefix", default="")
    tl = sub.add_parser("tail"); tl.add_argument("script"); tl.add_argument("--seconds", type=int, default=20)
    a = p.parse_args()
    {"verify": cmd_verify, "workers": cmd_workers, "zones": cmd_zones, "traffic": cmd_traffic,
     "zone-traffic": cmd_zone_traffic, "kv-list": cmd_kv_list, "tail": cmd_tail}[a.cmd](a)


if __name__ == "__main__":
    main()
