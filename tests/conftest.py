import sqlite3
from pathlib import Path
from typing import Any

import pytest

SHARED_DB = (Path(__file__).resolve().parent.parent / "data" / "listings.db").resolve()


@pytest.fixture(autouse=True)
def _never_open_shared_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """data/ is shared with prod (CLAUDE.md): fail any test that opens its DB, reads included.

    A read leak (DB_PATH bound at import) once got past an mtime check, hence opens, not writes.
    """
    real_connect = sqlite3.connect

    def guarded_connect(path: str | Path, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        assert Path(str(path)).resolve() != SHARED_DB, "test opened the shared data/ DB"
        conn: sqlite3.Connection = real_connect(path, *args, **kwargs)
        return conn

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
