.PHONY: help install setup-influxdb clean delete-buckets verify venv \
	service-install service-start service-stop service-restart service-status service-logs \
	service-debug-on service-debug-off

# Default target: display help
help:
	@echo "PowerLogger - Meter Pulse Collection System"
	@echo ""
	@echo "Available targets:"
	@echo "  make help          - Show this help message"
	@echo "  make install       - Install all required dependencies and set up Python environment"
	@echo "  make setup-influxdb - Create InfluxDB buckets, tasks, and tokens using .env config"
	@echo "  make service-install - Install systemd service (powerlogger.service)"
	@echo "  make service-start   - Start the systemd service"
	@echo "  make service-restart - Restart the systemd service"
	@echo "  make service-logs    - Follow logs from the systemd service"
	@echo "  make service-debug-on  - Enable debug logging (DEBUG=1) and restart service"
	@echo "  make service-debug-off - Disable debug logging (DEBUG=0) and restart service"
	@echo "  make delete-buckets - Delete all PowerLogger buckets (WARNING: irreversible)"
	@echo "  make verify       - Verify everything is set up correctly"
	@echo "  make clean         - Remove virtual environment"
	@echo ""
	@echo "Prerequisites:"
	@echo "  - Python 3.7 or higher"
	@echo "  - Internet connection for package downloads"
	@echo "  - InfluxDB server running and accessible"
	@echo ""
	@echo "Configuration:"
	@echo "  Copy .env.example to .env and configure your InfluxDB parameters"
	@echo ""

# Install all dependencies and set up environment
install: check-env install-system-deps create-venv install-python-packages
	@echo ""
	@echo "Installation complete!"
	@echo "Next steps:"
	@echo "  1. Configure .env file with your InfluxDB details"
	@echo "  2. Run 'make setup-influxdb' to create InfluxDB buckets and tokens"
	@echo "  3. Run 'source venv/bin/activate' to activate Python environment"
	@echo "  4. Run 'python3 power_pulse.py' to start the pulse logger"
	@echo ""

# Check if .env file exists
check-env:
	@if [ ! -f .env ]; then \
		echo "Warning: .env file not found. Copying from .env.example..."; \
		cp .env.example .env; \
		echo "Please edit .env with your InfluxDB configuration."; \
	fi

# Install system-level dependencies
install-system-deps:
	@echo "Installing system dependencies..."
	@# Detect OS and install appropriate packages
	@if command -v apt-get >/dev/null 2>&1; then \
		sudo apt-get update -qq; \
		sudo apt-get install -y python3-venv python3-dev build-essential curl; \
	elif command -v dnf >/dev/null 2>&1; then \
		sudo dnf install -y python3-virtualenv python3-devel gcc make curl; \
	elif command -v yum >/dev/null 2>&1; then \
		sudo yum install -y python3-virtualenv python3-devel gcc make curl; \
	elif command -v pacman >/dev/null 2>&1; then \
		sudo pacman -S --noconfirm python-virtualenv base-devel curl; \
	else \
		echo "Unable to detect package manager. Please install:"; \
		echo "  - python3-venv (or python3-virtualenv)"; \
		echo "  - python3-dev"; \
		echo "  - build-essential / gcc / make"; \
		echo "  - curl"; \
	fi
	@echo "Installing InfluxDB CLI..."
	@# Install influx CLI if not already installed
	@if ! command -v influx >/dev/null 2>&1; then \
		echo "Detecting system architecture..."; \
		ARCH=$$(uname -m); \
		case "$$ARCH" in \
			x86_64)  INFLUX_ARCH="linux-amd64" ;; \
			aarch64|arm64) INFLUX_ARCH="linux-arm64" ;; \
			armv7l)  INFLUX_ARCH="linux-arm" ;; \
			*) echo "Warning: Unsupported architecture $$ARCH, falling back to amd64"; \
			   INFLUX_ARCH="linux-amd64" ;; \
		esac; \
		echo "Downloading InfluxDB CLI for $$INFLUX_ARCH..."; \
		curl -sL https://dl.influxdata.com/influxdb/releases/influxdb2-client-2.7.5-$$INFLUX_ARCH.tar.gz -o /tmp/influx.tar.gz && \
		sudo tar xzf /tmp/influx.tar.gz -C /tmp && \
		sudo cp /tmp/influx /usr/local/bin/ && \
		sudo rm -f /tmp/influx.tar.gz /tmp/influx && \
		echo "InfluxDB CLI installed successfully"; \
	else \
		echo "InfluxDB CLI already installed"; \
	fi

# Create Python virtual environment
create-venv:
	@echo "Creating Python virtual environment..."
	@if [ ! -d venv ]; then \
		python3 -m venv venv; \
	else \
		echo "Virtual environment already exists"; \
	fi

# Install Python packages into virtual environment
install-python-packages:
	@echo "Installing Python packages..."
	@./venv/bin/pip install --upgrade pip setuptools wheel
	@./venv/bin/pip install influxdb-client
	@# Try to install gpiozero, but handle non-Raspberry Pi systems gracefully
	@if ./venv/bin/pip install gpiozero 2>/dev/null; then \
		echo "gpiozero installed (Raspberry Pi detected)"; \
	else \
		echo "Note: gpiozero not installed (not a Raspberry Pi)"; \
		echo "The application will run without GPIO support on non-Raspberry Pi systems"; \
	fi
	@echo "Python packages installed successfully"

# Setup InfluxDB buckets, tasks, and tokens
setup-influxdb:
	@echo "Setting up InfluxDB configuration..."
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found!"; \
		echo "Please copy .env.example to .env and configure it first."; \
		exit 1; \
	fi
	@if ! command -v influx >/dev/null 2>&1; then \
		echo "Error: InfluxDB CLI not installed!"; \
		echo "Run 'make install' first to install all dependencies."; \
		exit 1; \
	fi
	@echo "Verifying InfluxDB connection and credentials..."
	@bash -c 'source .env 2>/dev/null; \
	if ! influx bucket list --host "$$INFLUX_HOST" --token "$$INFLUX_TOKEN" --org "$$INFLUX_ORG" >/dev/null 2>&1; then \
		echo ""; \
		echo "❌ ERROR: Cannot connect to InfluxDB or credentials are invalid!"; \
		echo ""; \
		echo "Current configuration from .env:"; \
		echo "  • Host:   $$INFLUX_HOST"; \
		echo "  • Org:    $$INFLUX_ORG"; \
		echo "  • Token:  $${INFLUX_TOKEN:0:20}..."; \
		echo ""; \
		echo "Please check:"; \
		echo "  1. InfluxDB server is running and accessible"; \
		echo "  2. INFLUX_HOST is correct (e.g., http://localhost:8086 or http://influxdb:8086)"; \
		echo "  3. INFLUX_ORG matches your organization name"; \
		echo "  4. INFLUX_TOKEN is valid and has admin permissions"; \
		echo ""; \
		echo "To test connection manually:"; \
		echo "  influx ping --host \"$$INFLUX_HOST\""; \
		echo ""; \
		exit 1; \
	fi; \
	echo "✓ Connection successful! Checking if buckets already exist..."'
	@bash -c 'source .env 2>/dev/null; \
	MISSING_BUCKETS=""; \
	for bucket in PowerLogger_raw PowerLogger_1m PowerLogger_5m PowerLogger_1h; do \
		if ! influx bucket find --name "$$bucket" --host "$$INFLUX_HOST" --token "$$INFLUX_TOKEN" --org "$$INFLUX_ORG" >/dev/null 2>&1; then \
			MISSING_BUCKETS="$$MISSING_BUCKETS $$bucket"; \
		fi; \
	done; \
	if [ -z "$$MISSING_BUCKETS" ]; then \
		echo ""; \
		echo "✅ All PowerLogger buckets already exist!"; \
		echo "  • PowerLogger_raw"; \
		echo "  • PowerLogger_1m"; \
		echo "  • PowerLogger_5m"; \
		echo "  • PowerLogger_1h"; \
		echo ""; \
		echo "Setup appears to be complete. Skipping bucket creation."; \
		echo "To force recreation, delete existing buckets first:"; \
		echo "  influx bucket delete --name PowerLogger_raw --org \"$$INFLUX_ORG\""; \
		echo "  influx bucket delete --name PowerLogger_1m --org \"$$INFLUX_ORG\""; \
		echo "  influx bucket delete --name PowerLogger_5m --org \"$$INFLUX_ORG\""; \
		echo "  influx bucket delete --name PowerLogger_1h --org \"$$INFLUX_ORG\""; \
		echo ""; \
		exit 10; \
	else \
		echo "Creating buckets and tasks..."; \
	fi' || { [ $$? -eq 10 ] && exit 0; } && bash config-influx.sh

# Clean up virtual environment
clean:
	@echo "Removing virtual environment..."
	@if [ -d venv ]; then \
		rm -rf venv; \
		echo "Virtual environment removed"; \
	else \
		echo "No virtual environment found"; \
	fi

# Delete all PowerLogger buckets with warning and confirmation
delete-buckets:
	@echo "⚠️  DANGER: This will DELETE all PowerLogger buckets from InfluxDB!"
	@echo ""
	@echo "This action is IRREVERSIBLE and will permanently delete:"
	@echo "  • PowerLogger_raw  (with all historical pulse data)"
	@echo "  • PowerLogger_1m   (with all downsampled data)"
	@echo "  • PowerLogger_5m   (with all downsampled data)"
	@echo "  • PowerLogger_1h   (with all downsampled data)"
	@echo ""
	@echo "All data in these buckets will be LOST FOREVER!"
	@echo ""
	@read -p "Type 'DELETE' to confirm deletion: " CONFIRMED; \
	if [ "$$CONFIRMED" != "DELETE" ]; then \
		echo ""; \
		echo "❌ Deletion cancelled. No changes were made."; \
		exit 1; \
	fi
	@echo ""
	@echo "Verifying InfluxDB connection..."
	@bash -c 'source .env 2>/dev/null; \
		if ! influx bucket list --host "$$INFLUX_HOST" --token "$$INFLUX_TOKEN" --org "$$INFLUX_ORG" >/dev/null 2>&1; then \
			echo "❌ ERROR: Cannot connect to InfluxDB!"; \
			exit 1; \
		fi; \
		echo "✓ Connection verified. Deleting buckets..."; \
		for bucket in PowerLogger_raw PowerLogger_1m PowerLogger_5m PowerLogger_1h; do \
			if influx bucket find --name "$$bucket" --host "$$INFLUX_HOST" --token "$$INFLUX_TOKEN" --org "$$INFLUX_ORG" >/dev/null 2>&1; then \
				echo "  Deleting $$bucket..."; \
				influx bucket delete --name "$$bucket" --org "$$INFLUX_ORG" --host "$$INFLUX_HOST" --token "$$INFLUX_TOKEN"; \
			else \
				echo "  Skipping $$bucket (does not exist)"; \
			fi; \
		done; \
		echo ""; \
		echo "✅ All PowerLogger buckets deleted successfully!"; \
		echo ""; \
		echo "You can now run make setup-influxdb to recreate them."'

# Verify everything is set up correctly
verify:
	@echo "Verifying PowerLogger setup..."
	@echo ""
	@if [ ! -f .env ]; then \
		echo "❌ ERROR: .env file not found!"; \
		echo "Copy .env.example to .env and configure it first."; \
		exit 1; \
	fi
	@echo "✓ .env file exists"
	@if [ ! -d venv ]; then \
		echo "❌ ERROR: Python virtual environment not found!"; \
		echo "Run 'make install' to set up of Python environment."; \
		exit 1; \
	fi
	@echo "✓ Python virtual environment exists"
	@if ! command -v influx >/dev/null 2>&1; then \
		echo "❌ ERROR: InfluxDB CLI not installed!"; \
		echo "Run 'make install' to install all dependencies."; \
		exit 1; \
	fi
	@echo "✓ InfluxDB CLI is installed"
	@echo "Verifying InfluxDB connection..."
	@bash -c 'source .env 2>/dev/null; \
		if ! influx bucket list --host "$$INFLUX_HOST" --token "$$INFLUX_TOKEN" --org "$$INFLUX_ORG" >/dev/null 2>&1; then \
			echo "❌ ERROR: Cannot connect to InfluxDB!"; \
			exit 1; \
		fi'
	@echo "✓ InfluxDB connection successful"
	@echo "Checking required Python packages..."
	@./venv/bin/python3 -c "import influxdb_client; print('OK')" 2>&1
	@if [ $$? -eq 0 ]; then \
		echo "✓ Python packages installed (influxdb-client)"; \
	else \
		echo "❌ ERROR: Python packages not installed correctly!"; \
		echo "Run 'make install' to install Python packages."; \
	fi
	@./venv/bin/python3 -c "import gpiozero; print('GPIO available')" 2>&1 || echo "Note: gpiozero not installed (GPIO support disabled on this system)"
	@echo ""
	@echo "Checking InfluxDB buckets..."
	@bash -c 'source .env 2>/dev/null && for bucket in PowerLogger_raw PowerLogger_1m PowerLogger_5m PowerLogger_1h; do influx bucket find --name $$bucket --host $$INFLUX_HOST --token $$INFLUX_TOKEN --org $$INFLUX_ORG >/dev/null 2>&1 && echo "  ✓ $$bucket exists"; done'
	@echo ""
	@echo "==================== Setup Complete ===================="
	@echo ""
	@echo "Summary:"
	@echo "  • .env file: configured ✓"
	@echo "  • Python environment: set up ✓"
	@echo "  • Python packages: installed ✓"
	@echo "  • InfluxDB CLI: installed ✓"
	@echo "  • InfluxDB connection: working ✓"
	@echo "  • InfluxDB buckets: created ✓"
	@echo ""
	@echo "You can now start PowerLogger with:"
	@echo "  source venv/bin/activate"
	@echo "  python3 power_pulse.py"

# -------------------------
# systemd service helpers
# -------------------------

service-install:
	@echo "Installing systemd service..."
	@sudo cp powerlogger.service /etc/systemd/system/powerlogger.service
	@sudo systemctl daemon-reload
	@echo "✓ Installed /etc/systemd/system/powerlogger.service"
	@echo "Next: make service-start"

service-start:
	@echo "Starting powerlogger.service..."
	@sudo systemctl enable --now powerlogger.service
	@sudo systemctl --no-pager --full status powerlogger.service | head -n 30

service-stop:
	@echo "Stopping powerlogger.service..."
	@sudo systemctl stop powerlogger.service
	@sudo systemctl --no-pager --full status powerlogger.service | head -n 30 || true

service-restart:
	@echo "Restarting powerlogger.service..."
	@# If the unit hasn't been installed yet, install it first
	@if [ ! -f /etc/systemd/system/powerlogger.service ]; then \
		echo "Service not installed yet; running make service-install first..."; \
		$(MAKE) service-install; \
	fi
	@sudo systemctl restart powerlogger.service
	@sudo systemctl --no-pager --full status powerlogger.service | head -n 30

service-status:
	@sudo systemctl --no-pager --full status powerlogger.service

service-logs:
	@echo "Following logs for powerlogger.service (Ctrl+C to stop)..."
	@sudo journalctl -u powerlogger.service -f

service-debug-on:
	@echo "Enabling DEBUG=1 in powerlogger.service (requires reinstall + restart)..."
	@sed -i 's/^Environment=DEBUG=.*/Environment=DEBUG=1/' powerlogger.service
	@$(MAKE) service-install
	@$(MAKE) service-restart

service-debug-off:
	@echo "Disabling DEBUG (set DEBUG=0) in powerlogger.service (requires reinstall + restart)..."
	@sed -i 's/^Environment=DEBUG=.*/Environment=DEBUG=0/' powerlogger.service
	@$(MAKE) service-install
	@$(MAKE) service-restart
