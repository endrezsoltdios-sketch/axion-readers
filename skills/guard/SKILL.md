---
name: guard
description: A local policy engine for agent tool calls. A request (principal, resource, action) meets a YAML policy and gets ALLOW, DENY or ASK, with every decision written as evidence; explicit deny beats ask beats allow, no match is deny. Ships a Claude Code PreToolUse hook adapter, a shadow mode that logs without blocking, a replay of past traces, a hash-chain seal and verify for the event log, and an audit that flags redundant identical calls and overreach. Use when an agent needs guardrails you can read, test and replay, or when someone asks what an agent was allowed to do and whether the log was tampered with.
license: FSL-1.1-ALv2
---

# Guard

Adapters classify what a tool call touches; only the policy file decides. That split is the whole design: adding an
adapter changes no decision, and the selftest asserts it.

```bash
python scripts/guard.py init                                             # writes .guard/policy.yaml (14 starter rules)
python scripts/guard.py check --principal agent --resource secret://.env --action read
python scripts/guard.py explain --principal agent --resource fs://root --action delete
python scripts/guard.py policy validate | policy list
python scripts/guard.py hook --shadow                                    # stdin = Claude Code PreToolUse JSON
python scripts/guard.py replay traces/2026-09-16.jsonl                   # re-decide a real day, execute nothing
python scripts/guard.py audit traces/2026-09-16.jsonl                    # redundant identical calls, overreach vs policy
python scripts/guard.py seal --run 2026-09-16 && python scripts/guard.py verify .guard/runs/2026-09-16/events.sealed.jsonl
python scripts/guard.py selftest
```

Needs `pyyaml`. No network, no keys.

## Wiring it as a Claude Code hook

Add a PreToolUse command hook that runs `python scripts/guard.py hook --shadow` for Bash, PowerShell, Write and
Edit. Shadow mode writes every decision to `.guard/runs/<day>/events.jsonl` and never blocks; run it for two weeks
beside whatever you have today, compare its DENY rows to what actually got blocked, then drop `--shadow`. Live mode
exits 2 with the reason on DENY and returns a permission "ask" on ASK.

## Reading the policy

Each rule has a name, an effect (deny, ask, allow), a principal, a resource pattern (`secret://*`, `fs://root`,
`git://origin/master`, `broker://*`, `tool://*`) and a list of actions. Resources are what the adapters emit; the
starter file shows the shell classifier's vocabulary. Order does not matter: an explicit deny wins over ask, ask
over allow, and a request no rule names is denied.

## Evidence

Every run appends `request.created`, `policy.evaluated`, and for executed requests `execution.*` events. `seal`
hash-chains a day's file; `verify` prints the first broken index if anyone edited it. `audit` reads a trace and
names the calls that repeated with no mutation in between and the calls the policy would not have allowed.

Hosted version, managed policies and commercial licence: hello@getaxionlabs.com
