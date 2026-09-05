"""Semantic alias resolution across scenes.

`entity_resolution.py` catches the cheap case: names sharing words, where
MARISOL is plainly an abbreviation of MARISOL VEGA. It cannot catch the
case where two names for one thing share no words at all — SHEPHERD MIX in
scene 1 and DOG in scenes 2 and 4 are one animal, and counting them as two
puts a second animal on the call sheet and a second line in the budget.

Deciding whether DOG and SHEPHERD MIX denote the same creature is a reading
comprehension question, so it goes to the model. Deciding what that does to
the cast days and the budget is arithmetic, so it stays in code. The model
may only group names drawn from a list we hand it, and may only nominate a
canonical from within each group — it cannot invent an element, and it
never touches a number.

Runs after lexical resolution, so the model only sees what survived the
cheap pass.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from google.genai import types

from .gemini import get_client, get_model, parse_json_response, with_retries

log = logging.getLogger(__name__)

#: Categories where two names can denote one thing. Excludes categories
#: where similar-sounding entries are routinely separate line items.
RESOLVABLE = {
    "cast",
    "animals",
    "vehicles",
    "props",
    "special_effects",
    "special_equipment",
    "wardrobe",
}

#: Below this many distinct names in a category there is nothing to merge.
MIN_NAMES = 2

_SYSTEM = """\
You are reconciling a production breakdown. The same person, animal, vehicle \
or item was tagged in different scenes, sometimes under different names. \
Group the names that denote the same single entity.

Group only when the names refer to one and the same thing in this story:

- A character's short name and full name: DALE, DALE OKONKWO.
- A creature described two ways: DOG, SHEPHERD MIX.
- One physical event named twice: FIRE, GREASE FIRE — when the second is \
plainly the same fire.
- A vehicle named loosely and specifically: TRUCK, MARISOL'S PICKUP TRUCK.

Do NOT group:

- Two genuinely different items that share a word: TRUCKER'S RIG and \
MARISOL'S PICKUP TRUCK are two vehicles. SEMI is a third.
- Distinct stages of one event that different departments supply \
separately: a fire and the extinguisher discharge that puts it out are \
two effects.
- Anything you are not confident about. Leaving two names unmerged is a \
much smaller error than collapsing two real entities into one.

For each group, choose as canonical the name a first AD would put on a call \
sheet: the most specific and identifiable one. Prefer SHEPHERD MIX over \
DOG, MARISOL VEGA over MARISOL.

Return only groups containing two or more names. If nothing should be \
merged, return an empty list.
"""


def _response_schema() -> types.Schema:
    return types.Schema(
        type=types.Type.OBJECT,
        required=["groups"],
        properties={
            "groups": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["category", "canonical", "aliases"],
                    properties={
                        "category": types.Schema(type=types.Type.STRING),
                        "canonical": types.Schema(
                            type=types.Type.STRING,
                            description="The name to keep. Must be one of the supplied names.",
                        ),
                        "aliases": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                            description=(
                                "The other names for this same entity, all "
                                "from the supplied list. Excludes the canonical."
                            ),
                        ),
                        "reason": types.Schema(
                            type=types.Type.STRING,
                            description="Short justification, for the log.",
                        ),
                    },
                ),
            )
        },
    )


def _forbidden_pairs(scenes: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    """Pairs that may never be merged, as (category, name_a, name_b) sorted.

    **Two names tagged in the same scene, in the same category, are
    different things.** A tagger reading one scene does not name a single
    entity twice — exact repeats are already collapsed by the dedupe in the
    tagger. So co-occurrence is strong evidence of distinctness.

    This is the guard that keeps SEMI and TRUCKER'S RIG apart: both appear
    on the road in the same scene, so whatever a model believes about the
    words, they are two picture vehicles. The dog is unaffected — SHEPHERD
    MIX and DOG never share a scene.

    Learned the hard way: the prompt asks the model not to do this, and the
    model did it anyway.
    """
    forbidden: set[tuple[str, str, str]] = set()
    for scene in scenes:
        by_category: dict[str, set[str]] = defaultdict(set)
        for element in scene.get("elements") or []:
            by_category[element["category"]].add(element["name"])
        for category, names in by_category.items():
            ordered = sorted(names)
            for i, first in enumerate(ordered):
                for second in ordered[i + 1 :]:
                    forbidden.add((category, first, second))
    return forbidden


def _collect(scenes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Distinct names per resolvable category, where merging is possible."""
    names: dict[str, set[str]] = defaultdict(set)
    for scene in scenes:
        for element in scene.get("elements") or []:
            if element["category"] in RESOLVABLE:
                names[element["category"]].add(element["name"])
    return {
        category: sorted(values)
        for category, values in names.items()
        if len(values) >= MIN_NAMES
    }


def _apply(scenes: list[dict[str, Any]], merges: dict[str, str]) -> None:
    """Rewrite names and collapse duplicates a merge creates within a scene."""
    for scene in scenes:
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for element in scene.get("elements") or []:
            key = f"{element['category']}:{element['name']}"
            if key in merges:
                element["name"] = merges[key]

            dedupe_key = (element["category"], element["name"])
            if dedupe_key not in seen:
                seen[dedupe_key] = element
                continue

            kept = seen[dedupe_key]
            if not kept.get("source_quote") and element.get("source_quote"):
                kept["source_quote"] = element["source_quote"]
            if not kept.get("notes") and element.get("notes"):
                kept["notes"] = element["notes"]
            if element.get("speaking"):
                kept["speaking"] = True
            if element.get("quantity") and not kept.get("quantity"):
                kept["quantity"] = element["quantity"]

        scene["elements"] = list(seen.values())


def resolve_aliases(scenes: list[dict[str, Any]]) -> dict[str, str]:
    """Merge names that denote one entity but share no words.

    Returns the merge map applied, keyed "category:alias" -> canonical.
    Never raises: a failed reconciliation leaves the breakdown as it was.
    """
    candidates = _collect(scenes)
    if not candidates:
        return {}

    forbidden = _forbidden_pairs(scenes)

    payload = json.dumps(candidates, indent=1)
    log.info(
        "Alias resolution over %d categor(ies), %d name(s)",
        len(candidates),
        sum(len(v) for v in candidates.values()),
    )

    client = get_client()
    model = get_model()

    def call() -> Any:
        return client.models.generate_content(
            model=model,
            contents=(
                "These element names were tagged across the scenes of one "
                "screenplay, grouped by category. Identify names that denote "
                "the same single entity.\n\n"
                f"{payload}"
            ),
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                response_mime_type="application/json",
                response_schema=_response_schema(),
                temperature=0.0,
            ),
        )

    try:
        data = parse_json_response(
            with_retries(call, label="alias resolution"), label="alias resolution"
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Alias resolution failed, leaving names as tagged: %s", exc)
        return {}

    merges: dict[str, str] = {}

    for group in data.get("groups") or []:
        category = (group.get("category") or "").strip()
        canonical = (group.get("canonical") or "").strip().upper()
        aliases = [str(a).strip().upper() for a in (group.get("aliases") or [])]

        known = set(candidates.get(category, []))
        if not known:
            log.warning("Alias group names an unknown category %r; skipped", category)
            continue

        # The model may only choose from names we supplied. Anything else is
        # an invention and is discarded rather than trusted.
        if canonical not in known:
            log.warning(
                "Alias group canonical %r not in supplied %s names; skipped",
                canonical,
                category,
            )
            continue

        for alias in aliases:
            if alias == canonical:
                continue
            if alias not in known:
                log.warning(
                    "Alias %r not in supplied %s names; skipped", alias, category
                )
                continue

            pair = (category, *sorted((alias, canonical)))
            if pair in forbidden:
                log.warning(
                    "REFUSED alias merge [%s]: %r -> %r — both appear in the "
                    "same scene, so they are different things",
                    category,
                    alias,
                    canonical,
                )
                continue

            merges[f"{category}:{alias}"] = canonical
            log.info(
                "Alias merge [%s]: %r -> %r (%s)",
                category,
                alias,
                canonical,
                (group.get("reason") or "").strip() or "no reason given",
            )

    if merges:
        _apply(scenes, merges)
        log.info("Alias resolution merged %d name(s)", len(merges))

    return merges
