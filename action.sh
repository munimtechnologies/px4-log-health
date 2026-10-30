#!/usr/bin/env bash
# Runs checkup.py on the PX4 logs matching INPUT_LOGS and appends the report
# to the job summary; action.yml calls this. gps_lag.py and innov.py come from
# munimtechnologies/px4-gps-lag at INPUT_GPS_LAG_REF unless already present.
# By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
set -euo pipefail

here="${GITHUB_ACTION_PATH:-$(cd "$(dirname "$0")" && pwd)}"
ref="${INPUT_GPS_LAG_REF:-967b5ad92d04ca6ac7e29684b411fbf5df243e05}"
summary="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
patterns="${INPUT_LOGS:-**/*.ulg}"

python -m pip install --quiet --disable-pip-version-check numpy pyulog
for f in gps_lag.py innov.py; do
  if [ ! -f "$here/$f" ]; then
    curl -fsSL "https://raw.githubusercontent.com/munimtechnologies/px4-gps-lag/$ref/$f" -o "$here/$f"
  fi
done

shopt -s globstar nullglob
# shellcheck disable=SC2206  # the patterns are meant to be expanded
logs=( $patterns )
if [ "${#logs[@]}" -eq 0 ]; then
  echo "PX4 log health: no files match '$patterns'." >> "$summary"
  exit 0
fi

args=(--md "$summary" --json "${INPUT_JSON:-px4-log-health.jsonl}")
if [ "${INPUT_FAIL_ON_FLAG:-false}" = "true" ]; then
  args+=(--fail-on-flag)
fi
python "$here/checkup.py" "${args[@]}" "${logs[@]}"
