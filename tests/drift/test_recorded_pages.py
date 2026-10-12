"""Our parsers against real recorded pages: catches site drift (#28).

The pages live in tests/recorded/ (gitignored: third-party ads with personal
info, and this repo is public), so CI skips this file. `dirtbike drift` (or
`make drift`) re-records them; the daily cron runs the same check and alerts.
"""

from pathlib import Path

import pytest

from dirtbike_tracker.infrastructure.drift import problems

RECORDED = Path(__file__).resolve().parent.parent / "recorded"
if not (RECORDED / "manifest.json").exists():
    pytest.skip("no recorded pages: run `make drift`", allow_module_level=True)


def test_parsers_still_read_the_recorded_pages() -> None:
    assert problems(RECORDED) == []
