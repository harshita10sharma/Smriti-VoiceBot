#!/usr/bin/env bash
# Post-deployment smoke test against a real AWS-deployed VoiceBot URL.
# Reuses this repository's existing tools/smoke_test.py remote mode rather
# than duplicating the checks -- see that file for exactly what it covers
# (health, languages, an authenticated welcome, an authenticated text turn,
# and, with --voice, a synthetic-audio voice turn through job polling and
# audio retrieval).
#
# Usage:
#   SMRITI_SMOKE_API_KEY=<real key> ./smoke_test.sh https://your-domain.example --voice
set -euo pipefail

BASE_URL="${1:?Usage: smoke_test.sh <base-url> [--voice]}"
shift || true

if [ -z "${SMRITI_SMOKE_API_KEY:-}" ]; then
  echo "SMRITI_SMOKE_API_KEY must be set in the environment (never pass it as an argument)." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
python tools/smoke_test.py --base-url "$BASE_URL" "$@"
