"""Joining by invitation: the link, what it grants, and what it must not.

An invitation is a membership grant with a delay in the middle, and membership
is the whole of the access model — an organisation holds unreleased footage and
there are no per-project ACLs. So the interesting tests here are not that it
works; they are the ways a link could grant more than the person who sent it
intended.

* The address comes from the invitation and never from the request body. A link
  sent to one person must not be a way for whoever holds it to create an
  account under any address at all.
* One use, and expiry. A forwarded email is not a permanent way in.
* Four failure modes, one answer. Expired, revoked, spent and never-existed are
  all 404: distinct answers tell somebody guessing which guess was closest.
* The token is never stored, so a leaked database is not a set of keys.
* A failure to send undoes the invitation. A row nobody was told about is a
  link that will never be used, in a table an owner reads as "these people have
  been asked".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, requires_schema  # noqa: E402

pytestmark = requires_schema

PASSWORD = "a properly long passphrase"
INVITEE = "newcomer@example.test"


class Recorder:
    """A mailer that keeps what it was given. Not a mock of sending — the
    console mailer is the real local implementation; this is a test double for
    reading the link back out."""

    name = "recorder"

    def __init__(self, explode: bool = False):
        self.sent: list = []
        self.explode = explode

    def send(self, message) -> None:
        if self.explode:
            from mishne.mail import MailError

            raise MailError("SMTPServerDisconnected")
        self.sent.append(message)

    @property
    def token(self) -> str:
        link = re.search(r"/invite/([A-Za-z0-9_\-]+)", self.sent[-1].body)
        assert link, f"no invitation link in the email: {self.sent[-1].body!r}"
        return link.group(1)


@pytest.fixture
def mailer(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr("mishne.routers.org.get_mailer", lambda _s=None: recorder)
    return recorder


@pytest.fixture(autouse=True)
def clean_invitees(owner):
    """Whoever these tests let in, removed afterwards."""
    def _clean():
        with owner.begin() as conn:
            conn.execute(sa.text("DELETE FROM invitations WHERE org_id = :o"),
                         {"o": ORG})
            conn.execute(
                sa.text("DELETE FROM users WHERE lower(email) = :e"), {"e": INVITEE}
            )
    _clean()
    yield
    _clean()


def _invite(http, mailer, role: str = "member", email: str = INVITEE):
    return http.post("/v1/org/members/invite", json={"email": email, "role": role})


# ── sending one ────────────────────────────────────────────────────────────


@requires_schema
def test_an_owner_invites_and_the_link_is_emailed(api, mailer):
    http, _ = api

    resp = _invite(http, mailer)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == INVITEE and body["role"] == "member"
    # The response never carries the token: it is not stored and not needed by
    # the person who sent it.
    assert "token" not in resp.text
    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == INVITEE
    assert "/invite/" in mailer.sent[0].body


@requires_schema
def test_the_token_is_not_in_the_database(api, owner, mailer):
    """A leaked database must not be a set of keys to every organisation."""
    http, _ = api
    _invite(http, mailer)

    with owner.begin() as conn:
        stored = conn.execute(
            sa.text("SELECT token_hash FROM invitations WHERE org_id = :o"),
            {"o": ORG},
        ).scalar_one()

    assert mailer.token not in stored
    assert len(stored) == 64  # sha256, hex


@requires_schema
def test_a_viewer_cannot_invite(api, mailer, viewer_token):
    """Deciding who may see this footage is an owner's decision."""
    http, _ = api
    resp = http.post(
        "/v1/org/members/invite",
        json={"email": INVITEE, "role": "owner"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403
    assert mailer.sent == []


@requires_schema
def test_a_second_invitation_for_the_same_address_is_refused(api, mailer):
    """Two live links for one address is two ways in, and the owner should know
    one is already out there."""
    http, _ = api
    assert _invite(http, mailer).status_code == 201

    assert _invite(http, mailer).status_code == 409


@requires_schema
def test_inviting_someone_already_here_is_refused(api, mailer):
    http, _ = api
    already = http.get("/v1/org/members").json()[0]["email"]

    resp = _invite(http, mailer, email=already)

    assert resp.status_code == 409
    assert mailer.sent == []


@requires_schema
def test_a_send_that_fails_undoes_the_invitation(api, owner, monkeypatch):
    """A row nobody was told about is a link that will never be used, sitting
    in a table an owner reads as 'these people have been asked'."""
    http, _ = api
    monkeypatch.setattr("mishne.routers.org.get_mailer",
                        lambda _s=None: Recorder(explode=True))

    resp = http.post("/v1/org/members/invite",
                     json={"email": INVITEE, "role": "member"})

    assert resp.status_code == 502
    with owner.begin() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM invitations WHERE org_id = :o"),
            {"o": ORG},
        ).scalar() == 0


# ── using one ──────────────────────────────────────────────────────────────


@requires_schema
def test_the_link_shows_who_it_is_for_before_anything_is_typed(api, mailer):
    http, _ = api
    _invite(http, mailer)

    resp = http.get(f"/v1/auth/invitations/{mailer.token}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == INVITEE
    assert body["role"] == "member"
    assert body["org_name"]


@requires_schema
def test_accepting_creates_the_account_and_signs_them_in(api, owner, mailer):
    http, _ = api
    _invite(http, mailer, role="viewer")

    resp = http.post(f"/v1/auth/invitations/{mailer.token}/accept",
                     json={"name": "Newcomer", "password": PASSWORD})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["email"] == INVITEE
    # The role is the one that was offered, not one the invitee chose.
    assert body["user"]["role"] == "viewer"
    with owner.begin() as conn:
        row = conn.execute(
            sa.text("SELECT org_id, role FROM users WHERE lower(email) = :e"),
            {"e": INVITEE},
        ).one()
    assert row.org_id == ORG and row.role == "viewer"


@requires_schema
def test_the_body_cannot_choose_the_address(api, owner, mailer):
    """The whole grant, handed to the wrong identity: a link sent to one person
    must not create an account under another."""
    http, _ = api
    _invite(http, mailer)

    http.post(f"/v1/auth/invitations/{mailer.token}/accept",
              json={"name": "Someone Else", "password": PASSWORD,
                    "email": "attacker@example.test", "role": "owner"})

    with owner.begin() as conn:
        emails = conn.execute(
            sa.text("SELECT lower(email) FROM users WHERE org_id = :o"), {"o": ORG}
        ).scalars().all()
    assert INVITEE in emails
    assert "attacker@example.test" not in emails


@requires_schema
def test_an_invitation_is_used_once(api, mailer):
    http, _ = api
    _invite(http, mailer)
    token = mailer.token
    assert http.post(f"/v1/auth/invitations/{token}/accept",
                     json={"name": "First", "password": PASSWORD}).status_code == 201

    again = http.post(f"/v1/auth/invitations/{token}/accept",
                      json={"name": "Second", "password": PASSWORD})

    assert again.status_code == 404


@requires_schema
def test_an_expired_invitation_is_no_longer_valid(api, owner, mailer):
    """A forwarded email is not a permanent way in."""
    http, _ = api
    _invite(http, mailer)
    with owner.begin() as conn:
        conn.execute(
            sa.text("UPDATE invitations SET expires_at = now() - interval '1 day' "
                    "WHERE org_id = :o"), {"o": ORG})

    assert http.get(f"/v1/auth/invitations/{mailer.token}").status_code == 404


@requires_schema
def test_a_revoked_invitation_is_no_longer_valid_and_the_row_stays(
    api, owner, mailer
):
    """An invitation thought better of is the same kind of record as one
    accepted; deleting it loses the fact that somebody was once asked."""
    http, _ = api
    invitation_id = _invite(http, mailer).json()["id"]

    assert http.delete(f"/v1/org/invitations/{invitation_id}").status_code == 204

    assert http.get(f"/v1/auth/invitations/{mailer.token}").status_code == 404
    with owner.begin() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM invitations WHERE org_id = :o"),
            {"o": ORG},
        ).scalar() == 1


@requires_schema
def test_a_token_that_names_nothing_is_the_same_404(api):
    """Four failure modes and one answer: a distinct reply per case tells
    somebody guessing which guess was closest."""
    http, _ = api
    assert http.get("/v1/auth/invitations/not-a-real-token").status_code == 404


@requires_schema
def test_a_weak_password_is_refused_before_the_invitation_is_spent(api, mailer):
    """Otherwise the one use is burned on a rejected password and the person
    has to be invited again."""
    http, _ = api
    _invite(http, mailer)

    assert http.post(f"/v1/auth/invitations/{mailer.token}/accept",
                     json={"name": "N", "password": "short"}).status_code == 422
    assert http.get(f"/v1/auth/invitations/{mailer.token}").status_code == 200


# ── the list ───────────────────────────────────────────────────────────────


@requires_schema
def test_pending_invitations_are_listed_and_accepted_ones_are_not(api, mailer):
    http, _ = api
    _invite(http, mailer)
    assert [i["email"] for i in http.get("/v1/org/invitations").json()] == [INVITEE]

    http.post(f"/v1/auth/invitations/{mailer.token}/accept",
              json={"name": "Newcomer", "password": PASSWORD})
    # Accepting signs the invitee in, and this client now holds their cookie —
    # which is the flow working. The owner has to be back to read the list.
    http.cookies.clear()

    assert http.get("/v1/org/invitations").json() == []


@requires_schema
def test_accepting_replaces_whoever_was_signed_in(api, mailer):
    """The accept response sets a session cookie, so a browser that was signed
    in as somebody else is now signed in as the invitee. Worth pinning: the
    alternative — two identities in one browser — is how a person uploads
    footage into the wrong organisation."""
    http, _ = api
    _invite(http, mailer, role="viewer")

    http.post(f"/v1/auth/invitations/{mailer.token}/accept",
              json={"name": "Newcomer", "password": PASSWORD})

    assert http.get("/v1/auth/me").json()["user"]["email"] == INVITEE


@requires_schema
def test_another_tenants_invitations_are_invisible(api, owner, mailer,
                                                   other_tenant):
    """The policy escape is for one row and one token; it is not a way to read
    the table."""
    http, _ = api
    _invite(http, mailer)

    listed = http.get("/v1/org/invitations").json()

    assert all(i["email"] == INVITEE for i in listed)
    assert len(listed) == 1
