#!/usr/bin/env python3
"""
Docksmith CLI entry point.
Install this as 'docksmith' in your PATH.
"""
import sys
import os

# Add the project root to Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from docksmith.cli import main

if __name__ == "__main__":
    main()
