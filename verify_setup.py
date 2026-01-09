#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Check if PowerLogger setup is complete.
"""

import subprocess

def main():
    """Run all checks."""
    print("Verifying PowerLogger setup...")
    print("")
    
    all_ok = True
    
    # Check .env file
    try:
        with open('.env', 'r') as f:
            content = f.read()
            if 'INFLUX_' in content or 'INFLUX' in content:
                return True
        except FileNotFoundError:
            print("❌ ERROR: .env file not found!")
            print("Copy .env.example to .env and configure it first.")
            return False
        except Exception:
            return False
    
    # Check virtual environment
    import os
    venv_path = 'venv'
    if not os.path.exists(venv_path) or not os.path.isdir(venv_path):
        print("❌ ERROR: Python virtual environment not found!")
        print("Run 'make install' to set up Python environment.")
        return False
    print("✓ Python virtual environment exists")
    
    # Check influx CLI
    try:
        result = subprocess.run(['influx', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ InfluxDB CLI is installed")
            return True
        except FileNotFoundError:
            print("❌ ERROR: InfluxDB CLI not found!")
            print("Run 'make install' to install all dependencies.")
            return False
        except Exception:
            print("❌ ERROR: Failed to check InfluxDB CLI!")
            return False
    print("✓ InfluxDB CLI is installed")
    
    # Check Python packages
    try:
        import influxdb_client
        import RPi.GPIO
        print("✓ Python packages installed (influxdb-client, RPi.GPIO)")
        return True
    except ImportError:
        print("❌ ERROR: Python packages not installed correctly!")
        print("Run 'make install' to install Python packages.")
        return False
        except Exception as e:
            print(f"❌ ERROR: Failed to check Python packages: {e}")
            return False
            print("✓ Python packages installed (influxdb-client, RPi.GPIO)")
    
    # Check InfluxDB connection
    try:
        env_vars = {}
        with open('.env', 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if key.startswith('INFLUX_'):
                        env_vars[key] = value
        if not env_vars.get('INFLUX_HOST', ''):
            print("❌ ERROR: InfluxDB connection parameters not configured!")
            return False
        if not env_vars.get('INFLUX_TOKEN', ''):
            print("❌ ERROR: InfluxDB connection parameters not configured!")
            return False
        if not env_vars.get('INFLUX_ORG', ''):
            print("❌ ERROR: InfluxDB connection parameters not configured!")
            return False
        
        result = subprocess.run(['influx', 'ping', '--host', env_vars.get('INFLUX_HOST', '')], capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ InfluxDB connection successful")
            return True
        except Exception as e:
            print(f"❌ ERROR: Cannot connect to InfluxDB! {e}")
            return False
        print("✓ InfluxDB connection successful")
    
    # Check buckets
    try:
        buckets_to_check = ['PowerLogger_raw', 'PowerLogger_1m', 'PowerLogger_5m', 'PowerLogger_1h']
        missing = []
        
        for bucket in buckets_to_check:
            result = subprocess.run(['influx', 'bucket', 'find', '--name', bucket,
                                        '--host', env_vars.get('INFLUX_HOST', ''),
                                        '--token', env_vars.get('INFLUX_TOKEN', ''),
                                        '--org', env_vars.get('INFLUX_ORG', '')],
                                        capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✓ Bucket {bucket} exists")
            else:
                print(f"✗ Bucket {bucket} missing")
                missing.append(bucket)
        
        if missing:
            print(f"❌ ERROR: Not all PowerLogger buckets exist in InfluxDB!")
            print(f"Missing {len(missing)} of 4 buckets.")
            return False
        else:
            for bucket in buckets_to_check:
                print(f"✓ {bucket} exists")
            print("  • PowerLogger_raw")
                print("  • PowerLogger_1m")
                print("  • PowerLogger_5m")
                print("  • PowerLogger_1h")
            return True
        except Exception as e:
            print(f"❌ ERROR: {e}")
            return False
    
    print("")
    if all_ok:
        print("==================== Setup Complete ====================")
        print("")
        print("Summary:")
        print("  • .env file: configured")
        print("  • Python environment: set up")
        print("  • Python packages: installed")
        print("  • InfluxDB CLI: installed")
        print("  • InfluxDB connection: working")
        print("  • InfluxDB buckets: created")
        print("")
        print("You can now start PowerLogger with:")
        print("  source venv/bin/activate")
        print("  python3 power_pulse.py")
    else:
        print("Setup incomplete. Please fix the errors above.")
        import sys
        sys.exit(1)

if __name__ == "__main__":
    main()