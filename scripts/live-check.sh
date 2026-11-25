#!/usr/bin/env bash
# Live gates for agy-sandbox 0.6 (docs/plans/0.6-native-v3.md §11). Runs against a
# real sbx daemon using ONLY a throwaway project and a sandbox named
# agy-sandbox-probe-<pid>. It never touches any other sandbox. Needs sbx >= 0.50.
#
# Every step is an assertion: the first failure stops the script with a non-zero
# exit, and the EXIT trap removes the probe sandbox, its recreate snapshots and
# the scratch directory either way.
#
# Run it from a terminal: step 3 (`agy-sandbox upgrade`, which is `sbx kit add`)
# makes sbx ask you to confirm the network hosts the added kit needs. That prompt
# is sbx's permission check and this script does not answer it for you.
#
# Usage: scripts/live-check.sh                      (repo root, CLI installed in .venv)
#        KEEP_PROBE=1 scripts/live-check.sh         keep the probe sandbox when a step fails
#        SKIP_IN_PLACE=1 scripts/live-check.sh      skip step 3 (no terminal available)
set -euo pipefail
cd "$(dirname "$0")/.."
AGY="$PWD/.venv/bin/agy-sandbox"
PROBE="probe-$$"
NAME="agy-sandbox-$PROBE"
WORK=$(mktemp -d)
export AGY_SANDBOX_STATE_DIR="$WORK/state"
STATUS=1

cleanup() {
  echo; echo "== cleanup"
  if [ "${KEEP_PROBE:-}" = 1 ] && [ "$STATUS" -ne 0 ]; then
    echo "KEEP_PROBE=1: leaving $NAME and $WORK for inspection (remove with: sbx rm -f $NAME; rm -rf $WORK)"
    return
  fi
  sbx rm -f "$NAME" >/dev/null 2>&1 || true
  sbx template ls --json 2>/dev/null | grep -o "\"recreate-[0-9-]*\"" | tr -d '"' | sort -u | while read -r tag; do
    sbx template rm -f "$NAME:$tag" >/dev/null 2>&1 || true
  done
  rm -rf "$WORK"
  if sbx ls 2>/dev/null | grep -q "$NAME"; then echo "!! probe sandbox still present: sbx rm -f $NAME"; else echo "probe sandbox gone"; fi
  if [ "$STATUS" -eq 0 ]; then echo "LIVE CHECK PASSED"; else echo "LIVE CHECK FAILED (see the last step above)"; fi
}
trap cleanup EXIT

step()   { printf '\n== %s\n' "$*"; }
fail()   { echo "!! $*" >&2; exit 1; }
inside() { sbx exec "$NAME" sh -c "$1"; }
# assert_inside DESCRIPTION SCRIPT: the script must exit 0 inside the sandbox
assert_inside() {
  echo "-- $1"
  if ! inside "$2"; then
    echo "-- diagnostics:"
    inside 'echo "user=$(id -un) home=$HOME pwd=$PWD"; echo "PATH=$PATH"; for d in /usr/local/bin /home/agent/.local/bin; do echo "## $d"; ls -la "$d" 2>&1 | head -20; done; ls -la /home/agent 2>&1 | head -30' || true
    fail "$1"
  fi
}
# agy ARGS...: the CLI on the scratch project, without attaching; the terminal (if any)
# stays connected so sbx's own prompts reach the person running this.
agy() { (cd "$WORK/proj" && "$AGY" "$@" --no-attach) || fail "agy-sandbox $* exited non-zero"; }
# write_config "KITS" AUTH_MODE: the scratch project's agy.yaml
write_config() {
  cat > "$WORK/proj/agy.yaml" <<EOF
profile: default
project_name: $PROBE
agent: claude
kits: [$1]
auth_mode: $2
clone: true
build_args: {PYTHON_VERSION: "3.12"}
setup_scripts: ["echo setup-ran > /home/agent/setup-marker", "id -un > /home/agent/setup-user"]
env: [PROBE_ENV=1]
EOF
}

step "0. sbx version, login and builder"
sbx version
sbx ls >/dev/null || fail "sbx is not signed in; run: sbx login"
sbx kit builder status || true

step "1. scratch project: claude + chrome-devtools, PYTHON_VERSION, one setup script"
mkdir -p "$WORK/proj"
(cd "$WORK/proj" && git init -q && echo hello > README.md && git add . && git -c user.name=p -c user.email=p@x commit -qm init)
write_config "claude, chrome-devtools" sandbox-local

step "2. up: sbx builds the workload (gate 1), hooks run as agent, no proxy placeholders"
agy up
sbx ls | grep -q "$NAME" || fail "sandbox $NAME not listed after up"
assert_inside "session user is agent"            '[ "$(id -un)" = agent ] && [ "$HOME" = /home/agent ]'
assert_inside "setup script ran, as agent"       '[ "$(cat /home/agent/setup-marker)" = setup-ran ] && [ "$(cat /home/agent/setup-user)" = agent ]'
assert_inside "python from the workload build"   'python --version'
assert_inside "claude is the agent's native install" '[ "$(command -v claude)" = /home/agent/.local/bin/claude ] && [ -L /home/agent/.local/bin/claude ] && readlink -f /home/agent/.local/bin/claude | grep -q ^/home/agent/.local/share/claude/versions/ && [ "$(stat -c %U /home/agent/.local/bin/claude)" = agent ] && claude --version'
assert_inside "no API key placeholders"          '! env | grep -qE "_API_KEY="'
assert_inside "markers for later gates"          'echo rootfs-marker > /home/agent/rootfs-marker && mkdir -p /home/agent/.claude/projects && echo s > /home/agent/.claude/projects/marker'

KITS_NOW="claude, chrome-devtools"
if [ "${SKIP_IN_PLACE:-}" = 1 ]; then
  step "3. SKIPPED (SKIP_IN_PLACE=1): in-place upgrade needs a terminal to answer sbx's permission prompt"
else
  step "3. upgrade: add codex in place (kit add); binary arrives, filesystem untouched"
  [ -t 0 ] || echo "-- note: stdin is not a terminal; sbx will refuse its permission prompt (run this script from a terminal, or SKIP_IN_PLACE=1)"
  write_config "claude, chrome-devtools, codex" sandbox-local
  agy upgrade
  assert_inside "codex installed by kit add"       'command -v codex && codex --version'
  assert_inside "rootfs marker survived upgrade"   '[ "$(cat /home/agent/rootfs-marker)" = rootfs-marker ]'
  KITS_NOW="claude, chrome-devtools, codex"
fi

step "3b. upgrade refuses what it cannot do in place (kit removal) and names recreate"
write_config "claude" sandbox-local
if (cd "$WORK/proj" && "$AGY" upgrade --no-attach </dev/null 2>"$WORK/refusal.txt"); then fail "upgrade should refuse a kit removal"; fi
grep -q "recreate" "$WORK/refusal.txt" || { cat "$WORK/refusal.txt"; fail "refusal does not point at recreate"; }
echo "-- refused as expected: $(grep -m1 'kit removed' "$WORK/refusal.txt" || head -1 "$WORK/refusal.txt")"
write_config "$KITS_NOW" sandbox-local

step "4. recreate after removing a kit (gate 2): rootfs, volume and clone survive"
write_config "claude" sandbox-local
agy recreate -y
assert_inside "rootfs marker survived recreate"  '[ "$(cat /home/agent/rootfs-marker)" = rootfs-marker ]'
assert_inside "claude volume survived recreate"  '[ "$(cat /home/agent/.claude/projects/marker)" = s ]'
assert_inside "clone workspace survived"         'git -C "${WORKSPACE_DIR:-$PWD}" log --oneline | grep -q init'
assert_inside "session user still agent"         '[ "$(id -un)" = agent ]'
sbx template ls | grep -q "$NAME.*recreate-" || fail "recreate snapshot not kept"

step "5. recreate to proxy-managed (login mode change): credential kits attached"
write_config "claude" proxy-managed
agy recreate -y
assert_inside "credential mode surfaced"         'env | grep -qE "^SBX_CRED_ANTHROPIC_MODE="'
assert_inside "rootfs marker survived again"     '[ "$(cat /home/agent/rootfs-marker)" = rootfs-marker ]'

step "6. down removes the sandbox"
(cd "$WORK/proj" && "$AGY" down -y) || fail "down exited non-zero"
sbx ls | grep -q "$NAME" && fail "sandbox still present after down"
STATUS=0
echo; echo "all live gates passed${SKIP_IN_PLACE:+ (step 3 skipped)}"
