# ec-fan-curve

# matebook-fan-curve

Fan curve control for the Huawei MateBook X Pro 2021 (`MACHD-WXX9`, i7-1165G7,
BIOS 1.19) on Linux, by writing the EC's fan duty registers directly.

**Machine:** HUAWEI MACHD-WXX9 / M1040
**Problem:** the fan sits at ~5000 RPM at 50 °C idle and the firmware exposes no
way to slow it down.

## Supported hardware

| Model | Status |
|---|---|
| `MACHD-WXX9` — MateBook X Pro 2021, i7-1165G7, BIOS 1.19 | **verified** |
| other Huawei MateBooks | **untested** |

The EC register addresses are model-specific. `install.sh` refuses to run on an
unverified model unless you pass `--force`, and `fanctl` refuses to write at all
if `0x2A04` does not read a plausible 0–100 value.

If you have a different MateBook and want to find out whether it is compatible,
do it read-only first:

```bash
sudo modprobe acpi_call
sudo chmod 666 /proc/acpi/call
./fanctl.py status
```

Check that the reported duty roughly matches the observed RPM (RPM is about
`duty * 100` on the verified model). If it does not, the addresses are wrong for
your machine — stop there and open an issue with your `product_name`,
`board_name`, and BIOS version.

## Why the obvious tools don't work

This BIOS has no usable fan control surface:

| Path | Status |
|---|---|
| `_FSL` / `_FST` / `_FPS` on `TFN1`/`TFN2` | **absent entirely** |
| `acpi_fan` cooling devices (`PNP0C0B:00-04`) | present but inert |
| `SFND` / `SFNM` (what `fanctld` drives) | `AE_NOT_FOUND` |
| `SFRS` / `SMLS` (EC `0x46` / `0x48`) | binary mode toggles, don't reduce speed |
| `thermald` | claims the zone, does nothing |
| `nbfc-linux` | 0 Huawei configs out of 321 |

`GFNS`/`GTMP` (read RPM and temperature) work, but every write path is either
missing or a two-state switch. That is why generic fan tools silently fail here.

## What actually works

The EC keeps fan duty as a plain percentage in EC RAM:

| Address | Meaning |
|---|---|
| `0x2A00` / `0x2A01` | fan 0 tachometer, little-endian RPM |
| `0x2A02` / `0x2A03` | fan 1 tachometer, little-endian RPM |
| `0x2A04` | **fan 0 duty, percent** |
| `0x2A05` | **fan 1 duty, percent** |

RPM is roughly `duty * 100`. The EC recomputes those two registers about once
per second, so anything writing faster than that holds a lower duty. `fanctl`
rewrites every 500 ms.

Measured on this machine:

| Duty | RPM | Package |
|---|---|---|
| 50% (EC's own choice at ~50 °C) | 5013 | 52 °C |
| 40% | 4014 | 51 °C |
| 35% | 3571 | 51 °C |
| 30% | 3053 | 50 °C |

Dropping idle duty from 50% to 30% takes the fan from ~5000 to ~3050 RPM with no
meaningful temperature cost.

## Install

```bash
sudo apt install acpi-call-dkms linux-headers-$(uname -r)
sudo ./install.sh
```

That installs `/usr/local/bin/fanctl`, the config at
`/etc/ec-fan-curve/config.toml`, and enables `ec-fan-curve.service`.

## Where the curve lives

**`/etc/ec-fan-curve/config.toml`** — edit it, then
`sudo systemctl restart ec-fan-curve`.

```toml
[fan]
interval_ms   = 500
emergency_c   = 85
min_duty      = 25
hysteresis_c  = 1.5
max_step_up   = 10
max_step_down = 2

curve = [
  [0,  30],   # idle floor
  [50, 30],
  [55, 35],
  [60, 45],
  [65, 60],
  [70, 80],
  [75, 100],
]
```

Each entry is `[temperature_C, duty_percent]`. It is a step function: the
highest entry whose temperature is `<=` the current package temperature wins.
`min_duty` is a hard floor regardless of what the curve says.

`hysteresis_c` is a deadband around every boundary. Without it a temperature
sitting on a threshold flips duty every tick — measured at 84 changes/min at
55.0 °C, which is audible and pointless bearing wear. A change now needs the
temperature to move this far past the boundary.

`max_step_up` / `max_step_down` cap how far duty may move in one tick, in
percent. They are asymmetric on purpose: up fast for thermal safety, down
slowly so the fan does not audibly drop every time the load blips. At the
default 500 ms interval, `10` is 20 %/s up and `2` is 4 %/s down.

## Usage

```bash
fanctl status          # temp, RPM, the EC's own duty, and what the curve wants
fanctl curve           # print the active curve
fanctl run             # run the curve in the foreground
fanctl set 30          # hold a fixed duty until Ctrl-C
fanctl run --dry-run   # log decisions without touching the EC
fanctl --config ./config.toml status
```

`fanctl status` is read-only. `--dry-run` never writes.

## Safety

The EC's own thermal management is suspended while `fanctl` is writing, so the
curve is the only thing keeping the machine cool. Three things bound the risk:

- **`emergency_c` (85 °C)** — above this, `fanctl` stops writing and the EC
  takes over.
- **`min_duty` (25%)** — below roughly 25% the fan stalls or hunts.
- **Fail-safe by construction** — the EC recomputes duty within about a second
  of `fanctl` exiting, crashing, or being killed. There is no state to restore
  and no way to leave the fan locked low.

The curve ramps hard above 65 °C on purpose. Flattening it to keep things quiet
under sustained load is how you get a throttling laptop.

## Caveats

- This is a workaround around firmware that does not want to be controlled. It
  is not a vendor fan curve.
- `0x2A04`/`0x2A05` are undocumented. They were found by reading the DSDT
  (`GFNS` reads `0x2A00`-`0x2A03`) and confirmed by correlation: duty 50 → 5013
  RPM, duty 48 → 4860 RPM.
- Verified only on `MACHD-WXX9` / BIOS 1.19. Other MateBooks may use different
  addresses. Run `fanctl status` first and check that the reported duty matches
  the observed RPM before trusting it.
- `0x2A06` and `0x2A07` read back with a consistent `-3` offset from what was
  written, so they are derived values, not plain storage. Don't use them.

## Related

- [`matebook-fanctld`](https://github.com/duny31030/matebook-fanctld) — drives
  `SFND` on other MateBook models. Does not work here: this BIOS lacks that
  method entirely, which is what sent this project down the EC-RAM path.
- [`nbfc-linux`](https://github.com/nbfc-linux/nbfc-linux) — generic EC fan
  control for Linux. Has no Huawei configs.
- [`acpi_call`](https://github.com/nix-community/acpi_call) — the kernel module
  that exposes `/proc/acpi/call`, the only way to reach the EC from userspace.

## How this was found

Short version, in case it helps someone on a different model:

1. `_FSL`/`_FST`/`_FPS` are absent from `TFN1`/`TFN2`, so the standard ACPI fan
   path is dead. `acpi_fan` cooling devices exist but do nothing.
2. `SFND`/`SFNM` (the methods `fanctld` uses) return `AE_NOT_FOUND`. The only
   `ECCC` fan commands present are `0x46` and `0x48`, both binary toggles.
3. Dumping the DSDT and reading `GFNS` showed the tachometers live at EC
   `0x2A00`-`0x2A03`. The duty registers turned out to be the next two bytes.
4. Confirmed by correlation: duty 50 → 5013 RPM, duty 48 → 4860 RPM.
5. The EC recomputes duty about once a second, so writing faster than that holds
   a lower value. That is the whole trick.
