"""Start EdgeScribe:  python run.py   ->  http://127.0.0.1:8765
Check this machine:  python run.py --doctor"""
import sys

from edgescribe.server import main

if __name__ == "__main__":
    sys.exit(main())
