"""Set (or change) the Program Lead's admin username and password, or, with --grader, the
separate view-only login for course graders.

Run it yourself in a terminal; the password is typed at a hidden prompt and only its scrypt
hash is saved, to .env in the project folder (which git ignores):

    .venv/bin/python -m scripts.set_admin_password            # Program Lead
    .venv/bin/python -m scripts.set_admin_password --grader   # view-only grader login

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
    grader = "--grader" in sys.argv[1:]
    prefix = "GRADER" if grader else "ADMIN"
    who = "Grader" if grader else "Admin"
    username = input(f"{who} username (e.g. {'grader' if grader else 'mnghiem'}): ").strip().lower()
    if not re.fullmatch(r"[a-z0-9._@-]{3,64}", username):
        sys.exit("Username: 3-64 characters, letters, numbers, . _ @ - only.")
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.is_file() else []
    other = next((ln.split("=", 1)[1].strip().strip("'\"").lower() for ln in lines
                  if re.match(rf"\s*{'ADMIN' if grader else 'GRADER'}_USERNAME\s*=", ln)), "")
    if other and other == username:
        sys.exit("That username is already used by the other admin login. Pick a different one.")
    pw = getpass.getpass(f"New {who.lower()} password (min 12 characters): ")
    if len(pw) < 12:
        sys.exit("Password must be at least 12 characters.")
    if getpass.getpass("Repeat password: ") != pw:
        sys.exit("Passwords didn't match. Nothing was changed.")

    lines = _set(lines, f"{prefix}_USERNAME", username)
    # Single quotes: the hash contains '$', which dotenv would otherwise try to expand.
    lines = _set(lines, f"{prefix}_PASSWORD_HASH", f"'{hash_password(pw)}'")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(0o600)
    print(f"Saved {who.lower()} '{username}' to {ENV_PATH}. Restart the server to apply it.")


if __name__ == "__main__":
    main()
