#!/usr/bin/env bash
# City Brain - Linux / WSL launcher.  Usage: ./run_city.sh   (PORT=8000 by default)
cd "$(dirname "$0")"
if [ -z "$SUMO_HOME" ]; then
  for d in /usr/share/sumo /usr/local/share/sumo /opt/sumo; do [ -d "$d" ] && export SUMO_HOME="$d" && break; done
fi
command -v sumo >/dev/null || { echo "SUMO not found: sudo apt install sumo sumo-tools (or set SUMO_HOME)"; exit 1; }
python3 -m pip install --quiet --break-system-packages -r requirements_city.txt 2>/dev/null || python3 -m pip install --quiet -r requirements_city.txt
export PORT="${PORT:-8000}" PYTHONIOENCODING=utf-8
echo "City Brain on http://localhost:$PORT  (Ctrl+C to stop; training is saved automatically)"
exec python3 -m citybrain.server
