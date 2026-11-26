"""Stage runner with resume and approval-gate support."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Protocol

from yt_video.logging import get_logger
from yt_video.pipeline.context import RunContext

logger = get_logger()


class StageOutcome(Enum):
    DONE = "done"
    WAIT = "wait"


class Stage(Protocol):
    name: str

    def run(self, ctx: RunContext) -> StageOutcome | None: ...


class Pipeline:
    def __init__(self, stages: list[Stage]) -> None:
        self.stages = stages

    def run(self, ctx: RunContext) -> Literal["done", "awaiting_approval"]:
        run = ctx.run
        if run.status == "awaiting_approval":
            run.set_status("running")
        for stage in self.stages:
            if run.is_done(stage.name):
                logger.info("stage %s: skipped (already done)", stage.name)
                continue
            logger.info("stage %s: start", stage.name)
            outcome = stage.run(ctx)
            if outcome is StageOutcome.WAIT:
                run.set_status("awaiting_approval")
                logger.info("stage %s: waiting for approval", stage.name)
                return "awaiting_approval"
            if not run.is_done(stage.name):
                run.mark_done(stage.name, run.get(stage.name))
            logger.info("stage %s: finished", stage.name)
        run.set_status("done")
        return "done"
