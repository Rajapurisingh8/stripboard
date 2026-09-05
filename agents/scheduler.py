"""Stage 3 — the scheduler.

Groups scenes into shooting days. It **groups; it does not optimise.** That
is a deliberate scope decision: a real optimiser weighs cast availability,
daylight windows, location holds and turnaround against each other, and a
half-built one produces schedules a first AD can immediately disprove.
Grouping by location and time of day is what a human does on day one, it is
explainable, and every strip carries a rationale a human can argue with.

Deterministic on purpose. A judge can verify that eighths sum correctly and
that no scene appears twice; they cannot verify a model's arithmetic.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

EIGHTHS_PER_PAGE = 8

#: A conventional working day for a small unit. Eighths beyond this spill to
#: the next day rather than producing a day no one could shoot.
MAX_EIGHTHS_PER_DAY = 48

#: Shooting condition per time of day. DAWN and DUSK are deliberately
#: separate from NIGHT: they are hard windows of twenty or thirty minutes,
#: so they cannot be grouped inside a night block the way two NIGHT scenes
#: can. Getting this wrong produces a schedule that looks efficient and is
#: unshootable.
CONDITION = {
    "DAY": "day",
    "NIGHT": "night",
    "DAWN": "dawn",
    "DUSK": "dusk",
}

#: Conditions that require night rates, lighting packages and turnaround.
NIGHT_CONDITIONS = {"night", "dawn", "dusk"}

#: Times of day that inherit whatever the preceding scene was doing.
INHERITING_TIMES = {"CONTINUOUS", "MOMENTS LATER", "UNSPECIFIED"}


def _location_key(scene: dict[str, Any]) -> str:
    """The thing we group on: practical location, falling back to the set."""
    return (scene.get("location") or scene.get("set_name") or "UNKNOWN").upper()


def _resolve_times(scenes: list[dict[str, Any]]) -> list[str]:
    """Resolve inheriting times of day against the previous concrete scene.

    A CONTINUOUS scene is night work if the scene it continues from was.
    Without this, scene 3 of the demo script reads as neither day nor night
    and lands in the wrong unit.
    """
    resolved: list[str] = []
    last_concrete = "DAY"
    for scene in scenes:
        tod = (scene.get("time_of_day") or "UNSPECIFIED").upper()
        if tod in INHERITING_TIMES:
            resolved.append(last_concrete)
        else:
            last_concrete = tod
            resolved.append(tod)
    return resolved


def _condition(tod: str) -> str:
    """Shooting condition for a resolved time of day."""
    return CONDITION.get(tod, "day")


def build_schedule(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group tagged scenes into shooting days.

    Grouping rule, in order of precedence:
      1. Same practical location.
      2. Same shooting condition (day work vs night work) — mixing them in
         one day breaks turnaround.
      3. Respect a maximum day length in eighths.

    Scenes stay in script order within a day, which keeps the stripboard
    readable and makes continuity easier to hold.

    Returns:
        Shooting days conforming to the schema's `schedule` items.
    """
    if not scenes:
        return []

    resolved_times = _resolve_times(scenes)

    # Build ordered buckets keyed by (location, condition) so that the first
    # appearance of a location determines where it sits in the schedule.
    buckets: dict[tuple[str, str], list[int]] = {}
    order: list[tuple[str, str]] = []

    for idx, scene in enumerate(scenes):
        key = (_location_key(scene), _condition(resolved_times[idx]))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(idx)

    days: list[dict[str, Any]] = []
    day_number = 1
    previous_location: str | None = None

    for key in order:
        location, condition = key
        night = condition in NIGHT_CONDITIONS
        indices = buckets[key]

        # Split an over-long bucket across consecutive days.
        chunk: list[int] = []
        chunk_eighths = 0

        def flush(chunk: list[int], chunk_eighths: int) -> None:
            nonlocal day_number, previous_location
            if not chunk:
                return

            numbers = [scenes[i]["scene_number"] for i in chunk]
            company_move = (
                previous_location is not None and previous_location != location
            )

            times = sorted({resolved_times[i] for i in chunk})
            sets = sorted({(scenes[i].get("set_name") or "").upper() for i in chunk})

            rationale = (
                f"{len(numbers)} scene{'s' if len(numbers) != 1 else ''} at "
                f"{location} grouped as {condition} work "
                f"({chunk_eighths}/8 = {chunk_eighths / EIGHTHS_PER_PAGE:.2f} pages). "
                f"Sets: {', '.join(s for s in sets if s)}. "
                f"Times of day: {', '.join(times)}."
            )
            if condition in {"dawn", "dusk"}:
                rationale += (
                    f" {condition.title()} is a hard window and cannot be "
                    "absorbed into a night block."
                )
            if company_move:
                rationale += f" Company move from {previous_location}."

            days.append(
                {
                    "day": day_number,
                    "date": None,
                    "unit": "Main Unit",
                    "location": location,
                    "scene_numbers": numbers,
                    "total_eighths": chunk_eighths,
                    "company_move": company_move,
                    "night_work": night,
                    "rationale": rationale,
                }
            )
            day_number += 1
            previous_location = location

        for idx in indices:
            eighths = int(scenes[idx].get("eighths") or 1)
            if chunk and chunk_eighths + eighths > MAX_EIGHTHS_PER_DAY:
                flush(chunk, chunk_eighths)
                chunk, chunk_eighths = [], 0
            chunk.append(idx)
            chunk_eighths += eighths

        flush(chunk, chunk_eighths)

    _assert_complete(scenes, days)
    return days


def _assert_complete(
    scenes: list[dict[str, Any]], days: list[dict[str, Any]]
) -> None:
    """Log loudly if the schedule lost or duplicated a scene.

    A silently dropped scene is the worst failure this stage can have: the
    stripboard looks plausible and the shoot is short a day.
    """
    scheduled: list[str] = []
    for day in days:
        scheduled.extend(day["scene_numbers"])

    expected = [s["scene_number"] for s in scenes]

    missing = set(expected) - set(scheduled)
    if missing:
        log.error("Scheduler dropped scenes: %s", sorted(missing))

    if len(scheduled) != len(set(scheduled)):
        duplicates = {n for n in scheduled if scheduled.count(n) > 1}
        log.error("Scheduler duplicated scenes: %s", sorted(duplicates))
