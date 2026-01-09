#!/usr/bin/env python3

import os
import time
import queue
import sqlite3
import threading
from contextlib import closing
from collections import defaultdict

import RPi.GPIO as GPIO
from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import WriteOptions

# -------------------------
# InfluxDB v2 configuration
# -------------------------
INFLUX_URL = "http://10.10.100.252:8086"
INFLUX_TOKEN = "PASTE_YOUR_TOKEN_HERE"
INFLUX_ORG = "soothill"

RAW_BUCKET = "PowerLogger_raw"
DS_BUCKET  = "PowerLogger_1m"

RAW_MEASUREMENT = "PowerPulse"
DS_MEASUREMENT  = "PowerPulse_1m"

# -------------------------
# Async write tuning
# -------------------------
# These settings matter for high pulse rates.
WRITE_BATCH_SIZE = 5000          # max points per batch
WRITE_FLUSH_INTERVAL_MS = 500    # flush at least twice/sec
WRITE_JITTER_INTERVAL_MS = 200   # spread flushes (helps thundering herd)
WRITE_RETRY_INTERVAL_MS = 2500
WRITE_MAX_RETRIES = 8
WRITE_MAX_RETRY_DELAY_MS = 30000
WRITE_EXPONENTIAL_BASE = 2

# -------------------------
# Local durable buffer (SQLite)
# -------------------------
QUEUE_DIR = "/var/lib/powerlogger"
QUEUE_DB = os.path.join(QUEUE_DIR, "write_queue.sqlite")

# Replay tuning
REPLAY_EVERY_SECONDS = 10
REPLAY_BATCH_LINES = 5000

# -------------------------
# Downsample (1m) windowing
# -------------------------
DS_WINDOW_SECONDS = 60
DS_FLUSH_JITTER_SECONDS = 2  # wait a couple seconds past the boundary

# -------------------------
# In-memory event queue (fast path)
# -------------------------
EVENT_Q_MAX = 200000  # if you exceed this, we start spilling to SQLite
event_q = queue.Queue(maxsize=EVENT_Q_MAX)

stop_evt = threading.Event()
db_lock = threading.Lock()
ds_lock = threading.Lock()

# ds_counts[minute_epoch][PulseType] = count
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
    # Store line protocol strings durably
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
    # Minimal tag escaping for line protocol (spaces, commas, equals)
    return v.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def make_raw_lp(pulse_type: str, ts_ns: int) -> str:
    # measurement,tagset fieldset timestamp
    # field Pulse is integer => add trailing i
    return f"{RAW_MEASUREMENT},PulseType={lp_escape_tag(pulse_type)} Pulse=1i {ts_ns}"


def make_ds_lp(pulse_type: str, window_start_s: int, count: int) -> str:
    ts_ns = int(window_start_s) * 1_000_000_000
    # add tags window=1m so queries can filter easily
    return f"{DS_MEASUREMENT},PulseType={lp_escape_tag(pulse_type)},window=1m PulseCount={int(count)}i {ts_ns}"


# -------------------------
# Influx async write setup
# -------------------------
client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)

# We’ll use line protocol strings for speed.
# Async writer will batch + retry automatically.
def success_cb(conf, data):
    # data is the payload successfully written (line protocol string or list)
    pass

def error_cb(conf, data, exception):
    # After retries are exhausted, persist failed payload durably.
    # data may be a str (multi-line line protocol) or bytes.
    try:
        payload = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception:
        payload = str(data)

    # Split into lines; each line is one point
    lines = [ln for ln in payload.splitlines() if ln.strip()]
    if not lines:
        return

    bucket = conf.get("bucket") if isinstance(conf, dict) else None
    if not bucket:
        # If we can't detect, default to raw bucket (rare)
        bucket = RAW_BUCKET

    db_enqueue_lines(bucket, lines)
    print(f"Influx async write failed; buffered {len(lines)} line(s) to SQLite. Err={exception}")

def retry_cb(conf, data, exception):
    # Optional: noisy at high rates; keep silent or minimal
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
    """
    Non-blocking submit. Async writer handles batching internally.
    If it ultimately fails, error_cb persists payload to SQLite.
    """
    if not lines:
        return
    # write_api.write accepts a string with newline-separated LP, or list of strings
    payload = "\n".join(lines)
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

    # Async submit to downsample bucket
    write_async(DS_BUCKET, lines)


# -------------------------
# Worker threads
# -------------------------
def event_worker():
    """
    Pull events from in-memory queue, batch them, submit to async writer.
    Also maintains downsample counters and flushes completed windows.
    """
    raw_lines_batch = []
    last_ds_flush_s = 0

    while not stop_evt.is_set():
        try:
            # Small wait to allow batching, but keep latency low
            pulse_type, ts_ns = event_q.get(timeout=0.2)
        except queue.Empty:
            pulse_type = None

        now_s = int(time.time())

        if pulse_type is not None:
            raw_lines_batch.append(make_raw_lp(pulse_type, ts_ns))
            record_downsample(pulse_type, ts_ns)

            # If batch big enough, submit immediately
            if len(raw_lines_batch) >= 2000:
                write_async(RAW_BUCKET, raw_lines_batch)
                raw_lines_batch.clear()

        # Periodic raw flush (even if not full)
        if raw_lines_batch and (len(raw_lines_batch) >= 1) and (now_s % 1 == 0):
            # don’t flush every loop; just opportunistically when idle-ish
            # keep it cheap: flush if we haven't added in a moment (handled by timeout too)
            pass

        # Downsample flush every few seconds (only completed windows get emitted)
        if now_s - last_ds_flush_s >= 5:
            last_ds_flush_s = now_s
            flush_completed_downsample_windows(now_s)

        # If we were idle (queue empty), flush any accumulated raw lines
        if pulse_type is None and raw_lines_batch:
            write_async(RAW_BUCKET, raw_lines_batch)
            raw_lines_batch.clear()

    # Drain on stop
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
    """
    Periodically replays buffered line protocol from SQLite.
    This helps catch up after outages (even if async error_cb buffered data).
    """
    while not stop_evt.is_set():
        time.sleep(REPLAY_EVERY_SECONDS)

        rows = db_dequeue_lines(REPLAY_BATCH_LINES)
        if not rows:
            continue

        # Group by bucket for efficient writes
        by_bucket = {}
        ids = []
        for row_id, bucket, lp in rows:
            ids.append(row_id)
            by_bucket.setdefault(bucket, []).append(lp)

        # Submit async. If it fails again, error_cb will re-buffer;
        # to avoid duplicates, we only delete if submission succeeded immediately.
        # Async submit doesn't guarantee server accepted, but writer will retry.
        # We treat "submitted to async writer" as good enough and delete to prevent infinite loops.
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
    except queue.Full:
        # In-memory queue overflow: spill raw point to SQLite immediately
        db_enqueue_lines(RAW_BUCKET, [make_raw_lp(pulse_type, ts_ns)])
        # also record downsample in memory (best-effort)
        record_downsample(pulse_type, ts_ns)
        print("Event queue full; spilled raw pulse to SQLite.")

def ImportPowerEvent(pin):    enqueue_event("Import")
def ExportPowerEvent(pin):    enqueue_event("Export")
def GeneratePowerEvent(pin):  enqueue_event("Generate")


def main():
    ensure_db()

    # Start workers
    t1 = threading.Thread(target=event_worker, name="event_worker", daemon=True)
    t2 = threading.Thread(target=replay_worker, name="replay_worker", daemon=True)
    t1.start()
    t2.start()

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(20, GPIO.IN)
    GPIO.setup(26, GPIO.IN)
    GPIO.setup(21, GPIO.IN)

    GPIO.add_event_detect(20, GPIO.RISING, bouncetime=100)
    GPIO.add_event_detect(26, GPIO.RISING, bouncetime=100)
    GPIO.add_event_detect(21, GPIO.RISING, bouncetime=100)

    GPIO.add_event_callback(20, ImportPowerEvent)
    GPIO.add_event_callback(26, ExportPowerEvent)
    GPIO.add_event_callback(21, GeneratePowerEvent)

    try:
        while True:
            time.sleep(5)
            # Lightweight visibility
            print(f"Running. In-memory queue size: {event_q.qsize()}")
    except KeyboardInterrupt:
        print("Exiting…")
    finally:
        stop_evt.set()
        GPIO.cleanup()

        # Let workers drain briefly
        time.sleep(1)

        # Force flush of async writer
        try:
            write_api.flush()
        except Exception:
            pass

        client.close()


if __name__ == "__main__":
    main()
    