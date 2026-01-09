#!/usr/bin/env python3

import os
import time
import queue
import sqlite3
import threading
import warnings
from contextlib import closing
from collections import defaultdict

# -------------------------------------------------------------------
# GPIO / Raspberry Pi detection + gpiozero backend selection (FIXED)
# -------------------------------------------------------------------

GPIO_AVAILABLE = False
model = ""

# Force gpiozero to use libgpiod-backed factory (matches your working gpiomon/gpioinfo)
# This MUST be set before importing gpiozero devices.
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")


def _detect_rpi_model() -> tuple[bool, str]:
    """Best-effort Raspberry Pi detection.

    Returns (is_rpi, model_string).
    """
    if not (os.path.exists("/proc/device-tree/model") or os.path.exists("/proc/device-tree/compatible")):
        return False, ""
    try:
        with open("/proc/device-tree/model", "r") as f:
            m = f.read().strip("\x00")
        return ("Raspberry Pi" in m), m
    except (IOError, OSError):
        return False, ""


def _force_gpiozero_lgpio_factory() -> tuple[bool, str]:
    """Force gpiozero to use LGPIOFactory (libgpiod character device backend)."""
    try:
        from gpiozero import Device
        from gpiozero.pins.lgpio import LGPIOFactory

        Device.pin_factory = LGPIOFactory()
        return True, f"{Device.pin_factory}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


try:
    # Import lazily so non-RPi installs can still run.
    is_rpi, model = _detect_rpi_model()
    if is_rpi:
        ok, details = _force_gpiozero_lgpio_factory()
        if ok:
            GPIO_AVAILABLE = True
            print(f"Detected Raspberry Pi: {model}")
            print(f"GPIO pin factory forced to LGPIO: {details}")
        else:
            GPIO_AVAILABLE = False
            print(f"ERROR: Failed to initialise gpiozero LGPIO backend: {details}")
            print("Running without GPIO support.")
    else:
        print("Warning: No Raspberry Pi hardware detected. GPIO functionality will be disabled.")
        print("This is expected on non-Raspberry Pi systems.")
except Exception as e:
    GPIO_AVAILABLE = False
    print(f"Warning: GPIO initialisation failed. GPIO will be disabled. {type(e).__name__}: {e}")

# -------------------------------------------------------------------
# Influx / rest of your original code continues unchanged
# -------------------------------------------------------------------

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import WriteOptions

# -------------------------
# Configuration from environment or .env file
# -------------------------
def load_env():
    """Load environment variables from .env file if it exists"""
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())

load_env()


def _debug_enabled() -> bool:
    return os.getenv("DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}

def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return default


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    try:
        return float(v)
    except ValueError:
        return default


def env_pull_up() -> tuple[bool | None, str]:
    """Determine pull configuration for gpiozero DigitalInputDevice.

    Supports:
    - GPIO_PULL=up|down|none  (preferred)
    - GPIO_PULL_UP=true|false (legacy; true=>up, false=>down)

    Returns (pull_up, label).
    """

    cfg = os.getenv("GPIO_PULL", "").strip().lower()
    if cfg in {"up", "pullup", "pull_up"}:
        return True, "up"
    if cfg in {"down", "pulldown", "pull_down"}:
        return False, "down"
    if cfg in {"none", "floating", "float"}:
        return None, "none"

    # Legacy env
    pull_up_bool = env_bool("GPIO_PULL_UP", True)
    return (True, "up") if pull_up_bool else (False, "down")

def dprint(msg: str):
    """General debug output (enabled when DEBUG=1)."""
    if _debug_enabled():
        print(f"[DEBUG] {msg}")


def gprint(msg: str):
    """GPIO-focused debug output (enabled when DEBUG=1)."""
    if _debug_enabled():
        print(f"[GPIO] {msg}")

# -------------------------
# InfluxDB v2 configuration
# -------------------------
INFLUX_URL = os.getenv('INFLUX_HOST', 'http://10.10.100.252:8086')
INFLUX_TOKEN = os.getenv('INFLUX_TOKEN', 'PASTE_YOUR_TOKEN_HERE')
INFLUX_ORG = os.getenv('INFLUX_ORG', 'soothill')

RAW_BUCKET = os.getenv('RAW_BUCKET', 'PowerLogger_raw')
DS_BUCKET  = os.getenv('B1M_BUCKET', 'PowerLogger_1m')

RAW_MEASUREMENT = os.getenv('MEASUREMENT', 'PowerPulse')
DS_MEASUREMENT  = f"{RAW_MEASUREMENT}_1m"

# -------------------------
# Async write tuning
# -------------------------
WRITE_BATCH_SIZE = 5000
WRITE_FLUSH_INTERVAL_MS = 500
WRITE_JITTER_INTERVAL_MS = 200
WRITE_RETRY_INTERVAL_MS = 2500
WRITE_MAX_RETRIES = 8
WRITE_MAX_RETRY_DELAY_MS = 30000
WRITE_EXPONENTIAL_BASE = 2

# -------------------------
# Local durable buffer (SQLite)
# -------------------------
QUEUE_DIR = os.getenv('QUEUE_DIR', './queue')
QUEUE_DB = os.path.join(QUEUE_DIR, "write_queue.sqlite")

REPLAY_EVERY_SECONDS = 10
REPLAY_BATCH_LINES = 5000

# -------------------------
# Downsample (1m) windowing
# -------------------------
DS_WINDOW_SECONDS = 60
DS_FLUSH_JITTER_SECONDS = 2

# -------------------------
# In-memory event queue (fast path)
# -------------------------
EVENT_Q_MAX = 200000
event_q = queue.Queue(maxsize=EVENT_Q_MAX)

stop_evt = threading.Event()
db_lock = threading.Lock()
ds_lock = threading.Lock()

ds_counts = defaultdict(lambda: defaultdict(int))


def ensure_db():
    os.makedirs(QUEUE_DIR, exist_ok=True)
    with db_lock, closing(sqlite3.connect(QUEUE_DB, timeout=30)) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lp_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket TEXT NOT NULL,
                lp TEXT NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lp_queue_id ON lp_queue(id);")
        conn.commit()


def db_enqueue_lines(bucket: str, lines):
    if not lines:
        return
    with db_lock, closing(sqlite3.connect(QUEUE_DB, timeout=30)) as conn:
        conn.executemany(
            "INSERT INTO lp_queue (bucket, lp) VALUES (?, ?);",
            [(bucket, lp) for lp in lines]
        )
        conn.commit()


def db_dequeue_lines(limit: int):
    with db_lock, closing(sqlite3.connect(QUEUE_DB, timeout=30)) as conn:
        rows = conn.execute(
            "SELECT id, bucket, lp FROM lp_queue ORDER BY id ASC LIMIT ?;",
            (limit,)
        ).fetchall()
    return rows


def db_delete_ids(ids):
    if not ids:
        return
    with db_lock, closing(sqlite3.connect(QUEUE_DB, timeout=30)) as conn:
        conn.execute(
            f"DELETE FROM lp_queue WHERE id IN ({','.join('?' for _ in ids)});",
            ids
        )
        conn.commit()


def ns_now():
    return time.time_ns()


def lp_escape_tag(v: str) -> str:
    return v.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def make_raw_lp(pulse_type: str, ts_ns: int) -> str:
    return f"{RAW_MEASUREMENT},PulseType={lp_escape_tag(pulse_type)} Pulse=1i {ts_ns}"


def make_ds_lp(pulse_type: str, window_start_s: int, count: int) -> str:
    ts_ns = int(window_start_s) * 1_000_000_000
    return f"{DS_MEASUREMENT},PulseType={lp_escape_tag(pulse_type)},window=1m PulseCount={int(count)}i {ts_ns}"


# -------------------------
# Influx async write setup
# -------------------------
client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
dprint(f"Influx config: url={INFLUX_URL} org={INFLUX_ORG} raw_bucket={RAW_BUCKET} ds_bucket={DS_BUCKET}")

def success_cb(conf, data):
    pass

def error_cb(conf, data, exception):
    try:
        payload = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception:
        payload = str(data)

    lines = [ln for ln in payload.splitlines() if ln.strip()]
    if not lines:
        return

    bucket = conf.get("bucket") if isinstance(conf, dict) else None
    if not bucket:
        bucket = RAW_BUCKET

    db_enqueue_lines(bucket, lines)
    print(f"Influx async write failed; buffered {len(lines)} line(s) to SQLite. Err={exception}")
    dprint(f"Buffered to SQLite bucket={bucket} lines={len(lines)}")

def retry_cb(conf, data, exception):
    pass

write_api = client.write_api(
    write_options=WriteOptions(
        batch_size=WRITE_BATCH_SIZE,
        flush_interval=WRITE_FLUSH_INTERVAL_MS,
        jitter_interval=WRITE_JITTER_INTERVAL_MS,
        retry_interval=WRITE_RETRY_INTERVAL_MS,
        max_retries=WRITE_MAX_RETRIES,
        max_retry_delay=WRITE_MAX_RETRY_DELAY_MS,
        exponential_base=WRITE_EXPONENTIAL_BASE,
    ),
    success_callback=success_cb,
    error_callback=error_cb,
    retry_callback=retry_cb,
)


def write_async(bucket: str, lines):
    if not lines:
        return
    payload = "\n".join(lines)
    dprint(f"Submitting to Influx async writer: bucket={bucket} points={len(lines)}")
    write_api.write(bucket=bucket, org=INFLUX_ORG, record=payload)


# -------------------------
# Downsample accumulator
# -------------------------
def record_downsample(pulse_type: str, ts_ns: int):
    minute_epoch = (ts_ns // 1_000_000_000) // DS_WINDOW_SECONDS * DS_WINDOW_SECONDS
    with ds_lock:
        ds_counts[minute_epoch][pulse_type] += 1


def flush_completed_downsample_windows(now_s: int):
    cutoff = now_s - DS_FLUSH_JITTER_SECONDS
    complete_before = (cutoff // DS_WINDOW_SECONDS) * DS_WINDOW_SECONDS

    to_flush = []
    with ds_lock:
        for minute_epoch in sorted(ds_counts.keys()):
            if minute_epoch < complete_before:
                to_flush.append((minute_epoch, dict(ds_counts[minute_epoch])))
        for minute_epoch, _ in to_flush:
            ds_counts.pop(minute_epoch, None)

    if not to_flush:
        return

    lines = []
    for minute_epoch, counts in to_flush:
        for pulse_type, count in counts.items():
            lines.append(make_ds_lp(pulse_type, minute_epoch, count))

    write_async(DS_BUCKET, lines)


# -------------------------
# Worker threads
# -------------------------
def event_worker():
    raw_lines_batch = []
    last_ds_flush_s = 0

    while not stop_evt.is_set():
        try:
            pulse_type, ts_ns = event_q.get(timeout=0.2)
        except queue.Empty:
            pulse_type = None

        now_s = int(time.time())

        if pulse_type is not None:
            raw_lines_batch.append(make_raw_lp(pulse_type, ts_ns))
            record_downsample(pulse_type, ts_ns)

            if len(raw_lines_batch) >= 2000:
                write_async(RAW_BUCKET, raw_lines_batch)
                raw_lines_batch.clear()

        if now_s - last_ds_flush_s >= 5:
            last_ds_flush_s = now_s
            flush_completed_downsample_windows(now_s)

        if pulse_type is None and raw_lines_batch:
            write_async(RAW_BUCKET, raw_lines_batch)
            raw_lines_batch.clear()

    while True:
        try:
            pulse_type, ts_ns = event_q.get_nowait()
        except queue.Empty:
            break
        raw_lines_batch.append(make_raw_lp(pulse_type, ts_ns))
        record_downsample(pulse_type, ts_ns)

        if len(raw_lines_batch) >= 2000:
            write_async(RAW_BUCKET, raw_lines_batch)
            raw_lines_batch.clear()

    if raw_lines_batch:
        write_async(RAW_BUCKET, raw_lines_batch)

    flush_completed_downsample_windows(int(time.time()))


def replay_worker():
    while not stop_evt.is_set():
        time.sleep(REPLAY_EVERY_SECONDS)

        rows = db_dequeue_lines(REPLAY_BATCH_LINES)
        if not rows:
            continue

        by_bucket = {}
        ids = []
        for row_id, bucket, lp in rows:
            ids.append(row_id)
            by_bucket.setdefault(bucket, []).append(lp)

        for bucket, lines in by_bucket.items():
            write_async(bucket, lines)

        db_delete_ids(ids)
        print(f"Replayed {len(ids)} buffered line(s) from SQLite.")


# -------------------------
# GPIO callbacks (FAST)
# -------------------------
def enqueue_event(pulse_type: str):
    ts_ns = ns_now()
    try:
        event_q.put_nowait((pulse_type, ts_ns))
        dprint(f"Enqueued pulse: type={pulse_type} qsize={event_q.qsize()}")
    except queue.Full:
        db_enqueue_lines(RAW_BUCKET, [make_raw_lp(pulse_type, ts_ns)])
        record_downsample(pulse_type, ts_ns)
        print("Event queue full; spilled raw pulse to SQLite.")


def _make_gpio_edge_handler(
    *,
    pulse_type: str,
    edge: str,
    device,
    enqueue_on_edges: set[str],
    counters: dict,
):
    """Create a gpiozero callback for a specific edge.

    We always log the edge (when DEBUG=1), but only enqueue pulses for the edges
    selected by GPIO_ENQUEUE_EDGES.
    """

    def _handler():
        counters[f"{pulse_type}_{edge}"] += 1
        ts_ns = ns_now()
        try:
            val = getattr(device, "value", None)
            active = getattr(device, "is_active", None)
        except Exception:
            val = None
            active = None

        gprint(
            f"edge={edge} type={pulse_type} ts_ns={ts_ns} value={val} is_active={active} "
            f"counts(act={counters.get(pulse_type + '_activated', 0)} deact={counters.get(pulse_type + '_deactivated', 0)})"
        )

        if edge in enqueue_on_edges:
            enqueue_event(pulse_type)

    return _handler


def _start_gpio_poll_monitor(devices: dict, interval_s: float = 0.05):
    """Optional polling monitor to confirm pin state changes even if callbacks never fire.

    Enable with GPIO_POLL_DEBUG=1.
    """

    last = {}
    for name, dev in devices.items():
        try:
            last[name] = (dev.value, dev.is_active)
        except Exception:
            last[name] = (None, None)

    def _run():
        gprint(f"GPIO poll monitor started (interval={interval_s}s)")
        while not stop_evt.is_set():
            for name, dev in devices.items():
                try:
                    cur = (dev.value, dev.is_active)
                except Exception:
                    cur = (None, None)
                if cur != last.get(name):
                    gprint(f"POLL change {name}: value {last.get(name)} -> {cur}")
                    last[name] = cur
            time.sleep(interval_s)

    t = threading.Thread(target=_run, name="gpio_poll_monitor", daemon=True)
    t.start()
    return t


def main():
    global GPIO_AVAILABLE
    ensure_db()

    # Track observed edges/pulses in-process so we can debug “nothing logged”.
    gpio_counters = defaultdict(int)
    # (reserved) if we later want per-pulse-type counts independent of edges

    t1 = threading.Thread(target=event_worker, name="event_worker", daemon=True)
    t2 = threading.Thread(target=replay_worker, name="replay_worker", daemon=True)
    t1.start()
    t2.start()

    if _debug_enabled():
        try:
            ok = client.ping()
            dprint(f"Influx ping: {ok}")
        except Exception as e:
            dprint(f"Influx ping failed: {type(e).__name__}: {e}")

    # -------------------------
    # GPIO setup using gpiozero + LGPIO backend (FIXED)
    # -------------------------
    import_in = export_in = generate_in = None
    gpio_devices = {}
    gpio_pull_up = None
    gpio_bounce_time = None
    gpio_warned_stuck_low = False

    if GPIO_AVAILABLE:
        print("Setting up GPIO pins using gpiozero (LGPIO backend)...")
        try:
            # IMPORTANT:
            # Your gpiomon test shows RISING edges. So use DigitalInputDevice and when_activated.
            from gpiozero import DigitalInputDevice

            gpio_pull_up, gpio_pull_label = env_pull_up()

            # Optional override: define which electrical state counts as "active".
            # Useful when troubleshooting (e.g. meter output is active-low).
            # If unset, gpiozero will choose a sensible default based on pull_up.
            active_state_cfg = os.getenv("GPIO_ACTIVE_STATE", "").strip().lower()
            if active_state_cfg in {"low", "0", "false"}:
                gpio_active_state = False
            elif active_state_cfg in {"high", "1", "true"}:
                gpio_active_state = True
            else:
                gpio_active_state = None

            # gpiozero rejects specifying active_state when a pull-up/down is configured.
            # active_state is only meaningful for floating inputs.
            if gpio_pull_up is not None and gpio_active_state is not None:
                gprint(
                    "WARNING: GPIO_ACTIVE_STATE is set but GPIO pull is not 'none'. "
                    "Ignoring GPIO_ACTIVE_STATE to avoid PinInvalidState."
                )
                gpio_active_state = None

            gpio_bounce = env_float("GPIO_BOUNCE_TIME", 0.1)
            gpio_bounce_time = None if gpio_bounce <= 0 else gpio_bounce

            # Which edges should be ENQUEUED as pulses?
            # - rising  : only when_activated
            # - falling : only when_deactivated
            # - both    : enqueue on both edges (only for debugging; may double-count)
            enqueue_edges_cfg = os.getenv("GPIO_ENQUEUE_EDGES", "rising").strip().lower()
            if enqueue_edges_cfg not in {"rising", "falling", "both"}:
                enqueue_edges_cfg = "rising"
            enqueue_on_edges = {"activated"} if enqueue_edges_cfg == "rising" else {"deactivated"} if enqueue_edges_cfg == "falling" else {"activated", "deactivated"}

            # Which edges should we LOG callbacks for?
            # Default: both, so we can see what the pin is actually doing.
            log_edges_cfg = os.getenv("GPIO_LOG_EDGES", "both").strip().lower()
            if log_edges_cfg not in {"rising", "falling", "both"}:
                log_edges_cfg = "both"
            log_edges = {"activated"} if log_edges_cfg == "rising" else {"deactivated"} if log_edges_cfg == "falling" else {"activated", "deactivated"}

            import_pin = int(os.getenv("GPIO_IMPORT_PIN", "20"))
            export_pin = int(os.getenv("GPIO_EXPORT_PIN", "26"))
            generate_pin = int(os.getenv("GPIO_GENERATE_PIN", "21"))

            # If your sensor provides OPEN-COLLECTOR / active-low, set pull_up=True.
            # You requested these entries should be pull_up=true.
            import_in = DigitalInputDevice(
                import_pin,
                pull_up=gpio_pull_up,
                active_state=gpio_active_state,
                bounce_time=gpio_bounce_time,
            )
            export_in = DigitalInputDevice(
                export_pin,
                pull_up=gpio_pull_up,
                active_state=gpio_active_state,
                bounce_time=gpio_bounce_time,
            )
            generate_in = DigitalInputDevice(
                generate_pin,
                pull_up=gpio_pull_up,
                active_state=gpio_active_state,
                bounce_time=gpio_bounce_time,
            )

            gprint(
                "GPIO config: "
                f"pull={gpio_pull_label} pull_up={gpio_pull_up} active_state={gpio_active_state} bounce_time={gpio_bounce_time} "
                f"enqueue_edges={enqueue_edges_cfg} log_edges={log_edges_cfg} "
                f"pins: Import={import_pin} Export={export_pin} Generate={generate_pin}"
            )

            # Attach callbacks. We *always* count/log selected edges; we only enqueue
            # for GPIO_ENQUEUE_EDGES.
            if "activated" in log_edges:
                import_in.when_activated = _make_gpio_edge_handler(
                    pulse_type="Import",
                    edge="activated",
                    device=import_in,
                    enqueue_on_edges=enqueue_on_edges,
                    counters=gpio_counters,
                )
                export_in.when_activated = _make_gpio_edge_handler(
                    pulse_type="Export",
                    edge="activated",
                    device=export_in,
                    enqueue_on_edges=enqueue_on_edges,
                    counters=gpio_counters,
                )
                generate_in.when_activated = _make_gpio_edge_handler(
                    pulse_type="Generate",
                    edge="activated",
                    device=generate_in,
                    enqueue_on_edges=enqueue_on_edges,
                    counters=gpio_counters,
                )

            if "deactivated" in log_edges:
                import_in.when_deactivated = _make_gpio_edge_handler(
                    pulse_type="Import",
                    edge="deactivated",
                    device=import_in,
                    enqueue_on_edges=enqueue_on_edges,
                    counters=gpio_counters,
                )
                export_in.when_deactivated = _make_gpio_edge_handler(
                    pulse_type="Export",
                    edge="deactivated",
                    device=export_in,
                    enqueue_on_edges=enqueue_on_edges,
                    counters=gpio_counters,
                )
                generate_in.when_deactivated = _make_gpio_edge_handler(
                    pulse_type="Generate",
                    edge="deactivated",
                    device=generate_in,
                    enqueue_on_edges=enqueue_on_edges,
                    counters=gpio_counters,
                )

            # Print initial states (super helpful for pull_up diagnosis)
            gprint(
                "Initial states: "
                f"Import(value={import_in.value}, active={import_in.is_active}) "
                f"Export(value={export_in.value}, active={export_in.is_active}) "
                f"Generate(value={generate_in.value}, active={generate_in.is_active})"
            )

            gpio_devices = {"Import": import_in, "Export": export_in, "Generate": generate_in}

            # Helpful warning: if we expect a pull-up but the line is already low at startup,
            # it often indicates wiring/polarity issues or that the meter output is actively
            # pulling the line low all the time.
            if gpio_pull_up is True:
                try:
                    if any(dev.value == 0 for dev in gpio_devices.values()):
                        gprint(
                            "WARNING: One or more GPIO inputs read LOW at startup while GPIO_PULL_UP=true. "
                            "If this stays low, try GPIO_PULL_UP=false, check wiring (BCM vs physical pin), "
                            "or temporarily disconnect the meter output to see if the pin floats HIGH."
                        )
                        gpio_warned_stuck_low = True
                except Exception:
                    pass

            # Optional polling monitor (helps if edge callbacks never fire)
            if env_bool("GPIO_POLL_DEBUG", False):
                _start_gpio_poll_monitor(
                    gpio_devices,
                    interval_s=env_float("GPIO_POLL_INTERVAL", 0.05),
                )

            print(
                "GPIO pins configured: "
                f"Import=GPIO{import_pin}, Export=GPIO{export_pin}, Generate=GPIO{generate_pin}"
            )
        except (RuntimeError, PermissionError, Exception) as e:
            GPIO_AVAILABLE = False
            print(f"ERROR: Failed to initialize GPIO ({type(e).__name__}: {e})")
            print("Running without GPIO support.")
            print("\nTroubleshooting GPIO issues:")
            print("  1. Ensure /dev/gpiochip* exists: ls -l /dev/gpiochip*")
            print("  2. Ensure user is in 'gpio' group: groups")
            print("  3. Confirm events with gpiomon: gpiomon -e rising -n 10 GPIO20")
            print("  4. Force backend: GPIOZERO_PIN_FACTORY=lgpio python3 <script>")
            print("\nNote: The application will continue running without GPIO support.")
    else:
        print("GPIO not available - running without GPIO support")
        print("This is expected on non-Raspberry Pi systems")

    try:
        while True:
            time.sleep(5)
            # Snapshot counters (and reset per-interval) to make it obvious whether
            # GPIO is producing edges.
            if GPIO_AVAILABLE:
                # Derive pulse counts from the enqueue path by watching queue size deltas.
                # Additionally, maintain edge counters from callbacks.
                if _debug_enabled():
                    gprint(
                        "Edge counters (since start): "
                        + ", ".join(
                            f"{k}={v}" for k, v in sorted(gpio_counters.items())
                        )
                    )

                    if gpio_devices:
                        # Periodic state snapshot so we can see if pins are stuck or toggling.
                        state = " ".join(
                            f"{name}(value={dev.value},active={dev.is_active})"
                            for name, dev in gpio_devices.items()
                        )
                        gprint(
                            f"State snapshot: {state} pull_up={gpio_pull_up} bounce_time={gpio_bounce_time}"
                        )

                        if not gpio_warned_stuck_low and gpio_pull_up is True:
                            try:
                                if all(dev.value == 0 for dev in gpio_devices.values()):
                                    gprint(
                                        "WARNING: All GPIO inputs are still LOW with GPIO_PULL_UP=true. "
                                        "This strongly suggests the lines are being held low (wiring/polarity) "
                                        "or you’re on different pins than expected."
                                    )
                                    gpio_warned_stuck_low = True
                            except Exception:
                                pass

            print(f"Running. In-memory queue size: {event_q.qsize()}")
            if _debug_enabled():
                try:
                    rows = db_dequeue_lines(1)
                    dprint(f"SQLite queue has_rows={bool(rows)}")
                except Exception as e:
                    dprint(f"SQLite check failed: {type(e).__name__}: {e}")
    except KeyboardInterrupt:
        print("Exiting…")
    finally:
        stop_evt.set()

        # Close gpiozero devices if they were created
        for dev in (import_in, export_in, generate_in):
            try:
                if dev is not None:
                    dev.close()
            except Exception:
                pass

        time.sleep(1)

        try:
            write_api.flush()
        except Exception:
            pass

        client.close()


if __name__ == "__main__":
    main()

