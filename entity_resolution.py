"""Canonicalise element names across scenes.

The tagger is instructed to use one identical name for a person, prop or
vehicle in every scene. Models comply most of the time, which is worse than
never complying: MARISOL in scene 1 and MARISOL VEGA in scene 5 silently
becomes two actors in the day-out-of-days, two rows in the element index,
and two sets of cast days in the budget.

Day-out-of-days is built by matching strings, so a naming rule that
arithmetic depends on cannot live in a prompt. This is that rule as code.

Deliberately conservative. It merges only when one name's words are a
strict subset of another's *within the same category*, which catches the
real failure (a short form and a full name) without inventing merges
between genuinely different elements. Everything it does is logged, because
a wrong merge is harder to notice than a missed one.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

log = logging.getLogger(__name__)

#: Words that carry no identifying force, so their presence should not stop
#: two names from being recognised as the same thing.
STOPWORDS = {"THE", "A", "AN", "OF", "HIS", "HER", "THEIR", "ITS"}

#: Categories where a short form and a full name genuinely denote the same
#: entity. Excluded: notes, and anything where similar names are routinely
#: distinct items rather than aliases.
MERGEABLE = {
    "cast",
    "background",
    "vehicles",
    "animals",
    "props",
    "wardrobe",
    "special_equipment",
}


def _tokens(name: str) -> frozenset[str]:
    """Identifying words in a name, upper-cased, stripped of noise."""
    cleaned = re.sub(r"[^\w\s]", " ", name.upper())
    return frozenset(w for w in cleaned.split() if w and w not in STOPWORDS)


def _build_aliases(names: list[str]) -> dict[str, str]:
    """Map each name to its canonical form.

    Two passes, because there are two distinct kinds of duplicate:

    1. Names carrying the *same* identifying words, differing only in noise
       — "TRUCKER" and "THE TRUCKER". Here the shortest spelling wins; the
       extra words add nothing.
    2. Names where one's words are a strict subset of another's — "MARISOL"
       and "MARISOL VEGA". Here the longest wins, because the full name is
       the useful label and the short form is the abbreviation.

    Ties break alphabetically so results are stable across runs.
    """
    unique = sorted(set(names))
    token_map = {n: _tokens(n) for n in unique}

    # Pass 1 — collapse names sharing an identical token set.
    groups: dict[frozenset[str], list[str]] = defaultdict(list)
    for name in unique:
        groups[token_map[name]].append(name)

    representative: dict[frozenset[str], str] = {}
    canonical: dict[str, str] = {}
    for tokens, members in groups.items():
        # Shortest spelling, then alphabetical.
        rep = sorted(members, key=lambda n: (len(n), n))[0]
        representative[tokens] = rep
        for member in members:
            canonical[member] = rep

    # Pass 2 — fold each representative into any strict superset.
    ordered = sorted(
        representative.items(), key=lambda kv: (-len(kv[0]), kv[1])
    )
    settled: dict[frozenset[str], str] = {}

    for tokens, rep in ordered:
        if not tokens:
            settled[tokens] = rep
            continue

        target = rep
        for other_tokens, other_rep in ordered:
            if other_tokens is tokens or other_tokens == tokens:
                continue
            # Only fold into a name that is itself already canonical, so
            # chains collapse to a single representative.
            if settled.get(other_tokens) != other_rep:
                continue
            if tokens < other_tokens:  # strict subset
                target = other_rep
                break

        settled[tokens] = target
        if target != rep:
            for name, current in list(canonical.items()):
                if current == rep:
                    canonical[name] = target

    return canonical


def resolve_entities(scenes: list[dict[str, Any]]) -> dict[str, str]:
    """Rewrite element names in place to their canonical form.

    Returns:
        The alias map actually applied, for logging and for the UI to show
        if it wants to explain a merge.
    """
    by_category: dict[str, list[str]] = defaultdict(list)
    for scene in scenes:
        for element in scene.get("elements") or []:
            by_category[element["category"]].append(element["name"])

    applied: dict[str, str] = {}

    for category, names in by_category.items():
        if category not in MERGEABLE:
            continue
        aliases = _build_aliases(names)
        for original, canonical in aliases.items():
            if original != canonical:
                applied[f"{category}:{original}"] = canonical
                log.info(
                    "Entity merge [%s]: %r -> %r", category, original, canonical
                )

    if not applied:
        return {}

    # Rewrite, then collapse duplicates a merge may have created within a
    # single scene (e.g. a scene that tagged both MARISOL and MARISOL VEGA).
    for scene in scenes:
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for element in scene.get("elements") or []:
            key = f"{element['category']}:{element['name']}"
            if key in applied:
                element["name"] = applied[key]

            dedupe_key = (element["category"], element["name"])
            if dedupe_key not in seen:
                seen[dedupe_key] = element
            else:
                # Keep the richer record when merging two into one.
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

    log.info("Entity resolution merged %d alias(es)", len(applied))
    return applied
