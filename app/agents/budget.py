"""Stage 4 — the budget estimator.

Deterministic arithmetic over a visible rate card. Nothing here is a model
call, and that is the point: an estimate a producer cannot trace is an
estimate they cannot use. Every line item carries a `basis` string naming
exactly what it was derived from, and the UI is required to show it.

Cast days are computed the way a real day-out-of-days is: a cast member is
owed a day for each *shooting day* their scenes land on, not each scene.
That is why this stage runs after the scheduler.

The ranges are indie-feature order-of-magnitude figures, not quotes. They
exist so the number moves correctly when the breakdown changes — add a stunt
and the estimate rises, and you can see which line did it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

log = logging.getLogger(__name__)

CURRENCY = "USD"

#: (low, high) US dollars. Per-day unless the label says otherwise.
RATE_CARD: dict[str, tuple[float, float]] = {
    "crew_day": (8_000.0, 15_000.0),
    "cast_day": (800.0, 1_500.0),
    "background_day_person": (150.0, 250.0),
    "stunt_day_performer": (1_200.0, 2_500.0),
    "stunt_coordinator_day": (1_000.0, 1_800.0),
    "animal_day": (500.0, 1_200.0),
    "animal_handler_day": (600.0, 1_000.0),
    "sfx_day": (1_500.0, 4_000.0),
    "vfx_shot": (800.0, 3_000.0),
    "vehicle_day": (200.0, 600.0),
    "prop_unit": (50.0, 200.0),
    "set_dressing_set": (300.0, 1_000.0),
    "wardrobe_character": (200.0, 500.0),
    "makeup_hair_day": (500.0, 900.0),
    "special_equipment_day": (800.0, 2_500.0),
    "greenery_set": (250.0, 900.0),
    "security_day": (400.0, 800.0),
    "additional_labor_day": (300.0, 700.0),
    "company_move": (1_500.0, 3_000.0),
}

#: Night work costs more: premium pay, lighting packages, longer wraps.
NIGHT_PREMIUM = 0.15


def _scene_to_day(schedule: list[dict[str, Any]]) -> dict[str, int]:
    """Map scene_number -> shooting day number."""
    mapping: dict[str, int] = {}
    for day in schedule:
        for number in day.get("scene_numbers") or []:
            mapping[number] = int(day["day"])
    return mapping


def _elements_by_category(
    scenes: list[dict[str, Any]],
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Group every element across the script as (scene_number, element)."""
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for scene in scenes:
        number = scene["scene_number"]
        for element in scene.get("elements") or []:
            grouped[element["category"]].append((number, element))
    return grouped


def compute_cast_days(
    scenes: list[dict[str, Any]], schedule: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Day-out-of-days: how many shooting days each cast member is owed.

    Two scenes with the same actor on the same shooting day are one cast day.
    This is the whole reason the scheduler runs first.
    """
    scene_day = _scene_to_day(schedule)

    days_by_name: dict[str, set[int]] = defaultdict(set)
    scenes_by_name: dict[str, list[str]] = defaultdict(list)

    for scene in scenes:
        number = scene["scene_number"]
        day = scene_day.get(number)
        for element in scene.get("elements") or []:
            if element["category"] != "cast":
                continue
            name = element["name"]
            scenes_by_name[name].append(number)
            if day is not None:
                days_by_name[name].add(day)

    out = [
        {
            "name": name,
            "days": len(days_by_name.get(name, set())),
            "scene_numbers": scenes_by_name[name],
        }
        for name in sorted(scenes_by_name)
    ]
    # Largest role first — that is the order a producer reads.
    out.sort(key=lambda row: (-row["days"], row["name"]))
    return out


def _line(
    category: str,
    label: str,
    rate_key: str,
    units: float,
    basis: str,
    multiplier: float = 1.0,
) -> dict[str, Any]:
    low, high = RATE_CARD[rate_key]
    return {
        "category": category,
        "label": label,
        "estimate_low": round(low * units * multiplier, 2),
        "estimate_high": round(high * units * multiplier, 2),
        "basis": basis,
    }


def estimate_budget(
    scenes: list[dict[str, Any]], schedule: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the schema's `budget` object from the breakdown and schedule."""
    shoot_days = len(schedule)
    cast_days = compute_cast_days(scenes, schedule)
    by_category = _elements_by_category(scenes)

    night_days = sum(1 for d in schedule if d.get("night_work"))
    moves = sum(1 for d in schedule if d.get("company_move"))

    lines: list[dict[str, Any]] = []

    # --- crew ----------------------------------------------------------------
    if shoot_days:
        lines.append(
            _line(
                "crew",
                "Base crew, all departments",
                "crew_day",
                shoot_days,
                f"{shoot_days} shooting day(s) from the schedule",
            )
        )
        if night_days:
            lines.append(
                _line(
                    "crew",
                    "Night work premium",
                    "crew_day",
                    night_days,
                    f"{night_days} of {shoot_days} day(s) are night work",
                    multiplier=NIGHT_PREMIUM,
                )
            )

    # --- cast ----------------------------------------------------------------
    total_cast_days = sum(row["days"] for row in cast_days)
    if total_cast_days:
        lines.append(
            _line(
                "cast",
                "Principal cast",
                "cast_day",
                total_cast_days,
                f"{total_cast_days} cast day(s) across {len(cast_days)} role(s), "
                f"deduplicated by shooting day",
            )
        )

    # --- background ----------------------------------------------------------
    background = by_category.get("background", [])
    if background:
        heads = sum(int(el.get("quantity") or 1) for _, el in background)
        lines.append(
            _line(
                "background",
                "Background artists",
                "background_day_person",
                heads,
                f"{heads} background head(s) across {len(background)} scene appearance(s)",
            )
        )

    # --- stunts --------------------------------------------------------------
    stunts = by_category.get("stunts", [])
    if stunts:
        stunt_days = len({num for num, _ in stunts})
        lines.append(
            _line(
                "stunts",
                "Stunt performers",
                "stunt_day_performer",
                stunt_days,
                f"{len(stunts)} stunt element(s) in {stunt_days} scene(s)",
            )
        )
        lines.append(
            _line(
                "stunts",
                "Stunt coordinator",
                "stunt_coordinator_day",
                stunt_days,
                "One coordinator day per scene containing stunt work",
            )
        )

    # --- animals -------------------------------------------------------------
    animals = by_category.get("animals", [])
    if animals:
        animal_days = len({num for num, _ in animals})
        names = sorted({el["name"] for _, el in animals})
        lines.append(
            _line(
                "animals",
                f"Animal talent ({', '.join(names)})",
                "animal_day",
                animal_days,
                f"{len(names)} animal(s) appearing in {animal_days} scene(s)",
            )
        )
        lines.append(
            _line(
                "animals",
                "Animal handler",
                "animal_handler_day",
                animal_days,
                "Handler required on every day an animal is on camera",
            )
        )

    # --- effects -------------------------------------------------------------
    sfx = by_category.get("special_effects", [])
    if sfx:
        sfx_days = len({num for num, _ in sfx})
        lines.append(
            _line(
                "special_effects",
                "Practical effects unit",
                "sfx_day",
                sfx_days,
                f"{len(sfx)} effect(s) across {sfx_days} scene(s): "
                f"{', '.join(sorted({el['name'] for _, el in sfx})[:4])}",
            )
        )

    vfx = by_category.get("visual_effects", [])
    if vfx:
        lines.append(
            _line(
                "visual_effects",
                "Visual effects shots",
                "vfx_shot",
                len(vfx),
                f"{len(vfx)} tagged VFX element(s)",
            )
        )

    # --- vehicles ------------------------------------------------------------
    vehicles = by_category.get("vehicles", [])
    if vehicles:
        unique = sorted({el["name"] for _, el in vehicles})
        vehicle_days = len({num for num, _ in vehicles})
        lines.append(
            _line(
                "vehicles",
                f"Picture vehicles ({len(unique)})",
                "vehicle_day",
                len(unique) * vehicle_days,
                f"{len(unique)} vehicle(s) over {vehicle_days} scene day(s): "
                f"{', '.join(unique[:4])}",
            )
        )

    # --- art and wardrobe ----------------------------------------------------
    props = by_category.get("props", [])
    if props:
        unique_props = {el["name"] for _, el in props}
        lines.append(
            _line(
                "props",
                "Props, purchase and rental",
                "prop_unit",
                len(unique_props),
                f"{len(unique_props)} distinct prop(s) tagged",
            )
        )

    dressing = by_category.get("set_dressing", [])
    if dressing:
        sets = {(scene["set_name"]) for scene in scenes}
        lines.append(
            _line(
                "set_dressing",
                "Set dressing",
                "set_dressing_set",
                len(sets),
                f"{len(dressing)} dressing element(s) across {len(sets)} set(s)",
            )
        )

    wardrobe = by_category.get("wardrobe", [])
    if wardrobe:
        characters = {row["name"] for row in cast_days} or {
            el["name"] for _, el in wardrobe
        }
        lines.append(
            _line(
                "wardrobe",
                "Wardrobe",
                "wardrobe_character",
                len(characters),
                f"{len(characters)} character(s) with {len(wardrobe)} tagged wardrobe note(s)",
            )
        )

    makeup = by_category.get("makeup_hair", [])
    if makeup and shoot_days:
        lines.append(
            _line(
                "makeup_hair",
                "Makeup and hair",
                "makeup_hair_day",
                shoot_days,
                f"{len(makeup)} tagged makeup/hair element(s); costed per shooting day",
            )
        )

    greenery = by_category.get("greenery", [])
    if greenery:
        lines.append(
            _line(
                "greenery",
                "Greens department",
                "greenery_set",
                len({num for num, _ in greenery}),
                f"{len(greenery)} greenery element(s)",
            )
        )

    # --- equipment and labour ------------------------------------------------
    equipment = by_category.get("special_equipment", [])
    if equipment:
        equip_days = len({num for num, _ in equipment})
        lines.append(
            _line(
                "special_equipment",
                "Special equipment",
                "special_equipment_day",
                equip_days,
                f"{len(equipment)} item(s) required on {equip_days} scene day(s): "
                f"{', '.join(sorted({el['name'] for _, el in equipment})[:4])}",
            )
        )

    security = by_category.get("security", [])
    if security:
        lines.append(
            _line(
                "security",
                "Security",
                "security_day",
                len({num for num, _ in security}),
                f"{len(security)} scene(s) tagged as requiring security",
            )
        )

    labor = by_category.get("additional_labor", [])
    if labor:
        lines.append(
            _line(
                "additional_labor",
                "Additional labour",
                "additional_labor_day",
                len({num for num, _ in labor}),
                f"{len(labor)} additional labour element(s)",
            )
        )

    # --- logistics -----------------------------------------------------------
    if moves:
        lines.append(
            _line(
                "logistics",
                "Company moves",
                "company_move",
                moves,
                f"{moves} company move(s) between locations in the schedule",
            )
        )

    total_low = round(sum(item["estimate_low"] for item in lines), 2)
    total_high = round(sum(item["estimate_high"] for item in lines), 2)
    log.info(
        "Budget: %d line item(s), %s %.0f - %.0f over %d day(s)",
        len(lines),
        CURRENCY,
        total_low,
        total_high,
        shoot_days,
    )

    return {
        "currency": CURRENCY,
        "shoot_days": shoot_days,
        "cast_days": cast_days,
        "line_items": lines,
    }
