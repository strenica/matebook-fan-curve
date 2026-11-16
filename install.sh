#!/usr/bin/env bash
# install.sh - install fanctl and its systemd unit.
#
# Needs root: the controller writes to /proc/acpi/call, which is root-only.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DST=/usr/local/bin/fanctl
CONF_DIR=/etc/ec-fan-curve
CONF_DST="$CONF_DIR/config.toml"
UNIT_DST=/etc/systemd/system/ec-fan-curve.service
DOC_DIR=/usr/local/share/doc/ec-fan-curve

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"

command -v python3 >/dev/null || die "python3 not found"
python3 -c 'import tomllib' 2>/dev/null || die "python3 3.11+ required (tomllib)"

# acpi_call is what exposes /proc/acpi/call
if ! modinfo acpi_call >/dev/null 2>&1; then
    die "acpi_call module not found - install acpi-call-dkms first"
fi

say "installing $BIN_DST"
install -Dm755 "$REPO_DIR/fanctl.py" "$BIN_DST"

say "installing config to $CONF_DST"
install -d "$CONF_DIR"
if [[ -e "$CONF_DST" ]]; then
    say "config already exists, leaving it alone"
else
    install -m644 "$REPO_DIR/config.toml" "$CONF_DST"
fi

say "installing docs to $DOC_DIR"
install -d "$DOC_DIR"
install -m644 "$REPO_DIR/README.md" "$DOC_DIR/README.md"

say "installing systemd unit"
install -m644 "$REPO_DIR/systemd/ec-fan-curve.service" "$UNIT_DST"
systemctl daemon-reload
systemctl enable --now ec-fan-curve.service

say "done"
systemctl --no-pager --lines=5 status ec-fan-curve.service || true
