"""The back-office credential, and keeping it.

There is no sign-up for the back-office and no password reset in the product,
on purpose — so the shell command that makes an administrator is the only way
in, and how it behaves decides whether the back-office is usable on a laptop.

Two things it got wrong, both of which read as "the back-office keeps
forgetting me" rather than as anything a person would think to file:

* the test suite deleted every platform administrator to get a clean slate, so
  `pytest -q` — the command CLAUDE.md tells you to run — destroyed the login;
* a duplicate email was refused outright, so a forgotten password had no
  remedy short of inventing a second address for the same person.

`conftest._real_admins` guards the first. This module covers the second, and
the local-only escape hatch that lets `dev.sh` keep a login without anybody
remembering to make one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("sqlalchemy")

from mishne.admin import bootstrap  # noqa: E402


def test_ensure_is_refused_outside_local(monkeypatch, capsys):
    """The whole point of `--ensure` is a password that is not typed.

    That is a defensible trade on a loopback-bound back-office full of fixture
    data and nowhere else, so the check is on the environment and there is no
    flag that overrides it. Asserted before any database call, so it holds even
    when nothing is reachable.
    """
    class Staging:
        environment = "staging"

    monkeypatch.setattr(bootstrap, "get_settings", lambda: Staging())
    monkeypatch.setattr(bootstrap, "bypasses_rls", lambda: pytest.fail(
        "the environment check must not need a database"))

    code = bootstrap.main(["--email", "ops@example.com", "--ensure"])

    assert code == 2
    assert "staging" in capsys.readouterr().err


def test_ensure_needs_the_password_to_be_somewhere(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD", raising=False)
    assert bootstrap._password(from_env=True) is None
    assert "ADMIN_BOOTSTRAP_PASSWORD" in capsys.readouterr().err


def test_a_typed_password_has_to_be_typed_twice(monkeypatch, capsys):
    typed = iter(["one-long-passphrase", "a-different-one"])
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda _: next(typed))

    assert bootstrap._password(from_env=False) is None
    assert "did not match" in capsys.readouterr().err


def test_the_prompt_is_still_the_default(monkeypatch):
    """`--ensure` is opt-in; nothing else reads the environment.

    Worth pinning: the value of the rule in the module docstring is that it has
    no exceptions people can stumble into, and an `ADMIN_BOOTSTRAP_PASSWORD`
    left in a shell would otherwise silently take over the interactive path.
    """
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "from-the-environment")
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda _: "typed-in")

    assert bootstrap._password(from_env=False) == "typed-in"
