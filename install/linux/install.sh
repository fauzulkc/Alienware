#!/usr/bin/env bash
# Install x17tune as a systemd service. Dry-run unless --apply is given.
#   sudo ./install/linux/install.sh            # dry-run service
#   sudo ./install/linux/install.sh --apply    # tune for real
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PREFIX=/opt/x17tune

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Python 3.11+ required"; exit 1; }
mkdir -p "$PREFIX" /var/log/x17tune
[[ -d "$PREFIX/venv" ]] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip >/dev/null
"$PREFIX/venv/bin/pip" install "$REPO[nvidia]"

echo "== capability probe (read-only) =="
"$PREFIX/venv/bin/python" -m x17tune probe || true
modinfo alienware_wmi >/dev/null 2>&1 || echo "note: alienware_wmi module not found - thermal profiles/fan boost unavailable (kernel 6.15+ recommended)"

install -m 0644 "$REPO/install/linux/x17tune.service" /etc/systemd/system/x17tune.service
if [[ $APPLY -eq 1 ]]; then
  sed -i 's|x17tune run --logfile|x17tune run --apply --logfile|' /etc/systemd/system/x17tune.service
fi
systemctl daemon-reload
systemctl enable --now x17tune.service
echo "x17tune service installed ($([[ $APPLY -eq 1 ]] && echo APPLY || echo DRY-RUN)). Logs: journalctl -u x17tune -f"
