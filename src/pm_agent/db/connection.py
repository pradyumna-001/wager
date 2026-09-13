"""Postgres connection helper (psycopg3).

Autocommit is OFF: callers control transaction boundaries. Postgres is the
system of record — write groups (e.g. nodes + edges) must commit together.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from pm_agent.config import get_settings


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    """Yield a psycopg3 connection from ``settings.database_url``, autocommit off."""
    with psycopg.connect(get_settings().database_url, autocommit=False) as conn:
        yield conn
