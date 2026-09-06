#!/usr/bin/env python3
"""cfdash.py -- drive a logged-in Chrome over CDP, one command per call (Fig Tree primitive, 6 Sep 2026).

Why: two dashboards we need (Cloudflare's Bot Submission Form and Pay Per Crawl) have NO API,
and the page never reaches document_idle, which wedges both the Claude-in-Chrome extension and
browserd's single-threaded loop. This connects straight to a Chrome started with
--remote-debugging-port (ops/launch_chrome.cmd), waits only for domcontentloaded + a fixed
settle, and never types a password: if the page is a login screen it says so and stops.

  py scripts/cfdash.py --cdp 9226 goto <url>            open (or reuse) the dash tab, print title + url + login state
  py scripts/cfdash.py --cdp 9226 text [--max 4000]     visible text of the dash tab
  py scripts/cfdash.py --cdp 9226 shot <path.png>       screenshot to disk (read it with the Read tool)
  py scripts/cfdash.py --cdp 9226 click "<text>"        click the first element whose visible text/role name matches
  py scripts/cfdash.py --cdp 9226 fill "<label>" "<v>"  fill an input by its label/placeholder
  py scripts/cfdash.py --cdp 9226 select "<label>" "<option>"
  py scripts/cfdash.py --cdp 9226 eval "<js>"           evaluate JS in the page (debugging)
  py scripts/cfdash.py --cdp 9226 evalfile <path.js>    same, JS read from a file (multi-line scripts survive the shell)
  py scripts/cfdash.py --cdp 9226 pages                 list tabs in that Chrome
Exit 0 ok / 1 failure / 2 login wall or Chrome not reachable. Every command prints one JSON line.
"""
import json, sys, time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DASH = "dash.cloudflare.com"


def out(**k):
    print(json.dumps(k, ensure_ascii=False)); sys.exit(0 if k.get("ok") else (2 if k.get("login_wall") or k.get("unreachable") else 1))


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); sys.exit(2)
    cdp = "http://127.0.0.1:9226"
    if a[0] == "--cdp":
        cdp = a[1] if a[1].startswith("http") else "http://127.0.0.1:" + a[1]; a = a[2:]
    op, args = a[0], a[1:]
    mx = 4000
    if "--max" in args:
        i = args.index("--max"); mx = int(args[i + 1]); args = args[:i] + args[i + 2:]
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp, timeout=8000)
        except Exception as e:
            out(ok=False, unreachable=True, error="no Chrome at %s: %s" % (cdp, str(e)[:120]))
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        pages = ctx.pages
        if op == "pages":
            out(ok=True, pages=[{"i": i, "url": pg.url, "title": pg.title()} for i, pg in enumerate(pages)])
        page = next((pg for pg in pages if DASH in pg.url), None)
        if op == "goto":
            url = args[0]
            if page is None:
                page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PWTimeout:
                pass
            time.sleep(4)
            title = page.title(); cur = page.url
            wall = "/login" in cur or "Manage Your Account" in title
            out(ok=not wall, login_wall=wall, url=cur, title=title,
                note="LOGIN WALL: log in once in THIS Chrome window, then rerun" if wall else "")
        if page is None:
            out(ok=False, error="no dash.cloudflare.com tab in this Chrome; run goto first")
        if op == "text":
            t = page.evaluate("document.body.innerText")
            out(ok=True, url=page.url, chars=len(t), text=t[:mx])
        if op == "shot":
            page.screenshot(path=args[0], full_page=False)
            out(ok=True, path=args[0], url=page.url)
        if op == "click":
            target = args[0]
            loc = page.get_by_role("button", name=target, exact=False)
            if loc.count() == 0: loc = page.get_by_role("tab", name=target, exact=False)
            if loc.count() == 0: loc = page.get_by_role("link", name=target, exact=False)
            if loc.count() == 0: loc = page.get_by_text(target, exact=False)
            if loc.count() == 0:
                out(ok=False, error="nothing matches %r" % target)
            loc.first.click(timeout=10000)
            time.sleep(2.5)
            out(ok=True, clicked=target, url=page.url, title=page.title())
        if op == "fill":
            label, value = args[0], args[1]
            loc = page.get_by_label(label, exact=False)
            if loc.count() == 0: loc = page.get_by_placeholder(label, exact=False)
            if loc.count() == 0:
                out(ok=False, error="no field labelled %r" % label)
            loc.first.fill(value, timeout=10000)
            out(ok=True, filled=label, chars=len(value))
        if op == "select":
            label, option = args[0], args[1]
            loc = page.get_by_label(label, exact=False)
            if loc.count() == 0:
                out(ok=False, error="no select labelled %r" % label)
            loc.first.select_option(label=option, timeout=10000)
            out(ok=True, selected=option)
        if op == "eval":
            out(ok=True, result=page.evaluate(args[0]))
        if op == "evalfile":   # JS from a file — shell quoting mangles multi-line scripts
            out(ok=True, result=page.evaluate(open(args[0], encoding="utf-8").read()))
        out(ok=False, error="unknown op %s" % op)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
