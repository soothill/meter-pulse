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

The system expects GPIO pins configured as follows:

| Pin   | Type    | Purpose          |
|-------|----------|-------------------|
| 20     | RISING   | Import Power      |
| 26     | RISING   | Export Power     |
| 21     | RISING   | Generate Power   |

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
