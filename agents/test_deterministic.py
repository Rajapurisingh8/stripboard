"""Local test for the non-model stages.

Runs the real parser on the real demo screenplay, feeds a hand-built tagging
result through the scheduler and budget, and validates the whole document
against schema/scene-element.schema.json.

The tagged fixture below is what the tagger *should* produce for The Last
Shift. It exists so the deterministic stages can be tested without spending
a Vertex call, and so schema drift is caught before it reaches Replit.

    python3 agents/test_deterministic.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

from budget import estimate_budget  # noqa: E402
from scene_parser import split_scenes  # noqa: E402
from scheduler import build_schedule  # noqa: E402

# Elements the tagger is expected to find, keyed by scene number.
EXPECTED_TAGS: dict[str, dict] = {
    "1": {
        "location": "DELMAR DINER",
        "script_day": 1,
        "synopsis": "Marisol scrapes the flat top while a stray dog shoves in through the swinging door.",
        "elements": [
            {"category": "cast", "name": "MARISOL VEGA", "speaking": True},
            {"category": "cast", "name": "DALE OKONKWO", "speaking": True},
            {"category": "background", "name": "TRUCKER", "quantity": 1},
            {"category": "background", "name": "TEENAGER", "quantity": 2},
            {"category": "props", "name": "BUS TUB"},
            {"category": "set_dressing", "name": "FLAT TOP"},
            {"category": "set_dressing", "name": "WALK-IN DOOR"},
            {"category": "animals", "name": "SHEPHERD MIX"},
            {"category": "animal_handler", "name": "ANIMAL HANDLER"},
            {"category": "special_effects", "name": "COLD FOG"},
            {"category": "wardrobe", "name": "DALE APRON"},
        ],
    },
    "2": {
        "location": "DELMAR DINER",
        "script_day": 1,
        "synopsis": "The trucker claims the dog while Marisol quietly sets down water.",
        "elements": [
            {"category": "cast", "name": "MARISOL VEGA", "speaking": True},
            {"category": "cast", "name": "TRUCKER", "speaking": True},
            {"category": "cast", "name": "DALE OKONKWO", "speaking": True},
            {"category": "animals", "name": "SHEPHERD MIX"},
            {"category": "animal_handler", "name": "ANIMAL HANDLER"},
            {"category": "props", "name": "SAUCER"},
            {"category": "props", "name": "TELEVISION"},
            {"category": "props", "name": "TRUCKER COAT"},
            {"category": "set_dressing", "name": "BOOTH FOUR"},
        ],
    },
    "3": {
        "location": "DELMAR DINER",
        "script_day": 1,
        "synopsis": "A grease fire climbs the wall and Marisol puts it down in six seconds.",
        "elements": [
            {"category": "cast", "name": "MARISOL VEGA", "speaking": True},
            {"category": "cast", "name": "DALE OKONKWO", "speaking": True},
            {"category": "special_effects", "name": "GREASE FIRE"},
            {"category": "special_effects", "name": "EXTINGUISHER POWDER"},
            {"category": "props", "name": "FIRE EXTINGUISHER"},
            {"category": "props", "name": "SINK SPRAYER"},
            {"category": "makeup_hair", "name": "POWDER IN HAIR"},
            {"category": "special_equipment", "name": "FIRE SAFETY OFFICER"},
        ],
    },
    "4": {
        "location": "STATE ROUTE 9",
        "script_day": 1,
        "synopsis": "Marisol's truck hangs over a ditch in sideways snow; the trucker pulls in to help.",
        "elements": [
            {"category": "cast", "name": "MARISOL VEGA", "speaking": True},
            {"category": "cast", "name": "TRUCKER", "speaking": True},
            {"category": "vehicles", "name": "PICKUP TRUCK"},
            {"category": "vehicles", "name": "SEMI"},
            {"category": "stunts", "name": "TRUCK SLIDE TO DITCH EDGE"},
            {"category": "stunts", "name": "PRECISION DRIVER"},
            {"category": "special_effects", "name": "SNOW"},
            {"category": "greenery", "name": "BREAKAWAY BRUSH"},
            {"category": "special_equipment", "name": "SNOW MACHINE"},
            {"category": "animals", "name": "SHEPHERD MIX"},
            {"category": "animal_handler", "name": "ANIMAL HANDLER"},
            {"category": "props", "name": "TOW STRAP"},
            {"category": "wardrobe", "name": "MARISOL PARKA"},
        ],
    },
    "5": {
        "location": "DELMAR DINER",
        "script_day": 2,
        "synopsis": "Marisol locks up at dawn and finds one set of tire tracks leaving the lot.",
        "elements": [
            {"category": "cast", "name": "MARISOL VEGA", "speaking": False},
            {"category": "vehicles", "name": "PICKUP TRUCK"},
            {"category": "props", "name": "NEON SIGN"},
            {"category": "special_effects", "name": "SNOW COVER"},
            {"category": "special_effects", "name": "TIRE TRACKS"},
            {"category": "notes", "name": "SCRIPT DAY 2"},
        ],
    },
}


def build_document() -> dict:
    text = (ROOT / "demo" / "the-last-shift.fountain").read_text(encoding="utf-8")
    parsed = split_scenes(text)

    scenes = []
    for scene in parsed.scenes:
        row = scene.to_schema()
        tags = EXPECTED_TAGS.get(scene.scene_number, {})
        row["elements"] = tags.get("elements", [])
        row["synopsis"] = tags.get("synopsis", row["synopsis"])[:200]
        row["location"] = tags.get("location")
        row["script_day"] = tags.get("script_day")
        row["confidence"] = 0.86
        scenes.append(row)

    schedule = build_schedule(scenes)
    budget = estimate_budget(scenes, schedule)

    doc = {"script": parsed.script, "scenes": scenes, "schedule": schedule, "budget": budget}
    if parsed.warnings:
        doc["warnings"] = parsed.warnings
    return doc


def main() -> int:
    doc = build_document()

    print("=" * 72)
    print("SCHEDULE")
    print("=" * 72)
    for day in doc["schedule"]:
        move = "  [COMPANY MOVE]" if day["company_move"] else ""
        print(
            f"  Day {day['day']}  {day['location']:<18} "
            f"scenes {','.join(day['scene_numbers']):<10} "
            f"{day['total_eighths']}/8  night={day['night_work']}{move}"
        )
        print(f"          {day['rationale']}")
    print()

    print("=" * 72)
    print("CAST DAYS (day out of days)")
    print("=" * 72)
    for row in doc["budget"]["cast_days"]:
        print(
            f"  {row['name']:<16} {row['days']} day(s)   "
            f"scenes {','.join(row['scene_numbers'])}"
        )
    print()

    print("=" * 72)
    print("BUDGET")
    print("=" * 72)
    low = high = 0.0
    for item in doc["budget"]["line_items"]:
        low += item["estimate_low"]
        high += item["estimate_high"]
        print(f"  {item['category']:<18} {item['label']:<34} "
              f"{item['estimate_low']:>10,.0f} - {item['estimate_high']:>10,.0f}")
        print(f"    basis: {item['basis']}")
    print("-" * 72)
    print(f"  {'TOTAL':<53} {low:>10,.0f} - {high:>10,.0f}")
    print()

    # --- schema validation ---------------------------------------------------
    schema = json.loads(
        (ROOT / "schema" / "scene-element.schema.json").read_text(encoding="utf-8")
    )
    try:
        import jsonschema
    except ImportError:
        print("!! jsonschema not installed — skipping validation")
        return 0

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        print(f"!! {len(errors)} SCHEMA VIOLATION(S)")
        for err in errors[:15]:
            path = "/".join(str(p) for p in err.path) or "<root>"
            print(f"   {path}: {err.message}")
        return 1

    print("SCHEMA: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
