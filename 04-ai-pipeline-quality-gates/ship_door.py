#!/usr/bin/env python3
"""ship_door.py - THE ONE DOOR for publishing anything an AI pipeline produced.

THE PROBLEM IT SOLVES
  In an AI content pipeline every check that RAN can be green while the check that MATTERED never
  existed. This door makes that structurally impossible: every publish must carry a declaration
  sidecar describing the asset, every applicable law in the registry must have an executable check,
  and an applicable law with NO check BLOCKS. The absence of a check is a block, never a pass.

HOW IT WORKS
  1. The asset ships with a sidecar  <asset>.ship.json  declaring what it is (booleans + lists).
  2. ship_laws.json lists laws. Each has  applies_when  predicates over the declaration, a  gate
     (inline check id, or an external script), and a  hard  flag.
  3. Every applicable law is evaluated. All pass -> a  <asset>.ship_cert.json  is written with the
     asset's SHA-256 so a downstream publisher can refuse to post anything whose cert does not match
     the bytes on disk. Any block -> exit 1 with every blocking reason printed.
  4. An operator circuit-breaker file (SHIP_DOOR_BYPASS_FILE) disarms WORKFLOW laws for a hotfix;
     HARD laws (legal / safety) are never bypassable.

USAGE
  python ship_door.py --asset <path> --surface <youtube|tiktok|instagram|web|store> [--declare <sidecar>]
  python ship_door.py --selftest

EXIT  0 = cert issued   1 = blocked   2 = no / invalid declaration (nothing ships undeclared)
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LAWS_PATH = os.environ.get("SHIP_LAWS", os.path.join(HERE, "ship_laws.json"))
BYPASS = os.environ.get("SHIP_DOOR_BYPASS_FILE", os.path.join(HERE, "_GATES_BYPASS"))
SURFACES = ["youtube", "tiktok", "instagram", "web", "store"]

REQUIRED_CONTENT_BOOLS = [
    "has_ai_generated_content", "has_ai_generated_humans", "ai_disclosure_present",
    "makes_factual_claims", "claims_reviewed", "uses_third_party_media", "rights_cleared",
    "has_onscreen_text", "onscreen_text_proofread", "is_video",
]
REQUIRED_TOP = ["content", "lane", "declared_by", "date"]
PORTRAYAL_KEY = "names_portrayed_by_ai_humans"
ROLES_KEY = "ai_human_roles"
ROLES_MIN_CHARS = 40


def fail_no_declaration(msg: str) -> None:
    print("BLOCKED: NO DECLARATION - nothing ships undeclared.")
    print(f"  ({msg})")
    print("  Write <asset>.ship.json next to the asset (see example/ for the schema).")
    sys.exit(2)


def load_declaration(path: str) -> dict:
    if not os.path.isfile(path):
        fail_no_declaration(f"missing sidecar: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            dec = json.load(f)
    except Exception as e:
        fail_no_declaration(f"unreadable/invalid JSON: {type(e).__name__}: {e}")
    problems = [f"missing top-level key '{k}'" for k in REQUIRED_TOP if k not in dec]
    content = dec.get("content")
    if not isinstance(content, dict):
        problems.append("'content' must be an object")
    else:
        for k in REQUIRED_CONTENT_BOOLS:
            if not isinstance(content.get(k), bool):
                problems.append(f"content.{k} must be a bool (got {content.get(k)!r})")
        if not isinstance(content.get("names_real_people"), list):
            problems.append("content.names_real_people must be a list")
        pk = content.get(PORTRAYAL_KEY, [])
        if not isinstance(pk, list) or not all(isinstance(n, str) for n in pk):
            problems.append(f"content.{PORTRAYAL_KEY} must be a list of names")
    if problems:
        fail_no_declaration("invalid declaration: " + "; ".join(problems[:6]))
    return dec


def predicate(pred: str, content: dict) -> bool:
    if pred == "always":
        return True
    if pred == "names_real_people_nonempty":
        return bool(content.get("names_real_people"))
    return bool(content.get(pred))  # unknown key = False, so laws can key on optional fields


def applies(law: dict, content: dict, surface: str) -> bool:
    surf = law.get("surfaces")
    if surf and surface not in surf:
        return False
    return all(predicate(p, content) for p in law.get("applies_when", ["always"]))


# ---- inline checks ---------------------------------------------------------
def check_portrayal(content: dict):
    """HARD law: an AI-generated human may never portray a named real person.

    Keyed on an explicit PORTRAYAL LIST, not on a single boolean, because a boolean can be argued
    to either value. Outcomes:
      no AI humans                              -> PASS
      AI humans, portrayal list undeclared      -> BLOCK (fail closed)
      AI humans, portrayal list non-empty       -> BLOCK (the violation)
      AI humans, empty list, real people named  -> PASS only with a falsifiable role statement
    """
    has_ai = content.get("has_ai_generated_humans")
    declared = content.get(PORTRAYAL_KEY)
    named = [n for n in (content.get("names_real_people") or []) if isinstance(n, str)]
    if not has_ai:
        if declared:
            return False, (f"declaration contradicts itself: has_ai_generated_humans=false but "
                           f"{PORTRAYAL_KEY}={declared!r}")
        return True, "no AI-generated humans in this asset"
    if not isinstance(declared, list):
        return False, (f"asset has AI-generated humans but does not declare {PORTRAYAL_KEY} - "
                       f"fail closed. Add the list (or [] if none).")
    portrayed = [n for n in declared if isinstance(n, str) and n.strip()]
    if portrayed:
        stray = [n for n in portrayed if n not in named]
        extra = f" ({PORTRAYAL_KEY} names {stray} absent from names_real_people)" if stray else ""
        return False, ("AI-generated humans may never portray a named real person. Declared "
                       f"portrayals: {', '.join(portrayed)}." + extra)
    if named:
        roles = content.get(ROLES_KEY)
        if not isinstance(roles, str) or len(roles.strip()) < ROLES_MIN_CHARS:
            return False, (f"asset names real people ({', '.join(named)}) and contains AI humans while "
                           f"claiming none portrays them. Add content.{ROLES_KEY}: a specific, "
                           f"checkable statement (>= {ROLES_MIN_CHARS} chars) of who the AI humans DO portray.")
        return True, f"AI humans portray no named real person; role statement on record ({len(roles.strip())} chars)"
    return True, "AI humans present; no real people named, no portrayals declared"


def inline_check(check_id: str, content: dict):
    if check_id == "block_if_ai_human_portrays_named_real":
        return check_portrayal(content)
    if check_id == "require_ai_disclosure":
        if content["has_ai_generated_content"] and not content["ai_disclosure_present"]:
            return False, "AI-generated content without the platform-required AI disclosure"
        return True, "AI disclosure present (or no AI content)"
    if check_id == "require_claims_reviewed":
        if content["makes_factual_claims"] and not content["claims_reviewed"]:
            return False, "factual claims not reviewed by a second pair of eyes / fact-check pass"
        return True, "factual claims reviewed"
    if check_id == "require_rights_cleared":
        if content["uses_third_party_media"] and not content["rights_cleared"]:
            return False, "third-party media (music, footage, images) without cleared rights"
        return True, "rights cleared (or no third-party media)"
    if check_id == "require_text_proofread":
        if content["has_onscreen_text"] and not content["onscreen_text_proofread"]:
            return False, "on-screen text not proofread at the target surface's real scale"
        return True, "on-screen text proofread"
    return False, f"UNKNOWN inline check '{check_id}' - fail closed"


def run_script_gate(gate, asset: str):
    """External gate script. Missing or crashing = fail closed."""
    if isinstance(gate, dict):
        script, argv_t = gate.get("script", ""), gate.get("argv")
    else:
        script, argv_t = gate, None
    script_abs = script if os.path.isabs(script) else os.path.normpath(os.path.join(HERE, script))
    if not os.path.isfile(script_abs):
        return False, f"gate script MISSING: {script_abs} - fail closed"
    argv = ([a.replace("{py}", sys.executable).replace("{asset}", asset).replace("{script}", script_abs)
             for a in argv_t] if argv_t else [sys.executable, script_abs, "--asset", asset])
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except Exception as e:
        return False, f"gate crashed: {type(e).__name__}: {e} - fail closed"
    if r.returncode != 0:
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        return False, f"gate exit {r.returncode}: " + " | ".join(tail[-3:])
    return True, f"gate passed: {os.path.basename(script_abs)}"


def sha256_16(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ---- main ------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", required=True)
    ap.add_argument("--surface", required=True, choices=SURFACES)
    ap.add_argument("--declare", default=None)
    ap.add_argument("--now", default=None, help="cert timestamp (default: asset mtime, UTC)")
    args = ap.parse_args()

    if not os.path.isfile(args.asset):
        print(f"BLOCKED: asset not found: {args.asset}")
        sys.exit(1)
    dec = load_declaration(args.declare or (args.asset + ".ship.json"))
    content = dec["content"]
    try:
        with open(LAWS_PATH, encoding="utf-8") as f:
            laws = json.load(f)["laws"]
    except Exception as e:
        print(f"BLOCKED: law registry unreadable ({LAWS_PATH}): {e} - fail closed")
        sys.exit(1)

    bypass_active = os.path.isfile(BYPASS)
    results, blocks, already_blocked = [], [], False
    for law in laws:
        lid, hard = law["id"], bool(law.get("hard"))
        if not applies(law, content, args.surface):
            results.append((lid, "N/A", "not applicable to this declaration"))
            continue
        if bypass_active and not hard:
            results.append((lid, "BYPASSED", "bypass file present (workflow law)"))
            continue
        gate = law.get("gate")
        if gate is None:
            ok, reason = False, f"LAW {lid} HAS NO EXECUTABLE CHECK - fail closed"
        elif isinstance(gate, str) and gate.startswith("inline:"):
            ok, reason = inline_check(gate[len("inline:"):], content)
        else:
            if already_blocked:
                results.append((lid, "SKIPPED", "script gate skipped - already blocked above"))
                blocks.append((lid, hard, "script gate not run (already blocked) - rerun after fixes"))
                continue
            ok, reason = run_script_gate(gate, args.asset)
        results.append((lid, "PASS" if ok else "BLOCK", reason))
        if not ok:
            blocks.append((lid, hard, reason))
            already_blocked = True

    print(f"SHIP DOOR - asset={os.path.basename(args.asset)} surface={args.surface} "
          f"lane={dec.get('lane')} declared_by={dec.get('declared_by')}")
    for lid, st, reason in results:
        print(f"  [{st:8s}] {lid}: {reason}")

    if blocks:
        print(f"\nBLOCKED - {len(blocks)} law(s) refuse this publish:")
        for lid, hard, reason in blocks:
            print(f"  BLOCK [{'HARD' if hard else 'workflow'}] {lid}: {reason}")
        soft = [b for b in blocks if not b[1]]
        print("\nBypass file disarms WORKFLOW laws only"
              + (f" ({len(soft)} of these)" if soft else " (none of these - all HARD)")
              + ". HARD laws are never bypassable: fix the content, or the declaration is lying.")
        sys.exit(1)

    ts = args.now or datetime.fromtimestamp(os.path.getmtime(args.asset), tz=timezone.utc).isoformat()
    cert = {"asset": os.path.abspath(args.asset), "asset_sha256_16": sha256_16(args.asset),
            "surface": args.surface, "lane": dec.get("lane"), "declared_by": dec.get("declared_by"),
            "laws_checked": [{"id": l, "result": s, "reason": r} for l, s, r in results],
            "timestamp": ts, "issued_by": "ship_door.py"}
    cert_path = args.asset + ".ship_cert.json"
    with open(cert_path, "w", encoding="utf-8") as f:
        json.dump(cert, f, indent=2)
    print(f"\nSHIP CERT ISSUED -> {cert_path} (sha256:{cert['asset_sha256_16']})")
    sys.exit(0)


def selftest() -> int:
    with open(LAWS_PATH, encoding="utf-8") as f:
        laws = json.load(f)["laws"]
    ids = [l["id"] for l in laws]
    assert len(ids) == len(set(ids)), "duplicate law ids"
    dummy = {k: False for k in REQUIRED_CONTENT_BOOLS}
    dummy["names_real_people"] = []
    for l in laws:
        assert l.get("rule") and l.get("applies_when"), f"{l['id']} malformed"
        g = l.get("gate")
        if isinstance(g, str) and g.startswith("inline:"):
            _, reason = inline_check(g[7:], dict(dummy))
            assert "UNKNOWN inline check" not in reason, f"{l['id']}: {reason}"
    hard_ids = {l["id"] for l in laws if l.get("hard")}
    assert "real-person-likeness" in hard_ids, "the likeness law must be hard"

    def like(**kw):
        return inline_check("block_if_ai_human_portrays_named_real", dict(dummy, **kw))
    names = ["Jane Example", "John Sample"]
    role_ok = "unnamed composite staff, faces never visible; narration names no one in that window"
    ok, r = like(has_ai_generated_humans=True, names_real_people=names, names_portrayed_by_ai_humans=["Jane Example"])
    assert not ok and "Jane Example" in r, r                               # the violation blocks, by name
    ok, r = like(has_ai_generated_humans=True, names_real_people=names)
    assert not ok and PORTRAYAL_KEY in r, r                                # undeclared list = fail closed
    ok, r = like(has_ai_generated_humans=False, names_real_people=names)
    assert ok, r                                                           # naming people w/o AI humans is fine
    ok, r = like(has_ai_generated_humans=True, names_real_people=names, names_portrayed_by_ai_humans=[])
    assert not ok and ROLES_KEY in r, r                                    # bare assertion is not enough
    ok, r = like(has_ai_generated_humans=True, names_real_people=names, names_portrayed_by_ai_humans=[], ai_human_roles="crew")
    assert not ok, "a 4-char role statement is not falsifiable"
    ok, r = like(has_ai_generated_humans=True, names_real_people=names, names_portrayed_by_ai_humans=[], ai_human_roles=role_ok)
    assert ok, r
    ok, r = like(has_ai_generated_humans=False, names_portrayed_by_ai_humans=["Jane Example"])
    assert not ok and "contradicts" in r, r

    ok, _ = inline_check("require_ai_disclosure", dict(dummy, has_ai_generated_content=True))
    assert not ok, "AI content without disclosure must block"
    ok, _ = inline_check("require_rights_cleared", dict(dummy, uses_third_party_media=True, rights_cleared=True))
    assert ok
    ok, r = inline_check("nonexistent_check", dummy)
    assert not ok and "UNKNOWN" in r, "unknown check must fail closed"
    print(f"ship_door selftest OK ({len(laws)} laws, {len(hard_ids)} hard, fail-closed verified)")
    return 0


if __name__ == "__main__":
    sys.exit(selftest()) if "--selftest" in sys.argv else main()
