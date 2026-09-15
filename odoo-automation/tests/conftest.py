import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
FIXTURES = Path(__file__).parent / "fixtures"


import os

import pytest


@pytest.fixture(autouse=True)
def _clean_database():
    """When the suite is pointed at PostgreSQL (DATABASE_URL), give each test an empty
    schema.  SQLite tests already get a fresh file from tmp_path, so this does nothing there.

        DATABASE_URL=postgresql://... python -m pytest -q
    """
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith(("postgres://", "postgresql://")):
        import psycopg
        with psycopg.connect(url) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
            conn.commit()
    yield
