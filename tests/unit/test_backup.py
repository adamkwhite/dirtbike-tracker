import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.application.cli import cli
from dirtbike_tracker.application.ports import App
from dirtbike_tracker.infrastructure import backup as backup_module
from dirtbike_tracker.infrastructure.backup import BackupError, backup_offsite
from dirtbike_tracker.infrastructure.storage import connect, init_db

from .conftest import PushSpy


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "listings.db"
    init_db(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO listings (source, external_id, url, title, price, first_seen, last_seen)"
            " VALUES ('kijiji', '1', 'u', 't', 2500, '2026-10-11', '2026-10-11')"
        )
    return path


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    (d / "notified_ids.txt").write_text("kijiji:1")
    return d


class FakeRclone:
    """Records uploads into a dict; `size` reports what was 'uploaded'."""

    def __init__(self, size_skew: int = 0) -> None:
        self.objects: dict[str, bytes] = {}
        self.size_skew = size_skew

    def __call__(self, argv: list[str], **_: object) -> SimpleNamespace:
        if argv[1] == "copyto":
            self.objects[argv[3]] = Path(argv[2]).read_bytes()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1] == "size":
            size = len(self.objects[argv[3]]) + self.size_skew
            return SimpleNamespace(returncode=0, stdout=json.dumps({"bytes": size}), stderr="")
        raise AssertionError(argv)


def test_backup_uploads_db_and_state_to_named_slot(
    db: Path, state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rclone = FakeRclone()
    monkeypatch.setattr(backup_module.subprocess, "run", rclone)

    rows, size = backup_offsite("nightly", state_dir, db_path=db, remote="r:dir")

    assert rows == 1
    uploaded = tmp_path / "out.tar.gz"
    uploaded.write_bytes(rclone.objects["r:dir/nightly.tar.gz"])
    assert size == uploaded.stat().st_size
    with tarfile.open(uploaded) as tar:
        assert sorted(tar.getnames()) == ["listings.db", "state/notified_ids.txt"]


def test_backup_refuses_empty_db(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty.db"
    init_db(empty)
    monkeypatch.setattr(backup_module.subprocess, "run", lambda *a, **k: pytest.fail("uploaded"))

    with pytest.raises(BackupError, match="0 listings"):
        backup_offsite("nightly", state_dir, db_path=empty, remote="r:dir")


def test_backup_detects_remote_size_mismatch(
    db: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup_module.subprocess, "run", FakeRclone(size_skew=-1))

    with pytest.raises(BackupError, match="size mismatch"):
        backup_offsite("nightly", state_dir, db_path=db, remote="r:dir")


def test_backup_command_alerts_on_failure(
    monkeypatch: pytest.MonkeyPatch, app: App, sent: PushSpy
) -> None:
    def failing_backup(slot: str, **_: object) -> tuple[int, int]:
        raise BackupError("rclone copyto failed: token expired")

    monkeypatch.setattr(cli_module, "backup_offsite", failing_backup)  # backup: outside #74's ports

    result = CliRunner().invoke(cli, ["backup", "--slot", "weekly"], obj=app)

    assert result.exit_code == 1
    assert "token expired" in result.output
    assert [p["title"] for p in sent] == ["dirtbike backup FAILED (weekly)"]
