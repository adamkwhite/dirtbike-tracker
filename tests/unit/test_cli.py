"""CLI commands, with snapshot() stubbed so no DB is touched."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.application.cli import cli
from dirtbike_tracker.domain.scoring import MarketSnapshot


@pytest.fixture
def stub_snapshot(snap: MarketSnapshot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_module, "snapshot", lambda slug: snap)
    monkeypatch.setattr(cli_module, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(cli_module, "DASHBOARD_PATH", tmp_path / "dashboard.html")


@pytest.mark.usefixtures("stub_snapshot")
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["list", "--model", "crf150f"], "4 active listings for crf150f"),
        (["stats", "--model", "crf150f"], "Median:    $2000"),
        (["report", "--model", "crf150f"], "# CRF150F market snapshot"),
        (["report", "--model", "crf150f", "--write"], "Report written to"),
        (["dashboard", "--models", "crf150f"], "Dashboard written"),
    ],
)
def test_commands_render_snapshot(args: list[str], expected: str) -> None:
    result = CliRunner().invoke(cli, args)

    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_cli_registers_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in ("scrape", "list", "stats", "report", "watch", "dashboard", "models"):
        assert command in result.output
