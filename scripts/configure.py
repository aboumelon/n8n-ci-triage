"""Generate local secrets without printing them or overwriting an existing configuration."""

import argparse
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from common import ROOT


def https_origin(value: str) -> str:
    """Accept an HTTPS origin without credentials, a path, or shell/env syntax."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use an HTTPS origin, such as https://app.style.dev"
        ) from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for char in parsed.netloc)
        or port is not None
    ):
        raise argparse.ArgumentTypeError("Use an HTTPS hostname, such as https://app.style.dev")
    return "https://" + parsed.hostname


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--public-url",
    type=https_origin,
    help="n8n HTTPS origin behind a trusted reverse proxy; enables public port bindings",
)
args = parser.parse_args()
target = ROOT / ".env"
if target.exists():
    raise SystemExit(".env already exists; edit it directly. No files were changed.")
text = (ROOT / ".env.example").read_text()
for key in ("WEBHOOK_SECRET", "INTERNAL_API_KEY", "N8N_ENCRYPTION_KEY", "POSTGRES_PASSWORD"):
    text = text.replace(f"{key}=generate-with-scripts-configure", f"{key}={secrets.token_hex(32)}")
if args.public_url:
    settings = {
        "HOST_BIND": "0.0.0.0",
        "N8N_HOST": urlsplit(args.public_url).hostname,
        "N8N_PROTOCOL": "https",
        "N8N_PUBLIC_URL": args.public_url,
        "N8N_SECURE_COOKIE": "true",
    }
    text = (
        "\n".join(
            f"{key}={settings[key]}" if (key := line.split("=", 1)[0]) in settings else line
            for line in text.splitlines()
        )
        + "\n"
    )
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(text)
Path(ROOT / ".local").mkdir(exist_ok=True)
print("Created .env with private generated secrets. Next: docker compose up -d --build")
