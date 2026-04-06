"""Deferred seam for future live mission attach support."""

from __future__ import annotations


class LiveMissionAttachService:
    """Reserved for a future workflow that attaches the harness to an already-running mission."""

    @property
    def status(self) -> str:
        return "live-mission-attach-deferred"

    def attach(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError("Live mission attach is deferred in this pass.")
