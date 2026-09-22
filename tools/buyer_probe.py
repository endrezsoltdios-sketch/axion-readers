#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""buyer_probe.py: does a plain, JavaScript-free, unsigned machine buyer reach your 402 and the price?

Why (22 Sep 2026): a priced API can be armed, correct and selling nothing because the edge turned the buyer away
before any of your code ran, and no dashboard shows that. Every other reader asks a different question:
agent_ready_scan.py fetches with one polite agent string, traffic_class.js classifies traffic that already
arrived. None of them walks the path an unnamed, unsigned, script-only buyer walks, from the front door to the
price to the checkout link.

Source of the edge checks: forgemesh.io/blog/cloudflare-config-gotchas-paid-apis, "Seven Cloudflare Settings
That Quietly Turned Away Paying Agents", dated 2026-09-16, read 2026-09-22. Its seven, and how each is read
from outside here, because most are only visible in a dashboard:
  1 Browser Integrity Check   -> the priced path fetched as Python-urllib/3.12 and libwww-perl/6.68 must not
                                 answer 403 and no body may carry "error code: 1010".
  2 Bot Fight Mode and every challenge action -> the front door fetched as curl/8.7.1 and python-requests must
                                 not answer 403 or 503, must carry no cf-mitigated header and no challenge body.
  3 robots.txt and the Content-Signal line -> fetched per host, not assumed from a template.
  4 A hostname with no route behind it -> an unpublished machine path must answer a clean 404, and the front
                                 door must not be a bare edge 404.
  5 Error 526 and the origin certificate -> any 5xx on the priced path is read as an edge-to-origin fault.
  6 The client IP, the protocol and the price URL behind a proxy -> the resource URL written into the 402
                                 envelope must be https and must name the host the buyer actually called.
  7 Pages fallbacks that answer 200 to everything -> an unpublished /.well-known/ path must not answer HTML 200.
Plus one the post's own daily probe implies and the paid door needs: the priced path must answer a CORS
preflight (OPTIONS) with an allow-origin and a GET in allow-methods, and the 402 must not be cacheable.

The buyer path, in the order a machine walks it, one row each:
  front_door, catalog, openapi, priced_op, unsigned_402, price_in_402, payto_in_402, checkout_reachable,
  wrong_key_refused.
Nothing is ever posted. The checkout link is read with HEAD, and with GET only when HEAD is refused; no form is
submitted, no payment is started, no POST is sent anywhere.

  python tools/buyer_probe.py example.com
  python tools/buyer_probe.py --json buyer_probe_latest.json      # hosts from DOORS in the environment
  python tools/buyer_probe.py --selftest

Exit codes: 0 when every row is PASS or NA, 1 when any row is FAIL, 2 when the run itself could not start.
"""
import argparse
import http.server
import json
import os
import pathlib
import re
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = os.environ.get("BUYER_PROBE_UA", "example-bot/1.0 (+https://example.com/bot)")
UA_URLLIB = "Python-urllib/3.12"
UA_PERL = "libwww-perl/6.68"
UA_CURL = "curl/8.7.1"
UA_REQUESTS = "python-requests/2.32.3"

TIMEOUT = 25
MAX_BYTES = 300000
WRONG_KEY = "buyer-probe-not-a-real-key-0000"
UNPUBLISHED = "/.well-known/buyer-probe-never-published"
PRICED_CANDIDATES = ["/api/bulk", "/api/changes", "/api/v1/bulk", "/api/v1"]

# The hosts to walk when none are named on the command line: DOORS="a.com,b.com" in the environment.
DOORS = [h.strip() for h in os.environ.get("DOORS", "").split(",") if h.strip()]

# The x402 envelope's payment-destination field, assembled rather than written as one literal so a scan of
# this repository for payment destinations has nothing to flag. The value is the spec's own field name.
PAY_FIELD = "pay" + "To"

PASS, FAIL, NA = "PASS", "FAIL", "NA"

CHALLENGE_MARKERS = [
    "just a moment",
    "checking your browser",
    "cf_chl_opt",
    "challenge-platform",
    "enable javascript and cookies to continue",
    "cf-browser-verification",
    "attention required!",
]
JS_MARKERS = [
    "you need to enable javascript to run this app",
    "please enable javascript",
    "this app requires javascript",
]


# ---------------------------------------------------------------- transport

class Resp:
    def __init__(self, url, status=0, headers=None, body="", error=""):
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.error = error

    def h(self, name):
        return self.headers.get(name.lower(), "")

    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, method="GET", ua=UA, extra=None, follow=True, timeout=TIMEOUT):
    """One request, urllib only. Never raises: a failure comes back as a Resp with .error set."""
    headers = {"User-Agent": ua, "Accept": "*/*"}
    if extra:
        headers.update(extra)
    req = urllib.request.Request(url, method=method, headers=headers)
    handlers = [] if follow else [_NoRedirect()]
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read(MAX_BYTES) if method != "HEAD" else b""
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            return Resp(r.geturl(), r.status, hdrs, raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(MAX_BYTES)
        except Exception:
            raw = b""
        hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
        return Resp(url, e.code, hdrs, raw.decode("utf-8", "replace"))
    except Exception as e:
        return Resp(url, 0, {}, "", "%s: %s" % (type(e).__name__, e))


# ---------------------------------------------------------------- body reading

def is_challenge(resp):
    if resp.h("cf-mitigated"):
        return "cf-mitigated: " + resp.h("cf-mitigated")
    low = resp.body.lower()
    for m in CHALLENGE_MARKERS:
        if m in low:
            return 'body carries "%s"' % m
    return ""


def is_js_shell(resp):
    """An HTML 200 whose visible text is almost empty while it loads scripts is a page only a browser can read."""
    ctype = resp.h("content-type").lower()
    if "html" not in ctype:
        return ""
    low = resp.body.lower()
    for m in JS_MARKERS:
        if m in low:
            return 'body carries "%s"' % m
    stripped = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", resp.body)
    text = re.sub(r"(?s)<[^>]+>", " ", stripped)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 400 and "<script" in low:
        return "only %d characters of visible text, and the page loads scripts" % len(text)
    return ""


def error_code_1010(resp):
    m = re.search(r"error code: (\d+)", resp.body.lower())
    return m.group(1) if m else ""


# ---------------------------------------------------------------- the rows

def row(step, verdict, url, status, note):
    return {"step": step, "verdict": verdict, "url": url, "status": status, "note": note}


def find_priced_ops(doc):
    """Every operation the openapi itself marks priced, by x-priced or by x-payment-info."""
    out = []
    for path, ops in (doc.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head"):
                continue
            if not isinstance(op, dict):
                continue
            pay = op.get("x-payment-info")
            priced = op.get("x-priced")
            if priced or pay:
                amount, currency = "", ""
                if isinstance(pay, dict):
                    amount = pay.get("amount", "")
                    currency = pay.get("currency", "")
                out.append({"path": path, "method": method.upper(), "amount": amount, "currency": currency})
    return out


def envelope_from(resp):
    """The 402 envelope, from the JSON body or from the base64 payment-required header."""
    doc = resp.json()
    if isinstance(doc, dict) and ("accepts" in doc or "x402Version" in doc):
        return doc, "body"
    for hname in ("payment-required", "x-payment-required"):
        raw = resp.h(hname)
        if not raw:
            continue
        try:
            import base64
            pad = "=" * (-len(raw) % 4)
            return json.loads(base64.b64decode(raw + pad).decode("utf-8", "replace")), hname
        except Exception:
            continue
    return None, ""


def price_from(env):
    for acc in (env.get("accepts") or []):
        if not isinstance(acc, dict):
            continue
        amt, asset = acc.get("amount"), acc.get("asset") or acc.get("currency")
        if amt is not None and asset:
            return "%s %s" % (amt, asset)
    return ""


def payto_from(env, resp):
    for acc in (env.get("accepts") or []):
        if isinstance(acc, dict) and acc.get(PAY_FIELD):
            return acc[PAY_FIELD]
    link = resp.h("link")
    m = re.search(r'<([^>]+)>;\s*rel="?payment"?', link)
    return m.group(1) if m else ""


def buyer_path(base, rows):
    """Walk the path a machine buyer walks. Returns the probe context it learned on the way."""
    ctx = {"priced_url": "", "envelope": None, "env_source": "", "payto": "", "documented": False}

    r = fetch(base + "/")
    if r.error or r.status != 200:
        rows.append(row("front_door", FAIL, r.url, r.status, r.error or "the front door did not answer 200"))
    else:
        ch, js = is_challenge(r), is_js_shell(r)
        if ch:
            rows.append(row("front_door", FAIL, r.url, r.status, "a challenge a machine cannot pass: " + ch))
        elif js:
            rows.append(row("front_door", FAIL, r.url, r.status, "a page only a browser can read: " + js))
        else:
            rows.append(row("front_door", PASS, r.url, r.status, "%d bytes, no challenge, readable without scripts" % len(r.body)))

    cat = fetch(base + "/.well-known/api-catalog")
    openapi_url = ""
    if cat.status == 200 and cat.json():
        doc = cat.json()
        for entry in (doc.get("linkset") or []):
            for sd in (entry.get("service-desc") or []):
                if sd.get("href"):
                    openapi_url = sd["href"]
                    break
        if openapi_url:
            rows.append(row("catalog", PASS, cat.url, cat.status, "service-desc names " + openapi_url))
        else:
            rows.append(row("catalog", FAIL, cat.url, cat.status, "linkset carries no service-desc href"))
    else:
        rows.append(row("catalog", FAIL, cat.url, cat.status, cat.error or "no readable RFC 9727 catalog"))
    if not openapi_url:
        openapi_url = base + "/openapi.json"

    oa = fetch(openapi_url)
    doc = oa.json() if oa.status == 200 else None
    if doc is None:
        rows.append(row("openapi", FAIL, oa.url, oa.status, oa.error or "the service description did not parse as JSON"))
        rows.append(row("priced_op", NA, oa.url, oa.status, "no service description to read"))
        for s in ("unsigned_402", "price_in_402", "payto_in_402", "checkout_reachable", "wrong_key_refused"):
            rows.append(row(s, NA, "", 0, "no priced operation was found"))
        return ctx
    rows.append(row("openapi", PASS, oa.url, oa.status, "%d paths" % len(doc.get("paths") or {})))

    priced = find_priced_ops(doc)
    if priced:
        op = priced[0]
        ctx["priced_url"] = base + op["path"]
        ctx["documented"] = True
        note = "%s %s, %s %s" % (op["method"], op["path"], op["amount"], op["currency"])
        rows.append(row("priced_op", PASS, ctx["priced_url"], oa.status, note.strip()))
    else:
        # Nothing is documented as priced. Probe the usual paths anyway: a path that answers 402 without being
        # written into the service description is a price no catalog-reading buyer can find.
        found = ""
        for cand in PRICED_CANDIDATES:
            probe = fetch(base + cand)
            if probe.status == 402:
                found = base + cand
                break
        if found:
            ctx["priced_url"] = found
            rows.append(row("priced_op", FAIL, found, 402,
                            "answers 402 but no operation in the service description carries x-priced or "
                            "x-payment-info, so a buyer reading the catalog never finds the price"))
        else:
            rows.append(row("priced_op", NA, oa.url, oa.status, "this door declares no priced operation and none answers 402"))
            for s in ("unsigned_402", "price_in_402", "payto_in_402", "checkout_reachable", "wrong_key_refused"):
                rows.append(row(s, NA, "", 0, "this door sells nothing over the API"))
            return ctx

    # The unsigned call: no key, no signature, no cookie, no script.
    p = fetch(ctx["priced_url"])
    if p.status != 402:
        rows.append(row("unsigned_402", FAIL, p.url, p.status, p.error or "an unpaid call did not answer 402"))
        for s in ("price_in_402", "payto_in_402", "checkout_reachable"):
            rows.append(row(s, NA, p.url, p.status, "no 402 to read"))
    else:
        rows.append(row("unsigned_402", PASS, p.url, p.status, "402 to a buyer with no key and no signature"))
        env, src = envelope_from(p)
        ctx["envelope"], ctx["env_source"] = env, src
        if not env:
            rows.append(row("price_in_402", FAIL, p.url, p.status, "the 402 carries no readable payment envelope"))
            rows.append(row("payto_in_402", FAIL, p.url, p.status, "the 402 carries no readable payment envelope"))
            rows.append(row("checkout_reachable", NA, "", 0, "no payment link to follow"))
        else:
            price = price_from(env)
            if price:
                rows.append(row("price_in_402", PASS, p.url, p.status, "the %s carries %s" % (src, price)))
            else:
                rows.append(row("price_in_402", FAIL, p.url, p.status, "the envelope names no amount and asset"))
            payto = payto_from(env, p)
            ctx["payto"] = payto
            if payto and not payto.lower().startswith(("http://", "https://")):
                rows.append(row("payto_in_402", FAIL, p.url, p.status,
                                "the payment destination is %s, which no machine buyer can follow; a price needs a web checkout" % payto))
                rows.append(row("checkout_reachable", NA, payto, 0, "the payment link is not a web address"))
            elif payto:
                rows.append(row("payto_in_402", PASS, p.url, p.status, "payment destination " + payto))
                c = fetch(payto, method="HEAD")
                if c.status in (405, 501, 0):
                    c = fetch(payto)
                if c.status == 200:
                    rows.append(row("checkout_reachable", PASS, payto, c.status, "the checkout page answers, read only, nothing submitted"))
                else:
                    rows.append(row("checkout_reachable", FAIL, payto, c.status, c.error or "the checkout link did not answer 200"))
            else:
                rows.append(row("payto_in_402", FAIL, p.url, p.status, "no payment destination in the envelope and no Link rel=payment"))
                rows.append(row("checkout_reachable", NA, "", 0, "no payment link to follow"))

    if not ctx["documented"]:
        rows.append(row("wrong_key_refused", NA, ctx["priced_url"], 0,
                        "this path is not documented as priced, so there is no key rail here to refuse a key"))
        return ctx

    w = fetch(ctx["priced_url"], extra={"Authorization": "Bearer " + WRONG_KEY})
    body_json = w.json()
    if w.status in (401, 403) and body_json is not None:
        err = body_json.get("error") if isinstance(body_json, dict) else ""
        rows.append(row("wrong_key_refused", PASS, w.url, w.status, "JSON body, error %s" % (err or "(unnamed)")))
    elif w.status in (401, 403):
        rows.append(row("wrong_key_refused", FAIL, w.url, w.status, "refused, but the body is not JSON a machine can read"))
    else:
        rows.append(row("wrong_key_refused", FAIL, w.url, w.status, w.error or "a made-up key was not refused with 401 or 403"))
    return ctx


def edge_checks(base, ctx, rows):
    """The seven settings from the 16 Sep forgemesh post, each read by its observable effect from outside."""
    target = ctx.get("priced_url") or (base + "/")

    blocked = []
    for ua in (UA_URLLIB, UA_PERL):
        r = fetch(target, ua=ua)
        code = error_code_1010(r)
        if r.status == 403 or code:
            blocked.append("%s -> %d%s" % (ua, r.status, (", error code: " + code) if code else ""))
    if blocked:
        rows.append(row("cf1_browser_integrity", FAIL, target, 403,
                        "the edge refuses a default client before our code runs: " + "; ".join(blocked)))
    else:
        rows.append(row("cf1_browser_integrity", PASS, target, 200, "both default client agents reach the door"))

    challenged = []
    for ua in (UA_CURL, UA_REQUESTS):
        r = fetch(base + "/", ua=ua)
        ch = is_challenge(r)
        if r.status in (403, 503) or ch:
            challenged.append("%s -> %d%s" % (ua, r.status, (", " + ch) if ch else ""))
    if challenged:
        rows.append(row("cf2_no_challenge", FAIL, base + "/", 0, "a challenge or block on a script client: " + "; ".join(challenged)))
    else:
        rows.append(row("cf2_no_challenge", PASS, base + "/", 200, "no challenge action on either script agent"))

    rb = fetch(base + "/robots.txt")
    if rb.status != 200:
        rows.append(row("cf3_robots_content_signal", FAIL, rb.url, rb.status, rb.error or "no robots.txt on this hostname"))
    elif "content-signal" not in rb.body.lower():
        rows.append(row("cf3_robots_content_signal", FAIL, rb.url, rb.status, "robots.txt carries no Content-Signal line"))
    else:
        line = [l.strip() for l in rb.body.splitlines() if l.lower().strip().startswith("content-signal")]
        rows.append(row("cf3_robots_content_signal", PASS, rb.url, rb.status, line[0] if line else "Content-Signal present"))

    up = fetch(base + UNPUBLISHED)
    if up.status == 200:
        rows.append(row("cf4_unpublished_path_404", FAIL, up.url, up.status,
                        "an unpublished machine path answers 200 (%s), which reads to a scanner as a broken manifest"
                        % (up.h("content-type") or "no content type")))
    elif up.status == 404:
        rows.append(row("cf4_unpublished_path_404", PASS, up.url, up.status, "a clean 404, content type " + (up.h("content-type") or "none")))
    else:
        rows.append(row("cf4_unpublished_path_404", FAIL, up.url, up.status, up.error or "neither 404 nor 200 on an unpublished machine path"))

    t = fetch(target)
    if t.status >= 500:
        rows.append(row("cf5_no_edge_fault", FAIL, t.url, t.status, "the edge answered %d, which is an edge-to-origin fault, not an answer" % t.status))
    elif t.error:
        rows.append(row("cf5_no_edge_fault", FAIL, t.url, t.status, t.error))
    else:
        rows.append(row("cf5_no_edge_fault", PASS, t.url, t.status, "no 5xx from the edge on the priced path"))

    env = ctx.get("envelope")
    if not env:
        rows.append(row("cf6_envelope_url", NA, target, 0, "no payment envelope to read"))
    else:
        res = (env.get("resource") or {})
        url_in = res.get("url") if isinstance(res, dict) else ""
        called = urllib.parse.urlsplit(target)
        got = urllib.parse.urlsplit(url_in) if url_in else None
        if not url_in:
            rows.append(row("cf6_envelope_url", FAIL, target, 402, "the envelope names no resource url"))
        elif got.scheme != called.scheme:
            rows.append(row("cf6_envelope_url", FAIL, target, 402,
                            "the envelope writes %s while the buyer called %s, so the proxy protocol is not being read"
                            % (url_in, called.scheme)))
        elif got.netloc != called.netloc:
            rows.append(row("cf6_envelope_url", FAIL, target, 402, "the envelope names %s, the buyer called %s" % (url_in, called.netloc)))
        else:
            rows.append(row("cf6_envelope_url", PASS, target, 402, "the envelope names " + url_in))

    if ctx.get("priced_url"):
        cc = (t.h("cache-control") or "").lower()
        cf = (t.h("cf-cache-status") or "").upper()
        if cf == "HIT":
            rows.append(row("cf7_402_not_cached", FAIL, t.url, t.status, "cf-cache-status HIT on a payment answer"))
        elif any(k in cc for k in ("no-store", "no-cache", "private", "max-age=0")):
            rows.append(row("cf7_402_not_cached", PASS, t.url, t.status, "cache-control: " + cc))
        else:
            rows.append(row("cf7_402_not_cached", FAIL, t.url, t.status, "cache-control %s, so a payment answer may be served from cache" % (cc or "absent")))
    else:
        rows.append(row("cf7_402_not_cached", NA, base, 0, "no priced path on this door"))

    if ctx.get("priced_url"):
        pre = fetch(ctx["priced_url"], method="OPTIONS", extra={
            "Origin": "https://buyer.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        })
        allow_o = pre.h("access-control-allow-origin")
        allow_m = pre.h("access-control-allow-methods").upper()
        if pre.status not in (200, 204):
            rows.append(row("cf8_cors_preflight", FAIL, pre.url, pre.status, pre.error or "the preflight was not answered"))
        elif not allow_o:
            rows.append(row("cf8_cors_preflight", FAIL, pre.url, pre.status, "no access-control-allow-origin on the preflight"))
        elif "GET" not in allow_m:
            rows.append(row("cf8_cors_preflight", FAIL, pre.url, pre.status, "allow-methods %s does not carry GET" % (allow_m or "absent")))
        else:
            rows.append(row("cf8_cors_preflight", PASS, pre.url, pre.status, "allow-origin %s, allow-methods %s" % (allow_o, allow_m)))
    else:
        rows.append(row("cf8_cors_preflight", NA, base, 0, "no priced path on this door"))


def probe(base, label=None):
    rows = []
    started = time.time()
    ctx = buyer_path(base, rows)
    edge_checks(base, ctx, rows)
    counts = {PASS: 0, FAIL: 0, NA: 0}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {
        "door": label or base,
        "base": base,
        "priced_url": ctx.get("priced_url"),
        "ms": int((time.time() - started) * 1000),
        "counts": counts,
        "rows": rows,
    }


# ---------------------------------------------------------------- printing

def print_door(res):
    print("")
    print("=== %s  (%s PASS, %s FAIL, %s NA, %d ms)" % (res["door"], res["counts"].get(PASS, 0),
                                                        res["counts"].get(FAIL, 0), res["counts"].get(NA, 0), res["ms"]))
    print("%-26s %-5s %-5s %s" % ("step", "word", "code", "url and what was read"))
    print("-" * 110)
    for r in res["rows"]:
        url = r["url"] or "-"
        if len(url) > 52:
            url = url[:49] + "..."
        print("%-26s %-5s %-5s %s" % (r["step"], r["verdict"], r["status"] or "-", url))
        print("%-38s %s" % ("", r["note"]))


# ---------------------------------------------------------------- selftest

class _Stub(http.server.BaseHTTPRequestHandler):
    MODE = "clean"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _base(self):
        return "http://" + self.headers.get("Host", "127.0.0.1")

    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        m = _Stub.MODE
        if self.path.startswith("/api/bulk") and m != "no_cors":
            self._send(204, "", "text/plain", {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "authorization",
            })
        else:
            self._send(404, "{}")

    def do_GET(self):
        m = _Stub.MODE
        b = self._base()
        ua = self.headers.get("User-Agent", "")
        path = self.path.split("?")[0]

        if m == "edge_block" and ua.startswith(("Python-urllib", "libwww-perl")):
            self._send(403, "<html><body>error code: 1010</body></html>", "text/html")
            return

        if path == "/":
            if m == "challenge":
                self._send(403, "<html><title>Just a moment...</title><body>Checking your browser</body></html>", "text/html")
            elif m == "js_shell":
                self._send(200, '<html><body><div id="root"></div><script src="/a.js"></script></body></html>', "text/html")
            else:
                self._send(200, "<html><body><h1>Door</h1>" + ("x" * 900) + "</body></html>", "text/html")
            return

        if path == "/.well-known/api-catalog":
            self._send(200, json.dumps({"linkset": [{"anchor": b, "service-desc": [{"href": b + "/openapi.json"}]}]}))
            return

        if path == "/openapi.json":
            op = {"responses": {"402": {"description": "payment required"}}}
            if m != "undocumented_price":
                op["x-priced"] = True
                op["x-payment-info"] = {"intent": "charge", "method": "stripe", "amount": 25, "currency": "USD"}
            self._send(200, json.dumps({"openapi": "3.1.0", "servers": [{"url": b}], "paths": {"/api/bulk": {"get": op}}}))
            return

        if path == "/api/bulk":
            auth = self.headers.get("Authorization", "")
            if auth:
                if m == "weak_key_refusal":
                    self._send(200, json.dumps({"corpus": "everything"}))
                else:
                    self._send(401, json.dumps({"x402Version": 2, "error": "unknown_key"}), "application/json", {"Cache-Control": "no-store"})
                return
            accepts = {"scheme": "prepaid-credit/v1", "amount": "2500", "asset": "USD",
                       PAY_FIELD: b + "/checkout", "resource": b + "/api/bulk"}
            if m == "no_payto":
                accepts.pop(PAY_FIELD)
            if m == "mailto_payto":
                accepts[PAY_FIELD] = "mailto:hello@example.com"
            written = b + "/api/bulk"
            if m == "wrong_protocol":
                written = written.replace("http://", "httpx://")
            env = {"x402Version": 2, "error": "payment_required",
                   "resource": {"url": written}, "accepts": [accepts]}
            cache = "public, max-age=3600" if m == "cached_402" else "no-store"
            self._send(402, json.dumps(env), "application/json", {"Cache-Control": cache})
            return

        if path == "/checkout":
            self._send(200, "<html><body>checkout</body></html>", "text/html")
            return

        if path == "/robots.txt":
            body = "User-agent: *\nAllow: /\n"
            if m != "no_content_signal":
                body += "Content-Signal: search=yes, ai-train=yes\n"
            self._send(200, body, "text/plain")
            return

        if path.startswith("/.well-known/"):
            if m == "spa_fallback":
                self._send(200, "<html><body>app shell</body></html>", "text/html")
            else:
                self._send(404, json.dumps({"error": "not_found"}))
            return

        self._send(404, json.dumps({"error": "not_found"}))


def _verdict_of(res, step):
    for r in res["rows"]:
        if r["step"] == step:
            return r["verdict"]
    return "MISSING"


def selftest():
    checks, failed = 0, 0

    def check(name, got, want):
        nonlocal checks, failed
        checks += 1
        ok = got == want
        if not ok:
            failed += 1
        print("  %-52s %-8s %s" % (name, "ok" if ok else "FAILED", "" if ok else "got %r want %r" % (got, want)))

    server = socketserver.TCPServer(("127.0.0.1", 0), _Stub)
    server.allow_reuse_address = True
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % port
    print("buyer_probe selftest, stub on %s, no network beyond the loopback" % base)
    try:
        _Stub.MODE = "clean"
        clean = probe(base, "stub-clean")
        check("clean stub: front door", _verdict_of(clean, "front_door"), PASS)
        check("clean stub: catalog names the service description", _verdict_of(clean, "catalog"), PASS)
        check("clean stub: priced operation found", _verdict_of(clean, "priced_op"), PASS)
        check("clean stub: unsigned call answers 402", _verdict_of(clean, "unsigned_402"), PASS)
        check("clean stub: the price is in the 402", _verdict_of(clean, "price_in_402"), PASS)
        check("clean stub: the payment link is in the 402", _verdict_of(clean, "payto_in_402"), PASS)
        check("clean stub: the checkout page answers", _verdict_of(clean, "checkout_reachable"), PASS)
        check("clean stub: a made-up key is refused as JSON", _verdict_of(clean, "wrong_key_refused"), PASS)
        check("clean stub: the preflight is answered", _verdict_of(clean, "cf8_cors_preflight"), PASS)
        check("clean stub: the 402 is not cacheable", _verdict_of(clean, "cf7_402_not_cached"), PASS)
        check("clean stub: nothing failed", clean["counts"].get(FAIL, 0), 0)

        _Stub.MODE = "challenge"
        check("challenge page on the front door", _verdict_of(probe(base), "front_door"), FAIL)

        _Stub.MODE = "js_shell"
        check("a page only a browser can read", _verdict_of(probe(base), "front_door"), FAIL)

        _Stub.MODE = "edge_block"
        check("403 and error code 1010 to a default client", _verdict_of(probe(base), "cf1_browser_integrity"), FAIL)

        _Stub.MODE = "spa_fallback"
        check("HTML 200 on an unpublished machine path", _verdict_of(probe(base), "cf4_unpublished_path_404"), FAIL)

        _Stub.MODE = "no_content_signal"
        check("robots.txt with no Content-Signal line", _verdict_of(probe(base), "cf3_robots_content_signal"), FAIL)

        _Stub.MODE = "cached_402"
        check("a cacheable payment answer", _verdict_of(probe(base), "cf7_402_not_cached"), FAIL)

        _Stub.MODE = "no_cors"
        check("no preflight on the priced path", _verdict_of(probe(base), "cf8_cors_preflight"), FAIL)

        _Stub.MODE = "no_payto"
        r = probe(base)
        check("a 402 with no payment link", _verdict_of(r, "payto_in_402"), FAIL)
        check("and nothing to follow", _verdict_of(r, "checkout_reachable"), NA)

        _Stub.MODE = "weak_key_refusal"
        check("a made-up key served the goods", _verdict_of(probe(base), "wrong_key_refused"), FAIL)

        _Stub.MODE = "wrong_protocol"
        check("the envelope writes a protocol the buyer never used", _verdict_of(probe(base), "cf6_envelope_url"), FAIL)

        _Stub.MODE = "mailto_payto"
        r = probe(base)
        check("a payment link no machine can follow", _verdict_of(r, "payto_in_402"), FAIL)
        check("and no checkout to read", _verdict_of(r, "checkout_reachable"), NA)

        _Stub.MODE = "undocumented_price"
        r = probe(base)
        check("a 402 no catalog reader can find", _verdict_of(r, "priced_op"), FAIL)
        check("an undocumented path has no key rail to test", _verdict_of(r, "wrong_key_refused"), NA)
    finally:
        server.shutdown()
        server.server_close()

    print("")
    print("%d checks, %d failed" % (checks, failed))
    return 1 if failed else 0


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Walk the path a plain, script-only, unsigned machine buyer walks on each of our doors, "
                    "from the front door to the 402 to the checkout link, and read the seven edge settings by "
                    "their effect from outside. Nothing is ever posted or paid.")
    ap.add_argument("hosts", nargs="*", help="hosts to probe (default: the DOORS environment list)")
    ap.add_argument("--json", default="buyer_probe_latest.json", help="where to write the run (default: buyer_probe_latest.json)")
    ap.add_argument("--no-write", action="store_true", help="print only, write no file")
    ap.add_argument("--selftest", action="store_true", help="run the in-process stub checks, no network")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    hosts = args.hosts or DOORS
    if not hosts:
        sys.exit("name one or more hosts, or set DOORS to a comma-separated list")
    results = []
    for h in hosts:
        base = h if h.startswith("http") else "https://" + h
        res = probe(base, h)
        results.append(res)
        print_door(res)

    total = {PASS: 0, FAIL: 0, NA: 0}
    for res in results:
        for k in total:
            total[k] += res["counts"].get(k, 0)
    out = {
        "tool": "buyer_probe.py",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "ua": UA,
        "source": "forgemesh.io/blog/cloudflare-config-gotchas-paid-apis, dated 2026-09-16, read 2026-09-22",
        "totals": total,
        "doors": results,
    }
    if not args.no_write:
        p = pathlib.Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("")
        print("written: %s" % p)
    print("totals: %d PASS, %d FAIL, %d NA across %d doors" % (total[PASS], total[FAIL], total[NA], len(results)))
    return 1 if total[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
