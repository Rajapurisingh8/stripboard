"""Stage 5 — the continuity checker.

One Gemini call over the whole breakdown. This is the stage that most
justifies a model: the question "does the powder in her hair from scene 3
still exist in scene 4, and are those scenes shooting on the same day?"
requires reading across scenes and holding state, which is exactly what a
per-scene tagger cannot do and a rule engine does badly.

Flags are advisory. They are attached to a scene so the UI can surface them
where the user is looking, and every flag names the scenes it spans so a
script supervisor can check the claim rather than trust it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google.genai import types

from .gemini import get_client, get_model, parse_json_response, with_retries

log = logging.getLogger(__name__)

SEVERITIES = ("info", "warning", "critical")

#: Cap on flags kept. A model asked for continuity issues will happily
#: produce forty; a script supervisor reads the top handful.
MAX_FLAGS = 14

_SYSTEM = """\
You are a script supervisor reviewing a breakdown before a shoot. You are \
looking for things that will cost money or embarrass the production if they \
are noticed on set or in the edit.

Report only issues you can point at in the material you are given. Do not \
speculate about material you cannot see, and do not invent scenes.

Weight your attention toward:

- Physical state carried between scenes: damage, dirt, wounds, weather on \
clothing, substances on skin or hair. Note when a scene shot out of order \
would break it.
- Wardrobe and prop continuity across scenes shot on different days.
- Script day boundaries: an element that changes across a story-day change \
is fine; one that changes within a story day is a problem.
- Time-of-day claims that contradict each other or the story.
- A vehicle, animal or prop that appears in a state inconsistent with how it \
was last seen.
- Scheduling risk: two scenes on the same shooting day that require \
incompatible states of the same element.

Severity:
- critical — will produce unusable footage or a reshoot.
- warning — a real continuity risk needing a decision before the day.
- info — worth a note in the supervisor's book, not a blocker.

Be specific. "Continuity issue with Marisol" is useless. "Marisol has \
extinguisher powder in her hair at the end of scene 3; scene 4 is later the \
same night but is scheduled on a different day" is useful.
"""


def _response_schema() -> types.Schema:
    return types.Schema(
        type=types.Type.OBJECT,
        required=["flags"],
        properties={
            "flags": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["severity", "issue", "related_scenes"],
                    properties={
                        "severity": types.Schema(
                            type=types.Type.STRING, enum=list(SEVERITIES)
                        ),
                        "issue": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "One or two sentences naming the element, the "
                                "scenes involved, and what breaks."
                            ),
                        ),
                        "related_scenes": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                            description="Scene numbers exactly as given in the input.",
                        ),
                        "related_element": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "The element name this concerns, matching a "
                                "tagged element where possible. Empty if none."
                            ),
                        ),
                    },
                ),
            )
        },
    )


def _digest(scenes: list[dict[str, Any]], schedule: list[dict[str, Any]]) -> str:
    """Compact the breakdown into something worth spending context on.

    Full scene text would be more faithful but pushes a feature script past
    a comfortable prompt size. Synopsis plus tagged elements is what the
    continuity question actually needs.
    """
    scene_day = {
        number: day["day"]
        for day in schedule
        for number in day.get("scene_numbers") or []
    }

    rows = []
    for scene in scenes:
        elements = [
            f"{el['category']}:{el['name']}" for el in scene.get("elements") or []
        ]
        rows.append(
            {
                "scene": scene["scene_number"],
                "slug": f"{scene['int_ext']}. {scene['set_name']} - {scene['time_of_day']}",
                "script_day": scene.get("script_day"),
                "shooting_day": scene_day.get(scene["scene_number"]),
                "synopsis": scene.get("synopsis"),
                "elements": elements,
            }
        )
    return json.dumps(rows, indent=1)


def _clean_flag(raw: dict[str, Any], valid_scenes: set[str]) -> dict[str, Any] | None:
    severity = (raw.get("severity") or "").strip().lower()
    issue = (raw.get("issue") or "").strip()
    if severity not in SEVERITIES or not issue:
        return None

    related = [
        str(n).strip()
        for n in (raw.get("related_scenes") or [])
        if str(n).strip() in valid_scenes
    ]
    # A flag pointing at no real scene cannot be checked, so it is noise.
    if not related:
        return None

    out: dict[str, Any] = {
        "severity": severity,
        "issue": issue,
        "related_scenes": related,
    }
    element = (raw.get("related_element") or "").strip()
    if element:
        out["related_element"] = element.upper()
    return out


def check_continuity(
    scenes: list[dict[str, Any]], schedule: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach continuity flags to scenes in place; return the flat flag list.

    Each flag is attached to the earliest scene it references, so the UI
    surfaces it once, at the point the problem starts.
    """
    if not scenes:
        return []

    client = get_client()
    model = get_model()
    valid = {s["scene_number"] for s in scenes}

    prompt = (
        "Review this breakdown for continuity and scheduling risk.\n\n"
        "Each row is a scene with its slugline, story day, the shooting day "
        "it is currently scheduled on, a synopsis, and its tagged elements.\n\n"
        f"{_digest(scenes, schedule)}"
    )

    def call() -> Any:
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                response_mime_type="application/json",
                response_schema=_response_schema(),
                temperature=0.2,
            ),
        )

    try:
        data = parse_json_response(
            with_retries(call, label="continuity check"), label="continuity check"
        )
    except Exception as exc:  # noqa: BLE001
        # A failed QA pass must not fail the breakdown. The rest of the
        # document is still correct and useful without it.
        log.error("Continuity check failed: %s", exc)
        return []

    flags = [
        cleaned
        for cleaned in (
            _clean_flag(f, valid) for f in (data.get("flags") or [])
        )
        if cleaned
    ]

    rank = {"critical": 0, "warning": 1, "info": 2}
    flags.sort(key=lambda f: rank[f["severity"]])
    flags = flags[:MAX_FLAGS]

    # Attach each flag to the earliest scene it names.
    position = {s["scene_number"]: i for i, s in enumerate(scenes)}
    for scene in scenes:
        scene.setdefault("continuity_flags", [])

    for flag in flags:
        first = min(flag["related_scenes"], key=lambda n: position[n])
        scenes[position[first]]["continuity_flags"].append(flag)

    # Drop the key entirely where empty rather than shipping empty arrays.
    for scene in scenes:
        if not scene["continuity_flags"]:
            del scene["continuity_flags"]

    log.info(
        "Continuity: %d flag(s) — %s",
        len(flags),
        ", ".join(f"{s}:{sum(1 for f in flags if f['severity'] == s)}" for s in SEVERITIES),
    )
    return flags
