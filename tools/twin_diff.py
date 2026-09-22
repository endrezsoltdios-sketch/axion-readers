#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""twin_diff.py: the markdown-twin differential, your pages against the field's reference conformance rules.

Written 2026-09-22. Nothing from the reference is installed. It compares; it never changes a site.

Reference read 2026-09-22:
  https://raw.githubusercontent.com/dodopayments/dualmark/main/README.md
  https://raw.githubusercontent.com/dodopayments/dualmark/main/spec/conformance.md   (the 14-item check catalogue)
  https://raw.githubusercontent.com/dodopayments/dualmark/main/spec/headers.md       (the headers contract)
  dodopayments/dualmark, Apache-2.0, "AEO Specification v1.0"

The twin shape assumed here is the common one: <path>.md re-enters the same worker with Accept: text/markdown, so
the twin is the negotiated variant byte for byte.

Two halves, both measured per page:
  A. Our own differential, HTML against twin: same title, same h1, every number in the HTML present in the twin,
     every link in the twin present in the HTML, byte size ratio, and byte identity between the .md URL and the
     negotiated variant of the HTML URL.
  B. The reference's 14 conformance items, each read as AGREE (you do what it requires), DIFFER (you do something
     else on purpose, with the reason recorded in TWIN_REASONS) or ABSENT (you do not, with no recorded reason).

  python tools/twin_diff.py example.com         one host, home plus 5 sitemap pages
  python tools/twin_diff.py                     the hosts in the HOSTS environment list
  python tools/twin_diff.py --pages 10          more pages per host
  python tools/twin_diff.py --selftest          fixture checks, no network
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = os.environ.get("TWIN_DIFF_UA", "example-bot/1.0 (+https://example.com)")
BOT_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot"
OUT_DEFAULT = os.path.join(ROOT, "twin_diff_latest.json")
DOC_DEFAULT = os.path.join(ROOT, "twin_diff.md")

# The hosts to read when none are named on the command line: HOSTS="a.com,b.com" in the environment.
HOSTS = [h.strip() for h in os.environ.get("HOSTS", "").split(",") if h.strip()]

AGREE, DIFFER, ABSENT = "AGREE", "DIFFER", "ABSENT"

# The reference's check catalogue, spec/conformance.md, read 2026-09-22. weight and level are theirs.
SPEC_ITEMS = [
    ("md.fetch", "markdown twin reachable (2xx)", 20, "Basic"),
    ("md.contentType", "text/markdown; charset=utf-8", 10, "Basic"),
    ("md.tokensHeader", "X-Markdown-Tokens integer >= 1", 10, "Basic"),
    ("md.noindex", "X-Robots-Tag contains noindex", 10, "Basic"),
    ("md.vary", "Vary contains Accept on the twin", 10, "Basic"),
    ("md.body", "non-empty markdown body", 10, "Basic"),
    ("md.aeoVersion", "X-AEO-Version advertised", 5, "Advanced"),
    ("md.nosniff", "X-Content-Type-Options: nosniff", 5, "Advanced"),
    ("html.reachable", "HTML URL reachable (2xx)", 5, "Standard"),
    ("html.linkAlternate", 'HTML Link rel="alternate" type="text/markdown"', 10, "Standard"),
    ("html.vary", "HTML response Vary: Accept", 5, "Standard"),
    ("negotiation.botUa", "GPTBot UA receives markdown", 10, "Advanced"),
    ("negotiation.acceptHeader", "Accept: text/markdown receives markdown", 10, "Standard"),
    ("negotiation.notAcceptable", "Accept excluding both -> 406", 5, "Advanced"),
]

# Where you differ on purpose, as {check id: your recorded reason}, from the TWIN_REASONS environment value (a
# JSON object). A check you fail with a reason here reads DIFFER, which is a decision on the record; a check you
# fail with no reason reads ABSENT, which is a gap. Nothing is assumed on your behalf.
OUR_REASONS = json.loads(os.environ.get("TWIN_REASONS", "{}"))

# The three header rules implementations most often differ on.
RADAR_THREE = ["md.noindex", "html.linkAlternate", "md.vary"]


# ---------------------------------------------------------------- fetch


class Res(object):
    def __init__(self, status, headers, body, url, error=None):
        self.status, self.headers, self.body, self.url, self.error = status, headers, body, url, error

    def h(self, name):
        for k, v in (self.headers or {}).items():
            if k.lower() == name.lower():
                return v
        return ""

    def h_all(self, name):
        return [v for k, v in (self.headers or {}).items() if k.lower() == name.lower()]


def fetch(url, accept=None, ua=UA, method="GET"):
    hdrs = {"User-Agent": ua, "Accept-Encoding": "identity"}
    if accept:
        hdrs["Accept"] = accept
    req = urllib.request.Request(url, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return Res(r.status, dict(r.headers), r.read(), url)
    except urllib.error.HTTPError as e:
        return Res(e.code, dict(e.headers), e.read(), url)
    except Exception as e:
        return Res(0, {}, b"", url, error=repr(e)[:200])


# ---------------------------------------------------------------- parsing


def strip_html(html):
    t = re.sub(r"(?is)<(script|style|template|noscript)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<!--.*?-->", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", t).strip()


def html_title(html):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return re.sub(r"\s+", " ", strip_html(m.group(1))).strip() if m else ""


def html_h1(html):
    m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html or "")
    return re.sub(r"\s+", " ", strip_html(m.group(1))).strip() if m else ""


def md_title(md):
    """Front-matter title if present, else the first ATX h1."""
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end > 0:
            m = re.search(r'(?m)^title:\s*"?(.*?)"?\s*$', md[:end])
            if m:
                return m.group(1).strip()
    m = re.search(r"(?m)^#\s+(.+?)\s*$", md)
    return m.group(1).strip() if m else ""


def md_h1(md):
    m = re.search(r"(?m)^#\s+(.+?)\s*$", md)
    return m.group(1).strip() if m else ""


NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")


def numbers(text):
    """Every number a reader would see, normalised: commas dropped, a trailing percent kept."""
    out = []
    for m in NUM.finditer(text or ""):
        s = m.group(0).replace(",", "")
        if s.rstrip("%").rstrip(".") in ("", "0"):
            continue
        out.append(s)
    return out


def html_links(html, base):
    return {urllib.parse.urljoin(base, h) for h in re.findall(r'(?i)href="([^"#]+)"', html or "") if h.strip()}


def md_links(md, base):
    urls = set(re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", md or ""))
    urls |= set(re.findall(r"(?<![(\[])\bhttps?://[^\s)\]|<>\"]+", md or ""))
    return {urllib.parse.urljoin(base, u.rstrip(".,")) for u in urls if u.strip()}


def canon_url(u):
    p = urllib.parse.urlsplit(u)
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return "%s://%s%s" % (p.scheme, p.netloc.lower(), path)


def twin_url(page_url):
    p = urllib.parse.urlsplit(page_url)
    path = p.path or "/"
    path = path.rstrip("/") or "/"
    md = "/index.md" if path == "/" else path + ".md"
    return "%s://%s%s" % (p.scheme, p.netloc, md)


def link_has_md_alternate(link_values):
    joined = " , ".join(link_values)
    for part in re.split(r",(?=\s*<)", joined):
        if 'rel="alternate"' in part.replace("rel=alternate", 'rel="alternate"') and "text/markdown" in part:
            return True
    return False


def tokens_ok(value):
    try:
        return int(str(value).strip()) >= 1
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- one page


def compare(page_url, html_res, twin_res, neg_res, bot_res, na_res):
    html = html_res.body.decode("utf-8", "replace") if html_res.body else ""
    md = twin_res.body.decode("utf-8", "replace") if twin_res.body else ""
    h_text, m_text = strip_html(html), md

    h_nums, m_nums = numbers(h_text), set(numbers(m_text))
    missing_nums = sorted({n for n in h_nums if n not in m_nums})

    h_links = {canon_url(u) for u in html_links(html, page_url) if u.startswith("http")}
    m_links = {canon_url(u) for u in md_links(md, page_url) if u.startswith("http")}
    extra_links = sorted(u for u in m_links if u not in h_links and canon_url(page_url) != u)

    ours = [
        {"check": "twin.reachable", "ok": twin_res.status == 200,
         "evidence": "HTTP %d %s" % (twin_res.status, twin_res.error or "")},
        {"check": "title.same", "ok": html_title(html) == md_title(md),
         "evidence": "html %r | twin %r" % (html_title(html)[:80], md_title(md)[:80])},
        {"check": "h1.same", "ok": html_h1(html) == md_h1(md),
         "evidence": "html %r | twin %r" % (html_h1(html)[:80], md_h1(md)[:80])},
        {"check": "numbers.in.twin", "ok": not missing_nums,
         "evidence": "%d of %d HTML numbers absent from the twin%s"
                     % (len(missing_nums), len(set(h_nums)),
                        (": " + ", ".join(missing_nums[:12])) if missing_nums else "")},
        {"check": "links.twin.subset.of.html", "ok": not extra_links,
         "evidence": "%d twin link(s) the HTML does not carry%s"
                     % (len(extra_links), (": " + ", ".join(extra_links[:6])) if extra_links else "")},
        {"check": "bytes.ratio", "ok": True,
         "evidence": "twin %d B / html %d B = %.3f" % (len(twin_res.body), len(html_res.body),
                                                       (len(twin_res.body) / len(html_res.body))
                                                       if html_res.body else 0.0)},
        {"check": "md.url.identical.to.negotiated", "ok": twin_res.body == neg_res.body,
         "evidence": "sha256 %s vs %s" % (hashlib.sha256(twin_res.body).hexdigest()[:16],
                                          hashlib.sha256(neg_res.body).hexdigest()[:16])},
    ]

    ct = twin_res.h("content-type").lower()
    neg_ct = neg_res.h("content-type").lower()
    bot_ct = bot_res.h("content-type").lower()
    spec_state = {
        "md.fetch": twin_res.status == 200,
        "md.contentType": ct.replace(" ", "") == "text/markdown;charset=utf-8",
        "md.tokensHeader": tokens_ok(twin_res.h("x-markdown-tokens")),
        "md.noindex": "noindex" in twin_res.h("x-robots-tag").lower(),
        "md.vary": "accept" in twin_res.h("vary").lower(),
        "md.body": len(md.strip()) > 0,
        "md.aeoVersion": bool(twin_res.h("x-aeo-version")),
        "md.nosniff": twin_res.h("x-content-type-options").lower() == "nosniff",
        "html.reachable": html_res.status == 200,
        "html.linkAlternate": link_has_md_alternate(html_res.h_all("link")),
        "html.vary": "accept" in html_res.h("vary").lower(),
        "negotiation.botUa": bot_ct.startswith("text/markdown"),
        "negotiation.acceptHeader": neg_ct.startswith("text/markdown"),
        "negotiation.notAcceptable": na_res.status == 406,
    }
    spec = []
    for cid, desc, weight, level in SPEC_ITEMS:
        ok = spec_state[cid]
        verdict = AGREE if ok else (DIFFER if cid in OUR_REASONS else ABSENT)
        spec.append({"id": cid, "description": desc, "weight": weight, "level": level,
                     "verdict": verdict, "ours": ok,
                     "our_reason": OUR_REASONS.get(cid, "") if not ok else "",
                     "evidence": _spec_evidence(cid, twin_res, html_res, neg_res, bot_res, na_res)})
    score = sum(w for (cid, _d, w, _l) in SPEC_ITEMS if spec_state[cid])
    return {"url": page_url, "twin_url": twin_res.url, "ours": ours, "spec": spec,
            "spec_score": score, "spec_max": sum(w for _c, _d, w, _l in SPEC_ITEMS)}


def _spec_evidence(cid, twin, html, neg, bot, na):
    if cid.startswith("md.fetch"):
        return "HTTP %d" % twin.status
    if cid == "md.contentType":
        return twin.h("content-type") or "absent"
    if cid == "md.tokensHeader":
        return "X-Markdown-Tokens: %s" % (twin.h("x-markdown-tokens") or "absent")
    if cid == "md.noindex":
        return "X-Robots-Tag: %s | our twin Link: %s" % (twin.h("x-robots-tag") or "absent",
                                                         "canonical" if "canonical" in twin.h("link") else "none")
    if cid == "md.vary":
        return "Vary: %s" % (twin.h("vary") or "absent")
    if cid == "md.body":
        return "%d B" % len(twin.body)
    if cid == "md.aeoVersion":
        return "X-AEO-Version: %s" % (twin.h("x-aeo-version") or "absent")
    if cid == "md.nosniff":
        return "X-Content-Type-Options: %s" % (twin.h("x-content-type-options") or "absent")
    if cid == "html.reachable":
        return "HTTP %d" % html.status
    if cid == "html.linkAlternate":
        v = " , ".join(html.h_all("link"))
        return ("Link: " + v[:160]) if v else "no Link header"
    if cid == "html.vary":
        return "Vary: %s" % (html.h("vary") or "absent")
    if cid == "negotiation.botUa":
        return "GPTBot -> HTTP %d %s" % (bot.status, bot.h("content-type") or "")
    if cid == "negotiation.acceptHeader":
        return "Accept: text/markdown -> HTTP %d %s" % (neg.status, neg.h("content-type") or "")
    if cid == "negotiation.notAcceptable":
        return "Accept: image/webp -> HTTP %d %s" % (na.status, na.h("content-type") or "")
    return ""


def page_run(page_url):
    html_res = fetch(page_url, accept="text/html,application/xhtml+xml")
    twin_res = fetch(twin_url(page_url))
    neg_res = fetch(page_url, accept="text/markdown")
    bot_res = fetch(page_url, accept="*/*", ua=BOT_UA)
    na_res = fetch(page_url, accept="image/webp")
    return compare(page_url, html_res, twin_res, neg_res, bot_res, na_res)


def sitemap_pages(host, want):
    r = fetch("https://%s/sitemap.xml" % host)
    locs = re.findall(r"<loc>([^<]+)</loc>", r.body.decode("utf-8", "replace") if r.body else "")
    home = "https://%s/" % host
    rest = [u for u in locs if canon_url(u) != canon_url(home) and not u.endswith(".md")]
    return [home] + rest[:want]


# ---------------------------------------------------------------- selftest

FIX_HTML = (
    '<html><head><title>Two dates, free</title>'
    '<link rel="stylesheet" href="/a.css"></head><body><h1>Two dates, free</h1>'
    '<p>84,502,749 claims denied, 18.7% of them. See <a href="https://x.test/insurers">insurers</a>.</p>'
    '<script>var n = 999999;</script></body></html>'
)
FIX_MD = (
    '---\ntitle: "Two dates, free"\n---\n\n# Two dates, free\n\n'
    "84,502,749 claims denied, 18.7% of them. See https://x.test/insurers\n"
)


def selftest():
    n = [0]
    bad = []

    def eq(got, want, label):
        n[0] += 1
        if got != want:
            bad.append("%s: got %r want %r" % (label, got, want))

    eq(html_title(FIX_HTML), "Two dates, free", "html title")
    eq(html_h1(FIX_HTML), "Two dates, free", "html h1")
    eq(md_title(FIX_MD), "Two dates, free", "md front-matter title")
    eq(md_h1(FIX_MD), "Two dates, free", "md h1")
    eq(md_title("# Only a heading\n"), "Only a heading", "md title falls back to the h1")
    eq(sorted(set(numbers(strip_html(FIX_HTML)))), ["18.7%", "84502749"], "numbers, commas dropped, script ignored")
    eq([x for x in numbers(strip_html(FIX_HTML)) if x not in set(numbers(FIX_MD))], [], "every html number in twin")
    eq(sorted(numbers("no digits here")), [], "no numbers")
    eq({canon_url(u) for u in md_links(FIX_MD, "https://x.test/")}, {"https://x.test/insurers"}, "md bare link")
    eq({canon_url(u) for u in html_links(FIX_HTML, "https://x.test/") if u.startswith("http")},
       {"https://x.test/a.css", "https://x.test/insurers"}, "html links resolved")
    eq(canon_url("https://X.test/a/"), "https://x.test/a", "canonical url, trailing slash and case")
    eq(twin_url("https://x.test/"), "https://x.test/index.md", "home twin is /index.md")
    eq(twin_url("https://x.test/how-to-appeal"), "https://x.test/how-to-appeal.md", "page twin")
    eq(twin_url("https://x.test/how-to-appeal/"), "https://x.test/how-to-appeal.md", "trailing slash twin")
    eq(link_has_md_alternate(['</a.md>; rel="alternate"; type="text/markdown"']), True, "alternate link found")
    eq(link_has_md_alternate(['</openapi.json>; rel="service-desc", </a>; rel="canonical"']), False,
       "canonical link is not an alternate")
    eq(tokens_ok("821"), True, "tokens header integer")
    eq(tokens_ok(""), False, "tokens header absent")
    eq(tokens_ok("many"), False, "tokens header non-numeric")

    html_res = Res(200, {"Content-Type": "text/html;charset=utf-8", "Vary": "Accept"}, FIX_HTML.encode(),
                   "https://x.test/")
    twin_res = Res(200, {"Content-Type": "text/markdown; charset=utf-8", "x-markdown-tokens": "42",
                         "x-content-type-options": "nosniff", "Link": '</>; rel="canonical"'},
                   FIX_MD.encode(), "https://x.test/index.md")
    out = compare("https://x.test/", html_res, twin_res, twin_res, twin_res, Res(200, {}, b"", "https://x.test/"))
    by = {c["check"]: c for c in out["ours"]}
    eq(by["title.same"]["ok"], True, "fixture titles agree")
    eq(by["numbers.in.twin"]["ok"], True, "fixture numbers agree")
    eq(by["md.url.identical.to.negotiated"]["ok"], True, "fixture md equals negotiated")
    spec = {s["id"]: s for s in out["spec"]}
    eq(spec["md.vary"]["verdict"], ABSENT, "a failing item with no recorded reason is ABSENT")
    eq(spec["md.noindex"]["verdict"], ABSENT, "no noindex and no recorded reason is ABSENT")
    eq(spec["md.contentType"]["verdict"], AGREE, "content type agrees")
    eq(spec["negotiation.notAcceptable"]["verdict"], ABSENT, "no 406 and no recorded reason is ABSENT")

    OUR_REASONS.update({"md.vary": "deliberate: the .md URL negotiates nothing, so Vary would key one cache\n                                   entry for two audiences",
                        "md.noindex": "deliberate: a canonical Link to the HTML instead of X-Robots-Tag",
                        "negotiation.notAcceptable": "deliberate: HTML is served rather than 406"})
    spec2 = {s["id"]: s for s in compare("https://x.test/", html_res, twin_res, twin_res, twin_res,
                                         Res(200, {}, b"", "https://x.test/"))["spec"]}
    eq(spec2["md.vary"]["verdict"], DIFFER, "a recorded reason turns ABSENT into DIFFER")
    eq(spec2["md.noindex"]["verdict"], DIFFER, "no noindex with a recorded reason is a DIFFER")
    eq(spec2["negotiation.notAcceptable"]["verdict"], DIFFER, "406 with a recorded reason is a DIFFER")
    eq(spec2["md.contentType"]["verdict"], AGREE, "a recorded reason moves no passing item")
    OUR_REASONS.clear()

    print("selftest: %d checks, %d failed" % (n[0], len(bad)))
    for b in bad:
        print("  FAIL", b)
    return 1 if bad else 0


# ---------------------------------------------------------------- report


def write_doc(report, path):
    L = []
    L.append("# Markdown-twin differential\n")
    L.append("Tool: `tools/twin_diff.py`, read-only. Record: `twin_diff_latest.json`.")
    L.append("Reference read 22 Sep 2026: <https://github.com/dodopayments/dualmark> README plus")
    L.append("`spec/conformance.md` and `spec/headers.md` (AEO Specification v1.0, Apache-2.0). Nothing installed.")
    L.append("")
    L.append("## The three header rules implementations most often differ on\n")
    L.append("| reference rule | theirs | ours, measured | verdict |")
    L.append("|---|---|---|---|")
    first = report["doors"][0]["pages"][0] if report["doors"] and report["doors"][0]["pages"] else None
    if first:
        spec = {s["id"]: s for s in first["spec"]}
        rows = {
            "md.noindex": ("X-Robots-Tag: noindex on the twin",),
            "html.linkAlternate": ('Link: rel="alternate"; type="text/markdown" on the HTML',),
            "md.vary": ("Vary: Accept on the twin",),
        }
        for cid in RADAR_THREE:
            s = spec[cid]
            L.append("| `%s` | %s | %s | %s |" % (cid, rows[cid][0], s["evidence"].replace("|", "/")[:90],
                                                  s["verdict"]))
    L.append("")
    L.append("## Per door\n")
    L.append("| host | pages | spec score, their weighting, mean | home page | our checks failing |")
    L.append("|---|---|---|---|---|")
    for d in report["doors"]:
        fails = sum(1 for p in d["pages"] for c in p["ours"] if not c["ok"])
        avg = (sum(p["spec_score"] for p in d["pages"]) / len(d["pages"])) if d["pages"] else 0
        home = d["pages"][0]["spec_score"] if d["pages"] else 0
        L.append("| %s | %d | %.0f / 125 | %d / 125 | %d |" % (d["host"], len(d["pages"]), avg, home, fails))
    L.append("")
    L.append("## Coverage: pages with no markdown at all\n")
    L.append("The `.md` URL 404s AND `Accept: text/markdown` on the HTML URL returns HTML, so these pages have no")
    L.append("twin in either form. A twin module steps aside when the site renders no markdown for a route, so")
    L.append("this is the site's page model, not the twin module.\n")
    L.append("| host | page | .md | Accept: text/markdown |")
    L.append("|---|---|---|---|")
    none_total = 0
    for d in report["doors"]:
        for p in d["pages"]:
            s = {x["id"]: x for x in p["spec"]}
            if not s["md.fetch"]["ours"] and not s["negotiation.acceptHeader"]["ours"]:
                none_total += 1
                L.append("| %s | `%s` | %s | %s |" % (d["host"], urllib.parse.urlsplit(p["url"]).path,
                                                      s["md.fetch"]["evidence"],
                                                      s["negotiation.acceptHeader"]["evidence"][:60]))
    L.append("")
    L.append("%d of %d sampled pages carry no markdown.\n"
             % (none_total, sum(len(d["pages"]) for d in report["doors"])))
    for d in report["doors"]:
        L.append("### %s\n" % d["host"])
        for p in d["pages"]:
            L.append("**%s**  (twin %s)\n" % (p["url"], p["twin_url"]))
            L.append("| check | result | evidence |")
            L.append("|---|---|---|")
            for c in p["ours"]:
                L.append("| `%s` | %s | %s |" % (c["check"], "PASS" if c["ok"] else "FAIL",
                                                 c["evidence"].replace("|", "/")[:160]))
            L.append("")
        spec = {s["id"]: s for s in d["pages"][0]["spec"]} if d["pages"] else {}
        if spec:
            L.append("Reference items on the home page:\n")
            L.append("| id | verdict | evidence | your recorded reason when you differ |")
            L.append("|---|---|---|---|")
            for cid, _desc, _w, _l in SPEC_ITEMS:
                s = spec[cid]
                L.append("| `%s` | %s | %s | %s |" % (cid, s["verdict"], s["evidence"].replace("|", "/")[:80],
                                                      s["our_reason"][:150]))
            L.append("")
    L.append("## What a site would change\n")
    L.append("1. The twin response: set `X-Robots-Tag: noindex`, or keep a canonical Link to the HTML and record")
    L.append("   that choice in TWIN_REASONS. The two cannot both be right.")
    L.append("2. The HTML response of every twinned page: append")
    L.append('   `Link: <{path}.md>; rel="alternate"; type="text/markdown"`. It is the only way an agent reading')
    L.append("   headers learns the twin exists, and it is the rule most implementations fail silently.")
    L.append("3. Content negotiation by User-Agent: the reference serves markdown to 24 named bots by default.")
    L.append("   Negotiating on Accept alone is a choice; record it as one.")
    L.append("4. The pages in the coverage table above need a markdown model, or the claim \"markdown twin on")
    L.append("   the whole site\" needs the words \"on the pages that have one\".\n")
    L.append("Not verified: this tool reads conformance, never citation. It cannot tell you that a machine fetched")
    L.append("a twin URL, only that the twin is there and correct.")
    L.append("Not verified: the reference's own runner was not executed against these hosts; these are readings")
    L.append("of its published spec, not its runner's output.")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="markdown-twin differential against the AEO reference spec")
    ap.add_argument("hosts", nargs="*", help="hosts to read (default: the HOSTS environment list)")
    ap.add_argument("--pages", type=int, default=5, help="sitemap pages per door, on top of the home page")
    ap.add_argument("--json", dest="json_path", default=OUT_DEFAULT)
    ap.add_argument("--doc", dest="doc_path", default=DOC_DEFAULT)
    ap.add_argument("--selftest", action="store_true", help="fixture checks, no network")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    hosts = a.hosts or HOSTS
    if not hosts:
        sys.exit("name one or more hosts, or set HOSTS to a comma-separated list")
    report = {
        "tool": "twin_diff.py",
        "read_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference": {"repo": "dodopayments/dualmark", "spec": "AEO Specification v1.0",
                      "read_date": "2026-09-22",
                      "urls": ["https://raw.githubusercontent.com/dodopayments/dualmark/main/spec/conformance.md",
                               "https://raw.githubusercontent.com/dodopayments/dualmark/main/spec/headers.md"]},
        "doors": [],
    }
    for host in hosts:
        pages = sitemap_pages(host, a.pages)
        rows = [page_run(u) for u in pages]
        report["doors"].append({"host": host, "pages": rows})
        fails = sum(1 for p in rows for c in p["ours"] if not c["ok"])
        avg = (sum(p["spec_score"] for p in rows) / len(rows)) if rows else 0
        print("%-22s %d pages   spec %.0f/125   our checks failing: %d" % (host, len(rows), avg, fails))
        for p in rows:
            for c in p["ours"]:
                if not c["ok"]:
                    print("   FAIL %-32s %-38s %s" % (c["check"], urllib.parse.urlsplit(p["url"]).path or "/",
                                                      c["evidence"][:90]))

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
