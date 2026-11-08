#!/usr/bin/env bash
# Registers City Ground Zero with the desktop (name and TSYP14 icon in the dock, task switcher and app grid).
# GNOME and most desktops take an app's icon from a .desktop launcher that matches the window's class, not from the window
# itself, so this writes ~/.local/share/applications/city-ground-zero.desktop. It is safe to run any time (it only rewrites
# that one file) and the run scripts call it. Set CITY_NO_LAUNCHER=1 to skip it.
set -euo pipefail
[[ "${CITY_NO_LAUNCHER:-0}" == "1" ]] && exit 0
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
target="$applications/city-ground-zero.desktop"
icon="$bundle_dir/assets/branding/lg-out.png"
[[ -f "$icon" ]] || exit 0
mkdir -p "$applications"
content="[Desktop Entry]
Type=Application
Name=City Ground Zero
Comment=Live smart-city simulation and control room
Exec=\"$bundle_dir/run_control_room.sh\"
Icon=$icon
Terminal=true
Categories=Education;Science;
StartupWMClass=City Ground Zero
StartupNotify=true
"
if [[ ! -f "$target" ]] || [[ "$(cat "$target")" != "$content" ]]; then
    printf '%s\n' "$content" > "$target"
    chmod 644 "$target"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$applications" >/dev/null 2>&1 || true
    echo "Launcher registered: $target"
fi
