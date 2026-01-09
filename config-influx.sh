#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Load environment variables from .env file
# ------------------------------------------------------------------------------
if [ -f .env ]; then
  # Source .env file, handling both formats (with and without spaces around =)
  set -a
  source .env
  set +a
else
  echo "Warning: .env file not found" >&2
fi

# ------------------------------------------------------------------------------
# PowerLogger InfluxDB v2 bootstrap (buckets + retention + downsample tasks + tokens)
#
# Creates:
#   Buckets:
#     - PowerLogger_raw  (30 days)
#     - PowerLogger_1m   (90 days ~ 3 months)
#     - PowerLogger_5m   (365 days ~ 1 year)
#     - PowerLogger_1h   (forever)
#
#   Flux Tasks:
#     - powerlogger_downsample_1m: raw  -> 1m
#     - powerlogger_downsample_5m: 1m   -> 5m
#     - powerlogger_downsample_1h: 5m   -> 1h
#
#   Tokens:
#     - PowerLogger Writer token (WRITE to raw bucket only) for your Python logger
#     - Grafana ReadOnly token (READ all 4 buckets)
#
# Requirements:
#   - influx CLI configured to talk to your InfluxDB (either via influx config,
#     or env vars: INFLUX_HOST / INFLUX_TOKEN / INFLUX_ORG)
#   - python3 available (used only to parse JSON from influx CLI)
#
# Usage:
#   export INFLUX_ORG=soothill
#   export INFLUX_HOST=http://10.10.100.252:8086
#   export INFLUX_TOKEN=<ADMIN_TOKEN_WITH_BUCKET/TASK/AUTH_PERMS>
#   ./deploy_powerlogger_influx.sh
# ------------------------------------------------------------------------------

ORG="${INFLUX_ORG:-soothill}"

# Bucket names (can be overridden via .env)
RAW_BUCKET="${RAW_BUCKET:-PowerLogger_raw}"
B1M_BUCKET="${B1M_BUCKET:-PowerLogger_1m}"
B5M_BUCKET="${B5M_BUCKET:-PowerLogger_5m}"
B1H_BUCKET="${B1H_BUCKET:-PowerLogger_1h}"

# Measurement and field (can be overridden via .env)
RAW_MEASUREMENT="${MEASUREMENT:-PowerPulse}"
RAW_FIELD="Pulse"

# Retentions in hours (Influx accepts "720h", "2160h", etc.)
RET_RAW="720h"    # 30 days
RET_1M="2160h"    # 90 days
RET_5M="8760h"    # 365 days
RET_1H="0"        # forever

TASK_1M_NAME="powerlogger_downsample_1m"
TASK_5M_NAME="powerlogger_downsample_5m"
TASK_1H_NAME="powerlogger_downsample_1h"

desc_writer="PowerLogger Python writer (raw only)"
desc_grafana="Grafana read-only (PowerLogger buckets)"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }; }
need_cmd influx
need_cmd python3

json_get_first_id_by_name() {
  # Reads influx --json output on stdin and prints the first object's "id" where "name" matches $1
  local name="$1"
  python3 - "$name" <<'PY'
import json,sys
name=sys.argv[1]
data=json.load(sys.stdin)
for obj in data:
    if obj.get("name")==name and obj.get("id"):
        print(obj["id"])
        sys.exit(0)
sys.exit(0)
PY
}

json_get_task_id_by_name() {
  local name="$1"
  python3 - "$name" <<'PY'
import json,sys
name=sys.argv[1]
data=json.load(sys.stdin)
for obj in data:
    if obj.get("name")==name and obj.get("id"):
        print(obj["id"])
        sys.exit(0)
sys.exit(0)
PY
}

json_get_token_from_auth_create() {
  python3 <<'PY'
import json,sys
data=json.load(sys.stdin)
# influx auth create --json returns a list (usually length 1)
if isinstance(data, list) and data:
    tok=data[0].get("token","")
    if tok:
        print(tok)
PY
}

bucket_id_by_name() {
  local name="$1"
  local output
  output="$(influx bucket list --org "$ORG" --json 2>&1)"
  if [ $? -ne 0 ]; then
    echo "Warning: Failed to list buckets" >&2
    echo ""
    return 1
  fi
  echo "$output" | json_get_first_id_by_name "$name" || true
}

ensure_bucket() {
  local name="$1"
  local retention="$2"

  local id
  id="$(bucket_id_by_name "$name")"
  if [[ -n "${id:-}" ]]; then
    echo "Bucket exists: $name (id=$id)"
    return 0
  fi

  echo "Creating bucket: $name (retention=$retention)"
  if [[ "$retention" == "0" ]]; then
    influx bucket create --org "$ORG" --name "$name" --retention 0 >/dev/null
  else
    influx bucket create --org "$ORG" --name "$name" --retention "$retention" >/dev/null
  fi
}

task_id_by_name() {
  influx task list --org "$ORG" --json | json_get_task_id_by_name "$1" || true
}

delete_task_if_exists() {
  local name="$1"
  local id
  id="$(task_id_by_name "$name")"
  if [[ -n "${id:-}" ]]; then
    echo "Deleting existing task: $name (id=$id)"
    influx task delete --org "$ORG" --id "$id" >/dev/null
  fi
}

create_task_from_heredoc() {
  local name="$1"
  local flux_file="$2"
  echo "Creating task: $name"
  influx task create --org "$ORG" --file "$flux_file" >/dev/null
}

create_writer_token_raw_only() {
  local raw_id
  raw_id="$(bucket_id_by_name "$RAW_BUCKET")"
  if [[ -z "${raw_id:-}" ]]; then
    echo "ERROR: raw bucket id not found for $RAW_BUCKET" >&2
    exit 1
  fi

  # Create token scoped only to write the raw bucket
  influx auth create --org "$ORG" --write-bucket-id "$raw_id" --description "$desc_writer" --json \
    | json_get_token_from_auth_create
}

create_grafana_readonly_token_all() {
  local id_raw id_1m id_5m id_1h
  id_raw="$(bucket_id_by_name "$RAW_BUCKET")"
  id_1m="$(bucket_id_by_name "$B1M_BUCKET")"
  id_5m="$(bucket_id_by_name "$B5M_BUCKET")"
  id_1h="$(bucket_id_by_name "$B1H_BUCKET")"

  if [[ -z "${id_raw:-}" || -z "${id_1m:-}" || -z "${id_5m:-}" || -z "${id_1h:-}" ]]; then
    echo "ERROR: one or more bucket IDs missing; cannot create grafana token" >&2
    exit 1
  fi

  influx auth create \
    --org "$ORG" \
    --read-bucket-id "$id_raw" \
    --read-bucket-id "$id_1m" \
    --read-bucket-id "$id_5m" \
    --read-bucket-id "$id_1h" \
    --description "$desc_grafana" \
    --json \
    | json_get_token_from_auth_create
}

main() {
  echo "==> Ensuring buckets (org=$ORG)"
  ensure_bucket "$RAW_BUCKET" "$RET_RAW"
  ensure_bucket "$B1M_BUCKET" "$RET_1M"
  ensure_bucket "$B5M_BUCKET" "$RET_5M"
  ensure_bucket "$B1H_BUCKET" "$RET_1H"

  # ---------------------------------------------------------------------------
  # Flux tasks
  #
  # Notes:
  # - We keep the same measurement name ("PowerPulse") and field ("Pulse") after downsampling.
  # - aggregateWindow will create points aligned to the window boundaries.
  # - We intentionally overlap ranges a bit so late points get included; rewriting the same
  #   time window is OK (last write wins for identical series+timestamp+field).
  # ---------------------------------------------------------------------------

  tmpdir="$HOME/.powerlogger-tmp-$$"
  mkdir -p "$HOME/.powerlogger-tmp" 2>/dev/null
  trap 'rm -rf "$tmpdir"' EXIT

  echo "==> Creating Flux tasks (downsampling)"

  delete_task_if_exists "$TASK_1M_NAME"
  delete_task_if_exists "$TASK_5M_NAME"
  delete_task_if_exists "$TASK_1H_NAME"

  task1m="$tmpdir/task_1m.flux"
  cat >"$task1m" <<FLUX
option task = {name: "${TASK_1M_NAME}", every: 1m, offset: 10s}

from(bucket: "${RAW_BUCKET}")
  |> range(start: -5m)  // overlap for late arrivals + safety
  |> filter(fn: (r) => r._measurement == "${RAW_MEASUREMENT}" and r._field == "${RAW_FIELD}")
  |> aggregateWindow(every: 1m, fn: sum, createEmpty: false)
  |> to(bucket: "${B1M_BUCKET}", org: "${ORG}")
FLUX
  create_task_from_heredoc "$TASK_1M_NAME" "$task1m"

  task5m="$tmpdir/task_5m.flux"
  cat >"$task5m" <<FLUX
option task = {name: "${TASK_5M_NAME}", every: 5m, offset: 20s}

from(bucket: "${B1M_BUCKET}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "${RAW_MEASUREMENT}" and r._field == "${RAW_FIELD}")
  |> aggregateWindow(every: 5m, fn: sum, createEmpty: false)
  |> to(bucket: "${B5M_BUCKET}", org: "${ORG}")
FLUX
  create_task_from_heredoc "$TASK_5M_NAME" "$task5m"

  task1h="$tmpdir/task_1h.flux"
  cat >"$task1h" <<FLUX
option task = {name: "${TASK_1H_NAME}", every: 1h, offset: 30s}

from(bucket: "${B5M_BUCKET}")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "${RAW_MEASUREMENT}" and r._field == "${RAW_FIELD}")
  |> aggregateWindow(every: 1h, fn: sum, createEmpty: false)
  |> to(bucket: "${B1H_BUCKET}", org: "${ORG}")
FLUX
  create_task_from_heredoc "$TASK_1H_NAME" "$task1h"

  echo "==> Creating tokens"
  writer_token="$(create_writer_token_raw_only)"
  grafana_token="$(create_grafana_readonly_token_all)"

  echo
  echo "==================== DONE ===================="
  echo "Org:                 $ORG"
  echo "Buckets:"
  echo "  - $RAW_BUCKET  retention=$RET_RAW"
  echo "  - $B1M_BUCKET   retention=$RET_1M"
  echo "  - $B5M_BUCKET   retention=$RET_5M"
  echo "  - $B1H_BUCKET   retention=forever"
  echo
  echo "Tokens (SAVE THESE NOW):"
  echo "  PowerLogger writer (raw only):"
  echo "    $writer_token"
  echo
  echo "  Grafana read-only (all buckets):"
  echo "    $grafana_token"
  echo "============================================="
  echo
  echo "Python should now write ONLY to bucket: $RAW_BUCKET"
}

main "$@"