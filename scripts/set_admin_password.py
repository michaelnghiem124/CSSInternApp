"""Set (or change) the Program Lead's admin username and password.

Run it yourself in a terminal; the password is typed at a hidden prompt and only its scrypt
hash is saved, to .env in the project folder (which git ignores):

    .venv/bin/python -m scripts.set_admin_password

Restart the server afterwards. Changing the password signs out any open admin session only
when SESSION_SECRET also changes; to force that, delete data/.session_secret too.
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

from backend.auth import hash_password

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _set(lines: list[str], key: str, value: str) -> list[str]:
    out = [ln for ln in lines if not re.match(rf"\s*{key}\s*=", ln)]
    out.append(f"{key}={value}")
    return out


def main() -> None:
    username = input("Admin username (e.g. mnghiem): ").strip().lower()
    if not re.fullmatch(r"[a-z0-9._@-]{3,64}", username):
        sys.exit("Username: 3-64 characters, letters, numbers, . _ @ - only.")
    pw = getpass.getpass("New admin password (min 12 characters): ")
    if len(pw) < 12:
        sys.exit("Password must be at least 12 characters.")
    if getpass.getpass("Repeat password: ") != pw:
        sys.exit("Passwords didn't match. Nothing was changed.")

    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.is_file() else []
    lines = _set(lines, "ADMIN_USERNAME", username)
    # Single quotes: the hash contains '$', which dotenv would otherwise try to expand.
    lines = _set(lines, "ADMIN_PASSWORD_HASH", f"'{hash_password(pw)}'")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(0o600)
    print(f"Saved admin '{username}' to {ENV_PATH}. Restart the server to apply it.")


if __name__ == "__main__":
    main()
