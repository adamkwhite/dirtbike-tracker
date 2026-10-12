"""Smoke test: the CLI imports every layer and registers its commands."""

from click.testing import CliRunner

from dirtbike_tracker.application.cli import cli


def test_cli_registers_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in ("scrape", "list", "stats", "report", "watch", "dashboard", "models"):
        assert command in result.output
