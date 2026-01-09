# PowerLogger - Meter Pulse Collection System

A high-performance meter pulse logging system that captures power consumption data from Raspberry Pi GPIO pins and stores it in InfluxDB with automatic downsampling.

## Features

- **Real-time Pulse Capture**: Detects power meter pulses via Raspberry Pi GPIO pins (Import/Export/Generate)
- **InfluxDB Integration**: Stores raw pulse data with high-throughput async writes
- **Automatic Downsampling**: Creates 1-minute, 5-minute, and 1-hour aggregated views
- **Durable Buffering**: SQLite-based persistent queue survives network outages
- **Multi-architecture Support**: Works on x86_64, ARM64, and ARMv7 systems
- **Setup Verification**: `make verify` checks that everything is correctly configured

## Installation

### Quick Start

```bash
# Clone or download project
cd meter-pulse

# Install all dependencies (system packages, Python, InfluxDB CLI)
make install

# Configure your InfluxDB connection
cp .env.example .env
nano .env  # Edit with your InfluxDB details

# Setup InfluxDB buckets, tasks, and tokens
make setup-influxdb

# Verify everything is set up correctly
make verify

# Activate Python environment and run
source venv/bin/activate
python3 power_pulse.py
```

### Prerequisites

- **Python 3.7 or higher**
- **Raspberry Pi** (with GPIO access) or compatible hardware
- **InfluxDB 2.x** server running and accessible
- **Internet connection** (for downloading dependencies)
- **sudo access** (for installing system packages)

### Configuration

Edit `.env` file with your InfluxDB connection details:

```bash
# InfluxDB connection parameters
INFLUX_ORG=your_influx_db_org
INFLUX_HOST=http://your_influx_db_host:8086
INFLUX_TOKEN=your_admin_token_here

# Bucket names (customize if desired)
RAW_BUCKET=PowerLogger_raw
B1M_BUCKET=PowerLogger_1m
B5M_BUCKET=PowerLogger_5m
B1H_BUCKET=PowerLogger_1h

# Measurement name in InfluxDB
MEASUREMENT=PowerPulse
```

## Makefile Targets

### `make help` (default)
Display available make targets and usage information.

### `make install`
Install all required dependencies and set up Python environment:
- **System packages**: Detects OS (apt-get, dnf, yum, pacman) and installs python3-venv, python3-dev, build-essential, curl
- **InfluxDB CLI**: Detects system architecture (amd64, arm64, armv7l) and downloads correct binary
- **Python environment**: Creates `venv` virtual environment
- **Python packages**: Installs `influxdb-client` and `RPi.GPIO` (gracefully handles non-Raspberry Pi systems)
- **.env file**: Auto-creates `.env` file from template if missing

### `make setup-influxdb`
Create InfluxDB buckets, tasks, and tokens using `.env` configuration:
- Verifies InfluxDB connection and credentials
- Checks if buckets already exist (skips creation if present)
- Creates 4 buckets: PowerLogger_raw, PowerLogger_1m, PowerLogger_5m, PowerLogger_1h
- Sets up 3 Flux tasks for automatic downsampling
- Creates 2 tokens: Writer token (for Python) and Read-only token (for Grafana)

### `make verify`
Verify everything is set up correctly:
- Checks if `.env` file exists
- Verifies Python virtual environment exists
- Checks if InfluxDB CLI is installed
- Verifies InfluxDB connection is working
- Checks if required Python packages are installed (influxdb-client, RPi.GPIO)
- Checks if all 4 PowerLogger buckets exist in InfluxDB
- Shows summary with clear status indicators (✓/❌)
- Provides next steps if any component is missing

### systemd service targets

If GPIO edge detection fails when running as a normal user, run as a systemd service.

- `make service-install` – installs `powerlogger.service` to `/etc/systemd/system/` and reloads systemd
- `make service-start` – enables and starts the service
- `make service-stop` – stops the service
- `make service-restart` – restarts the service
- `make service-status` – shows full service status
- `make service-logs` – follows service logs (`journalctl -f`)
- `make service-debug-on` – enables debug logging (sets `DEBUG=1` in the unit file, reinstalls, restarts)
- `make service-debug-off` – disables debug logging (`DEBUG=0`)

### `make delete-buckets`
Delete all PowerLogger buckets from InfluxDB with safety warnings:
- Displays detailed warning listing all buckets and their data contents
- Requires typing "DELETE" to confirm (prevents accidental deletion)
- Verifies InfluxDB connection before attempting deletion
- Shows progress as each bucket is deleted
- Skips non-existent buckets gracefully
- Provides clear feedback on completion

### `make clean`
Remove the Python virtual environment directory.

## Architecture

### Data Flow

```
┌─────────────┐    ┌──────────────┐
│  GPIO Pins  │───▶│ Event Queue │───▶│ InfluxDB    │
│ (Import/     │    │ (In-memory) │    │ (PowerLogger │
│  Export/     │    │ + SQLite    │    │   _raw)     │
│  Generate)    │    │  buffer     │    │             │
└─────────────┘    └──────────────┘
                                                │
                                                ▼
                                         ┌──────────────────┐
                                         │ Flux Tasks      │
                                         │ (downsampling)  │
                                         └──────────────────┘
                                                │
                        ┌───────────────────────────────────┐
                        │  PowerLogger_1m/5m/1h       │
                        └───────────────────────────────────┘
```

### Bucket Retention Policies

| Bucket       | Retention | Purpose                 |
|--------------|------------|--------------------------|
| PowerLogger_raw  | 30 days     | Raw pulse data (high freq) |
| PowerLogger_1m  | 90 days     | 1-minute aggregates         |
| PowerLogger_5m  | 365 days    | 5-minute aggregates         |
| PowerLogger_1h  | Forever     | 1-hour aggregates          |

### Performance Tuning

The system includes several performance optimizations for high pulse rates:

- **Async Writes**: Batches up to 5000 points per request
- **Automatic Retry**: Up to 8 retries with exponential backoff
- **Durable Buffering**: SQLite queue survives network outages
- **Replay Worker**: Automatically replays buffered data every 10 seconds

## GPIO Configuration

The system uses the modern **gpiozero** library for GPIO access and expects GPIO pins configured as follows:

| Pin   | Type    | Purpose          |
|-------|----------|-------------------|
| 20     | Button (RISING) | Import Power      |
| 26     | Button (RISING) | Export Power     |
| 21     | Button (RISING) | Generate Power   |

**Note:** The application uses `gpiozero.Button` with `pull_up=True` and `bounce_time=0.1` for reliable pulse detection. This is the recommended approach for Raspberry Pi GPIO programming and provides better abstraction than the legacy RPi.GPIO library.

## Troubleshooting

### GPIO / Raspberry Pi

If you see errors like:

```
RuntimeError: Failed to add edge detection
```

This is usually caused by the chosen GPIO backend failing to register edge detection.
This project uses **gpiozero** and will now explicitly try the most compatible backend
first (RPi.GPIO), then fall back to lgpio.

Quick checks:

```bash
ls -l /dev/gpiomem /dev/gpiochip0
groups
```

You should be in the `gpio` group and `/dev/gpiomem` should be readable.

Install OS packages (Raspberry Pi OS):

```bash
sudo apt-get update
sudo apt-get install -y python3-gpiozero python3-rpi.gpio
```

Then log out/in after group changes.

### Running as a systemd service (recommended)

On some Debian/RPi kernel images, **GPIO edge detection works with `sudo` but fails as an unprivileged user**.
In that case, run PowerLogger as a system service.

This repo includes an example unit: `powerlogger.service`.

Install and start:

```bash
sudo cp powerlogger.service /etc/systemd/system/powerlogger.service
sudo systemctl daemon-reload
sudo systemctl enable --now powerlogger.service
```

View logs:

```bash
journalctl -u powerlogger.service -f
```

### Connection Issues

If `make setup-influxdb` fails with connection errors:
1. **Verify InfluxDB is running**: `docker ps` or `systemctl status influxdb`
2. **Test connectivity**: `influx ping --host http://your-host:8086`
3. **Check credentials**: Ensure token has admin permissions
4. **Verify hostname**: Use `http://localhost:8086` for local, `http://influxdb:8086` for Docker

### GPIO Issues

If pulse detection isn't working:

```bash
# Check GPIO permissions
sudo usermod -a -G gpio $USER

# Verify GPIO is accessible
python3 -c "import RPi.GPIO as GPIO; print('GPIO OK')"
```

### Performance Issues

If pulse data isn't appearing in InfluxDB:

1. **Check queue size**: The application logs queue size every 5 seconds
2. **Review SQLite buffer**: `/var/lib/powerlogger/write_queue.sqlite`
3. **Monitor InfluxDB**: Check for write errors in InfluxDB logs

## License

This project is provided as-is for monitoring power consumption.

## Support

For issues or questions:
1. Check this README's troubleshooting section
2. Review `make help` for available targets
3. Check InfluxDB logs: `docker logs influxdb` or `journalctl -u influxdb`
4. Verify configuration in `.env` file

## Features

- **Real-time Pulse Capture**: Detects power meter pulses via Raspberry Pi GPIO pins (Import/Export/Generate)
- **InfluxDB Integration**: Stores raw pulse data with high-throughput async writes
- **Automatic Downsampling**: Creates 1-minute, 5-minute, and 1-hour aggregated views
- **Durable Buffering**: SQLite-based persistent queue survives network outages
- **Multi-architecture Support**: Works on x86_64, ARM64, and ARMv7 systems

## Installation

### Quick Start

```bash
# Clone or download the project
cd meter-pulse

# Install all dependencies (system packages, Python, InfluxDB CLI)
make install

# Configure your InfluxDB connection
cp .env.example .env
nano .env  # Edit with your InfluxDB details

# Setup InfluxDB buckets, tasks, and tokens
make setup-influxdb

# Activate Python environment and run
source venv/bin/activate
python3 power_pulse.py
```

### Prerequisites

- **Python 3.7 or higher**
- **Raspberry Pi** (with GPIO access) or compatible hardware
- **InfluxDB 2.x** server running and accessible
- **Internet connection** (for downloading dependencies)
- **sudo access** (for installing system packages)

### Configuration

Edit `.env` file with your InfluxDB connection details:

```bash
# InfluxDB connection parameters
INFLUX_ORG=your_influx_db_org
INFLUX_HOST=http://your_influx_db_host:8086
INFLUX_TOKEN=your_admin_token_here

# Bucket names (customize if desired)
RAW_BUCKET=PowerLogger_raw
B1M_BUCKET=PowerLogger_1m
B5M_BUCKET=PowerLogger_5m
B1H_BUCKET=PowerLogger_1h

# Measurement name in InfluxDB
MEASUREMENT=PowerPulse
```

## Makefile Targets

### `make help` (default)
Display available make targets and usage information.

### `make install`
Install all required dependencies and set up Python environment:
- Detects OS and installs system packages (python3-venv, python3-dev, build-essential, curl)
- Downloads and installs InfluxDB CLI for your system architecture
- Creates Python virtual environment (`venv/`)
- Installs Python packages (`influxdb-client`, `RPi.GPIO`)
- Creates `.env` file from template if missing

### `make setup-influxdb`
Create InfluxDB buckets, tasks, and tokens using `.env` configuration:
- Verifies InfluxDB connection and credentials
- Checks if buckets already exist (skips creation if present)
- Creates 4 buckets: PowerLogger_raw, PowerLogger_1m, PowerLogger_5m, PowerLogger_1h
- Sets up 3 Flux tasks for automatic downsampling
- Creates 2 tokens: Writer token (for Python) and Read-only token (for Grafana)

**Output when buckets already exist:**
```
✅ All PowerLogger buckets already exist!
  • PowerLogger_raw
  • PowerLogger_1m
  • PowerLogger_5m
  • PowerLogger_1h

Setup appears to be complete. Skipping bucket creation.
```

**Connection error example:**
```
❌ ERROR: Cannot connect to InfluxDB or credentials are invalid!

Current configuration from .env:
  • Host:   http://influxdb:8086
  • Org:    soothill
  • Token:  3EDgXV1TOvCk4ssy3wb...

Please check:
  1. InfluxDB server is running and accessible
  2. INFLUX_HOST is correct
  3. INFLUX_ORG matches your organization name
  4. INFLUX_TOKEN is valid and has admin permissions
```

### `make delete-buckets`
Delete all PowerLogger buckets from InfluxDB with safety warnings:
- Displays detailed warning listing all buckets and their data contents
- Requires typing "DELETE" to confirm (prevents accidental deletion)
- Verifies InfluxDB connection before attempting deletion
- Shows progress as each bucket is deleted

**Warning message:**
```
⚠️  DANGER: This will DELETE all PowerLogger buckets from InfluxDB!

This action is IRREVERSIBLE and will permanently delete:
  • PowerLogger_raw  (with all historical pulse data)
  • PowerLogger_1m   (with all downsampled data)
  • PowerLogger_5m   (with all downsampled data)
  • PowerLogger_1h   (with all downsampled data)

All data in these buckets will be LOST FOREVER!

Type 'DELETE' to confirm deletion:
```

### `make clean`
Remove the Python virtual environment directory.

## Architecture

### Data Flow

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  GPIO Pins  │───▶│ Event Queue │───▶│ InfluxDB    │
│ (Import/     │    │ (In-memory) │    │ (PowerLogger │
│  Export/     │    │ + SQLite    │    │   _raw)     │
│  Generate)    │    │  buffer     │    │             │
└─────────────┘    └──────────────┘    └─────────────┘
                                                │
                                                ▼
                                         ┌──────────────────┐
                                         │ Flux Tasks      │
                                         │ (downsampling)  │
                                         └──────────────────┘
                                                │
                                                ▼
                        ┌───────────────────────────────────┐
                        │  PowerLogger_1m/5m/1h       │
                        └───────────────────────────────────┘
```

### Bucket Retention Policies

| Bucket       | Retention | Purpose                 |
|--------------|------------|--------------------------|
| PowerLogger_raw  | 30 days     | Raw pulse data (high freq) |
| PowerLogger_1m  | 90 days     | 1-minute aggregates         |
| PowerLogger_5m  | 365 days    | 5-minute aggregates         |
| PowerLogger_1h  | Forever     | 1-hour aggregates          |

### Performance Tuning

The system includes several performance optimizations for high pulse rates:

- **Async Writes**: Batches up to 5000 points per request
- **Automatic Retry**: Up to 8 retries with exponential backoff
- **Durable Buffering**: SQLite queue survives network outages
- **Replay Worker**: Automatically replays buffered data every 10 seconds

## GPIO Configuration

The system expects GPIO pins configured as follows:

| Pin   | Type    | Purpose          |
|-------|----------|-------------------|
| 20     | RISING   | Import Power      |
| 26     | RISING   | Export Power     |
| 21     | RISING   | Generate Power   |

### GPIO debug / troubleshooting

If you are not seeing pulses logged, it is usually one of:

1) The **wrong edge** (your meter is active-low and you’re only listening for rising)
2) The **wrong pull configuration** (`pull_up=True` vs `pull_up=False`)
3) Edge callbacks not firing due to permissions/backend (less likely here since lgpio is used)

This project supports extra GPIO debug via env vars (printed when `DEBUG=1`).

**GPIO env vars**:

- `GPIO_PULL` (preferred) – `up|down|none` to select internal pull configuration
- `GPIO_PULL_UP` (default `true`) – set `false` to disable internal pull-up
- `GPIO_ACTIVE_STATE` (optional) – force active level: `high` or `low` (helps when diagnosing polarity)
- `GPIO_BOUNCE_TIME` (default `0.1`) – set `0` to disable debounce
- `GPIO_MIN_PULSE_INTERVAL` (default `0.03`) – additional software debounce (seconds) applied per input based on pulse spacing
- `GPIO_ENQUEUE_EDGES` (default `rising`) – which edge counts as a pulse: `rising|falling|both`
- `GPIO_LOG_EDGES` (default `both`) – which edges to log (diagnostic): `rising|falling|both`
- `GPIO_POLL_DEBUG` (default `false`) – if `true`, starts a polling monitor that logs value changes
- `GPIO_POLL_INTERVAL` (default `0.05`) – poll interval seconds
- `GPIO_IMPORT_PIN`/`GPIO_EXPORT_PIN`/`GPIO_GENERATE_PIN` – override BCM pins (defaults 20/26/21)

**Recommended debug runs**:

Listen/log *both* edges and use poll monitor:

```bash
DEBUG=1 GPIO_LOG_EDGES=both GPIO_POLL_DEBUG=1 python3 power_pulse.py
```

If you see only **deactivated** edges firing, your pulse is likely active-low; switch pulse enqueueing:

```bash
DEBUG=1 GPIO_ENQUEUE_EDGES=falling python3 power_pulse.py
```

If your idle state looks wrong (e.g. always active), try flipping the pull-up:

```bash
DEBUG=1 GPIO_PULL_UP=false python3 power_pulse.py
```

**Active-low pulse tip (like your BCM20 result):**

If the line idles HIGH and pulses LOW, you typically want:

```bash
DEBUG=1 GPIO_PULL=up GPIO_ENQUEUE_EDGES=falling GPIO_LOG_EDGES=both GPIO_BOUNCE_TIME=0 python3 power_pulse.py
```

Note: `GPIO_ACTIVE_STATE` only applies when `GPIO_PULL=none` (floating). If you use `GPIO_PULL=up|down`, the logger will ignore `GPIO_ACTIVE_STATE` to avoid gpiozero `PinInvalidState`.

### Standalone GPIO test tool

If you want to ignore Influx entirely and just confirm which pins are changing state, use:

```bash
python3 gpio_watch.py --pins 2-27 --pull up
```

Try different pull configurations:

```bash
python3 gpio_watch.py --pins 2-27 --pull up
python3 gpio_watch.py --pins 2-27 --pull down
python3 gpio_watch.py --pins 2-27 --pull none --active high
python3 gpio_watch.py --pins 2-27 --pull none --active low
```

Or watch just the expected pins:

```bash
python3 gpio_watch.py --pins 20,21,26 --pull up
```

Notes:
- Pin numbers are **BCM** numbers (gpiozero default), not physical header pin numbers.
- The tool prints initial states, then prints both edge callbacks and polling-detected changes.

## Troubleshooting

### Connection Issues

If `make setup-influxdb` fails with connection errors:

1. **Verify InfluxDB is running**: `docker ps` or `systemctl status influxdb`
2. **Test connectivity**: `influx ping --host http://your-host:8086`
3. **Check credentials**: Ensure token has admin permissions
4. **Verify hostname**: Use `http://localhost:8086` for local, `http://influxdb:8086` for Docker

### GPIO Issues

If pulse detection isn't working:

```bash
# Check GPIO permissions
sudo usermod -a -G gpio $USER

# Verify GPIO is accessible
python3 -c "import RPi.GPIO as GPIO; print('GPIO OK')"
```

### Performance Issues

If pulse data isn't appearing in InfluxDB:

1. **Check queue size**: The application logs queue size every 5 seconds
2. **Review SQLite buffer**: `/var/lib/powerlogger/write_queue.sqlite`
3. **Monitor InfluxDB**: Check for write errors in InfluxDB logs

## License

This project is provided as-is for monitoring power consumption.

## Support

For issues or questions:
1. Check this README's troubleshooting section
2. Review `make help` for available targets
3. Check InfluxDB logs: `docker logs influxdb` or `journalctl -u influxdb`
4. Verify configuration in `.env` file
