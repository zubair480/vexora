#!/bin/sh
# Safety checks for this repository. Run before every push (the pre-push hook does) and in CI.
#
#   scripts/check.sh            scans only
#   scripts/check.sh --tests    tests first, then scans (with time limits: a test that hangs fails)
#
# 1. gitleaks over the full git history and over the working tree (no keys or passwords).
# 2. No key, certificate or .env files tracked.
# 3. Maintainer deny list: a private list of words that must never appear in this
#    repository, its history or its commit messages. The list is not part of the repository.
#    Point to it with   git config kit.denyFile /path/to/list   or the KIT_DENY_FILE
#    variable. Without the list this step fails; contributors who do not have it can set
#    KIT_DENY_SKIP=1 (CI still runs it).
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
say() { printf '%s\n' "$*"; }

# Time limits, so that a test that hangs fails instead of blocking for hours.
# One Node test may take TEST_SECONDS; one whole group of tests may take GROUP_SECONDS.
TEST_SECONDS="${KIT_TEST_SECONDS:-120}"
GROUP_SECONDS="${KIT_GROUP_SECONDS:-900}"
# limited COMMAND...: run it; if it is still running after GROUP_SECONDS, stop it (and
# everything it started) and fail. The command is not put in the background with "&":
# the shell would then make it ignore Ctrl+C, and the tests that send Ctrl+C would fail.
limited() {
  python3 -c '
import os, signal, subprocess, sys
seconds, command = float(sys.argv[1]), sys.argv[2:]
job = subprocess.Popen(command, start_new_session=True)
try:
    sys.exit(job.wait(timeout=seconds))
except subprocess.TimeoutExpired:
    print("TIME LIMIT: stopped after %d seconds: %s" % (seconds, " ".join(command)[:200]), flush=True)
except KeyboardInterrupt:
    pass
try:
    os.killpg(job.pid, signal.SIGKILL)
except OSError:
    pass
sys.exit(124)
' "$GROUP_SECONDS" "$@"
}

if [ "${1:-}" = "--tests" ]; then
  say "== tests"
  limited node --test --test-timeout="${TEST_SECONDS}000" js/test/*.test.js dashboard/test/*.test.mjs examples/test/*.mjs || fail=1
  (cd python && limited python3 -m unittest discover -s tests) || fail=1
  limited python3 -m unittest discover -s examples/test || fail=1
  limited python3 -m unittest discover -s projects/test || fail=1
  limited node --test --test-timeout="${TEST_SECONDS}000" projects/test/*.test.mjs || fail=1
fi

say "== gitleaks (history)"
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks git --redact --no-banner . || fail=1
  say "== gitleaks (working tree)"
  gitleaks dir --redact --no-banner . || fail=1
else
  say "gitleaks is not installed (https://github.com/gitleaks/gitleaks). Install it, then run again."
  fail=1
fi

say "== forbidden file types"
bad=$(git ls-files | grep -iE '(\.pem|\.key|\.der|\.p12|\.pfx|\.env|\.uf2|\.hex|\.bin|\.elf)$|(^|/)\.env(\.|$)|(^|/)build/')
if [ -n "$bad" ]; then say "These files must not be in the repository:"; say "$bad"; fail=1; fi

say "== deny list"
deny="${KIT_DENY_FILE:-$(git config --get kit.denyFile 2>/dev/null || true)}"
if [ -n "$deny" ] && [ -r "$deny" ]; then
  pat=$(mktemp)
  trap 'rm -f "$pat"' EXIT
  grep -v '^#' "$deny" | grep -v '^[[:space:]]*$' > "$pat"
  # Matches are reported as file:line only, so the output never repeats a listed word.
  # In a git worktree ".git" is a file that holds a local path: it is not part of the kit.
  hits=$(grep -rnIiE --exclude-dir=.git --exclude=.git --exclude-dir=node_modules -f "$pat" . | cut -d: -f1,2)
  if [ -n "$hits" ]; then say "Deny-list match in the working tree:"; say "$hits"; fail=1; fi
  hits=$(git grep -nIiE -f "$pat" $(git rev-list --all) | cut -d: -f1,2,3)
  if [ -n "$hits" ]; then say "Deny-list match in history:"; say "$hits"; fail=1; fi
  hits=$(git log --all --format='%H %an %ae %cn %ce %B' | grep -ciE -f "$pat")
  if [ "$hits" != "0" ]; then say "Deny-list match in $hits line(s) of commit messages or author names."; fail=1; fi
elif [ "${KIT_DENY_SKIP:-}" = "1" ]; then
  say "skipped (KIT_DENY_SKIP=1)"
else
  say "No deny list configured. Maintainers: git config kit.denyFile /path/to/list"
  say "Contributors without the list: KIT_DENY_SKIP=1 git push"
  fail=1
fi

if [ "$fail" = 0 ]; then say "All checks passed."; else say "CHECKS FAILED. Nothing should be pushed until this is fixed."; fi
exit $fail
