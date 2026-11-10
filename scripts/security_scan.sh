#!/usr/bin/env bash
# Scan the files that would be committed for credentials, private keys, private network addresses,
# local paths and enterprise identifiers. Exit status 0 = nothing found. sync_from_project.sh runs it
# before every commit. Maintainers can add fixed-string patterns (one per line, '#' comments) in a
# file outside the repository: SCAN_PATTERNS=<file> (default: ../artifact_sync/scan_patterns.txt).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
EXTRA="${SCAN_PATTERNS:-$ROOT/../artifact_sync/scan_patterns.txt}"
# every file git would see (tracked + untracked, honouring .gitignore), except this scanner
FILES=$(git ls-files --cached --others --exclude-standard | grep -v '^scripts/security_scan.sh$' | sort -u)
TEXT=$(echo "$FILES" | tr '\n' '\0' | xargs -0 grep -Il '' 2>/dev/null || true)   # text files only
PATTERNS=(password passwd token secret api_key apikey "BEGIN RSA" "BEGIN OPENSSH" "PRIVATE KEY" ssh-rsa ssh-ed25519
          "192.168." "10.0." "/Users/" "/home/" "_private/erp")
if [ -f "$EXTRA" ]; then
  while IFS= read -r l; do [ -n "$l" ] && [ "${l#\#}" = "$l" ] && PATTERNS+=("$l"); done < "$EXTRA"
  echo "security scan: ${#PATTERNS[@]} patterns (including $(grep -cv '^#' "$EXTRA") from $EXTRA)"
else
  echo "security scan: ${#PATTERNS[@]} built-in patterns (no maintainer pattern file)"
fi
found=0
for p in "${PATTERNS[@]}"; do
  hits=$(echo "$FILES" | tr '\n' '\0' | xargs -0 grep -IilF -- "$p" 2>/dev/null || true)
  if [ -n "$hits" ]; then echo "pattern '$p' in:"; echo "$hits" | sed 's/^/    /'; found=1; fi
done
# enterprise template ids and CJK characters (internal folder names) in text files
hits=$(echo "$TEXT" | tr '\n' '\0' | xargs -0 grep -IlE '(^|[^a-z])tpl[0-9]+' 2>/dev/null || true)
[ -n "$hits" ] && { echo "template ids in:"; echo "$hits" | sed 's/^/    /'; found=1; }
hits=$(echo "$TEXT" | tr '\n' '\0' | xargs -0 perl -CSD -ne 'if (/[\x{3000}-\x{9fff}]/) { print "$ARGV\n"; close ARGV; }' 2>/dev/null || true)
[ -n "$hits" ] && { echo "CJK characters in:"; echo "$hits" | sed 's/^/    /'; found=1; }
if [ $found -eq 0 ]; then echo "security scan: clean ($(echo "$FILES" | wc -l | tr -d ' ') files, $(echo "$TEXT" | wc -l | tr -d ' ') text)"; fi
exit $found
