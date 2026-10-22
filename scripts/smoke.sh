#!/usr/bin/env bash
# Runs every command block in the README against a running stack (docker compose up --build -d --wait)
# and checks what each one is supposed to print. CI runs it; so can you, but it changes the stack's state.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
API="http://localhost:${CASHFLOW_API_PORT:-8000}"
DASH="http://localhost:${CASHFLOW_DASHBOARD_PORT:-8501}"
OUT="$(mktemp -d)"
cf() { docker compose run --rm -T pipeline cashflow "$@"; }
expect() { if ! grep -q -- "$2" "$1"; then echo "expected '$2' in:"; cat "$1"; exit 1; fi; }

echo "== close status after the seed"
cf close --period 2026-09 > "$OUT/close1.txt"
expect "$OUT/close1.txt" "September 2026: 5 ready, 3 held, 0 failed, 8 clients"
expect "$OUT/close1.txt" "Equipment Loan - Mower Fleet"
expect "$OUT/close1.txt" "expired on 2 Oct 2026"
expect "$OUT/close1.txt" "equity moved \$2,350.00 less than net income"

echo "== mapping queue"
cf suggestions > "$OUT/suggestions.txt"
expect "$OUT/suggestions.txt" "suggested: Loans \[loans\], confidence 0.90, from rules"

echo "== approve the loan mapping, which re-runs Birchline"
cf approve --client birchline-grounds --by mara.quist@example.com > "$OUT/approve.txt"
expect "$OUT/approve.txt" '"line": "loans"'
expect "$OUT/approve.txt" '"status": "ready"'

echo "== reconnect the bakery and run it"
cf simulate reconnect --client rye-and-ember-bakery > "$OUT/reconnect.txt"
expect "$OUT/reconnect.txt" '"connection": "connected"'
cf run --client rye-and-ember-bakery --period 2026-09 > "$OUT/run.txt"
expect "$OUT/run.txt" '"status": "ready"'

echo "== mark a ready workbook as sent and download it"
cf deliver --client wrenfield-dental --period 2026-09 --by mara.quist@example.com > "$OUT/deliver.txt"
expect "$OUT/deliver.txt" '"delivered": true'
curl -fsS -o "$OUT/wrenfield-2026-09.xlsx" "$API/clients/wrenfield-dental/workbook?period=2026-09"
head -c 2 "$OUT/wrenfield-2026-09.xlsx" > "$OUT/magic.txt"
expect "$OUT/magic.txt" "PK"

echo "== template inventory"
cf inventory > "$OUT/inventory.txt"
expect "$OUT/inventory.txt" '"formulas": 172'

echo "== close status afterwards: only the retained earnings posting is still held"
cf close --period 2026-09 > "$OUT/close2.txt"
expect "$OUT/close2.txt" "September 2026: 7 ready, 1 held, 0 failed, 8 clients"

echo "== API and dashboard answer"
curl -fsS "$API/close?period=2026-09" > "$OUT/close.json"
expect "$OUT/close.json" '"held":1'
curl -fsS "$API/template" > "$OUT/template.json"
expect "$OUT/template.json" '"formula_count":172'
curl -fsS "$DASH/_stcore/health" > "$OUT/dash.txt"
expect "$OUT/dash.txt" "ok"

echo "smoke passed"
