#!/usr/bin/env python3
"""browserd — Axion's own browser door (Fig Tree primitive, 2 Sep 2026).

A local Playwright daemon with a PERSISTENT profile, driven by one-line commands.
Exists because the Chrome extension waits for document_idle and dashboards that never
idle (Etsy Shop Manager, Google Ads) wall it; the in-app pane cannot hold our logins;
computer-use treats browsers as read-only. This door is ours: real Chrome, our profile,
one human login seeds it, every later run is a script.

  py scripts/browserd.py start [--profile etsy] [--port 8765]   # background daemon
    [--max-consecutive-failures N]   # default 5, see LOUD-FAILURE GUARD below
  py scripts/browserd.py goto URL
  py scripts/browserd.py snap [--interactive]                    # aria tree of the page
  py scripts/browserd.py find "text"                             # matching aria lines
  py scripts/browserd.py click  SELECTOR      # playwright selector, e.g. role=button[name="Run a sale"]
  py scripts/browserd.py fill   SELECTOR VALUE
  py scripts/browserd.py setfiles SELECTOR PATH [PATH...]   # set an <input type=file> directly
  py scripts/browserd.py filechooser SELECTOR PATH          # click a button, satisfy its file dialog
  py scripts/browserd.py type   TEXT
  py scripts/browserd.py key    KEY           # Enter, Escape, Tab ...
  py scripts/browserd.py shot   [PATH]        # screenshot -> ops/browserd_shots/
  py scripts/browserd.py eval   JS            # returns JSON
  py scripts/browserd.py text                 # visible innerText
  py scripts/browserd.py checkpoint [NOTE]     # append {ts,url,note} -> ops/browserd_shots/progress_<port>.jsonl
  py scripts/browserd.py resume                # clear a halt (see below), whatever the current state
  py scripts/browserd.py url | wait SECS | status | stop

Every command prints one JSON object. Exit 0 = ok, 1 = error, 2 = daemon not running.
Profile dir: %LOCALAPPDATA%/AxionLabs/browserd/<profile>  (never inside the repo).
Nothing here reads, stores or prints credentials: the human types the login once into
the daemon's own window; the cookie jar lives in the profile dir like any browser's.

LOUD-FAILURE GUARD (built 2 Sep 2026, adopting the MECHANISM from gh_radar
proposal 3 — creator-store-scraper's silent-Cloudflare-block-produces-garbage
failure mode — never its code): a batch loop hammering a wall must never keep
returning confident garbage. The daemon tracks consecutive op failures
(timeouts, target-closed, selector-not-found, any op that comes back
ok:false) per session. After `--max-consecutive-failures` (default 5) in a
row, EVERY further op except `resume`, `status` and `stop` gets
`{"ok": false, "halted": true, "reason": "..."}` instead of running (a
halted daemon must still be stoppable), and a
progress note is written to ops/browserd_shots/HALT_<port>.json with the
last op/url/error. One success resets the counter to zero. Run
`py scripts/browserd.py resume [--port N]` to clear the halt and continue.
Every existing op keeps its exact existing output shape — the guard only
ever intercepts BEFORE an op runs, or writes a side-file, never rewrites an
op's own response.
"""
import argparse, json, os, sys, time, subprocess, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "127.0.0.1"
SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops", "browserd_shots")


def profile_dir(name):
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "AxionLabs", "browserd", name)
    os.makedirs(d, exist_ok=True)
    return d


# ── loud-failure guard: pure logic, no Playwright, no I/O — unit-testable ──────
def new_failure_state(max_failures):
    return {"consec_failures": 0, "halted": False, "halt_reason": None,
            "max_failures": max_failures}


def register_result(fstate, op, ok, error=None):
    """Update fstate after one op's outcome. Returns True the moment this call
    crosses the halt threshold (caller writes the halt note exactly once)."""
    if ok:
        fstate["consec_failures"] = 0
        return False
    fstate["consec_failures"] += 1
    if fstate["consec_failures"] >= fstate["max_failures"] and not fstate["halted"]:
        fstate["halted"] = True
        fstate["halt_reason"] = (f"{fstate['consec_failures']} consecutive op failures "
                                  f"(limit {fstate['max_failures']}); last: {op} — {error}")
        return True
    return False


def build_halt_note(fstate, op, args, url, error):
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "op": op, "args": args, "url": url, "error": error,
            "consec_failures": fstate["consec_failures"],
            "max_consecutive_failures": fstate["max_failures"]}


def build_checkpoint_note(url, note):
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "url": url, "note": note}


# ── server ───────────────────────────────────────────────────────────────────
def serve(profile, port, cdp=None, max_failures=5):
    """cdp: attach to a Chrome the HUMAN launched with --remote-debugging-port (their own
    window, their own login, no automation launch flags). We drive it; we never launch it.
    Chrome 136+ refuses the debugging port on the default profile, so the human launches
    with a separate --user-data-dir and logs in there once."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    if cdp:
        browser = pw.chromium.connect_over_cdp(cdp)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    else:
        ctx = pw.chromium.launch_persistent_context(
            profile_dir(profile), channel="chrome", headless=False,
            viewport={"width": 1400, "height": 900}, locale="en-GB",
        )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    state = {"page": page, "ctx": ctx, "profile": profile, "started": time.time()}
    fstate = new_failure_state(max_failures)
    os.makedirs(SHOTS, exist_ok=True)

    def handle(cmd):
        if state["page"].is_closed():  # human closed the tab/window: reopen, keep the session
            state["page"] = ctx.new_page() if ctx.pages == [] or all(pg.is_closed() for pg in ctx.pages) else ctx.pages[-1]
        p = state["page"]
        op = cmd.get("op")
        a = cmd.get("args", [])
        if op == "status":
            return {"ok": True, "profile": profile, "mode": "cdp-attach" if cdp else "own-profile",
                    "url": p.url, "title": p.title(), "pages": len(ctx.pages),
                    "up_secs": int(time.time() - state["started"]),
                    "halted": fstate["halted"], "consec_failures": fstate["consec_failures"],
                    "max_consecutive_failures": fstate["max_failures"]}
        if op == "resume":
            fstate["halted"] = False
            fstate["consec_failures"] = 0
            fstate["halt_reason"] = None
            return {"ok": True, "resumed": True}
        if op == "checkpoint":  # checkpoint [NOTE] — current page's url, appended not overwritten
            note = build_checkpoint_note(p.url, a[0] if a else cmd.get("note", ""))
            with open(os.path.join(SHOTS, f"progress_{port}.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(note, ensure_ascii=False) + "\n")
            return {"ok": True, "checkpoint": note}
        if op == "pages":
            return {"ok": True, "pages": [{"i": i, "url": pg.url, "title": pg.title()} for i, pg in enumerate(ctx.pages)]}
        if op == "use":  # switch the driven page by index
            state["page"] = ctx.pages[int(a[0])]
            return {"ok": True, "url": state["page"].url}
        if op == "goto":
            p.goto(a[0], wait_until="domcontentloaded", timeout=60000)
            return {"ok": True, "url": p.url, "title": p.title()}
        if op == "url":
            return {"ok": True, "url": p.url, "title": p.title()}
        if op == "wait":
            time.sleep(float(a[0]))
            return {"ok": True}
        if op == "snap":
            tree = p.locator("body").aria_snapshot()
            if cmd.get("interactive"):
                keep = ("button", "link", "textbox", "checkbox", "combobox", "radio", "menuitem",
                        "tab", "switch", "spinbutton", "option", "searchbox")
                tree = "\n".join(l for l in tree.splitlines() if any(f"- {k}" in l for k in keep))
            return {"ok": True, "url": p.url, "tree": tree}
        if op == "find":
            tree = p.locator("body").aria_snapshot()
            q = a[0].lower()
            return {"ok": True, "lines": [l.strip() for l in tree.splitlines() if q in l.lower()][:40]}
        if op == "click":
            loc = p.locator(a[0]).first
            loc.click(timeout=15000)
            return {"ok": True, "url": p.url}
        if op == "fill":
            loc = p.locator(a[0]).first
            loc.fill(a[1], timeout=15000)
            return {"ok": True, "value": loc.input_value()}
        if op == "download":  # download SELECTOR PATH — click, capture the browser download, save to PATH
            loc = p.locator(a[0]).first
            with p.expect_download(timeout=60000) as dl:
                loc.click(timeout=15000)
            d = dl.value
            d.save_as(a[1])
            return {"ok": True, "path": a[1], "suggested": d.suggested_filename, "bytes": os.path.getsize(a[1])}
        if op == "setfiles":  # setfiles SELECTOR PATH [PATH...] — set an <input type=file> directly (no OS dialog)
            loc = p.locator(a[0]).first
            loc.set_input_files(list(a[1:]), timeout=120000)
            return {"ok": True, "selector": a[0], "files": list(a[1:])}
        if op == "filechooser":  # filechooser SELECTOR PATH — click SELECTOR, satisfy the file dialog it opens
            with p.expect_file_chooser(timeout=30000) as fc:
                p.locator(a[0]).first.click(timeout=15000)
            fc.value.set_files(a[1], timeout=120000)
            return {"ok": True, "path": a[1]}
        if op == "select":  # select SELECTOR LABEL  (native <select> by visible label)
            loc = p.locator(a[0]).first
            loc.select_option(label=a[1], timeout=15000)
            return {"ok": True, "value": loc.input_value()}
        if op == "type":
            p.keyboard.type(a[0])
            return {"ok": True}
        if op == "key":
            p.keyboard.press(a[0])
            return {"ok": True}
        if op == "shot":
            path = a[0] if a else os.path.join(SHOTS, time.strftime("%Y%m%d_%H%M%S") + ".png")
            p.screenshot(path=path, full_page=False)
            return {"ok": True, "path": path}
        if op == "eval":
            return {"ok": True, "result": p.evaluate(a[0])}
        if op == "evalfile":  # JS from a file (argv quoting is hostile to JS)
            js = open(a[0], encoding="utf-8").read()
            return {"ok": True, "result": p.evaluate(js)}
        if op == "text":
            return {"ok": True, "text": p.evaluate("document.body.innerText")[: int(cmd.get("max", 20000))]}
        if op == "stop":
            return {"ok": True, "stopping": True}
        return {"ok": False, "error": f"unknown op {op}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *x):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            cmd = json.loads(self.rfile.read(n) or b"{}")
            op = cmd.get("op")

            if fstate["halted"] and op not in ("resume", "status", "stop"):
                # short-circuit BEFORE the op runs — never let a halted session
                # keep returning confident garbage. Blocked calls do not affect
                # the counter; only resume clears it.
                out = {"ok": False, "halted": True, "reason": fstate["halt_reason"]}
            else:
                cur_url = None
                try:
                    out = handle(cmd)
                    ok = out.get("ok") is not False
                    err = None if ok else out.get("error", f"{op} returned ok:false")
                    cur_url = out.get("url") or (state["page"].url if not state["page"].is_closed() else None)
                except Exception as e:
                    err = f"{type(e).__name__}: {str(e)[:400]}"
                    cur_url = state["page"].url if not state["page"].is_closed() else None
                    out = {"ok": False, "error": err, "url": cur_url}
                    ok = False
                crossed = register_result(fstate, op, ok, err)
                if crossed:
                    note = build_halt_note(fstate, op, cmd.get("args", []), cur_url, err)
                    try:
                        with open(os.path.join(SHOTS, f"HALT_{port}.json"), "w", encoding="utf-8") as f:
                            json.dump(note, f, indent=1)
                    except Exception:
                        pass  # the halt itself must never crash the response path
            body = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if out.get("stopping"):
                raise KeyboardInterrupt

    srv = HTTPServer((HOST, port), H)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if not cdp:  # never close the human's own browser on detach
            try:
                ctx.close()
            except Exception:
                pass
        pw.stop()


# ── client ───────────────────────────────────────────────────────────────────
def send(port, cmd, timeout=90):
    req = urllib.request.Request(f"http://{HOST}:{port}/", data=json.dumps(cmd).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"daemon not running on {port}: {e}", "code": 2}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 console must never crash a read
    except Exception:
        pass
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("op")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--profile", default="etsy")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--max", type=int, default=20000)
    ap.add_argument("--cdp", default=None, help="attach to a human-launched Chrome, e.g. http://127.0.0.1:9222")
    ap.add_argument("--max-consecutive-failures", type=int, default=5,
                     help="loud-failure guard threshold (default 5); halt until `resume`")
    a = ap.parse_args()
    if a.op == "start":
        if a.serve:
            serve(a.profile, a.port, a.cdp, max_failures=a.max_consecutive_failures)
            return
        probe = send(a.port, {"op": "status"}, timeout=5)
        if probe.get("ok"):
            print(json.dumps({"ok": True, "already_running": True}))
            return
        if probe.get("code") != 2:
            # 5 Sep 2026: a daemon ANSWERED but its browser is dead (the Chrome it
            # attached to was closed). Windows lets a second bind succeed on the same
            # port, so the old `start` spawned another daemon that could never be
            # reached — four dead daemons stacked on 8770 and every restart "failed"
            # while the real cause stayed invisible. Refuse to stack; say what to kill.
            print(json.dumps({
                "ok": False,
                "error": f"a daemon is already listening on {a.port} but its browser is gone",
                "detail": probe.get("error"),
                "why": "spawning another would stack a second listener on the same port "
                       "and the dead one would keep answering",
                "fix": f"kill the process holding the port, then start again: "
                       f"netstat -ano | findstr 127.0.0.1:{a.port}  ->  taskkill /PID <pid> /F",
            }))
            sys.exit(1)
        os.makedirs(SHOTS, exist_ok=True)
        log = open(os.path.join(SHOTS, f"daemon_{a.port}.log"), "ab")
        flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "start", "--serve",
                          "--profile", a.profile, "--port", str(a.port),
                          "--max-consecutive-failures", str(a.max_consecutive_failures)]
                         + (["--cdp", a.cdp] if a.cdp else []),
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags)
        for _ in range(60):
            time.sleep(0.5)
            r = send(a.port, {"op": "status"}, timeout=3)
            if r.get("ok"):
                print(json.dumps(r))
                return
        print(json.dumps({"ok": False, "error": "daemon did not come up; see ops/browserd_shots/daemon log"}))
        sys.exit(1)
    if a.op in ("help", "--help"):
        print(__doc__)
        return
    r = send(a.port, {"op": a.op, "args": a.args, "interactive": a.interactive, "max": a.max})
    print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r.get("ok") else r.get("code", 1))


if __name__ == "__main__":
    main()
