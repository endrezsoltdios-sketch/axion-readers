#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""guard.py - local policy decision and audit for tool calls. Our first field is our own Claude Code session: the adapter turns a
PreToolUse tool call into a Request (principal, resource, action) and policy.yaml decides. Adapters classify; only policy decides.
  request -> policy decision (ALLOW | DENY | ASK) -> evidence (.guard/runs/<run>/events.jsonl)
Order is fixed: explicit DENY beats ASK beats ALLOW; no match = DENY. Malformed request = DENY. Missing policy = DENY.
  py scripts/guard.py init                                              # writes .guard/policy.yaml with our own rules
  py scripts/guard.py check --principal claude-code --resource secret://.env --action read [--json]
  py scripts/guard.py explain --principal claude-code --resource fs://root --action delete
  py scripts/guard.py policy validate | policy list
  py scripts/guard.py hook [--shadow]                                   # stdin = Claude Code PreToolUse JSON; exit 2 on DENY
  cat requests.jsonl | py scripts/guard.py check --json                 # one decision per line; pipe to jq
  py scripts/guard.py request req.json [--json]                         # full pipeline through the adapter registry (mock:// in v0)
  py scripts/guard.py logs [--run <id>] [--all] [--json]                # friendly view over .guard/runs/*/events.jsonl
  py scripts/guard.py replay autonomous/traces/2026-09-16.jsonl [--json]   # re-decide a real day, execute nothing
  py scripts/guard.py audit autonomous/traces/2026-09-16.jsonl [--json]    # findings: redundant identical calls, overreach vs policy
  py scripts/guard.py seal --run 2026-09-16 ; py scripts/guard.py verify .guard/runs/2026-09-16/events.sealed.jsonl   # hash chain
  py scripts/guard.py selftest                                          # contract, adapter, evidence checks
GUARD_SHADOW=1 makes `hook` log its decision and never block (shadow mode beside the live hook). No network, no keys."""
import argparse, fnmatch, json, os, re, sys, time, uuid
from pathlib import Path

VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parents[1]
GUARD_DIR = ROOT / ".guard"
POLICY = GUARD_DIR / "policy.yaml"
EFFECTS = ("deny", "ask", "allow")

DEFAULT_POLICY = {
    "version": 0,
    "policy": "claude-code",
    "note": "Encodes .claude/hooks/security_check.py as data (16 Sep 2026). Adapters classify; this file decides. "
            "Default DENY; explicit deny beats ask beats allow. First knob to turn: fs://engine/core write -> ask.",
    "rules": [
        {"name": "credentials-file", "effect": "deny", "principal": "claude-code", "resource": "secret://*", "actions": ["read", "write"]},
        {"name": "root-wipe", "effect": "deny", "principal": "claude-code", "resource": "fs://root", "actions": ["delete"]},
        {"name": "device-wipe", "effect": "deny", "principal": "claude-code", "resource": "fs://device", "actions": ["wipe"]},
        {"name": "system-perms", "effect": "deny", "principal": "claude-code", "resource": "fs://system", "actions": ["chmod"]},
        {"name": "engine-core-delete", "effect": "deny", "principal": "claude-code", "resource": "fs://engine/core", "actions": ["delete"]},
        {"name": "broker-execution", "effect": "deny", "principal": "claude-code", "resource": "broker://*", "actions": ["execute"]},
        {"name": "git-history", "effect": "deny", "principal": "claude-code", "resource": "git://origin/master", "actions": ["force_push", "destroy_history"]},
        {"name": "git-reset-hard", "effect": "deny", "principal": "claude-code", "resource": "git://local", "actions": ["reset_hard"]},
        {"name": "prop-firm-mode", "effect": "deny", "principal": "claude-code", "resource": "config://PROP_FIRM_MODE", "actions": ["disable"]},
        {"name": "pipe-to-shell", "effect": "deny", "principal": "claude-code", "resource": "net://download", "actions": ["execute"]},
        {"name": "engine-core-write", "effect": "allow", "principal": "claude-code", "resource": "fs://engine/core", "actions": ["write"]},
        {"name": "shell", "effect": "allow", "principal": "claude-code", "resource": "shell://local", "actions": ["execute"]},
        {"name": "repo-files", "effect": "allow", "principal": "claude-code", "resource": "fs://repo", "actions": ["read", "write"]},
        {"name": "other-tools", "effect": "allow", "principal": "claude-code", "resource": "tool://*", "actions": ["use"]},
    ],
}

# ---------------------------------------------------------------- policy
def load_policy(path=POLICY):
    """Returns (policy_dict or None, errors list)."""
    if not Path(path).is_file():
        return None, [f"no policy at {path} (run: guard init)"]
    try:
        import yaml
        pol = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - a broken policy is reported as a line
        return None, [f"policy unreadable: {e}"]
    return pol, validate(pol)


def validate(pol):
    errs = []
    if not isinstance(pol, dict):
        return ["policy is not a mapping"]
    if pol.get("version") != 0:
        errs.append("version must be 0")
    rules = pol.get("rules")
    if not isinstance(rules, list) or not rules:
        return errs + ["rules must be a non-empty list"]
    names = set()
    for i, r in enumerate(rules):
        tag = f"rule {i} ({r.get('name', '?') if isinstance(r, dict) else '?'})"
        if not isinstance(r, dict):
            errs.append(tag + ": not a mapping"); continue
        for k in ("name", "effect", "principal", "resource", "actions"):
            if k not in r:
                errs.append(tag + f": missing {k}")
        if r.get("effect") not in EFFECTS:
            errs.append(tag + f": effect must be one of {EFFECTS}")
        if not isinstance(r.get("actions"), list) or not all(isinstance(a, str) for a in r.get("actions", [])):
            errs.append(tag + ": actions must be a list of strings")
        if r.get("name") in names:
            errs.append(tag + ": duplicate name")
        names.add(r.get("name"))
    return errs


def matches(rule, req):
    return (fnmatch.fnmatchcase(req["principal"], str(rule["principal"]))
            and fnmatch.fnmatchcase(req["resource"], str(rule["resource"]))
            and (req["action"] in rule["actions"] or "*" in rule["actions"]))


def evaluate(pol, req):
    """One Request -> one Decision. Never raises."""
    base = {"policy": pol.get("policy") if isinstance(pol, dict) else None, "rule": None, "matched": []}
    if not isinstance(req, dict) or any(not isinstance(req.get(k), str) or not req.get(k)
                                        for k in ("principal", "resource", "action")):
        return {**base, "decision": "DENY", "reason": "malformed request (principal, resource, action must be non-empty strings)"}
    if pol is None or validate(pol):
        return {**base, "decision": "DENY", "reason": "no valid policy"}
    hit = [r for r in pol["rules"] if matches(r, req)]
    base["matched"] = [{"rule": r["name"], "effect": r["effect"]} for r in hit]
    for effect, word in (("deny", "DENY"), ("ask", "ASK"), ("allow", "ALLOW")):
        for r in hit:
            if r["effect"] == effect:
                return {**base, "decision": word, "rule": r["name"], "reason": f"rule {r['name']}: {effect} {req['action']} on {req['resource']}"}
    return {**base, "decision": "DENY", "reason": "no rule matched (default deny)"}


# ---------------------------------------------------------------- adapter: Claude Code tool call -> Request
# Starter shell classifier: credential files, root wipes, pipe-to-shell, git history, broker calls. Grow it, or add an adapter.
# A classifier says WHAT is being touched and with WHICH verb; it never says whether that is allowed.
_SHELL = [
    (r"(>|tee|write|out-file|set-content)[^\n]*\.env\b", re.I, "secret://.env", "write"),
    (r"(cat|type|echo|print|get-content)[^\n]*(\.env)\b", re.I, "secret://.env", "read"),
    (r"rm\s+-[rRfF]+.*(/home/\w+|/srv|/opt|/var/www)\b", 0, "fs://root", "delete"),
    (r"\bmkfs\b|\bshred\b|\bdd\b[^\n]*\bof=/dev/|>\s*/dev/(?:sd|nvme|hd|disk)|:\(\)\s*\{\s*:\s*\|\s*:?\s*&\s*\}\s*;\s*:", 0, "fs://device", "wipe"),
    (r"(?:Remove-Item|rmdir|\brd\b|\bdel\b)[^\n|;&]*?(?:-Recurse|/s\b)[^\n|;&]*?(?:[A-Za-z]:\\(?:\s|\*|$)|\$env:USERPROFILE|\$HOME|(?:^|\s)~(?=\s|$))", re.I, "fs://root", "delete"),
    (r"(?<![\w-])format\s+[A-Za-z]:", re.I, "fs://device", "wipe"),
    (r"(?:\bcurl\b|\bwget\b|\biwr\b|Invoke-WebRequest)[^\n]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b|(?:\biwr\b|Invoke-WebRequest)[^\n]*\|\s*iex\b|\biex\s*\(\s*(?:iwr|Invoke-WebRequest)", re.I, "net://download", "execute"),
    (r"\b(?:chmod|chown)\s+(?:-[A-Za-z]*R|--recursive)[^\n|;&]*\s(?:/(?:etc|usr|var|bin|boot|lib|sbin)\b|/\s*$|/\*|C:\\Windows)", re.I, "fs://system", "chmod"),
    (r"git\s+push\b[^\n]*--delete\b[^\n]*\b(main|master)\b|git\s+reflog\s+expire[^\n]*--expire(?:-unreachable)?=now|git\s+(?:filter-branch|filter-repo)\b", 0, "git://origin/master", "destroy_history"),
    (r"(create_open_position|place_order|market_order|execute_trade|open_position|close_position|submit_order|deal_reference)\s*\(", 0, "broker://engine", "execute"),
    (r"(ig\.create_open_position|ig\.close_open_position|t212\.(buy|sell)|requests\.(post|put)[^\n]*(ig-interface|trading212))", re.I, "broker://ig", "execute"),
    (r"git\s+reset\s+--hard[^\n]*(main|master|HEAD)", re.I, "git://local", "reset_hard"),
    (r"PROP_FIRM_MODE\s*=\s*False", 0, "config://PROP_FIRM_MODE", "disable"),
    (r"(\brm\s|\bdel\s|remove-item)[^\n]*(trading_engine|supervisor\.py|swarm_vote\.py)", re.I, "fs://engine/core", "delete"),
]
_CRITICAL = ("trading_engine.py", "supervisor.py", "swarm_vote.py")


def _catastrophic_rm(cmd):
    rec = re.search(r"\brm\b[^\n|;&]*?(?:-[A-Za-z]*[rR]|--recursive)", cmd)
    force = re.search(r"\brm\b[^\n|;&]*?(?:-[A-Za-z]*[fF]|--force)", cmd)
    return bool(rec and force and (re.search(r"--no-preserve-root", cmd) or re.search(
        r"(?:^|[\s=])(?:/|/\*|~|~/|\$HOME|\$\{HOME\}|\$env:USERPROFILE|\.|\./|\.\.|\*)(?=$|\s)", cmd)))


def _force_push(cmd):
    return bool(re.search(r"git\s+push\b", cmd) and re.search(r"\b(main|master)\b", cmd) and
                (re.search(r"--force(?!-with-lease)\b", cmd) or re.search(r"(?:^|\s)-\w*f\b", cmd)))


def classify(tool_name, tool_input, principal="claude-code"):
    """Claude Code tool call -> Request. Pure function, never raises on odd input."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    res, act = f"tool://{tool_name or 'unknown'}", "use"
    if tool_name in ("Bash", "PowerShell"):
        cmd = tool_input.get("command", "") or ""
        res, act = "shell://local", "execute"
        for pat, flags, r, a in _SHELL:
            if re.search(pat, cmd, flags):
                res, act = r, a
                break
        else:
            if _catastrophic_rm(cmd):
                res, act = "fs://root", "delete"
            elif _force_push(cmd):
                res, act = "git://origin/master", "force_push"
    elif tool_name in ("Write", "Edit"):
        path = tool_input.get("file_path", "") or ""
        content = tool_input.get("new_string", "") or tool_input.get("content", "") or ""
        if path.endswith(".env"):
            res, act = "secret://.env", "write"
        elif re.search(r"PROP_FIRM_MODE\s*=\s*False", content):
            res, act = "config://PROP_FIRM_MODE", "disable"
        elif any(c in path for c in _CRITICAL):
            res, act = "fs://engine/core", "write"
        else:
            res, act = "fs://repo", "write"
    return {"version": 0, "request_id": "req_" + uuid.uuid4().hex[:12], "principal": {"type": "agent", "id": principal},
            "resource": res, "action": act, "tool": tool_name, "input": tool_input}


def flat(req):
    return {"principal": req["principal"]["id"], "resource": req["resource"], "action": req["action"]}


def normalise(raw, principal="claude-code"):
    """Any request shape on the wire -> the canonical Request. Resource may be a URI string or {type, uri}; principal a string
    or {type, id}. Missing pieces stay missing (evaluate() then denies as malformed). Never raises."""
    if not isinstance(raw, dict):
        return {"version": 0, "request_id": "req_" + uuid.uuid4().hex[:12], "principal": {"type": "agent", "id": ""},
                "resource": "", "action": "", "input": None, "malformed": True}
    p = raw.get("principal", principal)
    p = p if isinstance(p, dict) else {"type": "agent", "id": p if isinstance(p, str) else ""}
    r = raw.get("resource", "")
    r = r.get("uri", "") if isinstance(r, dict) else r
    return {"version": 0, "request_id": raw.get("request_id") or "req_" + uuid.uuid4().hex[:12], "run_id": raw.get("run_id"),
            "principal": {"type": p.get("type", "agent"), "id": p.get("id", "")}, "resource": r if isinstance(r, str) else "",
            "action": raw.get("action", "") if isinstance(raw.get("action", ""), str) else "",
            "operation": raw.get("operation"), "input": raw.get("input"), "context": raw.get("context") or {}}


def rtype(resource):
    """postgres://prod/customers -> postgres. The only thing the registry looks at."""
    return resource.split("://", 1)[0] if isinstance(resource, str) and "://" in resource else ""


# ---------------------------------------------------------------- adapters: know HOW to execute, never WHETHER
class MockAdapter:
    """mock://* resources. Records what it was asked to do and returns a structured Result. The contract every adapter meets."""
    name = "mock"

    def __init__(self):
        self.calls = []

    def match(self, req):
        return rtype(req["resource"]) == "mock"

    def execute(self, req):
        self.calls.append(req["request_id"])
        return {"status": "completed", "data": {"echo": {"resource": req["resource"], "action": req["action"],
                                                          "operation": req.get("operation"), "input": req.get("input")}},
                "metadata": {"adapter": self.name}}


class Registry:
    def __init__(self, adapters=()):
        self.adapters = list(adapters)

    def register(self, adapter):
        self.adapters.append(adapter)
        return self

    def resolve(self, req):
        for a in self.adapters:
            if a.match(req):
                return a
        return None


def execute(pol, registry, req, run_id=None):
    """The whole pipeline: request.created -> policy.evaluated -> (denied | approval_required | execution) -> Result envelope."""
    append_event(req, {"decision": "-", "rule": None, "reason": "request received"}, run_id=run_id, kind="request.created")
    dec = evaluate(pol, flat(req))
    append_event(req, dec, run_id=run_id)
    if dec["decision"] == "DENY":
        return {"status": "denied", "error": {"code": "denied", "message": dec["reason"]}, "decision": dec}
    if dec["decision"] == "ASK":
        apr = {"approval_id": "apr_" + uuid.uuid4().hex[:12], "request_id": req["request_id"], "required": 1,
               "approvers": [{"type": "human"}], "expires_in": 300}
        append_event(req, dec, run_id=run_id, kind="approval.requested")
        return {"status": "approval_required", "approval": apr, "decision": dec}
    adapter = registry.resolve(req)
    if adapter is None:
        append_event(req, dec, run_id=run_id, kind="execution.failed")
        return {"status": "failed", "error": {"code": "no_adapter", "message": f"no adapter for resource type '{rtype(req['resource'])}'"},
                "decision": dec}
    append_event(req, dec, run_id=run_id, kind="execution.started")
    try:
        res = adapter.execute(req)
        append_event(req, dec, run_id=run_id, kind="execution.completed")
        return {**res, "decision": dec}
    except Exception as e:  # noqa: BLE001 - an adapter failure is a Result, not a crash
        append_event(req, dec, run_id=run_id, kind="execution.failed")
        return {"status": "failed", "error": {"code": "adapter_error", "message": str(e)}, "decision": dec}


def default_registry():
    return Registry([MockAdapter()])


# ---------------------------------------------------------------- audit
def append_event(req, dec, run_id=None, tool=None, kind="policy.evaluated"):
    run_id = run_id or req.get("run_id") or os.environ.get("GUARD_RUN_ID") or time.strftime("%Y-%m-%d")
    d = GUARD_DIR / "runs" / run_id
    try:
        d.mkdir(parents=True, exist_ok=True)
        ev = {"version": 0, "event_id": "evt_" + uuid.uuid4().hex[:12], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "run_id": run_id, "request_id": req.get("request_id"), "type": kind, "principal": req["principal"]["id"],
              "resource": req["resource"], "action": req["action"], "tool": tool, "decision": dec["decision"], "rule": dec["rule"],
              "reason": dec["reason"]}
        with open(d / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass  # audit failure must never change a decision


# ---------------------------------------------------------------- commands
def cmd_init(a):
    import yaml
    if POLICY.is_file() and not a.force:
        print(f"[exists: {POLICY}; use --force to overwrite]")
        return 1
    (GUARD_DIR / "runs").mkdir(parents=True, exist_ok=True)
    POLICY.write_text(yaml.safe_dump(DEFAULT_POLICY, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    print(f"wrote {POLICY} ({len(DEFAULT_POLICY['rules'])} rules) and {GUARD_DIR / 'runs'}/")
    return 0


def _req_from_args(a):
    return {"principal": a.principal, "resource": a.resource, "action": a.action}


def cmd_check(a):
    pol, errs = load_policy()
    if not a.resource:  # JSONL on stdin: cat requests.jsonl | guard check --json | jq 'select(.decision=="DENY")'
        n = 0
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                raw = None
            req = normalise(raw, principal=a.principal)
            dec = evaluate(pol if not errs else None, flat(req))
            n += 1
            if a.json:
                print(json.dumps({"request_id": req["request_id"], "resource": req["resource"], "action": req["action"], **dec},
                                 ensure_ascii=False))
            else:
                print(f"{dec['decision']:5}  {req['principal']['id']}  {req['resource']}  {req['action']}  [{dec['rule'] or 'default'}]")
        if n == 0:
            print("[no --resource and nothing on stdin]"); return 2
        return 0
    dec = evaluate(pol if not errs else None, _req_from_args(a))
    if a.json:
        print(json.dumps({**dec, "errors": errs}, ensure_ascii=False))
    else:
        print(dec["decision"])
        print(f"policy: {dec['policy']}  rule: {dec['rule']}  reason: {dec['reason']}")
        for e in errs:
            print("policy error: " + e)
    return {"ALLOW": 0, "ASK": 3, "DENY": 2}[dec["decision"]]


def cmd_explain(a):
    pol, errs = load_policy()
    dec = evaluate(pol if not errs else None, _req_from_args(a))
    if a.json:
        print(json.dumps({**dec, "errors": errs}, ensure_ascii=False)); return 0
    print(dec["decision"])
    if not dec["matched"]:
        print("matched: nothing -> default deny" if not errs else "policy errors: " + "; ".join(errs))
    for m in dec["matched"]:
        print(f"  {'WINNER ' if m['rule'] == dec['rule'] else '       '}{m['effect']:5}  {m['rule']}")
    print("order: explicit deny > ask > allow > default deny")
    return 0


def cmd_policy(a):
    pol, errs = load_policy()
    if a.what == "validate":
        print("policy OK" if not errs else "\n".join("policy error: " + e for e in errs))
        return 0 if not errs else 1
    if pol is None:
        print(errs[0]); return 1
    for r in pol["rules"]:
        print(f"{r['effect']:5}  {r['principal']:12}  {r['resource']:26}  {', '.join(r['actions'])}   [{r['name']}]")
    return 0


def cmd_hook(a):
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0  # unreadable hook input = do not block on our own failure (same contract as security_check)
    req = classify(data.get("tool_name", ""), data.get("tool_input", {}))
    pol, errs = load_policy()
    dec = evaluate(pol if not errs else None, flat(req))
    append_event(req, dec, tool=req["tool"])
    shadow = os.environ.get("GUARD_SHADOW") == "1" or getattr(a, "shadow", False)
    if dec["decision"] == "DENY" and not shadow:
        print(f"GUARD DENY: {dec['reason']}", file=sys.stderr)
        return 2
    if dec["decision"] == "ASK" and not shadow:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                 "permissionDecisionReason": "GUARD ASK: " + dec["reason"]}}))
    return 0


def cmd_request(a):
    """guard request req.json: the full pipeline on one canonical request, through the adapter registry. Mock adapter only in v0."""
    try:
        raw = json.loads(Path(a.file).read_text(encoding="utf-8")) if a.file != "-" else json.load(sys.stdin)
    except (OSError, ValueError) as e:
        print(f"[request unreadable: {e}]"); return 2
    pol, errs = load_policy()
    res = execute(pol if not errs else None, default_registry(), normalise(raw, principal=a.principal))
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        d = res["decision"]
        print(f"{res['status'].upper():18} {d['decision']}  rule: {d['rule']}  reason: {d['reason']}")
        if res.get("error"):
            print(f"error: {res['error']['message']}")
        if res.get("approval"):
            print(f"approval: {res['approval']['approval_id']} (expires in {res['approval']['expires_in']}s)")
    return {"completed": 0, "approval_required": 3, "denied": 2, "failed": 1}[res["status"]]


def cmd_logs(a):
    runs = sorted((GUARD_DIR / "runs").glob("*")) if (GUARD_DIR / "runs").is_dir() else []
    if a.run:
        runs = [r for r in runs if r.name == a.run]
    if not runs:
        print("[no runs]" + (f" named {a.run}" if a.run else "")); return 1
    for r in runs[-1:] if not a.run and not a.all else runs:
        f = r / "events.jsonl"
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if a.json:
                print(line)
            else:
                print(f"{ev.get('ts')}  {ev.get('run_id')}  {ev.get('type'):20} {str(ev.get('decision')):5}  "
                      f"{ev.get('resource')} {ev.get('action')}  [{ev.get('rule') or '-'}]")
    return 0


# ---------------------------------------------------------------- evidence: seal, verify, audit (method: ToolReplay, read 16 Sep 2026; ours)
import hashlib


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal_lines(lines):
    """Hash-chain a list of event dicts: each gets prev + sha256(prev + canonical(event without seal))."""
    prev, out = "0" * 64, []
    for ev in lines:
        body = {k: v for k, v in ev.items() if k not in ("seal", "prev")}
        h = hashlib.sha256((prev + _canon(body)).encode()).hexdigest()
        out.append({**body, "prev": prev, "seal": h})
        prev = h
    return out


def verify_lines(lines):
    """Returns (ok, first_broken_index or None)."""
    prev = "0" * 64
    for i, ev in enumerate(lines):
        body = {k: v for k, v in ev.items() if k not in ("seal", "prev")}
        if ev.get("prev") != prev or ev.get("seal") != hashlib.sha256((prev + _canon(body)).encode()).hexdigest():
            return False, i
        prev = ev.get("seal")
    return True, None


def _read_jsonl(path):
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def cmd_seal(a):
    run = GUARD_DIR / "runs" / a.run / "events.jsonl"
    if not run.is_file():
        print(f"[no events for run {a.run}]"); return 1
    sealed = seal_lines(_read_jsonl(run))
    out = run.with_name("events.sealed.jsonl")
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in sealed), encoding="utf-8")
    print(f"sealed {len(sealed)} events -> {out}  head {sealed[-1]['seal'][:16] if sealed else '-'}")
    return 0


def cmd_verify(a):
    if not Path(a.file).is_file():
        print(f"[no such file: {a.file}]"); return 1
    ok, bad = verify_lines(_read_jsonl(a.file))
    print("VERIFIED chain intact" if ok else f"BROKEN at index {bad}")
    return 0 if ok else 1


MUTATORS = ("Write", "Edit", "NotebookEdit", "Bash", "PowerShell")


def audit_trace(records, pol):
    """Findings over a trace: redundant identical calls with no mutation between, and overreach (a call the policy denies)."""
    findings, seen = [], {}
    for i, (tool, tin, raw) in enumerate(records):
        key = tool + ":" + _canon(tin)
        if key in seen and seen[key][1] == i - 1 and tool not in MUTATORS:
            findings.append({"index": i, "kind": "redundant-call", "tool": tool,
                             "detail": f"identical to index {seen[key][0]} with nothing between"})
        seen[key] = (seen.get(key, (i,))[0], i)
        if pol is not None:
            dec = evaluate(pol, flat(classify(tool, tin)))
            if dec["decision"] != "ALLOW":
                findings.append({"index": i, "kind": "overreach", "tool": tool, "detail": f"{dec['decision']} by {dec['rule'] or 'default deny'}"})
    return findings


def cmd_audit(a):
    pol, errs = load_policy()
    recs = list(_iter_trace(a.file))
    f = audit_trace(recs, pol if not errs else None)
    tools = {}
    for tool, _t, _r in recs:
        tools[tool] = tools.get(tool, 0) + 1
    if a.json:
        print(json.dumps({"file": str(a.file), "calls": len(recs), "tools": tools, "findings": f}, ensure_ascii=False, indent=1))
        return 1 if f else 0
    print(f"calls: {len(recs)}  tools: {', '.join(f'{k} {v}' for k, v in sorted(tools.items(), key=lambda kv: -kv[1]))}")
    print(f"findings: {len(f)}")
    for x in f:
        print(f"  index {x['index']}: {x['kind']}: {x['tool']} {x['detail']}")
    return 1 if f else 0


def _iter_trace(path):
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "tool_name" in r:
            yield r["tool_name"], r.get("tool_input", {}), r
        elif "tool" in r:  # autonomous/traces format: context = command or file_path
            t, ctx = r["tool"], r.get("context", "") or ""
            yield t, ({"command": ctx} if t in ("Bash", "PowerShell") else {"file_path": ctx}), r


def cmd_replay(a):
    pol, errs = load_policy()
    if errs:
        print("policy errors: " + "; ".join(errs)); return 1
    tally, flagged, n = {"ALLOW": 0, "DENY": 0, "ASK": 0}, [], 0
    for tool, tin, raw in _iter_trace(a.file):
        n += 1
        req = classify(tool, tin)
        dec = evaluate(pol, flat(req))
        tally[dec["decision"]] += 1
        if dec["decision"] != "ALLOW":
            flagged.append({"decision": dec["decision"], "rule": dec["rule"], "tool": tool, "resource": req["resource"],
                            "action": req["action"], "ts": raw.get("ts"), "context": str(tin.get("command") or tin.get("file_path"))[:160]})
    out = {"file": str(a.file), "requests": n, **tally, "flagged": flagged}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1)); return 0
    print(f"replay {a.file}: {n} requests -> ALLOW {tally['ALLOW']}  ASK {tally['ASK']}  DENY {tally['DENY']}  (nothing executed)")
    for f in flagged:
        print(f"  {f['decision']:5} {f['rule'] or '-':20} {f['tool']:10} {f['resource']} {f['action']}  {f['ts']}\n        {f['context']}")
    return 0


def selftest():
    fails = []
    pol = json.loads(json.dumps(DEFAULT_POLICY))
    T = [  # (principal, resource, action, expected) - the contractual guarantees
        ("claude-code", "shell://local", "execute", "ALLOW"),
        ("claude-code", "secret://.env", "read", "DENY"),
        ("claude-code", "secret://anything", "write", "DENY"),          # glob resource
        ("claude-code", "shell://local", "delete", "DENY"),             # unknown action -> default deny
        ("nobody", "shell://local", "execute", "DENY"),                 # unknown principal -> default deny
        ("claude-code", "mock://database", "read", "DENY"),             # unknown resource -> default deny
        ("claude-code", "fs://engine/core", "write", "ALLOW"),
        ("claude-code", "fs://engine/core", "delete", "DENY"),
    ]
    for p, r, ac, exp in T:
        got = evaluate(pol, {"principal": p, "resource": r, "action": ac})["decision"]
        if got != exp:
            fails.append(f"{p} {r} {ac}: expected {exp} got {got}")
    # explicit deny beats allow; ask beats allow; deny beats ask
    p2 = {"version": 0, "policy": "t", "rules": [
        {"name": "a", "effect": "allow", "principal": "x", "resource": "mock://db", "actions": ["write"]},
        {"name": "k", "effect": "ask", "principal": "x", "resource": "mock://*", "actions": ["write"]},
        {"name": "d", "effect": "deny", "principal": "*", "resource": "mock://db", "actions": ["write"]}]}
    if evaluate(p2, {"principal": "x", "resource": "mock://db", "action": "write"})["rule"] != "d":
        fails.append("explicit deny did not beat ask and allow")
    p2["rules"].pop()
    if evaluate(p2, {"principal": "x", "resource": "mock://db", "action": "write"})["decision"] != "ASK":
        fails.append("ask did not beat allow")
    for bad in ({"principal": "x"}, {"principal": "", "resource": "a", "action": "b"}, "junk", None):
        if evaluate(pol, bad)["decision"] != "DENY":
            fails.append(f"malformed request not denied: {bad!r}")
    if evaluate(None, {"principal": "x", "resource": "a", "action": "b"})["decision"] != "DENY":
        fails.append("missing policy not denied")
    if evaluate({"version": 1, "rules": []}, {"principal": "x", "resource": "a", "action": "b"})["decision"] != "DENY":
        fails.append("invalid policy not denied")
    if validate({"version": 0, "rules": [{"name": "a", "effect": "maybe", "principal": "x", "resource": "y", "actions": ["z"]}]}) == []:
        fails.append("validate accepted an unknown effect")
    if evaluate(pol, {"principal": "claude-code", "resource": "fs://engine/core", "action": "delete"})["rule"] != "engine-core-delete":
        fails.append("explanation does not identify the winning rule")
    # wire shapes: resource as {type, uri}, principal as {type, id}, JSONL-style raw dicts
    n = normalise({"principal": {"type": "agent", "id": "billing"}, "resource": {"type": "postgres", "uri": "postgres://prod/c"}, "action": "read"})
    if flat(n) != {"principal": "billing", "resource": "postgres://prod/c", "action": "read"} or rtype(n["resource"]) != "postgres":
        fails.append(f"normalise mangled the canonical request: {flat(n)}")
    if evaluate(pol, flat(normalise("junk")))["decision"] != "DENY" or evaluate(pol, flat(normalise({})))["decision"] != "DENY":
        fails.append("junk on the wire was not denied")
    # adapters: the registry resolves by resource type only; the mock meets the contract; unknown resource never executes
    old_events, os.environ["GUARD_RUN_ID"] = os.environ.get("GUARD_RUN_ID"), "selftest"
    try:
        mp = {"version": 0, "policy": "t", "rules": [
            {"name": "mock-read", "effect": "allow", "principal": "x", "resource": "mock://*", "actions": ["read"]},
            {"name": "mock-write", "effect": "ask", "principal": "x", "resource": "mock://*", "actions": ["write"]},
            {"name": "pg-read", "effect": "allow", "principal": "x", "resource": "postgres://*", "actions": ["read"]}]}
        mock = MockAdapter()
        reg = Registry([mock])
        if mock.name != "mock" or not mock.match(normalise({"resource": "mock://db", "action": "read"})) or \
           mock.match(normalise({"resource": "postgres://db", "action": "read"})):
            fails.append("mock adapter contract: name / matches own / rejects foreign")
        r = execute(mp, reg, normalise({"principal": "x", "resource": "mock://db", "action": "read", "operation": "q", "input": {"k": 1}}))
        if r["status"] != "completed" or r["data"]["echo"]["input"] != {"k": 1} or r["metadata"]["adapter"] != "mock" or len(mock.calls) != 1:
            fails.append(f"authorised mock request did not execute with a structured result: {r}")
        r = execute(mp, reg, normalise({"principal": "x", "resource": "mock://db", "action": "write"}))
        if r["status"] != "approval_required" or not r.get("approval", {}).get("approval_id") or len(mock.calls) != 1:
            fails.append("ASK did not produce an approval (or executed anyway)")
        r = execute(mp, reg, normalise({"principal": "x", "resource": "mock://db", "action": "delete"}))
        if r["status"] != "denied" or len(mock.calls) != 1:
            fails.append("DENY reached the adapter")
        r = execute(mp, reg, normalise({"principal": "x", "resource": "postgres://db", "action": "read"}))
        if r["status"] != "failed" or r["error"]["code"] != "no_adapter":
            fails.append("allowed request for a resource with no adapter did not fail cleanly")
        # the architectural test: registering another adapter changes no decision
        before = [evaluate(mp, flat(normalise(q)))["decision"] for q in
                  ({"principal": "x", "resource": "mock://db", "action": "read"}, {"principal": "x", "resource": "postgres://db", "action": "read"},
                   {"principal": "x", "resource": "postgres://db", "action": "delete"}, {"principal": "y", "resource": "mock://db", "action": "read"})]

        class PgAdapter(MockAdapter):
            name = "postgres"

            def match(self, req):
                return rtype(req["resource"]) == "postgres"
        reg.register(PgAdapter())
        after = [evaluate(mp, flat(normalise(q)))["decision"] for q in
                 ({"principal": "x", "resource": "mock://db", "action": "read"}, {"principal": "x", "resource": "postgres://db", "action": "read"},
                  {"principal": "x", "resource": "postgres://db", "action": "delete"}, {"principal": "y", "resource": "mock://db", "action": "read"})]
        if before != after or before != ["ALLOW", "ALLOW", "DENY", "DENY"]:
            fails.append(f"adding an adapter changed policy behaviour: {before} -> {after}")
        if execute(mp, reg, normalise({"principal": "x", "resource": "postgres://db", "action": "read"}))["status"] != "completed":
            fails.append("registered adapter #2 did not execute an allowed request")
        evs = (GUARD_DIR / "runs" / "selftest" / "events.jsonl")
        kinds = [json.loads(l)["type"] for l in evs.read_text(encoding="utf-8").splitlines()] if evs.is_file() else []
        for k in ("request.created", "policy.evaluated", "approval.requested", "execution.started", "execution.completed", "execution.failed"):
            if k not in kinds:
                fails.append(f"audit never emitted {k}")
    finally:
        if old_events is None:
            os.environ.pop("GUARD_RUN_ID", None)
        else:
            os.environ["GUARD_RUN_ID"] = old_events
        import shutil
        shutil.rmtree(GUARD_DIR / "runs" / "selftest", ignore_errors=True)
    # parity with the live hook's 46-case battery: blocked <=> DENY through the adapter
    try:
        sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
        import test_security_check as tsc
        cases = [(lbl, "Bash" if fn is tsc.bash else "PowerShell", cmd, "DENY") for lbl, fn, cmd in tsc.BLOCK] + \
                [(lbl, "Bash" if fn is tsc.bash else "PowerShell", cmd, "ALLOW") for lbl, fn, cmd in tsc.ALLOW]
        bad = []
        for lbl, tool, cmd, exp in cases:
            got = evaluate(pol, flat(classify(tool, {"command": cmd})))["decision"]
            if got != exp:
                bad.append(f"{lbl!r}: expected {exp} got {got}")
        if bad:
            fails.append(f"parity with security_check failed on {len(bad)}/{len(cases)}: " + "; ".join(bad[:5]))
        parity = f"{len(cases) - len(bad)}/{len(cases)}"
    except Exception as e:  # noqa: BLE001
        parity = "not run (no local hook battery)"
    # evidence: seal/verify round-trip, tamper detection, audit findings
    evs = [{"type": "a", "n": 1}, {"type": "b", "n": 2}, {"type": "c", "n": 3}]
    sealed = seal_lines(evs)
    if verify_lines(sealed) != (True, None):
        fails.append("sealed chain did not verify")
    tampered = json.loads(json.dumps(sealed)); tampered[1]["n"] = 99
    if verify_lines(tampered) != (False, 1):
        fails.append(f"tamper at index 1 not caught: {verify_lines(tampered)}")
    recs = [("Read", {"file_path": "a.md"}, {}), ("Read", {"file_path": "a.md"}, {}), ("Bash", {"command": "ls"}, {}),
            ("Read", {"file_path": "a.md"}, {}), ("Bash", {"command": "cd /tmp && rm -rf ~"}, {})]
    f = audit_trace(recs, pol)
    kinds = [(x["index"], x["kind"]) for x in f]
    if kinds != [(1, "redundant-call"), (4, "overreach")]:
        fails.append(f"audit findings wrong: {kinds}")
    for f in fails:
        print("FAIL " + f)
    print(f"selftest {'PASS' if not fails else 'FAIL'}: {len(T) + 7} policy contract checks, 9 adapter/pipeline checks, "
          f"3 evidence checks, parity with security_check {parity}")
    return 1 if fails else 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="guard v0: request -> policy decision -> evidence")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("init"); s.add_argument("--force", action="store_true")
    for name in ("check", "explain"):
        s = sub.add_parser(name)
        s.add_argument("--principal", default="claude-code"); s.add_argument("--resource", required=(name == "explain"))
        s.add_argument("--action", required=(name == "explain")); s.add_argument("--json", action="store_true")
    s = sub.add_parser("policy"); s.add_argument("what", choices=("validate", "list"))
    s = sub.add_parser("hook"); s.add_argument("--shadow", action="store_true", help="log the decision, never block (same as GUARD_SHADOW=1)")
    s = sub.add_parser("request"); s.add_argument("file", help="canonical request JSON, or - for stdin")
    s.add_argument("--principal", default="claude-code"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("logs"); s.add_argument("--run"); s.add_argument("--all", action="store_true"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("replay"); s.add_argument("file"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("seal"); s.add_argument("--run", default=time.strftime("%Y-%m-%d"))
    s = sub.add_parser("verify"); s.add_argument("file")
    s = sub.add_parser("audit"); s.add_argument("file"); s.add_argument("--json", action="store_true")
    sub.add_parser("selftest")
    sub.add_parser("version")
    a = ap.parse_args()
    if a.cmd == "version":
        print(f"guard {VERSION} (protocol v0)"); return 0
    if a.cmd == "selftest":
        return selftest()
    if a.cmd is None:
        ap.print_usage(); return 2
    return {"init": cmd_init, "check": cmd_check, "explain": cmd_explain, "policy": cmd_policy, "hook": cmd_hook,
            "request": cmd_request, "logs": cmd_logs, "replay": cmd_replay, "seal": cmd_seal, "verify": cmd_verify,
            "audit": cmd_audit}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
