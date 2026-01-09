#!/bin/bash
"""
Verify PowerLogger setup without Python encoding issues.
"""

ALL_OK=true

echo "Verifying PowerLogger setup..."
echo ""

# Check .env file
if [ ! -f .env ]; then
    echo "❌ ERROR: .env file not found!"
    echo "Copy .env.example to .env and configure it first."
    ALL_OK=false
else
    echo "✓ .env file exists"
fi

# Check virtual environment
if [ ! -d venv ]; then
    echo "❌ ERROR: Python virtual environment not found!"
    echo "Run 'make install' to set up Python environment."
    ALL_OK=false
else
    echo "✓ Python virtual environment exists"
fi

# Check InfluxDB CLI
if ! command -v influx >/dev/null 2>&1; then
    echo "❌ ERROR: InfluxDB CLI not installed!"
    echo "Run 'make install' to install all dependencies."
    ALL_OK=false
else
    echo "✓ InfluxDB CLI is installed"
fi

# Check InfluxDB connection
source .env 2>/dev/null
if ! influx ping --host "$$INFLUX_HOST" >/dev/null 2>&1; then
    echo "❌ ERROR: Cannot connect to InfluxDB!"
    echo ""
    echo "Current configuration from .env:"
    echo "  • Host:   $$INFLUX_HOST"
    echo "  • Org:    $$INFLUX_ORG"
    echo "  • Token:  $${INFLUX_TOKEN:0:20}..."
    echo ""
    echo "Please check:"
    echo "  1. InfluxDB server is running and accessible"
    echo "  2. INFLUX_HOST is correct"
    echo "  3. INFLUX_ORG matches your organization name"
    echo "  4. INFLUX_TOKEN is valid and has admin permissions"
    echo ""
    echo "To test connection manually:"
    echo "  influx ping --host \"$$INFLUX_HOST\""
    echo ""
    ALL_OK=false
else
    echo "✓ InfluxDB connection successful"
fi

# Check Python packages
./venv/bin/python3 -c "import influxdb_client; import RPi.GPIO; print('OK')" 2>&1
if [ $? -eq 0 ]; then
    echo "✓ Python packages installed (influxdb-client, RPi.GPIO)"
else
    echo "❌ ERROR: Python packages not installed correctly!"
    echo "Run 'make install' to install Python packages."
    ALL_OK=false
fi

# Check InfluxDB buckets
source .env 2>/dev/null
MISSING_COUNT=0
for bucket in PowerLogger_raw PowerLogger_1m PowerLogger_5m PowerLogger_1h; do
    if ! influx bucket find --name "$$bucket" --host "$$INFLUX_HOST" --token "$$INFLUX_TOKEN" --org "$$INFLUX_ORG" >/dev/null 2>&1; then
        echo "✗ Bucket $bucket missing"
        MISSING_COUNT=$((MISSING_COUNT + 1))
    else
        echo "✓ Bucket $bucket exists"
done

if [ $MISSING_COUNT -gt 0 ]; then
    echo "❌ ERROR: Not all PowerLogger buckets exist in InfluxDB!"
    echo "Missing $MISSING_COUNT of 4 buckets."
    echo "Run 'make setup-influxdb' to create them."
    ALL_OK=false
else
    echo "✓ All 4 PowerLogger buckets exist"
fi

echo ""
if [ "$ALL_OK" = "true" ]; then
    echo "==================== Setup Complete ===================="
    echo ""
    echo "Summary:"
    echo "  • .env file: configured ✓"
    echo "  • Python environment: set up ✓"
    echo "  • Python packages: installed ✓"
    echo "  • InfluxDB CLI: installed ✓"
    echo "  • InfluxDB connection: working ✓"
    echo "  • InfluxDB buckets: created ✓"
    echo ""
    echo "You can now start PowerLogger with:"
    echo "  source venv/bin/activate"
    echo "  python3 power_pulse.py"
else
    echo "Setup incomplete. Please fix the errors above."
    exit 1
fi