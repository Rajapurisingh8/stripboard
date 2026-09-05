"""Stage 2 — the element tagger.

One Gemini call per scene, run concurrently. Each call returns the scene's
production elements plus the three judgments the parser cannot make:
a one-line synopsis, the story day, and the practical location.

The category list is enforced by the response schema rather than by the
prompt, because a tagger inventing a nineteenth category is not a style
issue — the scheduler and budget agents switch on these values, so an
unknown category is a downstream crash.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from google.genai import types

from .gemini import get_client, get_model, parse_json_response, with_retries
from .scene_parser import ParsedScene

log = logging.getLogger(__name__)

#: Must stay identical to the schema's element.category enum.
CATEGORIES = (
    "cast",
    "background",
    "stunts",
    "vehicles",
    "props",
    "set_dressing",
    "wardrobe",
    "makeup_hair",
    "special_effects",
    "visual_effects",
    "animals",
    "animal_handler",
    "music",
    "sound",
    "special_equipment",
    "greenery",
    "security",
    "additional_labor",
    "notes",
)

#: How many scenes to tag at once. Modest on purpose: a feature script is
#: ~120 scenes, and hammering Vertex with 120 parallel calls earns a 429.
MAX_CONCURRENCY = 6

_SYSTEM = """\
You are a first assistant director doing a production breakdown. You have \
done this for twenty years. You tag what a department would actually have to \
supply, budget, or wrangle — not every noun in the prose.

Rules that matter:

- CAST is a named or clearly individuated speaking character. BACKGROUND is \
non-speaking atmosphere; give it a quantity.
- Tag an object as PROPS only if a character handles it or the story needs it. \
Scenery a department must build or supply is SET_DRESSING.
- Fire, smoke, snow, rain, breath, steam and practical explosions are \
SPECIAL_EFFECTS, even when the script states them plainly.
- Any animal on camera implies both ANIMALS and ANIMAL_HANDLER.
- Stunt language ("slides", "precision driver", "breakaway", a fall, a crash) \
implies STUNTS. A vehicle in a stunt is also VEHICLES.
- A named practical vehicle is VEHICLES whether or not it moves.
- Note SPECIAL_EQUIPMENT when the described shot needs it (process trailer, \
crane, snow machine, insert car).
- Use NOTES for production facts that are not suppliable items, such as an \
explicit script-day marker.

Naming: canonical, upper case, singular. "MARISOL VEGA", not "Marisol" or \
"the cook". The same person, prop or vehicle must carry the identical name in \
every scene, because day-out-of-days is built by matching these strings.

Do not invent elements that are not supported by the text. An empty category \
is a correct answer.
"""


def _response_schema() -> types.Schema:
    """Structured-output contract for a single scene.

    Nullable fields are modelled as sentinels ("" and 0) rather than nulls;
    the SDK's nullable support varies by version and a sentinel that we clean
    up ourselves is more portable than one that silently fails.
    """
    return types.Schema(
        type=types.Type.OBJECT,
        required=["synopsis", "location", "script_day", "confidence", "elements"],
        properties={
            "synopsis": types.Schema(
                type=types.Type.STRING,
                description=(
                    "One line, present tense, under 140 characters. This "
                    "prints on the stripboard strip, so it must read as "
                    "action, not summary."
                ),
            ),
            "location": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Practical shooting location if inferable from the set, "
                    "e.g. DELMAR DINER for its kitchen and dining room. Empty "
                    "string if not inferable. Scenes sharing a practical "
                    "location must return the identical string."
                ),
            ),
            "script_day": types.Schema(
                type=types.Type.INTEGER,
                description=(
                    "Story day number if determinable from an explicit marker "
                    "or unambiguous continuity. 0 if unknown. Do not guess."
                ),
            ),
            "confidence": types.Schema(
                type=types.Type.NUMBER,
                description=(
                    "Your own confidence in this breakdown, 0 to 1. Below 0.6 "
                    "the UI flags the scene for human review, so be honest."
                ),
            ),
            "elements": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["category", "name", "source_quote"],
                    properties={
                        "category": types.Schema(
                            type=types.Type.STRING, enum=list(CATEGORIES)
                        ),
                        "name": types.Schema(type=types.Type.STRING),
                        "quantity": types.Schema(
                            type=types.Type.INTEGER,
                            description="Head count for background, or unit count. 0 if not applicable.",
                        ),
                        "speaking": types.Schema(
                            type=types.Type.BOOLEAN,
                            description="Cast only: does this character speak in this scene.",
                        ),
                        "notes": types.Schema(type=types.Type.STRING),
                        "source_quote": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "Verbatim span from the scene that justifies "
                                "this tag, under 400 characters. A human uses "
                                "this to check you, so it must be real text "
                                "from the scene."
                            ),
                        ),
                    },
                ),
            ),
        },
    )


def _clean_element(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Coerce one model-produced element into a schema-valid one, or drop it."""
    category = (raw.get("category") or "").strip()
    name = (raw.get("name") or "").strip().upper()

    if category not in CATEGORIES or not name:
        log.warning("Dropping unusable element: %r", raw)
        return None

    out: dict[str, Any] = {"category": category, "name": name}

    quantity = raw.get("quantity")
    if isinstance(quantity, int) and quantity >= 1:
        out["quantity"] = quantity

    if category == "cast" and isinstance(raw.get("speaking"), bool):
        out["speaking"] = raw["speaking"]

    notes = (raw.get("notes") or "").strip()
    if notes:
        out["notes"] = notes

    quote = (raw.get("source_quote") or "").strip()
    if quote:
        out["source_quote"] = quote[:400]

    return out


def _dedupe(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate (category, name) pairs within a scene.

    Models occasionally tag the same prop twice from two different quotes.
    Keep the first, but prefer a version that carries a source quote.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for el in elements:
        key = (el["category"], el["name"])
        if key not in seen:
            seen[key] = el
        elif not seen[key].get("source_quote") and el.get("source_quote"):
            seen[key] = el
    return list(seen.values())


def tag_scene(scene: ParsedScene) -> dict[str, Any]:
    """Tag one scene. Returns the schema-shaped scene dict.

    On failure the scene is returned parsed-but-untagged with a low
    confidence, so one bad call costs one scene's tags rather than the run.
    """
    client = get_client()
    model = get_model()

    hint = ""
    if scene.script_day_hint is not None:
        hint = (
            f"\nThe scene text contains an explicit script day marker: "
            f"day {scene.script_day_hint}. Use it.\n"
        )

    prompt = (
        f"Break down this scene.\n"
        f"{hint}\n"
        f"Slugline as parsed: {scene.int_ext}. {scene.set_name} - {scene.time_of_day}\n"
        f"Scene number: {scene.scene_number}\n\n"
        f"--- SCENE TEXT ---\n{scene.body}\n--- END SCENE TEXT ---"
    )

    def call() -> Any:
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                response_mime_type="application/json",
                response_schema=_response_schema(),
                temperature=0.1,
            ),
        )

    base = scene.to_schema()

    try:
        data = parse_json_response(
            with_retries(call, label=f"tag scene {scene.scene_number}"),
            label=f"tag scene {scene.scene_number}",
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Tagging failed for scene %s: %s", scene.scene_number, exc)
        base["confidence"] = 0.0
        base["synopsis"] = f"{scene.set_name.title()} (tagging failed)"
        return base

    elements = _dedupe(
        [
            cleaned
            for cleaned in (_clean_element(e) for e in data.get("elements") or [])
            if cleaned
        ]
    )

    synopsis = (data.get("synopsis") or "").strip()
    base["synopsis"] = (synopsis or scene.set_name.title())[:200]
    base["elements"] = elements

    location = (data.get("location") or "").strip()
    base["location"] = location.upper() if location else None

    script_day = data.get("script_day")
    if isinstance(script_day, int) and script_day >= 1:
        base["script_day"] = script_day
    elif scene.script_day_hint is not None:
        base["script_day"] = scene.script_day_hint
    else:
        base["script_day"] = None

    confidence = data.get("confidence")
    if isinstance(confidence, (int, float)):
        base["confidence"] = max(0.0, min(1.0, float(confidence)))

    return base


def tag_elements(scenes: list[ParsedScene]) -> list[dict[str, Any]]:
    """Tag every scene concurrently, preserving script order."""
    if not scenes:
        return []

    workers = min(MAX_CONCURRENCY, len(scenes))
    log.info("Tagging %d scenes with %d workers", len(scenes), workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(tag_scene, scenes))
