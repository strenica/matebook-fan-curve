#!/usr/bin/env bash
# uninstall.sh - remove fanctl, its unit, and its docs.
#
# Leaves /etc/ec-fan-curve/config.toml in place so a reinstall keeps your
# curve. Pass --purge to remove that too.
set -euo pipefail

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

say() { printf '==> %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { printf 'error: run with sudo\n' >&2; exit 1; }

if systemctl list-unit-files ec-fan-curve.service >/dev/null 2>&1; then
    say "stopping and disabling ec-fan-curve"
    systemctl disable --now ec-fan-curve.service || true
fi

say "removing unit"
rm -f /etc/systemd/system/ec-fan-curve.service
systemctl daemon-reload

say "removing binary and docs"
rm -f /usr/local/bin/fanctl
rm -rf /usr/local/share/doc/ec-fan-curve

if [[ $PURGE -eq 1 ]]; then
    say "purging config"
    rm -rf /etc/ec-fan-curve
else
    say "kept /etc/ec-fan-curve/config.toml (use --purge to remove)"
fi

say "done - the EC has control"
