import sqlite3
from pathlib import Path
from typing import Any

import pytest

SHARED_DB = (Path(__file__).resolve().parent.parent / "data" / "listings.db").resolve()


@pytest.fixture(autouse=True)
def _shoparide_cache_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Shop a Ride caches units under ~/.cache by default: keep tests out of it."""
    from dirtbike_tracker.infrastructure import shoparide

    monkeypatch.setattr(shoparide, "CACHE_DIR", tmp_path / "shoparide-cache")
    shoparide._units.cache_clear()


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
