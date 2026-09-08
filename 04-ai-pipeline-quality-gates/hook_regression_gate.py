#!/usr/bin/env python3
"""hook_regression_gate.py - a hook that VANISHED from your automation config is a silent regression.

THE FAILURE CLASS
  An AI agent estate runs dozens of hook scripts (session-start audits, pre-tool safety checks,
  post-edit tests). One day a config rewrite drops two of them. The scripts still sit on disk,
  healthy, exiting 0. Nothing runs them. Every existing sensor asks "is what is here correct?" -
  none asks "is anything MISSING that was here yesterday?" A subsystem stays dark for days while
  every dashboard reads green.

WHAT IT MEASURES
  For every dated backup of the hooks config, collect the set of SCRIPT BASENAMES referenced by
  hook commands. Compare against the live config. A script referenced by any backup and by NO
  live hook is VANISHED. Script basenames, not raw command strings - commands legitimately get
  edited (new flag, corrected path), and a gate that cries wolf gets bypassed.

  One level of indirection is resolved: a gate invoked by a live DISPATCHER script still counts
  as wired even though the config never names it directly.

  A removal is clean only if DECLARED in hook_retirements.json ({"script": "reason"}). Honest in
  both directions: it cannot be silenced by deleting more, and it does not block deliberate cleanup.

CONFIG SHAPE (Claude Code settings.json style; adapt `scripts_in` for other formats)
  {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "python .../x.py"}]}]}}

USAGE
  python hook_regression_gate.py --settings <live.json> --backups <dir with */settings.json>
                                 [--search-dir <dir>]... [--brief] [--json]
  python hook_regression_gate.py --retire <script> --why "<reason>"
  python hook_regression_gate.py --selftest

EXIT  0 = clean (or --brief, which never blocks)   2 = VANISHED hook(s)   3 = cannot run
"""
import argparse
import glob
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
RETIRE = os.path.join(HERE, "hook_retirements.json")
PASS, FAIL, CANNOT_RUN = 0, 2, 3
_SCRIPT = re.compile(r"[\w./\\-]*?([\w-]+\.(?:py|sh|ps1))", re.I)


def scripts_in(settings_path: str):
    """Set of script basenames any hook in this config actually invokes (None if unreadable)."""
    try:
        with open(settings_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    out = set()
    for _event, groups in (cfg.get("hooks") or {}).items():
        for g in groups or []:
            for h in (g.get("hooks") or []):
                for m in _SCRIPT.finditer(h.get("command") or ""):
                    out.add(m.group(1).lower())
    return out


def load_retirements() -> dict:
    try:
        with open(RETIRE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def reachable(live_scripts: set, search_dirs: list, depth: int = 2) -> set:
    """Expand live hook scripts to everything they in turn invoke (dispatchers, chains)."""
    def find_source(basename):
        for d in search_dirs:
            p = os.path.join(d, basename)
            if os.path.isfile(p):
                return p
        return None

    seen, frontier = set(live_scripts), set(live_scripts)
    for _ in range(depth):
        nxt = set()
        for name in frontier:
            src = find_source(name)
            if not src:
                continue
            try:
                with open(src, encoding="utf-8", errors="ignore") as f:
                    blob = f.read()
            except Exception:
                continue
            for m in _SCRIPT.finditer(blob):
                got = m.group(1).lower()
                if got not in seen:
                    nxt.add(got)
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return seen


def evaluate(live: set, backups: list, retired: dict = None):
    """Pure: live=set, backups=[(label,set)] -> (code, [(script, last_seen_label)])."""
    retired = retired if retired is not None else load_retirements()
    seen_in = {}
    for label, s in backups:
        for script in s:
            seen_in.setdefault(script, []).append(label)
    vanished = [(sc, max(lbl)) for sc, lbl in sorted(seen_in.items())
                if sc not in live and sc not in retired]
    return (FAIL if vanished else PASS), vanished


def selftest() -> int:
    ok = fail = 0

    def chk(label, got, want=True):
        nonlocal ok, fail
        if got == want:
            ok += 1
            print(f"  ok   {label}")
        else:
            fail += 1
            print(f"  FAIL {label}: got {got!r} want {want!r}")

    live = {"memory_retrieve.py", "evolution_meter.py"}
    backs = [("2026-08-15", {"memory_retrieve.py", "index_refresh.py", "consolidate.py"}),
             ("2026-08-16", {"memory_retrieve.py"})]
    code, van = evaluate(live, backs, retired={})
    chk("catches the two-script disappearance", code, FAIL)
    chk("names both vanished scripts", sorted(s for s, _ in van), ["consolidate.py", "index_refresh.py"])
    chk("reports the last backup that had it", dict(van)["index_refresh.py"], "2026-08-15")
    code2, van2 = evaluate({"a.py", "b.py"}, [("d1", {"a.py"}), ("d2", {"a.py", "b.py"})], retired={})
    chk("clean wiring passes", (code2, van2), (PASS, []))
    chk("newly added hook is not a regression",
        evaluate({"a.py", "brand_new.py"}, [("d1", {"a.py"})], retired={})[0], PASS)
    chk("declared retirement is not a finding",
        evaluate({"a.py"}, [("d1", {"a.py", "old.py"})], retired={"old.py": "replaced"})[0], PASS)
    chk("parses a script out of a guarded shell command",
        sorted(_SCRIPT.findall('[ -n "$X" ] && exit 0; python C:/a/b/index_refresh.py --refresh')),
        ["index_refresh.py"])
    print(f"[hook_regression_gate selftest] {ok} passed, {fail} failed")
    return 0 if fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", help="live hooks config (JSON)")
    ap.add_argument("--backups", help="directory containing <label>/settings.json backups")
    ap.add_argument("--search-dir", action="append", default=[], help="where dispatcher scripts live")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--retire")
    ap.add_argument("--why", default="")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.retire:
        if not a.why.strip():
            print("--retire needs --why (a removal without a reason is the defect)")
            return CANNOT_RUN
        r = load_retirements()
        r[a.retire.lower()] = a.why.strip()
        with open(RETIRE, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, sort_keys=True)
        print(f"retired {a.retire}: {a.why.strip()}")
        return PASS
    if not a.settings or not a.backups:
        print("need --settings and --backups (or --selftest)")
        return CANNOT_RUN

    live = scripts_in(a.settings)
    if live is None:
        print(f"CANNOT RUN - unreadable {a.settings}")
        return CANNOT_RUN
    backups = []
    for p in sorted(glob.glob(os.path.join(a.backups, "*", "settings.json"))):
        s = scripts_in(p)
        if s:
            backups.append((os.path.basename(os.path.dirname(p)), s))
    if not backups:
        print("CANNOT RUN - no readable backups to compare against")
        return CANNOT_RUN

    reach = reachable(live, a.search_dir)
    code, vanished = evaluate(reach, backups)
    if a.json:
        print(json.dumps({"live_scripts": len(live), "reachable": len(reach), "backups": len(backups),
                          "vanished": [{"script": s, "last_seen": l} for s, l in vanished], "exit": code}))
        return code
    if a.brief:
        if vanished:
            print(f"[hook-regression] {len(vanished)} hook script(s) VANISHED, removal never declared: "
                  f"{', '.join(s for s, _ in vanished[:3])}")
        else:
            print(f"[hook-regression] ok - {len(reach)} script(s) reachable, none vanished across {len(backups)} backup(s)")
        return PASS
    print("=== HOOK REGRESSION AUDIT (what STOPPED running, not what is wired) ===")
    print(f"  live config        : {len(live)} hook script(s) directly named")
    print(f"  reachable via them : {len(reach)} (dispatchers resolved, depth 2)")
    print(f"  backups compared   : {len(backups)}  ({backups[0][0]} .. {backups[-1][0]})")
    print(f"  declared retired   : {len(load_retirements())}")
    if not vanished:
        print("  VERDICT: PASS - every script any backup ever wired is still wired.")
        return PASS
    print(f"\n  VANISHED ({len(vanished)}) - wired once, gone now, removal never declared:")
    for s, last in vanished:
        print(f"    ! {s:44} last seen in backup {last}")
    print("\n  Re-wire each after verifying it still runs, or declare the removal:")
    print("    python hook_regression_gate.py --retire <script> --why \"<reason>\"")
    return FAIL


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # a sensor must never trap the session it protects
        print(f"hook_regression_gate: internal error, not blocking - {e}")
        sys.exit(0)
