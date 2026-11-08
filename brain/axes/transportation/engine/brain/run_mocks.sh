#!/usr/bin/env bash
# Mock Twin (9001) + mock Guardian (9002) in the background.  Stop with: kill $(cat /tmp/mocks.pid)
cd "$(dirname "$0")"
export LOGS_DIR="${LOGS_DIR:-../logs}" BRAIN_URL="${BRAIN_URL:-http://127.0.0.1:8000}"
python3 -m mocks.mock_twin & echo $! > /tmp/mocks.pid
python3 -m mocks.mock_guardian & echo $! >> /tmp/mocks.pid
wait
