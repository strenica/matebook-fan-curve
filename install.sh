#!/usr/bin/env bash
# install.sh - install fanctl and its systemd unit.
#
# Needs root: the controller writes to /proc/acpi/call, which is root-only.
#
# The EC register addresses this tool writes are model-specific. Writing the
# wrong address on a different machine can hang or damage the EC, so the
# installer refuses to continue on an unverified model unless --force is given.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DST=/usr/local/bin/fanctl
CONF_DIR=/etc/ec-fan-curve
CONF_DST="$CONF_DIR/config.toml"
UNIT_DST=/etc/systemd/system/ec-fan-curve.service
DOC_DIR=/usr/local/share/doc/ec-fan-curve

# Models this has actually been run on. Add yours only after verifying that
# 'fanctl status' reports a duty that matches the observed RPM.
SUPPORTED_MODELS=("MACHD-WXX9")

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) printf 'error: unknown argument: %s\n' "$arg" >&2; exit 1 ;;
    esac
done

say() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Add any key present in the shipped config but missing from the installed one.
# Keeps the user's values and comments; only appends what is absent, so a new
# setting added to the repo reaches an existing install instead of being
# silently covered by a code default.
merge_config() {
    local defaults=$1 target=$2
    python3 - "$defaults" "$target" <<'PY'
import re
import sys
import tomllib

defaults_path, target_path = sys.argv[1], sys.argv[2]

with open(defaults_path, "rb") as f:
    defaults = tomllib.load(f).get("fan", {})
with open(target_path, "rb") as f:
    current = tomllib.load(f).get("fan", {})

missing = {k: v for k, v in defaults.items() if k not in current}
if not missing:
    print("config already has every key")
    raise SystemExit(0)


def fmt(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"%s"' % value
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(fmt(v) for v in value) + "]"
    raise TypeError("cannot serialise %r" % (value,))


lines = open(target_path).read().splitlines(keepends=True)

start = next((i for i, l in enumerate(lines) if l.strip() == "[fan]"), None)
if start is None:
    lines.append("\n[fan]\n")
    start = len(lines) - 1

end = len(lines)
# A table header is [name]; an array element like [0, 30], is not. Requiring a
# leading letter keeps the insert out of a multi-line `curve = [...]` block.
HEADER = re.compile(r"^\s*\[[A-Za-z_][A-Za-z0-9_.-]*\]\s*$")
for j in range(start + 1, len(lines)):
    if HEADER.match(lines[j]):
        end = j
        break

block = "".join("%s = %s\n" % (k, fmt(v)) for k, v in missing.items())
lines.insert(end, block)
open(target_path, "w").write("".join(lines))
print("added: " + ", ".join(missing))
PY
}

[[ $EUID -eq 0 ]] || die "run with sudo"

command -v python3 >/dev/null || die "python3 not found"
python3 -c 'import tomllib' 2>/dev/null || die "python3 3.11+ required (tomllib)"

# acpi_call is what exposes /proc/acpi/call
modinfo acpi_call >/dev/null 2>&1 \
    || die "acpi_call module not found - install acpi-call-dkms first"

# ---------------------------------------------------------------- hardware

PRODUCT="$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo unknown)"
BOARD="$(cat /sys/class/dmi/id/board_name 2>/dev/null || echo unknown)"
BIOS="$(cat /sys/class/dmi/id/bios_version 2>/dev/null || echo unknown)"

say "detected: $PRODUCT ($BOARD), BIOS $BIOS"

supported=0
for model in "${SUPPORTED_MODELS[@]}"; do
    [[ "$PRODUCT" == "$model" ]] && supported=1
done

if [[ $supported -eq 0 ]]; then
    warn "$PRODUCT is not a verified model"
    warn "verified: ${SUPPORTED_MODELS[*]}"
    warn ""
    warn "This tool writes EC RAM addresses 0x2A04/0x2A05. Those addresses are"
    warn "model-specific: on a different machine they may be something else"
    warn "entirely, and writing them can hang or damage the EC."
    warn ""
    warn "If you want to find out whether your machine is compatible, do it"
    warn "read-only first:"
    warn ""
    warn "    sudo modprobe acpi_call"
    warn "    sudo chmod 666 /proc/acpi/call"
    warn "    ./fanctl.py status"
    warn ""
    warn "Check that the reported duty roughly matches the observed RPM"
    warn "(RPM is about duty * 100 on the verified model). If it does not,"
    warn "stop - the addresses are wrong for your machine."
    warn ""
    if [[ $FORCE -ne 1 ]]; then
        die "refusing to install on an unverified model (use --force to override)"
    fi
    warn "--force given, continuing anyway"
fi

# ---------------------------------------------------------------- install

say "installing $BIN_DST"
install -Dm755 "$REPO_DIR/fanctl.py" "$BIN_DST"

say "installing config to $CONF_DST"
install -d "$CONF_DIR"
if [[ -e "$CONF_DST" ]]; then
    say "config exists, merging in any new keys"
    merge_config "$REPO_DIR/config.toml" "$CONF_DST"
else
    install -m644 "$REPO_DIR/config.toml" "$CONF_DST"
fi

say "installing docs to $DOC_DIR"
install -d "$DOC_DIR"
install -m644 "$REPO_DIR/README.md" "$DOC_DIR/README.md"

say "installing systemd unit"
install -m644 "$REPO_DIR/systemd/ec-fan-curve.service" "$UNIT_DST"
systemctl daemon-reload
systemctl enable ec-fan-curve.service
# restart, not 'enable --now': on a reinstall the service is already running,
# and 'enable --now' leaves the old process (and old code) in place.
systemctl restart ec-fan-curve.service

say "done"
systemctl --no-pager --lines=5 status ec-fan-curve.service || true
