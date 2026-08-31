"""The platform back-office.

A separate FastAPI application, on a separate port, on a separate database
connection, with a separate credential. None of that is ceremony: it is the
reason the customer-facing API never acquires a cross-tenant code path.

See migrations/versions/0009_platform_administration.py for the schema and the
reasoning, and `main.py` for the two checks that stop a misconfigured admin
process from starting.
"""
