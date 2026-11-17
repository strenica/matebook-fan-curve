#!/usr/bin/env python3
"""fanctl - EC fan curve controller for Huawei MateBook X Pro 2021 (MACHD-WXX9).

The firmware exposes no fan RPM method. SFND/SFNM are absent, TFN1 has no
_FSL/_FST/_FPS, and the only ECCC fan commands (0x46 via SFRS, 0x48 via SMLS)
are binary mode toggles that do not reduce speed. Generic tools such as
fanctld and nbfc cannot drive this machine.

What does work: the EC keeps fan duty as a percentage in EC RAM at 0x2A04
(fan 0) and 0x2A05 (fan 1), with RPM ~= duty * 100. The EC recomputes those
registers roughly once per second, so writing faster than that holds a lower
duty. Measured on this machine: 30% -> ~3050 RPM, 35% -> ~3600, 40% -> ~4000,
50% (EC default at 50C) -> ~5000.

Safety: above emergency_c the controller stops writing and lets the EC's own
curve take over. Same on SIGTERM/SIGINT and on any exception. If this process
dies the EC reclaims control within about a second, so the fan can never stay
locked low.
"""

import argparse
import os
import signal
import sys
import time

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

ACPI = "/proc/acpi/call"
EC = "\\_SB.PC00.LPCB.H_EC"
DUTY_REGS = (0x2A04, 0x2A05)
TEMP_PATH = "/sys/class/thermal/thermal_zone11/temp"
FAN_PATHS = (
    "/sys/class/hwmon/hwmon1/fan1_input",
    "/sys/class/hwmon/hwmon2/fan1_input",
)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_CANDIDATES = (
    os.path.join(HERE, "config.toml"),
    "/etc/ec-fan-curve/config.toml",
)


def default_config():
    """Prefer a config next to the script, else the installed one.

    Installed to /usr/local/bin the script has no config beside it, so the
    /etc path is what actually gets used in production.
    """
    for path in CONFIG_CANDIDATES:
        if os.path.exists(path):
            return path
    return CONFIG_CANDIDATES[0]

_running = True


def _stop(signum, frame):
    global _running
    _running = False


# ---------------------------------------------------------------- config

def load_config(path):
    if tomllib is None:
        raise SystemExit("fanctl: Python 3.11+ required for tomllib")
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    fan = raw.get("fan", {})
    curve = fan.get("curve")
    if not curve:
        raise SystemExit("fanctl: %s has no [[fan.curve]] entries" % path)

    points = sorted((int(t), int(d)) for t, d in curve)
    for temp, duty in points:
        if not 0 <= duty <= 100:
            raise SystemExit("fanctl: duty %d out of range in %s" % (duty, path))

    return {
        "interval": fan.get("interval_ms", 500) / 1000.0,
        "emergency_c": fan.get("emergency_c", 85),
        "min_duty": fan.get("min_duty", 25),
        "hysteresis_c": fan.get("hysteresis_c", 1.5),
        "max_step_up": fan.get("max_step_up", 10),
        "max_step_down": fan.get("max_step_down", 2),
        "idle_c": fan.get("idle_c", 45),
        "curve": points,
    }


def _step(temp, cfg):
    """Raw step-function lookup, no hysteresis."""
    duty = cfg["curve"][0][1]
    for threshold, value in cfg["curve"]:
        if temp >= threshold:
            duty = value
    return max(duty, cfg["min_duty"])


def duty_for(temp, cfg, current=None):
    """Target duty for `temp`, with hysteresis around the current value.

    Without `current` this is the plain step function. With it, a change only
    happens once the temperature has moved `hysteresis_c` past the boundary,
    so a value sitting on a threshold stops flapping between two duties.
    """
    raw = _step(temp, cfg)
    if current is None or raw == current:
        return raw

    h = cfg["hysteresis_c"]
    if raw > current:
        return max(_step(temp - h, cfg), current)
    return min(_step(temp + h, cfg), current)


def slew(target, current, cfg):
    """Limit how far duty may move in one tick.

    Asymmetric on purpose: up fast for thermal safety, down slowly so the fan
    does not audibly drop every time the load blips.
    """
    if target > current:
        return min(target, current + cfg["max_step_up"])
    if target < current:
        return max(target, current - cfg["max_step_down"])
    return current


def should_idle(temp, cfg, idle):
    """True while the curve should stay out of the EC's way.

    Hysteresis on the way out: once released, stay released until the
    temperature climbs hysteresis_c above idle_c. Without that, a value
    sitting on the boundary flaps between 'EC turns the fan off' and 'curve
    commands min_duty', which is audible as the fan starting and stopping.
    """
    if temp < cfg["idle_c"]:
        return True
    if idle and temp < cfg["idle_c"] + cfg["hysteresis_c"]:
        return True
    return False


# ---------------------------------------------------------------- EC access

def acpi(cmd):
    with open(ACPI, "w") as f:
        f.write(cmd)
    with open(ACPI, "rb") as f:
        return f.read().replace(b"\x00", b"").decode(errors="ignore")


def read_byte(addr):
    out = acpi("\\%s.RDER 0x%04X" % (EC, addr))
    for tok in out.split():
        if tok.startswith("0x"):
            try:
                return int(tok, 16)
            except ValueError:
                return None
    return None


def write_byte(addr, value):
    acpi("\\%s.WTER 0x%04X 0x%02X" % (EC, addr, value & 0xFF))


def read_temp():
    with open(TEMP_PATH) as f:
        return int(f.read().strip()) / 1000.0


def read_fans():
    out = []
    for path in FAN_PATHS:
        try:
            with open(path) as f:
                out.append(int(f.read().strip()))
        except OSError:
            out.append(0)
    return out


def preflight():
    """Refuse to write if the EC does not look like the one we expect.

    The duty registers are model-specific. A value outside 0-100 means we are
    looking at something else entirely, and writing it would be reckless.
    """
    if not os.path.exists(ACPI):
        raise SystemExit(
            "fanctl: %s missing - run 'sudo modprobe acpi_call'" % ACPI)
    if not os.access(ACPI, os.W_OK):
        raise SystemExit("fanctl: %s not writable - run as root" % ACPI)

    duty = read_byte(DUTY_REGS[0])
    if duty is None or not 0 <= duty <= 100:
        raise SystemExit(
            "fanctl: EC 0x%04X reads %r, expected 0-100.\n"
            "This does not look like a supported machine, so nothing was "
            "written.\nRun 'fanctl status' to inspect, and see the README "
            "section 'Supported hardware'." % (DUTY_REGS[0], duty))


# ---------------------------------------------------------------- commands

def cmd_status(cfg):
    temp = read_temp()
    fans = read_fans()
    duties = [read_byte(a) for a in DUTY_REGS]
    print("temp      %.1f C" % temp)
    print("fan0      %d RPM   fan1 %d RPM" % (fans[0], fans[1]))
    print("duty      %s / %s %%   (EC's own value)" % tuple(
        "?" if d is None else d for d in duties))
    print("curve     %d %% at this temp" % duty_for(temp, cfg))
    print("hysteresis %.1f C, slew +%d/-%d %% per tick"
          % (cfg["hysteresis_c"], cfg["max_step_up"], cfg["max_step_down"]))
    print("idle_c %d C (release to EC below this)" % cfg["idle_c"])
    print("emergency release at %d C" % cfg["emergency_c"])


def cmd_curve(cfg):
    print("temp C   duty %")
    for temp, duty in cfg["curve"]:
        print("%6d   %4d" % (temp, duty))
    print("\nmin_duty %d %%, interval %d ms, emergency %d C"
          % (cfg["min_duty"], cfg["interval"] * 1000, cfg["emergency_c"]))
    print("hysteresis %.1f C, slew +%d/-%d %% per tick"
          % (cfg["hysteresis_c"], cfg["max_step_up"], cfg["max_step_down"]))
    print("idle_c %d C (release to EC below this)" % cfg["idle_c"])


def _apply(duty, cfg, dry_run):
    if dry_run:
        return
    for addr in DUTY_REGS:
        write_byte(addr, duty)


def cmd_run(cfg, dry_run):
    print("fanctl: curve mode, %.0f ms interval, emergency at %d C"
          % (cfg["interval"] * 1000, cfg["emergency_c"]), flush=True)
    current = None
    logged = None
    released = False
    idle = False
    while _running:
        try:
            temp = read_temp()
        except OSError as exc:
            print("fanctl: cannot read temp: %s" % exc, file=sys.stderr)
            break

        # Below idle_c the EC wants the fan off and we want it at min_duty.
        # Writing anyway makes the two fight, which is audible as the fan
        # starting and stopping. Hand control back instead.
        if should_idle(temp, cfg, idle):
            if not idle:
                print("fanctl: %.1f C below idle_c, releasing to EC"
                      % temp, flush=True)
                idle = True
            time.sleep(cfg["interval"])
            continue
        if idle:
            print("fanctl: %.1f C, resuming curve" % temp, flush=True)
            idle = False
            current = None
            logged = None

        if temp >= cfg["emergency_c"]:
            if not released:
                print("fanctl: %.1f C >= %d C, releasing to EC until it cools"
                      % (temp, cfg["emergency_c"]), file=sys.stderr, flush=True)
                released = True
            time.sleep(cfg["interval"])
            continue

        if released:
            # Resume only once it has cooled past the deadband, otherwise the
            # curve and the emergency threshold fight at the boundary.
            if temp > cfg["emergency_c"] - cfg["hysteresis_c"]:
                time.sleep(cfg["interval"])
                continue
            print("fanctl: %.1f C, resuming curve" % temp, flush=True)
            released = False
            current = None
            logged = None

        target = duty_for(temp, cfg, current)
        current = target if current is None else slew(target, current, cfg)

        if current != logged:
            fans = read_fans()
            print("fanctl: %.1f C -> %d %% (fan %d/%d RPM)"
                  % (temp, current, fans[0], fans[1]), flush=True)
            logged = current

        _apply(current, cfg, dry_run)
        time.sleep(cfg["interval"])

    print("fanctl: stopped, EC has control", flush=True)


def cmd_set(cfg, duty, dry_run):
    duty = max(cfg["min_duty"], min(100, duty))
    print("fanctl: holding %d %% until interrupted" % duty, flush=True)
    released = False
    while _running:
        try:
            temp = read_temp()
        except OSError:
            break
        if temp >= cfg["emergency_c"]:
            if not released:
                print("fanctl: %.1f C >= %d C, releasing to EC until it cools"
                      % (temp, cfg["emergency_c"]), file=sys.stderr, flush=True)
                released = True
            time.sleep(cfg["interval"])
            continue
        if released:
            if temp > cfg["emergency_c"] - cfg["hysteresis_c"]:
                time.sleep(cfg["interval"])
                continue
            print("fanctl: %.1f C, resuming" % temp, flush=True)
            released = False
        _apply(duty, cfg, dry_run)
        time.sleep(cfg["interval"])
    print("fanctl: stopped, EC has control", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="EC fan curve controller for Huawei MateBook X Pro 2021")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "status", "curve", "set"],
                        help="run the curve (default), show status, show the "
                             "curve, or hold a fixed duty")
    parser.add_argument("value", nargs="?", type=int,
                        help="duty percentage for 'set'")
    parser.add_argument("--config", default=None,
                        help="path to config.toml (default: next to the "
                             "script, else /etc/ec-fan-curve/config.toml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute and log, but never write to the EC")
    args = parser.parse_args()

    cfg = load_config(args.config or default_config())

    if args.command == "status":
        cmd_status(cfg)
        return 0
    if args.command == "curve":
        cmd_curve(cfg)
        return 0

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    preflight()

    if args.command == "set":
        if args.value is None:
            parser.error("'set' needs a duty percentage")
        cmd_set(cfg, args.value, args.dry_run)
    else:
        cmd_run(cfg, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
