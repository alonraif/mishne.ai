"""Column types the stock dialect does not carry."""

from __future__ import annotations

import sqlalchemy as sa


class Vector(sa.types.UserDefinedType):
    """pgvector's `vector(n)`.

    A minimal type rather than the `pgvector` package: nothing populates
    `beats.embedding` yet, so a dependency bought only to spell a DDL type would
    be one to keep in step for no return. Swap it for `pgvector.sqlalchemy` the
    day something writes a vector.
    """

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_: object) -> str:
        return f"vector({self.dim})"
