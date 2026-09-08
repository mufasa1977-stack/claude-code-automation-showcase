# 04 - Fail-closed quality gates for AI pipelines

**Problem.** Teams bolt AI generation onto a content pipeline and then discover the failure mode:
every check that *ran* was green, but the check that *mattered* never existed. A deepfake of a named
person, an undisclosed AI clip, an uncleared music bed - none of them tripped anything, because
nothing was looking. Separately, in an agent estate with dozens of automated hooks, a config rewrite
silently drops two of them and the dashboards keep reading green for days.

**What it does.** Two tools, both born from real incidents.

### `ship_door.py` - the one door for anything that publishes
- Every asset ships with a declaration sidecar (`<asset>.ship.json`): what it is, in booleans and lists.
- `ship_laws.json` is the rule registry. Each law says when it applies, how it is checked (an inline
  check or an external script), and whether it is **hard** (legal/safety, never bypassable) or
  **workflow** (an operator circuit-breaker file can disarm it for a hotfix).
- An applicable law with **no executable check blocks**. A missing or crashing gate script blocks.
  The absence of a check is a block, never a pass.
- All laws pass -> a `.ship_cert.json` is written containing the asset's SHA-256, so a downstream
  uploader can refuse anything whose cert does not match the bytes on disk.
- Laws included as examples: AI-human-must-not-portray-a-named-real-person (keyed on an explicit
  list, not on a boolean that can be argued either way), AI disclosure, rights cleared, claims
  reviewed, on-screen text proofread, secrets scan (external script), video duration (external script).

### `hook_regression_gate.py` - "what stopped running?"
- Diffs the set of hook scripts referenced by dated config backups against the live config.
  Anything wired once and gone now, with no declared retirement, is a finding.
- Script-basename level (not string level) so flag/path edits do not cry wolf; one hop of dispatcher
  indirection resolved so gates called by a live runner still count as wired.
- Never traps the session it protects: any internal error exits 0 with a message.

**Stack.** Python 3 standard library. Works with Claude Code `settings.json` hooks out of the box;
`scripts_in()` is the one function to adapt for another config format.

**Run.**
```
python ship_door.py --selftest
python hook_regression_gate.py --selftest
# demo: one asset certified, one blocked
python ship_door.py --asset example/launch_teaser.txt   --surface youtube
python ship_door.py --asset example/blocked_example.txt --surface youtube
```

**Proof.** `../docs/04_ship_door_cert.png`, `../docs/04_ship_door_blocked.png`, `../docs/04_hook_regression_selftest.png`.
