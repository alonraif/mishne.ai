"""Create the first platform administrator, from a shell on the machine.

    python -m mishne.admin.bootstrap --email you@example.com --name "Your Name"

There is no sign-up form for the back-office and there is not going to be one:
the first credential that can see every tenant should require shell access to
the box, not a URL. After this, further admins are created by an existing admin
through `POST /admin/v1/admins`.

The password is read from a prompt, never from an argument — an argument is in
the shell history and in `ps` output for as long as the command runs.

## Getting back in

`--reset-password` sets a new password on an administrator who already exists.
Without it there was no way back in at all: the command refused a duplicate
email, the back-office has no reset flow on purpose, and so a forgotten
password meant inventing a second address for the same person. The password is
the only thing it changes — the id, the action log and everything attributed to
them stay where they are, which is the reason to reset rather than to delete
and recreate.

## `--ensure`, and why it is local-only

`--ensure` is the idempotent form `dev.sh` calls: create the administrator if
there is not one, do nothing if there is, and take the password from
`ADMIN_BOOTSTRAP_PASSWORD` rather than a prompt. That is a real weakening of
the rule above, so it is refused outside `environment=local` — a deployment
gets the prompt, always, and there is no flag that changes it.

The trade it makes is worth stating. On a laptop the alternative was not a
stronger secret, it was a *ritual*: choose a password, forget it, run this
again. The credential that comes out of `--ensure` protects a back-office bound
to loopback holding fixture data, and being able to write it down once in
`.env` is what makes the back-office survive a reboot and a test run.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import sqlalchemy as sa

from ..auth import passwords
from ..config import get_settings, load_env_file
from ..db import models as m
from . import actions, auth
from .db import bypasses_rls, connected_as, transaction


def _password(from_env: bool) -> str | None:
    """The new password, typed twice — or read from the environment for `--ensure`."""
    if from_env:
        password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
        if not password:
            print(
                "--ensure needs ADMIN_BOOTSTRAP_PASSWORD set. Put it in "
                "apps/api/.env and it survives a reboot and a test run.",
                file=sys.stderr,
            )
            return None
        return password

    password = getpass.getpass("password: ")
    if password != getpass.getpass("again: "):
        print("those did not match.", file=sys.stderr)
        return None
    return password


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description="Create a platform administrator.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument(
        "--reset-password", action="store_true",
        help="set a new password on an administrator who already exists")
    parser.add_argument(
        "--ensure", action="store_true",
        help="create only if absent, taking the password from "
             "ADMIN_BOOTSTRAP_PASSWORD. Local environments only.")
    args = parser.parse_args(argv)

    if args.ensure and get_settings().environment != "local":
        print(
            f"--ensure takes the password from the environment and this is "
            f"{get_settings().environment!r}. Run it without --ensure and type "
            "one.",
            file=sys.stderr,
        )
        return 2

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
        first = s.execute(sa.select(sa.func.count()).select_from(admins)).scalar() or 0

    if existing is not None:
        if args.ensure:
            print(f"{email} is already an administrator.")
            return 0
        if not args.reset_password:
            print(
                f"{email} is already an administrator. Use --reset-password to "
                "set a new password for them.",
                file=sys.stderr,
            )
            return 1

    if args.reset_password and existing is None:
        print(f"{email} is not an administrator.", file=sys.stderr)
        return 1

    password = _password(args.ensure)
    if password is None:
        return 1

    if existing is not None:  # --reset-password, checked above
        try:
            with transaction() as s:
                auth.set_password(s, existing[0], password)
                actions.record(
                    s,
                    actions.ADMIN_PASSWORD_RESET,
                    admin_id=existing[0],
                    target_type="admin",
                    target_id=existing[0],
                    reason="reset from the shell",
                    detail={"email": email},
                )
        except passwords.WeakPassword as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"new password set for {email} ({existing[0]}).")
        return 0

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
