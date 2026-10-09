#!/usr/bin/env bash
# Run the asset-intake sample against the live OpenAI Decisions API.
#
#   ./run.sh                          test with the sample image
#   ./run.sh --image photo.jpg        test with your own image
#   ./run.sh --image photo.jpg --alt "Correct alt text"   ... plus a control case
#   ./run.sh --runs 3                 call each case three times (latency, stability)
#   ./run.sh --skip-sdk               skip step 2 (the OpenAI SDK run)
#
# The key comes from OPENAI_API_KEY, or from a .env file next to this script
# (copy .env.example to .env and fill it in).
set -euo pipefail

cd "$(dirname "$0")"

IMAGE="fixtures/sample-asset.png"
RUNS=1
ALT=""
SKIP_SDK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    --alt) ALT="$2"; shift 2 ;;
    --skip-sdk) SKIP_SDK=1; shift ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is not set. Export it, or copy .env.example to .env and add your key." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "Python 3.9 or later is required (found: $(python3 --version 2>&1))." >&2
  exit 1
fi
if [[ ! -f "$IMAGE" ]]; then
  echo "Image not found: $IMAGE" >&2
  exit 1
fi

mkdir -p results

echo "== 1/2  Live test: sample and control cases, then an end-to-end triage() run (standard library only)"
TEST_STATUS=0
TEST_ARGS=(--image "$IMAGE" --runs "$RUNS")
[[ -n "$ALT" ]] && TEST_ARGS+=(--alt "$ALT")
python3 test_sample.py "${TEST_ARGS[@]}" || TEST_STATUS=$?

if [[ "$SKIP_SDK" == 0 ]]; then
  echo
  echo "== 2/2  The example itself, through the OpenAI SDK"
  if [[ ! -d .venv ]]; then
    echo "Creating .venv and installing requirements.txt ..."
    python3 -m venv .venv
  fi
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
  .venv/bin/python asset_triage.py "$IMAGE" fixtures/metadata.txt fixtures/rights.txt \
    | tee results/sdk-run.json
fi

echo
if [[ "$TEST_STATUS" == 0 ]]; then
  if [[ "$SKIP_SDK" == 0 ]]; then
    echo "Done. Results are in results/: live-run-*.json, live-answers.json, sdk-run.json"
  else
    echo "Done. Results are in results/: live-run-*.json, live-answers.json"
  fi
else
  echo "The live test reported failures (exit $TEST_STATUS). See the FAIL lines above and results/." >&2
fi
exit "$TEST_STATUS"
