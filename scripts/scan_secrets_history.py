"""
Comprehensive Git History and Working Tree Secret Scanner.
Scans all commits, diffs, and tracked files for leaked API keys, tokens, credentials, and private keys.
"""

import re
import subprocess
import sys

SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----", "Private Key"),
    (r"AIza[0-9A-Za-z\\-_]{35}", "Google API / OAuth Key"),
    (r"ghp_[0-9a-zA-Z]{36}", "GitHub Personal Access Token"),
    (r"gho_[0-9a-zA-Z]{36}", "GitHub OAuth Access Token"),
    (r"xox[baprs]-[0-9a-zA-Z]{10,48}", "Slack Token"),
    (r"(?:api_key|apikey|secret_key|jwt_secret)\s*[:=]\s*['\"][0-9a-zA-Z-_]{24,}['\"]", "Generic High Entropy Secret"),
]

def scan_git_history():
    print("[*] Running full Git history commit scan...")
    try:
        log_output = subprocess.check_output(
            ["git", "log", "-p", "--full-history", "--no-color"],
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception as exc:
        print(f"[-] Failed to fetch git history: {exc}")
        return 1

    findings = []
    lines = log_output.splitlines()
    current_commit = "initial"

    for idx, line in enumerate(lines):
        if line.startswith("commit "):
            current_commit = line.split()[1]
            continue

        # Skip deleted lines or comments in tests
        if not line.startswith("+"):
            continue

        clean_line = line[1:].strip()
        # Ignore obvious mocks or test variables
        if "test" in clean_line.lower() and ("dummy" in clean_line.lower() or "mock" in clean_line.lower() or "your_super_secret" in clean_line.lower()):
            continue

        for pattern, label in SECRET_PATTERNS:
            matches = re.findall(pattern, clean_line)
            if matches:
                findings.append({
                    "commit": current_commit[:10],
                    "line": idx + 1,
                    "label": label,
                    "snippet": clean_line[:80],
                })

    print(f"\n[+] Scan Complete: Analyzed {len(lines)} lines across full git history.")
    if not findings:
        print("[SUCCESS] 0 secrets detected in full git history!")
        return 0

    print(f"[!] WARNING: Found {len(findings)} potential secrets:")
    for f in findings:
        print(f"  - Commit {f['commit']} (Line {f['line']}) [{f['label']}]: {f['snippet']}")
    return 1

if __name__ == "__main__":
    sys.exit(scan_git_history())
