"""Create the first platform administrator, from a shell on the machine.

    python -m mishne.admin.bootstrap --email you@example.com --name "Your Name"

There is no sign-up form for the back-office and there is not going to be one:
the first credential that can see every tenant should require shell access to
the box, not a URL. After this, further admins are created by an existing admin
through `POST /admin/v1/admins`.

The password is read from a prompt, never from an argument — an argument is in
the shell history and in `ps` output for as long as the command runs.
"""

from __future__ import annotations

import argparse
import getpass
import sys

import sqlalchemy as sa

from ..auth import passwords
from ..config import load_env_file
from ..db import models as m
from . import actions, auth
from .db import bypasses_rls, connected_as, transaction


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description="Create a platform administrator.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args(argv)

    if not bypasses_rls():
        print(
            f"connected as {connected_as()!r}, which row-level security applies to.\n"
            "Point ADMIN_DATABASE_URL at a superuser or a role with BYPASSRLS.",
            file=sys.stderr,
        )
        return 2

    email = args.email.strip().lower()
    with transaction() as s:
        admins = m.PlatformAdmin.__table__
        existing = s.execute(
            sa.select(admins.c.id).where(admins.c.email == email)
        ).first()
        if existing is not None:
            print(f"{email} is already an administrator.", file=sys.stderr)
            return 1
        first = s.execute(sa.select(sa.func.count()).select_from(admins)).scalar() or 0

    password = getpass.getpass("password: ")
    if password != getpass.getpass("again: "):
        print("those did not match.", file=sys.stderr)
        return 1

    try:
        with transaction() as s:
            admin_id = auth.create_admin(
                s, email=email, name=args.name, password=password
            )
            actions.record(
                s,
                actions.ADMIN_CREATED,
                admin_id=admin_id,
                target_type="admin",
                target_id=admin_id,
                # Attributed to itself, because there is nobody else yet. That
                # is a fact worth being able to see in the log rather than a
                # gap in it.
                reason="bootstrap" if first == 0 else "created from the shell",
                detail={"email": email, "bootstrap": first == 0},
            )
    except passwords.WeakPassword as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"created {email} ({admin_id}).")
    if first == 0:
        print("This is the first administrator. Start the back-office with:")
        print("  uvicorn mishne.admin.main:app --host 127.0.0.1 --port 8001")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
