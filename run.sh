#!/usr/bin/env bash
# Smart-MB — one-shot launcher
#   ./run.sh          start the platform on http://localhost:8000
#   ./run.sh test     install deps, rebuild sample files + demo data, run the smoke test
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
"$PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt

if [ "$1" = "test" ]; then
  "$PY" -m tools.make_samples
  "$PY" -c "from app import seed; seed.ensure_seed(); print('database ready')"
  "$PY" -m tools.embed_demo
  "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --log-level warning &
  SRV=$!
  trap 'kill $SRV 2>/dev/null || true' EXIT
  for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8010/api/health >/dev/null; then break; fi
    sleep 0.5
  done
  "$PY" -m tools.smoke_test http://127.0.0.1:8010
  exit $?
fi

echo "Smart-MB starting on http://localhost:8000  (Ctrl+C to stop)"
exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --log-level info
