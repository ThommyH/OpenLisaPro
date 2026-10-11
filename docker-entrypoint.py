"""Fix volume ownership before starting the app as its unprivileged user."""

from __future__ import annotations

import os

APP_UID = 10001
APP_GID = 10001
DATA_DIR = os.environ.get("LISA_PRO_DATA", "/data")

os.makedirs(DATA_DIR, exist_ok=True)
for path in (DATA_DIR, os.path.join(DATA_DIR, "control.json"), os.path.join(DATA_DIR, "control.tmp")):
    if os.path.exists(path):
        os.chown(path, APP_UID, APP_GID)

os.setgid(APP_GID)
os.setuid(APP_UID)
os.execv("/app/.venv/bin/lisa-pro-ui", ["lisa-pro-ui"])
