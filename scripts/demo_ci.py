"""Opt-in, deliberately failing CI examples. Never imported by the normal test suite."""

import argparse
import importlib
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("scenario", choices=("dependency", "test", "lint", "success"))
scenario = parser.parse_args().scenario
if scenario == "dependency":
    importlib.import_module("intentionally_missing_ci_dependency")
elif scenario == "test":
    assert 2 + 2 == 5, "Deliberate demonstration of a failing assertion"
elif scenario == "lint":
    target = Path(".local/lint_demo.py")
    target.parent.mkdir(exist_ok=True)
    target.write_text("import os\n")
    raise SystemExit(subprocess.call(["ruff", "check", "--output-format", "concise", str(target)]))
else:
    print("The controlled demo succeeded.")
