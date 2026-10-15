"""Generate local secrets without printing them or overwriting an existing configuration."""

import os
import secrets
from pathlib import Path

from common import ROOT

target = ROOT / ".env"
if target.exists():
    raise SystemExit(".env already exists; edit it directly. No files were changed.")
text = (ROOT / ".env.example").read_text()
for key in ("WEBHOOK_SECRET", "INTERNAL_API_KEY", "N8N_ENCRYPTION_KEY", "POSTGRES_PASSWORD"):
    text = text.replace(f"{key}=generate-with-scripts-configure", f"{key}={secrets.token_hex(32)}")
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(text)
Path(ROOT / ".local").mkdir(exist_ok=True)
print("Created .env with private generated secrets. Next: docker compose up -d --build")
