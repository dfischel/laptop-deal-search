"""Load config.toml and state.json from the repo root."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
STATE_PATH = ROOT / "state.json"

EMPTY_STATE = {
    "alerted": {},          # listing id -> {title, price, url, date}; never alert on these again
    "reminders_sent": [],   # reminder dates already sent
    "source_failures": {},  # source name -> consecutive failed runs
    "last_keepalive": None, # see __main__.keepalive
    "finished": False,
}


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return json.loads(json.dumps(EMPTY_STATE))
    with open(path, encoding="utf-8") as f:
        return {**json.loads(json.dumps(EMPTY_STATE)), **json.load(f)}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
