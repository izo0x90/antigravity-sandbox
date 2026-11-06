#!/usr/bin/env bash
# Validates every bundled v3 kit descriptor, plus a rendered sample workload, by
# building them through the docker/sandbox-kit:3 frontend on the host Docker engine.
# No sandbox is created and nothing is pushed. Needs Docker running.
set -euo pipefail
cd "$(dirname "$0")/.."
KITS=src/agy_sandbox/kits
status=0
for dir in "$KITS"/*/; do
  name=$(basename "$dir")
  descriptor="$dir/$name.yaml"
  [ -f "$descriptor" ] || continue
  printf '== %s\n' "$name"
  if docker buildx build -q -f "$descriptor" -t "agy-kit-check-$name:dev" "$dir" >/dev/null 2>"/tmp/agy-kit-check-$name.log"; then
    docker image rm -f "agy-kit-check-$name:dev" >/dev/null 2>&1 || true
  else
    status=1
    echo "   FAILED; see /tmp/agy-kit-check-$name.log"
    tail -5 "/tmp/agy-kit-check-$name.log" | sed 's/^/   /'
  fi
done

printf '== sample workload\n'
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
AGY_SANDBOX_STATE_DIR="$tmp/state" .venv/bin/python - <<'EOF'
from pathlib import Path
from agy_sandbox.config import AgyConfig
from agy_sandbox import workload
cfg = AgyConfig(project_name="kitcheck", build_args={"PYTHON_VERSION": "3.12"}, setup_scripts=["echo setup"])
ref = workload.write(cfg, Path.cwd())
print(ref.dir)
EOF
wl="$tmp/state/kits/agy-sandbox-kitcheck/workload"
if docker buildx build -q -f "$wl/workload.yaml" -t agy-kit-check-workload:dev "$wl" >/dev/null 2>"/tmp/agy-kit-check-workload.log"; then
  docker image rm -f agy-kit-check-workload:dev >/dev/null 2>&1 || true
else
  status=1
  echo "   FAILED; see /tmp/agy-kit-check-workload.log"
  tail -5 /tmp/agy-kit-check-workload.log | sed 's/^/   /'
fi
exit $status
