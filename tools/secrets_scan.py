#!/usr/bin/env python3
"""secrets_scan.py - refuse to ship a tree (or a single file) that carries a credential.

Patterns: API keys / tokens / passwords in assignments, bearer headers, private-key blocks,
well-known key shapes (Google, AWS, GitHub, Slack, Stripe, Telegram bot tokens), absolute
home-directory paths, phone numbers, and personal e-mail addresses (business contact addresses
can be allow-listed).

  python secrets_scan.py --path <dir or file> [--allow-email you@example.com]... [--quiet]
Exit 0 = clean, 2 = findings (each printed as file:line: rule).
Used by ship_door.py as the external gate behind the "no-secrets-in-asset" law, and as a
pre-commit / CI step for this repository.
"""
import argparse
import os
import re
import sys

RULES = [
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("bearer-token-literal", re.compile(r"Bearer\s+[A-Za-z0-9\-_\.=]{20,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("google-oauth-secret", re.compile(r"\bGOCSPX-[A-Za-z0-9\-_]{20,}\b")),
    ("assigned-secret", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|refresh_token|client_secret)\b\s*[=:]\s*"
        r"['\"][A-Za-z0-9\-_\./+=]{16,}['\"]")),
    ("home-dir-path", re.compile(r"(?i)(?:[A-Z]:\\Users\\[^\\\s\"']+|/home/[^/\s\"']+|/Users/[^/\s\"']+)")),
    ("phone-number", re.compile(r"(?<![\w/])\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")),
    ("email-address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]
ALLOW_EMAIL_DOMAINS = {"example.com", "example.org", "anthropic.com"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "_state", "docs"}
TEXT_EXT = {".py", ".js", ".ts", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".sh", ".ps1",
            ".env", ".example", ".cfg", ".ini", ".html", ".css"}


def scan_file(path: str, allow_emails: set):
    findings = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for n, line in enumerate(f, 1):
                for rule, rx in RULES:
                    for m in rx.finditer(line):
                        hit = m.group(0)
                        if rule == "email-address":
                            dom = hit.split("@", 1)[1].lower()
                            if hit.lower() in allow_emails or dom in ALLOW_EMAIL_DOMAINS:
                                continue
                        findings.append((path, n, rule, hit[:60]))
    except Exception:
        pass
    return findings


def walk(root: str):
    if os.path.isfile(root):
        yield root
        return
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext in TEXT_EXT or fn.startswith(".env") or "." not in fn:
                yield os.path.join(dp, fn)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--allow-email", action="append", default=[])
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    allow = {e.lower() for e in a.allow_email}
    me = os.path.basename(__file__)
    files = [p for p in walk(a.path) if os.path.basename(p) != me]  # the rule source is not a finding
    findings = []
    for p in files:
        findings.extend(scan_file(p, allow))
    if findings:
        for path, n, rule, hit in findings:
            print(f"{os.path.relpath(path, a.path) if os.path.isdir(a.path) else path}:{n}: {rule}: {hit}")
        print(f"SECRETS SCAN: {len(findings)} finding(s) in {len(files)} file(s) - REFUSE")
        return 2
    if not a.quiet:
        print(f"SECRETS SCAN: clean ({len(files)} file(s), {len(RULES)} rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
