"""Pipeline orchestrator.

Runs the five stages in order and reports progress through the same six SSE
events the scaffold already streams: parse, split, tag, schedule, budget, qa.

Design rule: **a stage failing degrades the document, it does not fail the
run.** A breakdown with no continuity flags is still a useful breakdown. A
breakdown with one untagged scene is still worth reading. The only
unrecoverable failure is being unable to find any scenes at all, and even
that returns a document explaining why rather than a stack trace.

    from app.agents.pipeline import run_pipeline

    doc = run_pipeline(text, source_format="fountain", on_progress=emit)
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
from typing import Any, Callable, Protocol

from .budget import estimate_budget
from .continuity import check_continuity
from .element_tagger import tag_elements
from .scene_parser import split_scenes
from .scheduler import build_schedule

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, str, str, int], None]

STAGES = ("parse", "split", "tag", "schedule", "budget", "qa")

#: Percentage complete at the *end* of each stage. Tagging dominates wall
#: clock because it is the only per-scene model call, so it owns the widest
#: band — a progress bar that sits at 30% for twenty seconds looks frozen.
STAGE_END_PCT = {
    "parse": 8,
    "split": 18,
    "tag": 70,
    "schedule": 80,
    "budget": 88,
    "qa": 100,
}


class _NullProgress:
    def __call__(self, *args: Any, **kwargs: Any) -> None:
        return None


def _validate(doc: dict[str, Any]) -> list[str]:
    """Validate against the committed schema. Returns human-readable errors.

    Runs on every breakdown, not just in development. Schema drift between
    the agents and the contract is the failure this project is most exposed
    to, and it is silent unless something looks for it.
    """
    schema_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "schema"
        / "scene-element.schema.json"
    )
    if not schema_path.exists():
        log.warning("Schema not found at %s; skipping validation", schema_path)
        return []

    try:
        import jsonschema
    except ImportError:
        log.warning("jsonschema not installed; skipping validation")
        return []

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors


def run_pipeline(
    text: str,
    *,
    source_format: str = "fountain",
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Run the full breakdown.

    Args:
        text: Raw screenplay text.
        source_format: One of the schema's source_format enum values.
        on_progress: Called as (stage, status, message, pct). `status` is
            "running", "complete" or "error".

    Returns:
        A document conforming to schema/scene-element.schema.json.
    """
    emit: ProgressFn = on_progress or _NullProgress()
    started = time.monotonic()
    warnings: list[dict[str, Any]] = []

    # --- parse ---------------------------------------------------------------
    emit("parse", "running", "Reading screenplay", 2)
    parsed = split_scenes(text, source_format=source_format)
    warnings.extend(parsed.warnings)

    if not parsed.scenes:
        emit("parse", "error", "No scene headings found", STAGE_END_PCT["parse"])
        return {
            "script": parsed.script,
            "scenes": [],
            "warnings": warnings
            or [
                {
                    "code": "no_scenes_found",
                    "message": "No scene headings were found in this file.",
                }
            ],
        }

    emit(
        "parse",
        "complete",
        f"{parsed.script['title']} — {parsed.script['total_pages']} pages",
        STAGE_END_PCT["parse"],
    )

    # --- split ---------------------------------------------------------------
    emit("split", "running", "Splitting scenes", STAGE_END_PCT["parse"] + 4)
    count = len(parsed.scenes)
    emit(
        "split",
        "complete",
        f"{count} scene{'s' if count != 1 else ''} found",
        STAGE_END_PCT["split"],
    )

    # --- tag -----------------------------------------------------------------
    emit(
        "tag",
        "running",
        f"Tagging production elements across {count} scenes",
        STAGE_END_PCT["split"] + 5,
    )
    try:
        scenes = tag_elements(parsed.scenes)
    except Exception as exc:  # noqa: BLE001
        log.exception("Tagging stage failed wholesale")
        warnings.append(
            {
                "code": "tagging_unavailable",
                "message": (
                    f"Element tagging could not run ({exc}). Scenes are listed "
                    "with sluglines and lengths but no tagged elements."
                ),
            }
        )
        scenes = [s.to_schema() for s in parsed.scenes]

    tagged = sum(len(s.get("elements") or []) for s in scenes)
    low_confidence = [
        s["scene_number"] for s in scenes if (s.get("confidence") or 1.0) < 0.6
    ]
    if low_confidence:
        warnings.append(
            {
                "code": "low_confidence_scenes",
                "message": (
                    "The tagger reported low confidence on scene(s) "
                    f"{', '.join(low_confidence)}. Review before scheduling."
                ),
            }
        )
    emit("tag", "complete", f"{tagged} elements tagged", STAGE_END_PCT["tag"])

    # --- schedule ------------------------------------------------------------
    emit("schedule", "running", "Grouping scenes into shooting days", STAGE_END_PCT["tag"] + 4)
    try:
        schedule = build_schedule(scenes)
    except Exception as exc:  # noqa: BLE001
        log.exception("Scheduler failed")
        warnings.append(
            {"code": "schedule_unavailable", "message": f"Scheduling failed: {exc}"}
        )
        schedule = []
    moves = sum(1 for d in schedule if d.get("company_move"))
    emit(
        "schedule",
        "complete",
        f"{len(schedule)} shooting day{'s' if len(schedule) != 1 else ''}"
        + (f", {moves} company move{'s' if moves != 1 else ''}" if moves else ""),
        STAGE_END_PCT["schedule"],
    )

    # --- budget --------------------------------------------------------------
    emit("budget", "running", "Estimating cost", STAGE_END_PCT["schedule"] + 3)
    try:
        budget = estimate_budget(scenes, schedule)
    except Exception as exc:  # noqa: BLE001
        log.exception("Budget failed")
        warnings.append(
            {"code": "budget_unavailable", "message": f"Budget estimate failed: {exc}"}
        )
        budget = None
    if budget:
        span = (
            sum(i["estimate_low"] for i in budget["line_items"]),
            sum(i["estimate_high"] for i in budget["line_items"]),
        )
        message = f"${span[0]:,.0f} – ${span[1]:,.0f} estimated"
    else:
        message = "Budget unavailable"
    emit("budget", "complete", message, STAGE_END_PCT["budget"])

    # --- qa ------------------------------------------------------------------
    emit("qa", "running", "Checking continuity across scenes", STAGE_END_PCT["budget"] + 4)
    flags = check_continuity(scenes, schedule)
    critical = sum(1 for f in flags if f["severity"] == "critical")
    emit(
        "qa",
        "complete",
        f"{len(flags)} continuity note{'s' if len(flags) != 1 else ''}"
        + (f", {critical} critical" if critical else ""),
        STAGE_END_PCT["qa"],
    )

    # --- assemble ------------------------------------------------------------
    doc: dict[str, Any] = {"script": parsed.script, "scenes": scenes}
    if schedule:
        doc["schedule"] = schedule
    if budget:
        doc["budget"] = budget

    errors = _validate(doc)
    if errors:
        log.error("Breakdown violates schema (%d error(s)):", len(errors))
        for err in errors[:10]:
            log.error("  %s", err)
        warnings.append(
            {
                "code": "schema_violation",
                "message": (
                    f"{len(errors)} schema violation(s) — the breakdown is "
                    "shown but may render incorrectly. First: " + errors[0]
                ),
            }
        )

    if warnings:
        doc["warnings"] = warnings

    log.info(
        "Breakdown complete in %.1fs: %d scenes, %d elements, %d days",
        time.monotonic() - started,
        len(scenes),
        tagged,
        len(schedule),
    )
    return doc
