#!/usr/bin/env python3

"""GPIO watch / diagnostic tool for Raspberry Pi.

Goal: quickly determine which BCM pins are changing state (and on which edge),
and whether your wiring expects an internal pull-up or pull-down.

This uses gpiozero with the lgpio (libgpiod) backend.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from collections import defaultdict


# Must be set before importing gpiozero devices.
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Watch BCM GPIO pins and report state changes")
    p.add_argument(
        "--pins",
        default="2-27",
        help="Pins to watch (BCM numbers). Examples: '20,21,26' or '2-27'. Default: 2-27",
    )
    p.add_argument(
        "--pull",
        choices=["up", "down", "none"],
        default="up",
        help="Internal pull resistor configuration. Default: up",
    )
    p.add_argument(
        "--active",
        choices=["high", "low"],
        default="high",
        help=(
            "Defines which electrical level counts as 'active'. Useful when --pull none (floating). "
            "Default: high"
        ),
    )
    p.add_argument(
        "--bounce",
        type=float,
        default=0.0,
        help="Debounce time seconds (gpiozero bounce_time). 0 disables. Default: 0",
    )
    p.add_argument(
        "--poll",
        type=float,
        default=0.05,
        help="Polling interval seconds for additional state-change detection. Default: 0.05",
    )
    return p.parse_args()


def parse_pins(spec: str) -> list[int]:
    spec = spec.strip()
    pins: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a)
            end = int(b)
            step = 1 if end >= start else -1
            pins.extend(list(range(start, end + step, step)))
        else:
            pins.append(int(part))
    # de-dupe while preserving order
    seen = set()
    out = []
    for pin in pins:
        if pin not in seen:
            out.append(pin)
            seen.add(pin)
    return out


def ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    args = parse_args()
    pins = parse_pins(args.pins)
    if not pins:
        print("No pins to watch")
        return 2

    pull_up = True if args.pull == "up" else False if args.pull == "down" else None
    active_state = True if args.active == "high" else False
    bounce_time = None if args.bounce <= 0 else args.bounce

    print("GPIO watch starting")
    print(f"  pin factory: {os.getenv('GPIOZERO_PIN_FACTORY')}")
    print(f"  pins: {pins}")
    print(f"  pull: {args.pull} (pull_up={pull_up})")
    print(f"  active: {args.active} (active_state={active_state})")
    print(f"  bounce_time: {bounce_time}")
    print(f"  poll_interval: {args.poll}s")
    print("  NOTE: pins are BCM numbers (gpiozero default), NOT physical header pin numbers")
    print("")

    from gpiozero import DigitalInputDevice

    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    devices = {}
    counters = defaultdict(int)

    def attach(pin: int):
        # NOTE: When pull_up=None (floating), gpiozero requires active_state to be defined.
        dev = DigitalInputDevice(
            pin,
            pull_up=pull_up,
            active_state=active_state,
            bounce_time=bounce_time,
        )
        devices[pin] = dev

        def on_activated():
            counters[(pin, "activated")] += 1
            print(f"{ts()}  BCM{pin:02d}  activated   value={dev.value} active={dev.is_active}  count={counters[(pin, 'activated')]}")

        def on_deactivated():
            counters[(pin, "deactivated")] += 1
            print(f"{ts()}  BCM{pin:02d}  deactivated value={dev.value} active={dev.is_active}  count={counters[(pin, 'deactivated')]}")

        dev.when_activated = on_activated
        dev.when_deactivated = on_deactivated

    # Create devices
    for pin in pins:
        try:
            attach(pin)
        except Exception as e:
            print(f"{ts()}  BCM{pin:02d}  ERROR: {type(e).__name__}: {e}")

    # Print initial states
    print("Initial states:")
    for pin in pins:
        dev = devices.get(pin)
        if not dev:
            continue
        try:
            print(f"{ts()}  BCM{pin:02d}  value={dev.value} active={dev.is_active}")
        except Exception as e:
            print(f"{ts()}  BCM{pin:02d}  ERROR reading: {type(e).__name__}: {e}")
    print("")

    # Polling monitor (catches changes even if callbacks somehow don't fire)
    last = {}
    for pin, dev in devices.items():
        try:
            last[pin] = (dev.value, dev.is_active)
        except Exception:
            last[pin] = (None, None)

    print("Watching for changes... (Ctrl+C to stop)")
    while not stop:
        for pin, dev in devices.items():
            try:
                cur = (dev.value, dev.is_active)
            except Exception:
                cur = (None, None)
            if cur != last.get(pin):
                last_val = last.get(pin)
                last[pin] = cur
                counters[(pin, "poll_change")] += 1
                print(
                    f"{ts()}  BCM{pin:02d}  POLL change value={last_val} -> {cur}  poll_count={counters[(pin, 'poll_change')]}"
                )
        time.sleep(args.poll)

    print("\nStopping; closing devices...")
    for dev in devices.values():
        try:
            dev.close()
        except Exception:
            pass
    print("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
