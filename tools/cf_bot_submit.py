#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""cf_bot_submit.py: fill and submit Cloudflare's Bots and Agents Directory application (BotBase) for one of your
own readers, through the logged-in dashboard (tools/cfdash.py over a Chrome on port 9226), from a JSON spec.

Why (22 Sep 2026): Cloudflare has no API for the directory form, and a directory entry should not cost an hour of
clicking. The form is React (base-ui): values are set through the native setter plus an input event, list controls
are opened with pointer events and their options clicked by text. Nothing here types a password; a login wall
stops it.

  python tools/cf_bot_submit.py botbase/mybot.json            fill, open the review, screenshot, STOP
  python tools/cf_bot_submit.py botbase/mybot.json --apply    ... and press the final submit, then read the history
  python tools/cf_bot_submit.py --discover                                             dump the form's fields and every list's options
  python tools/cf_bot_submit.py --history                                              read the Submission history tab
  python tools/cf_bot_submit.py --selftest

Spec keys: name, documentation_url, operator, own (bool), description, access_type (Direct|Intermediary),
categories [..], content_use (Immediate|Reference|Full), attestation (Web Bot Auth|IP list|Reverse DNS),
attestation_fields {label-or-placeholder: value} filled after the attestation method is chosen.
Needs: CLOUDFLARE_ACCOUNT_ID and a Chrome started with --remote-debugging-port=9226 already logged into the
dashboard. Submitting is human-fired: without --apply it stops at the review screen.
Exit 0 when every value read back equals the spec; 1 otherwise; 2 when the Chrome is not up.
"""
import argparse, json, os, pathlib, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFDASH = [sys.executable, str(ROOT / "tools" / "cfdash.py"), "--cdp", "9226"]
SHOTS = ROOT / "botbase"


def account():
    """The Cloudflare account id from your dashboard URL. No default: a wrong id opens another form."""
    a = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not a:
        sys.exit("set CLOUDFLARE_ACCOUNT_ID (the id in your https://dash.cloudflare.com/<id>/ URL)")
    return a


def form_url():
    return f"https://dash.cloudflare.com/{account()}/application-security/botbase/form"


def history_url():
    return f"https://dash.cloudflare.com/{account()}/application-security/botbase/history"

def cf(*args):
    p = subprocess.run(CFDASH + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = (p.stdout.strip().splitlines() or [""])[-1]
    try:
        return json.loads(line)
    except Exception:
        return {"ok": False, "error": (p.stderr or p.stdout)[-300:]}

def evaljs(js):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        # evalfile takes an expression; the scripts use a top-level return, so wrap them in an async function call
        f.write("(async () => {\n" + js + "\n})()"); path = f.name
    try:
        r = cf("evalfile", path)
        res = r.get("result")
        if isinstance(res, str) and res[:1] in "{[":
            try: return json.loads(res)
            except Exception: return res
        return res if res is not None else r
    finally:
        pathlib.Path(path).unlink(missing_ok=True)

HELPERS = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const norm = (s) => String(s || "").trim().replace(/\s+/g, " ");
const press = (el, key) => { for (const t of ["keydown", "keyup"]) el.dispatchEvent(new KeyboardEvent(t, { key, code: key, bubbles: true })); };
const pointer = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, button: 0 })); };
const closeAll = async () => { press(document.activeElement || document.body, "Escape"); await sleep(250); document.body.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true })); await sleep(250); };
const setNative = (el, value) => {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  el.focus(); setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
};
const labelText = (el) => {
  let l = "";
  if (el.id) { const L = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (L) l = norm(L.innerText); }
  if (!l) { const p = el.closest("label"); if (p) l = norm(p.innerText); }
  if (!l) l = el.getAttribute("aria-label") || el.getAttribute("placeholder") || "";
  return l;
};
const fieldBy = (key) => [...document.querySelectorAll("input, textarea")].find((el) => el.name === key || el.placeholder === key || labelText(el) === key || labelText(el).startsWith(key));
const options = () => [...document.querySelectorAll('[role="option"], [role="menuitem"], [role="menuitemcheckbox"]')];
const optionByText = (text) => options().find((o) => norm(o.innerText).toLowerCase().startsWith(String(text).toLowerCase()));
const controlAfterLabel = (text) => {
  const L = [...document.querySelectorAll("label, span, div, p, legend")].find((e) => e.childElementCount === 0 && norm(e.innerText) === text);
  if (!L) return null;
  let n = L;
  for (let i = 0; i < 7 && n; i++) { n = n.parentElement; const b = n && n.querySelector('button[aria-haspopup], [role="combobox"], input[placeholder]'); if (b) return b; }
  return null;
};
const pick = async (control, text) => {
  if (!control) return "control not found";
  control.scrollIntoView({ block: "center" }); control.focus(); pointer(control); await sleep(700);
  let o = optionByText(text);
  if (!o && control.tagName === "INPUT") { setNative(control, text); await sleep(700); o = optionByText(text); }
  if (!o) { const seen = options().map((x) => norm(x.innerText)).slice(0, 30); await closeAll(); return "option not found: " + text + " in " + JSON.stringify(seen); }
  pointer(o); await sleep(500);
  return "picked " + norm(o.innerText).slice(0, 60);
};
const readback = () => {
  const out = {};
  for (const el of document.querySelectorAll("input, textarea")) { const l = labelText(el) || el.name; if (!l || el.type === "hidden") continue; out[l] = el.type === "checkbox" || el.type === "radio" ? (el.checked ? "checked" : "") : el.value; }
  for (const el of document.querySelectorAll('[role="checkbox"], [role="radio"], [role="switch"]')) { const l = labelText(el) || norm(el.parentElement && el.parentElement.innerText).slice(0, 60); out["*" + l] = el.getAttribute("aria-checked"); }
  for (const b of document.querySelectorAll('button[aria-haspopup]')) { const t = norm(b.innerText); if (t) out["button:" + b.id] = t; }
  out.__chips = [...document.querySelectorAll('[data-chip], [role="listitem"], .chip, [class*="Chip"], [class*="chip"], [class*="tag"]')].map((c) => norm(c.innerText)).filter((t) => t && t.length < 60).slice(0, 20);
  return out;
};
"""

DISCOVER = HELPERS + r"""
return (async () => {
  await closeAll();
  const fields = [...document.querySelectorAll("input, textarea, [role=checkbox], [role=radio], button[aria-haspopup]")].map((el) => ({ tag: el.tagName, type: el.type || el.getAttribute("role") || el.getAttribute("aria-haspopup"), name: el.name || "", ph: el.placeholder || "", label: labelText(el), value: (el.value || el.getAttribute("aria-checked") || norm(el.innerText) || "").slice(0, 80) }));
  const lists = {};
  for (const c of document.querySelectorAll('button[aria-haspopup="listbox"], input[placeholder]')) {
    if (c.placeholder === "" && !c.getAttribute("aria-haspopup")) continue;
    c.scrollIntoView({ block: "center" }); c.focus(); pointer(c); await sleep(700);
    lists[labelText(c) || c.placeholder || c.id] = options().map((o) => norm(o.innerText).slice(0, 80));
    await closeAll();
  }
  const buttons = [...document.querySelectorAll("button")].map((b) => norm(b.innerText)).filter((t) => t && t.length < 40).slice(-8);
  return JSON.stringify({ url: location.href, fields, lists, buttons });
})();
"""

def fill_js(spec):
    return HELPERS + r"""
const SPEC = %s;
return (async () => {
  const log = [];
  await closeAll();
  for (const [key, val] of [["name", SPEC.name], ["documentationUrl", SPEC.documentation_url], ["description", SPEC.description]]) {
    const el = fieldBy(key); if (!el) { log.push("missing field " + key); continue; }
    setNative(el, val); log.push(key + " set");
  }
  const op = document.querySelector('input[placeholder="Search operators"]');
  log.push("operator: " + await pick(op, SPEC.operator));
  if (SPEC.own) {
    const cb = [...document.querySelectorAll('[role="checkbox"], input[type="checkbox"]')].find((el) => (labelText(el) || "").includes("I own this bot"));
    if (cb && cb.getAttribute("aria-checked") !== "true" && !cb.checked) { pointer(cb); await sleep(300); }
    log.push("own: " + (cb ? (cb.getAttribute("aria-checked") || String(cb.checked)) : "not found"));
  }
  const radio = [...document.querySelectorAll('[role="radio"], input[type="radio"]')].find((el) => (labelText(el) || norm(el.parentElement.innerText)).startsWith(SPEC.access_type));
  if (radio) { pointer(radio); await sleep(300); log.push("access: " + (radio.getAttribute("aria-checked") || String(radio.checked))); } else log.push("access radio not found");
  const cat = document.querySelector('input[placeholder="Select categories"]');
  for (const c of SPEC.categories || []) { log.push("category " + c + ": " + await pick(cat, c)); await closeAll(); }
  log.push("content use: " + await pick(controlAfterLabel("Content use"), SPEC.content_use));
  await closeAll();
  log.push("attestation: " + await pick(controlAfterLabel("Identity attestation method"), SPEC.attestation));
  await closeAll(); await sleep(800);
  for (const [k, v] of Object.entries(SPEC.attestation_fields || {})) {
    const el = fieldBy(k); if (!el) { log.push("attestation field missing: " + k); continue; }
    setNative(el, v); log.push("attestation field " + k + " set");
  }
  await sleep(400);
  return JSON.stringify({ log, readback: readback() });
})();
""" % json.dumps(spec)

REVIEW = HELPERS + r"""
return (async () => {
  const b = [...document.querySelectorAll("button")].find((x) => norm(x.innerText) === "Review submission");
  if (!b) return JSON.stringify({ error: "no Review submission button" });
  b.scrollIntoView({ block: "center" }); pointer(b); await sleep(2500);
  const dlg = document.querySelector('[role="dialog"]');
  const errors = [...document.querySelectorAll('[role="alert"], [aria-invalid="true"], [class*="error"]')].map((e) => norm(e.innerText)).filter(Boolean).slice(0, 10);
  const buttons = [...document.querySelectorAll("button")].map((x) => norm(x.innerText)).filter((t) => t && t.length < 40).slice(-10);
  return JSON.stringify({ dialog: dlg ? norm(dlg.innerText).slice(0, 3000) : null, page_tail: norm(document.body.innerText).slice(-1500), errors, buttons });
})();
"""

SUBMIT = HELPERS + r"""
return (async () => {
  const scope = document.querySelector('[role="dialog"]') || document;
  const b = [...scope.querySelectorAll("button")].find((x) => /^(submit|submit bot|confirm|submit application|confirm and submit)$/i.test(norm(x.innerText)));
  if (!b) return JSON.stringify({ error: "no submit button", buttons: [...scope.querySelectorAll("button")].map((x) => norm(x.innerText)).filter(Boolean).slice(-10) });
  pointer(b); await sleep(3500);
  return JSON.stringify({ pressed: norm(b.innerText), after: norm(document.body.innerText).slice(-1500), url: location.href });
})();
"""

def goto(url):
    g = cf("goto", url)
    if g.get("unreachable"):
        print("Chrome on 9226 is not reachable; start it with --remote-debugging-port=9226"); sys.exit(2)
    if g.get("login_wall"):
        time.sleep(4)
        g = cf("goto", url)
        if g.get("login_wall"):
            print("LOGIN WALL: log in once in that Chrome window, then rerun"); sys.exit(2)
    time.sleep(3)
    return g

def shot(name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / name
    cf("shot", str(p))
    return p

def compare(spec, rb):
    """Read-back is the truth: every text value must appear verbatim."""
    bad = []
    for want in [spec["name"], spec["documentation_url"], spec["description"]]:
        if want not in rb.values():
            bad.append(want[:40])
    return bad

def selftest():
    s = {"name": "X", "documentation_url": "https://x/y", "description": "d", "operator": "O", "own": True, "access_type": "Direct", "categories": ["Agent"], "content_use": "Reference", "attestation": "Web Bot Auth", "attestation_fields": {}}
    js = fill_js(s)
    assert '"name": "X"' in js and "Review submission" not in js and "Search operators" in js
    assert compare(s, {"Bot name": "X", "Bot documentation URL": "https://x/y", "Short description": "d"}) == []
    assert compare(s, {"Bot name": "X"}) == ["https://x/y", "d"]
    assert "password" not in (HELPERS + DISCOVER + REVIEW + SUBMIT).lower()
    print("selftest OK: 4 checks")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.history:
        goto(history_url())
        t = cf("text", "--max", "6000").get("text", "")
        i = t.find("Submission history"); print(t[i:i + 2500] if i >= 0 else t[-2500:]); return
    goto(form_url())
    if a.discover:
        print(json.dumps(evaljs(DISCOVER), indent=1)); return
    if not a.spec:
        ap.error("spec path or --discover/--history")
    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    filled = evaljs(fill_js(spec))
    if not isinstance(filled, dict):
        print("fill failed:", str(filled)[:400]); sys.exit(1)
    for line in filled["log"]:
        print("  ", line)
    bad = compare(spec, filled["readback"])
    print("read back:", json.dumps({k: v for k, v in filled["readback"].items() if k != "__chips"}, indent=1)[:1500])
    print("chips:", filled["readback"].get("__chips"))
    p = shot("form_filled_%s.png" % time.strftime("%Y-%m-%d_%H%M"))
    print("screenshot", p.relative_to(ROOT))
    if bad:
        print("MISMATCH, not reviewing:", bad); sys.exit(1)
    review = evaljs(REVIEW)
    print("review:", json.dumps(review, indent=1)[:3000])
    p = shot("form_review_%s.png" % time.strftime("%Y-%m-%d_%H%M"))
    print("screenshot", p.relative_to(ROOT))
    if not a.apply:
        print("dry run: stopped at the review. Rerun with --apply to submit."); return
    res = evaljs(SUBMIT)
    print("submit:", json.dumps(res, indent=1)[:2000])
    p = shot("form_submitted_%s.png" % time.strftime("%Y-%m-%d_%H%M"))
    print("screenshot", p.relative_to(ROOT))
    time.sleep(2)
    goto(history_url())
    t = cf("text", "--max", "6000").get("text", "")
    i = t.find("Submission history"); print("history:", (t[i:i + 2000] if i >= 0 else t[-2000:]))

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
