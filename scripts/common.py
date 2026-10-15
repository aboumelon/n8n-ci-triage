import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def local_env() -> dict[str, str]:
    values = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}
