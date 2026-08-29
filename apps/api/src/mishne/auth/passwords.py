"""Password hashing, with the parameters stored beside the hash.

`hashlib.scrypt` from the standard library rather than argon2 or bcrypt from
PyPI. Not because it is better — argon2id is the current recommendation — but
because it is memory-hard, it is in Python itself, and a dependency that guards
credentials is one whose supply chain has to be watched forever. The encoded
form carries its own parameters, so moving to argon2 later is a new prefix and a
rehash on next login, not a migration that logs everybody out.

The encoded form:

    scrypt$n$r$p$<salt hex>$<digest hex>

Never store, log, or return anything else. A password does not appear in this
module's arguments after `hash_password` returns, and `verify` compares in
constant time — a fast negative on the first wrong byte is a timing oracle for
the hash, which is a slow but real way to forge one.
"""

from __future__ import annotations

import hashlib
import hmac
import os

#: CPU/memory cost. 2**15 is roughly 32 MiB and ~100 ms on a modern core, which
#: is the usual balance: slow enough to make offline cracking expensive, fast
#: enough that a login is not noticeably slower than the network round trip.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
DIGEST_BYTES = 32

#: scrypt allocates roughly 128 * N * r bytes and CPython refuses to exceed
#: `maxmem`. The default is far below what N above needs.
_MAXMEM = 256 * 1024 * 1024

MIN_LENGTH = 12


class WeakPassword(ValueError):
    """Refused before hashing. The message is safe to show a user."""


def check_strength(password: str) -> None:
    """The smallest rule that is worth having.

    Length only, deliberately. Composition rules (a digit, a symbol, a capital)
    are well established to push people towards `Password1!` and away from a
    passphrase, and this is a product for professionals who will use a password
    manager.
    """
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"a password must be at least {MIN_LENGTH} characters")


def hash_password(password: str) -> str:
    check_strength(password)
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=DIGEST_BYTES,
        maxmem=_MAXMEM,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify(password: str, encoded: str) -> bool:
    """Constant-time. A malformed or unknown encoding is a failed login, not a crash."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(digest_hex) // 2,
            maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))


def needs_rehash(encoded: str) -> bool:
    """Whether this hash was made with parameters we have since moved on from.

    Called on a successful login, where the plaintext is in hand for the only
    moment it ever will be.
    """
    try:
        scheme, n, r, p, _salt, _digest = encoded.split("$")
    except ValueError:
        return True
    return (scheme, int(n), int(r), int(p)) != ("scrypt", SCRYPT_N, SCRYPT_R, SCRYPT_P)
