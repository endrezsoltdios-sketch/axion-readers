#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""traffic_read.py: who is really at each door, read back from the edge meter (people, verified bots, impersonators,
tools, headless browsers, monitors, our own scanners, probes), over the Workers Analytics Engine SQL API.

Why (22 Sep 2026): the old reader (agents_traffic.py) counted declared AI user-agents and JavaScript beacons, so a
headless scanner counted as a person and an undeclared script counted as nothing. The new meter
(edge/src/traffic_meter.js, classifier traffic_class.js) writes one classified data point for every
outside request; this reads them. Two people numbers are printed, and both are named for what they are:
  people (edge)      browser user-agent + browser navigation headers + not a hosting network, page requests only
  people (beacon)    the same class on the /e beacon: a page that also ran our JavaScript, the stricter count
An "own" row is your own readers and scanners, so your own audits never inflate a stranger count.

  python tools/traffic_read.py                       every door, last 30 days
  python tools/traffic_read.py --days 7 --host example.com
  python tools/traffic_read.py --paths --host example.com     top people pages and top bot paths
  python tools/traffic_read.py --by-day --host example.com    people and AI per day for one door
  python tools/traffic_read.py --json                write traffic_latest.json as well
  python tools/traffic_read.py --selftest

Token: CLOUDFLARE_API_TOKEN, read through tools/cf.py and never printed. CLOUDFLARE_ACCOUNT_ID names the
account, TRAFFIC_HOSTS the comma-separated column order (unset = the hosts found in the data), and
TRAFFIC_DATASET the dataset (default edge_traffic). The SQL API needs Account Analytics Read. Blob order is the meter's contract:
blob1 host, blob2 cls, blob3 operator, blob4 ver, blob5 pcls, blob6 method, blob7 status, blob8 country,
blob9 network, blob10 ua family, blob11 path, blob12 dc, blob13 browser evidence 0-5 (added 22 Sep 2026; older
points have no blob13, so nothing here may require it). Sampling: SUM(_sample_interval) restores true counts.
Every query groups by the blob columns themselves rather than by the SELECT alias, and the only value ever
interpolated into SQL is a host name that matched HOST_OK first.
"""
import argparse, datetime, json, os, pathlib, re, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import cf  # noqa: E402

DATASET = os.environ.get("TRAFFIC_DATASET", "edge_traffic")
HOSTS = [h.strip() for h in os.environ.get("TRAFFIC_HOSTS", "").split(",") if h.strip()]
OUT = ROOT / "traffic_latest.json"

HOST_OK = re.compile(r"^[a-z0-9][a-z0-9.-]{0,80}$")
CLS_OK = re.compile(r"^[a-z-]{1,16}$")

def literal(value, pattern, what):
    """The only values this reader ever puts inside a SQL string. Anything else stops the run here rather than
    reaching the API as a broken or widened query."""
    v = str(value).strip().lower()
    if not pattern.match(v):
        raise SystemExit(f"refusing to build SQL from an odd {what}: {value!r}")
    return v

def refusal_lines(code, reason, query, body):
    """What a refused query prints: the API's own words first, then the query that earned them. Pure, so the
    --selftest can prove the error text survives instead of being swallowed."""
    return [f"the SQL API refused the query with HTTP {code} {reason}",
            "  query : " + " ".join(str(query).split())[:300],
            "  answer: " + (str(body).strip()[:900] or "(no body)")]

def sql(query):
    """POST one query. A refusal is printed with the API's own words and returns no rows: a reader that dies on a
    traceback teaches nothing, and the SQL dialect is the thing most likely to be wrong here."""
    tok = cf.token(); acct = cf.account()
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{acct}/analytics_engine/sql",
                                 data=query.encode("utf-8"), headers={"authorization": f"Bearer {tok}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").strip()[:900] if hasattr(e, "read") else ""
        for line in refusal_lines(e.code, e.reason, query, detail):
            print(line)
        return {"data": [], "error": detail or str(e.code)}
    except urllib.error.URLError as e:
        print(f"the SQL API could not be reached: {e.reason}")
        return {"data": [], "error": str(e.reason)}
    try:
        return json.loads(body)
    except ValueError:
        print("the SQL API answered something that is not JSON:")
        print("  " + body.strip()[:900])
        return {"data": [], "error": body.strip()[:900]}

def q_by_class(days, host=None):
    where = f"timestamp > NOW() - INTERVAL '{int(days)}' DAY"
    if host:
        where += f" AND blob1 = '{literal(host, HOST_OK, 'host name')}'"
    return (f"SELECT blob1 AS host, blob2 AS cls, blob3 AS op, blob4 AS ver, blob5 AS pcls, SUM(_sample_interval) AS n "
            f"FROM {DATASET} WHERE {where} GROUP BY blob1, blob2, blob3, blob4, blob5 ORDER BY n DESC LIMIT 5000")

def q_top_paths(days, host, cls, pcls, limit=12):
    return (f"SELECT blob11 AS path, SUM(_sample_interval) AS n FROM {DATASET} "
            f"WHERE timestamp > NOW() - INTERVAL '{int(days)}' DAY AND blob1 = '{literal(host, HOST_OK, 'host name')}' "
            f"AND blob2 = '{literal(cls, CLS_OK, 'class')}' AND blob5 = '{literal(pcls, CLS_OK, 'path class')}' "
            f"GROUP BY blob11 ORDER BY n DESC LIMIT {int(limit)}")

def q_by_day(days, host):
    """toDate(timestamp) is the date function documented for Workers Analytics Engine SQL, which is why it is used
    here instead of toStartOfInterval. If the API rejects it, sql() prints the API's own error text and this query
    returns no rows: change the function here, do not guess at the data."""
    return (f"SELECT toDate(timestamp) AS day, blob2 AS cls, blob5 AS pcls, SUM(_sample_interval) AS n "
            f"FROM {DATASET} WHERE timestamp > NOW() - INTERVAL '{int(days)}' DAY "
            f"AND blob1 = '{literal(host, HOST_OK, 'host name')}' "
            f"GROUP BY toDate(timestamp), blob2, blob5 ORDER BY day LIMIT 5000")

def rows_by_class(days, host=None):
    return sql(q_by_class(days, host)).get("data", [])

def top_paths(days, host, cls, pcls, limit=12):
    return sql(q_top_paths(days, host, cls, pcls, limit)).get("data", [])

def summarise(rows):
    """rows -> {host: {metric: n}} with the metrics the report prints. Pure; tested by --selftest."""
    out = {}
    for r in rows:
        h = out.setdefault(r["host"], {})
        n = int(r["n"]); cls, ver, pcls, op = r["cls"], r["ver"], r["pcls"], r["op"]
        def add(k, v=n): h[k] = h.get(k, 0) + v
        add("requests")
        if cls == "person" and pcls == "page": add("people_pages")
        if cls == "person" and pcls == "beacon": add("people_beacon")
        if cls == "person-dc" and pcls == "page": add("browser_from_hosting")
        if cls == "headless": add("headless")
        if cls == "ai":
            add("ai"); add("ai_" + {"v": "verified", "i": "impersonated", "c": "cf_verified", "n": "unproven"}.get(ver, "unproven"))
            if pcls in ("api", "machine"): add("ai_on_machine_doors")
            h.setdefault("ai_ops", {}); h["ai_ops"][op] = h["ai_ops"].get(op, 0) + n
        if cls == "crawler":
            add("crawlers"); add("crawlers_" + {"v": "verified", "i": "impersonated", "c": "cf_verified", "n": "unproven"}.get(ver, "unproven"))
            h.setdefault("crawler_ops", {}); h["crawler_ops"][op] = h["crawler_ops"].get(op, 0) + n
        if cls == "tool": add("tools")
        if cls == "monitor": add("monitors")
        if cls == "own": add("own")
        if cls in ("other-bot", "empty"): add("other_bots")
        if pcls == "probe": add("probes")
    return out

def by_day(rows):
    """rows of {day, cls, pcls, n} -> [(day, {metric: n})] in date order. Pure; tested by --selftest.
    'people' is the same rule the table above prints as people (edge): class person on a page request."""
    days = {}
    for r in rows:
        day = str(r.get("day") or "")[:10]
        n = int(r["n"]); cls = r.get("cls") or ""; pcls = r.get("pcls") or ""
        m = days.setdefault(day, {"people": 0, "people_beacon": 0, "ai": 0, "crawlers": 0, "headless": 0, "requests": 0})
        m["requests"] += n
        if cls == "person" and pcls == "page": m["people"] += n
        if cls == "person" and pcls == "beacon": m["people_beacon"] += n
        if cls == "ai": m["ai"] += n
        if cls == "crawler": m["crawlers"] += n
        if cls == "headless": m["headless"] += n
    return sorted(days.items())

DAY_COLS = [("people", "people"), ("people_beacon", "beacon"), ("ai", "AI"), ("crawlers", "crawlers"),
            ("headless", "headless"), ("requests", "requests")]

def report_by_day(host, rows, days):
    table = by_day(rows)
    print(f"{host}: per day, last {days} days. people = a browser user-agent with browser navigation evidence on a page request")
    print(f"{'day':12s}" + "".join(f"{label:>11s}" for _, label in DAY_COLS))
    for day, m in table:
        print(f"{day:12s}" + "".join(f"{m[key]:>11,d}" for key, _ in DAY_COLS))
    if not table:
        print("  (no rows; either nothing was metered or the query above was refused)")
    return table

METRICS = [("people_pages", "people, page views (edge)"), ("people_beacon", "people, beacon-confirmed"),
           ("browser_from_hosting", "browser UA from hosting nets"), ("headless", "headless browsers"),
           ("ai", "declared AI agents"), ("ai_verified", "  verified by operator address"), ("ai_cf_verified", "  verified by Cloudflare"),
           ("ai_impersonated", "  IMPERSONATED"), ("ai_unproven", "  unproven"), ("ai_on_machine_doors", "  on api or machine doors"),
           ("crawlers", "search and social crawlers"), ("crawlers_verified", "  verified by operator address"), ("crawlers_impersonated", "  IMPERSONATED"),
           ("tools", "HTTP tools and libraries"), ("monitors", "uptime monitors"), ("own", "our own readers and scanners"),
           ("other_bots", "other or empty user-agent"), ("probes", "probe paths (wp-login, dotfiles ...)"), ("requests", "all requests")]

def report(summary, days, hosts):
    print(f"WHO IS AT THE DOOR, last {days} days, read {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print("classes from edge/src/traffic_class.js; a declared bot is verified against its operator's own published address blocks\n")
    head = f"{'':34s}" + "".join(f"{h[:16]:>17s}" for h in hosts)
    print(head)
    for key, label in METRICS:
        print(f"{label:34s}" + "".join(f"{summary.get(h, {}).get(key, 0):>17,d}" for h in hosts))
    print()
    for h in hosts:
        s = summary.get(h, {})
        ops = sorted((s.get("ai_ops") or {}).items(), key=lambda x: -x[1])[:8]
        if ops:
            print(f"{h:22s} AI operators: " + ", ".join(f"{o} {n:,d}" for o, n in ops))
    print("\nHonesty: 'people (edge)' is a browser string with browser navigation headers from a non-hosting network; it can still be a\n"
          "well-built bot. 'beacon-confirmed' also ran our page script. Neither is a session count; both are page requests.\n"
          "'IMPERSONATED' means the user-agent names an operator whose published address blocks do not contain the sender.")

def selftest():
    rows = [
        {"host": "x.com", "cls": "person", "op": "", "ver": "-", "pcls": "page", "n": "10"},
        {"host": "x.com", "cls": "person", "op": "", "ver": "-", "pcls": "beacon", "n": "4"},
        {"host": "x.com", "cls": "ai", "op": "openai-gptbot", "ver": "v", "pcls": "api", "n": "7"},
        {"host": "x.com", "cls": "ai", "op": "openai-gptbot", "ver": "i", "pcls": "page", "n": "2"},
        {"host": "x.com", "cls": "crawler", "op": "googlebot", "ver": "v", "pcls": "page", "n": "3"},
        {"host": "x.com", "cls": "own", "op": "own", "ver": "-", "pcls": "page", "n": "5"},
        {"host": "x.com", "cls": "tool", "op": "curl", "ver": "-", "pcls": "probe", "n": "1"},
    ]
    s = summarise(rows)["x.com"]
    assert s["people_pages"] == 10 and s["people_beacon"] == 4 and s["ai"] == 9 and s["ai_verified"] == 7 and s["ai_impersonated"] == 2
    assert s["ai_on_machine_doors"] == 7 and s["crawlers_verified"] == 3 and s["own"] == 5 and s["tools"] == 1 and s["probes"] == 1 and s["requests"] == 32
    assert s["ai_ops"] == {"openai-gptbot": 9}
    # headless is counted apart from people: a browser string without a browser's headers is not a page view
    hl = summarise([{"host": "x.com", "cls": "headless", "op": "", "ver": "-", "pcls": "page", "n": "11"}])["x.com"]
    assert hl["headless"] == 11 and "people_pages" not in hl and hl["requests"] == 11

    # --by-day, out of order on purpose: the reader sorts, the API is not trusted to
    day_rows = [{"day": "2026-09-21", "cls": "ai", "pcls": "machine", "n": "6"},
                {"day": "2026-09-20", "cls": "person", "pcls": "page", "n": "3"},
                {"day": "2026-09-20", "cls": "person", "pcls": "beacon", "n": "2"},
                {"day": "2026-09-20", "cls": "headless", "pcls": "page", "n": "9"},
                {"day": "2026-09-21 00:00:00", "cls": "person", "pcls": "page", "n": "4"},
                {"day": "2026-09-20", "cls": "crawler", "pcls": "page", "n": "1"}]
    d = dict(by_day(day_rows))
    assert [k for k, _ in by_day(day_rows)] == ["2026-09-20", "2026-09-21"], by_day(day_rows)
    assert d["2026-09-20"] == {"people": 3, "people_beacon": 2, "ai": 0, "crawlers": 1, "headless": 9, "requests": 15}, d["2026-09-20"]
    assert d["2026-09-21"]["people"] == 4 and d["2026-09-21"]["ai"] == 6, d["2026-09-21"]
    assert by_day([]) == []

    # SQL: grouped by the blob columns, not by the SELECT alias, and every blob read is one the meter writes
    q = q_by_class(30, "example.com")
    assert "GROUP BY blob1, blob2, blob3, blob4, blob5" in q and "blob1 = 'example.com'" in q, q
    assert "NOW() - INTERVAL '30' DAY" in q and "SUM(_sample_interval)" in q, q
    assert "GROUP BY blob11" in q_top_paths(7, "x.co", "person", "page")
    assert "toDate(timestamp)" in q_by_day(7, "x.co") and "GROUP BY toDate(timestamp), blob2, blob5" in q_by_day(7, "x.co")
    for blob in re.findall(r"blob(\d+)", q + q_top_paths(7, "x.co", "ai", "api") + q_by_day(7, "x.co")):
        assert 1 <= int(blob) <= 13, f"blob{blob} is outside the meter's contract"

    # a host name is the only thing interpolated, and it has to look like one
    for bad in ["x.com' OR '1'='1", "x com", "", "-- drop", "X.COM/evil"]:
        try:
            literal(bad, HOST_OK, "host name")
            raise AssertionError(f"accepted a bad host: {bad!r}")
        except SystemExit:
            pass
    assert literal("Example.com", HOST_OK, "host name") == "example.com"

    # a refused query prints what the API said, including the name of a function it does not have
    lines = refusal_lines(400, "Bad Request", q_by_day(7, "x.co"), "Unknown function toDate")
    assert "Unknown function toDate" in lines[2] and "HTTP 400 Bad Request" in lines[0] and "toDate(timestamp)" in lines[1], lines
    assert refusal_lines(500, "x", "SELECT 1", "")[2].endswith("(no body)")
    print("selftest OK: 10 checks")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--host")
    ap.add_argument("--paths", action="store_true")
    ap.add_argument("--by-day", action="store_true", help="people and AI per day for one host (needs --host)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.by_day:
        if not a.host:
            raise SystemExit("--by-day reads one door: add --host " + HOSTS[0])
        report_by_day(a.host, sql(q_by_day(a.days, a.host)).get("data", []), a.days)
        return
    rows = rows_by_class(a.days, a.host)
    hosts = [a.host] if a.host else HOSTS
    summary = summarise(rows)
    for h in [r["host"] for r in rows]:
        if h not in hosts: hosts.append(h)
    if not rows:
        print(f"no data points in the last {a.days} days (dataset {DATASET}); the meter writes only after your Workers deploy with the TRAFFIC binding")
    report(summary, a.days, hosts)
    if a.paths:
        for h in hosts:
            print(f"\n{h}: top people pages")
            for r in top_paths(a.days, h, "person", "page"): print(f"  {int(r['n']):7,d}  {r['path']}")
            print(f"{h}: top AI-agent paths")
            for cls in ("ai",):
                for pcls in ("api", "machine", "page"):
                    for r in top_paths(a.days, h, cls, pcls, 6): print(f"  {int(r['n']):7,d}  {pcls:8s} {r['path']}")
    if a.json:
        OUT.write_text(json.dumps({"read": datetime.datetime.now(datetime.timezone.utc).isoformat(), "days": a.days, "summary": summary}, indent=1), encoding="utf-8")
        print("wrote", OUT.relative_to(ROOT))

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
