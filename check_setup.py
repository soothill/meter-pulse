#!/usr/bin/env python3
"""
Check if PowerLogger setup is complete.
"""

import sys

def main():
    print("Verifying PowerLogger setup...")
    print("")
    
    all_ok = True
    
    # Check .env file
    if not sys.path.exists('.env'):
        print("❌ ERROR: .env file not found!")
        print("Copy .env.example to .env and configure it first.")
        all_ok = False
        return
    
    # Check virtual environment
    if not sys.path.isdir('venv'):
        print("❌ ERROR: Python virtual environment not found!")
        print("Run 'make install' to set up Python environment.")
        all_ok = False
        return
    
    print("✓ Python virtual environment exists")
    
    # Check InfluxDB CLI
    try:
        import subprocess
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
            all_ok = False
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
        all_ok = False
        return False
    except Exception as e:
        print(f"❌ ERROR: Failed to check Python packages: {e}")
        all_ok = False
            return False
    
    # Check InfluxDB connection
    try:
        import subprocess
        from dotenv import load_dotenv
        env_vars = load_dotenv()
        host = env_vars.get('INFLUX_HOST', '')
        token = env_vars.get('INFLUX_TOKEN', '')
        org = env_vars.get('INFLUX_ORG', '')
        
        if not host or not token or not org:
            print("❌ ERROR: InfluxDB connection parameters not configured!")
            return False
        
        result = subprocess.run(['influx', 'ping', '--host', host], capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ InfluxDB connection successful")
            return True
        except Exception as e:
            print(f"❌ ERROR: Cannot connect to InfluxDB! {e}")
            return False
    
    print("✓ InfluxDB connection successful")
    
    # Check buckets
    try:
        import subprocess
        from dotenv import load_dotenv
        env_vars = load_dotenv()
        host = env_vars.get('INFLUX_HOST', '')
        token = env_vars.get('INFLUX_TOKEN', '')
        org = env_vars.get('INFLUX_ORG', '')
        
        if not host or not token or not org:
            print("❌ ERROR: InfluxDB connection parameters not configured!")
            return False
        
        buckets_to_check = ['PowerLogger_raw', 'PowerLogger_1m', 'PowerLogger_5m', 'PowerLogger_1h']
        missing = []
        
        for bucket in buckets_to_check:
            result = subprocess.run(['influx', 'bucket', 'find', '--name', bucket,
                                        '--host', host, '--token', token, '--org', org],
                                        capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✓ Bucket {bucket} exists")
            else:
                print(f"✗ Bucket {bucket} missing")
                missing.append(bucket)
        
        if missing:
            print(f"❌ ERROR: Not all PowerLogger buckets exist in InfluxDB!")
            print(f"Missing {len(missing)} of 4 buckets.")
            all_ok = False
            return False
        
        print("✓ Bucket PowerLogger_raw exists")
        print("✓ Bucket PowerLogger_1m exists")
        print("✓ Bucket PowerLogger_5m exists")
        print("✓ Bucket PowerLogger_1h exists")
        print("✓ All 4 PowerLogger buckets exist")
        return True
    except Exception as e:
        print(f"❌ ERROR: {e}")
        all_ok = False
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
        sys.exit(1)

if __name__ == "__main__":
    main()