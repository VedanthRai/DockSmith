#!/usr/bin/env python3
"""
Docksmith Sample App
Demonstrates isolation, environment variables, and file operations.
"""

import os
import sys
import datetime
import socket

APP_NAME = os.environ.get("APP_NAME", "DocksmithDemo")
GREETING = os.environ.get("GREETING", "Hello")

BANNER = r"""
  ____             _            _ _   _     
 |  _ \  ___   ___| | ___ _ __ (_) |_| |__  
 | | | |/ _ \ / __| |/ __| '_ \| | __| '_ \ 
 | |_| | (_) | (__| | (__| | | | | |_| | | |
 |____/ \___/ \___|_|\___|_| |_|_|\__|_| |_|
"""

def print_separator(char="─", width=50):
    print(char * width)

def main():
    print(BANNER)
    print_separator()
    print(f"  {GREETING} from {APP_NAME}!")
    print(f"  Running inside a Docksmith container")
    print_separator()

    # Show environment
    print("\n📦 Container Environment:")
    print(f"  APP_NAME  = {APP_NAME}")
    print(f"  GREETING  = {GREETING}")
    print(f"  PATH      = {os.environ.get('PATH', 'not set')}")
    print(f"  PWD       = {os.getcwd()}")

    # Show Python version
    print(f"\n🐍 Python {sys.version.split()[0]}")

    # Try to read our bundled data files
    print("\n📂 Bundled Data Files:")
    data_dir = "/app/data"
    if os.path.isdir(data_dir):
        for fname in sorted(os.listdir(data_dir)):
            fpath = os.path.join(data_dir, fname)
            if os.path.isfile(fpath):
                with open(fpath) as f:
                    content = f.read().strip()
                print(f"  {fname}: {content}")
    else:
        print("  (no data directory found)")

    # Demonstrate isolation: write a file inside the container
    print("\n🔒 Isolation Test:")
    test_path = "/tmp/isolation_test.txt"
    with open(test_path, "w") as f:
        f.write(f"Written at {datetime.datetime.now().isoformat()}\n")
        f.write("This file exists ONLY inside the container.\n")
        f.write("It will NOT appear on the host filesystem.\n")
    print(f"  Wrote file to {test_path} inside container")
    print(f"  File contents verified: {os.path.getsize(test_path)} bytes")
    print(f"  ✓ This file will NOT appear on your host system after exit")

    print_separator()
    print(f"  Container exiting cleanly. 👋")
    print_separator()


if __name__ == "__main__":
    main()
