import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB = ROOT / "web"
DB_PATH = pathlib.Path(os.environ.get("EDGESCRIBE_DB", DATA / "edgescribe.db"))
HOST = "127.0.0.1"          # local only: nothing is exposed to the network
PORT = int(os.environ.get("EDGESCRIBE_PORT", 8765))

SR = 16000                  # sample rate
FRAME = 400                 # 25 ms analysis window
HOP = 160                   # 10 ms hop
N_BANDS = 16
