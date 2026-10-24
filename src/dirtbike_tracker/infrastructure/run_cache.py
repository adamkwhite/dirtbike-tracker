"""Fetch a whole-inventory dealer once per watch run, failure included."""

from collections.abc import Callable, Hashable
from typing import cast


class OncePerRun[**P, R]:
    """A cache that also keeps a failure. Every model filters the same dealer inventory, so a
    run (one cron process) asks the dealer once; a dealer that refused one model's fetch
    isn't asked again for the next ~20 in the same run. Retrying it each model was 37
    requests a run to a host that had just answered 403, which kept the block going.
    `cache_clear()` forgets both (tests)."""

    def __init__(self, fetch: Callable[P, R]) -> None:
        self.fetch = fetch
        self.outcomes: dict[Hashable, tuple[R | None, Exception | None]] = {}

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        key = (args, tuple(sorted(kwargs.items())))
        if key not in self.outcomes:
            try:
                self.outcomes[key] = (self.fetch(*args, **kwargs), None)
            except Exception as exc:  # noqa: BLE001 - re-raised below, to every caller this run
                self.outcomes[key] = (None, exc)
        value, error = self.outcomes[key]
        if error is not None:
            raise error
        return cast("R", value)

    def cache_clear(self) -> None:
        self.outcomes.clear()
