#!/usr/bin/env bash
# Live gates for agy-sandbox 0.6 (docs/plans/0.6-native-v3.md §11). Runs against a
# real sbx daemon using ONLY a throwaway project and a sandbox named
# agy-sandbox-probe-<pid>. It never touches any other sandbox. Needs sbx >= 0.50.
#
# Usage: scripts/live-check.sh            (from the repo root, CLI installed in .venv)
set -euo pipefail
cd "$(dirname "$0")/.."
AGY=".venv/bin/agy-sandbox"
PROBE="probe-$$"
NAME="agy-sandbox-$PROBE"
WORK=$(mktemp -d)
export AGY_SANDBOX_STATE_DIR="$WORK/state"
trap 'echo; echo "== cleanup"; sbx rm -f "$NAME" >/dev/null 2>&1 || true; sbx template ls --json 2>/dev/null | grep -o "'"$NAME"':recreate-[0-9-]*" | xargs -r -n1 sbx template rm -f >/dev/null 2>&1 || true; rm -rf "$WORK"; sbx ls | grep -q "$NAME" && echo "!! probe sandbox still present" || echo "probe sandbox gone"' EXIT

step() { printf '\n== %s\n' "$*"; }
inside() { sbx exec "$NAME" sh -c "$1"; }

step "0. sbx version and builder"
sbx version
sbx kit builder status || true

step "1. scratch project: claude + chrome-devtools, PYTHON_VERSION, one setup script"
mkdir -p "$WORK/proj" && cd "$WORK/proj" && git init -q && echo hello > README.md && git add . && git -c user.name=p -c user.email=p@x commit -qm init
cat > agy.yaml <<EOF
profile: default
project_name: $PROBE
agent: claude
kits: [claude, chrome-devtools]
auth_mode: sandbox-local
clone: true
build_args: {PYTHON_VERSION: "3.12"}
setup_scripts: ["echo setup-ran > /home/agent/setup-marker"]
env: [PROBE_ENV=1]
EOF
cd "$OLDPWD"

step "2. up: sbx builds the workload (gate 1), hooks run, no proxy placeholders"
(cd "$WORK/proj" && "$AGY" up < /dev/null) || true   # the attach ends when stdin closes
sbx ls | grep "$NAME"
inside 'python --version && cat /home/agent/setup-marker && claude --version'
inside 'env | grep -E "_API_KEY=" && echo "!! placeholder present" || echo "no API key placeholders"'
inside 'echo rootfs-marker > /home/agent/rootfs-marker; mkdir -p /home/agent/.claude/projects && echo s > /home/agent/.claude/projects/marker'

step "3. upgrade: add codex in place (kit add); binary arrives with the kit"
sed -i.bak 's/kits: \[claude, chrome-devtools\]/kits: [claude, chrome-devtools, codex]/' "$WORK/proj/agy.yaml"
(cd "$WORK/proj" && "$AGY" upgrade < /dev/null) || true
inside 'codex --version && cat /home/agent/rootfs-marker'

step "4. recreate after removing a kit (gate 2): rootfs, volume and clone survive"
sed -i.bak 's/kits: \[claude, chrome-devtools, codex\]/kits: [claude, codex]/' "$WORK/proj/agy.yaml"
(cd "$WORK/proj" && "$AGY" recreate < /dev/null) || true
inside 'cat /home/agent/rootfs-marker && cat /home/agent/.claude/projects/marker && git -C "$WORKSPACE_DIR" log --oneline | head -1'

step "5. recreate to proxy-managed (login mode change)"
sed -i.bak 's/auth_mode: sandbox-local/auth_mode: proxy-managed/' "$WORK/proj/agy.yaml"
(cd "$WORK/proj" && "$AGY" recreate < /dev/null) || true
inside 'env | grep -E "^SBX_CRED_(ANTHROPIC|OPENAI)_MODE=" || echo "!! no credential mode surfaced"'

step "6. down"
(cd "$WORK/proj" && yes | "$AGY" down) || true
echo "live check finished; see the trap output for cleanup"
