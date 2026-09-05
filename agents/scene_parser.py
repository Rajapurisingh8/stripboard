"""Deterministic Fountain scene splitter.

Stage 1 of the pipeline. No model calls: Fountain sluglines are structured
text, so parsing them with a model would be slower, costlier, and less
correct than reading them directly. The model's judgment is spent on
tagging and continuity instead, where it is actually needed.

Emits objects conforming to the `scene` definition in
schema/scene-element.schema.json, minus the fields the tagger fills in
(synopsis, elements, script_day, location, confidence).

Anything the parser cannot read becomes a warning rather than a guess or
an exception. A breakdown with a flagged scene is useful; a crash is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- constants ---------------------------------------------------------------

#: Lines of Fountain body text that correspond to one printed page. A US
#: screenplay page is ~55 lines at 12pt Courier; Fountain omits some of the
#: blocking whitespace, so this is deliberately approximate. Eighths are a
#: scheduling estimate, not a measurement.
LINES_PER_PAGE = 55

EIGHTHS_PER_PAGE = 8

#: From the schema's time_of_day enum. Order matters: longer keys first so
#: "MOMENTS LATER" is matched before "LATER" would be.
TIME_OF_DAY = (
    "MOMENTS LATER",
    "CONTINUOUS",
    "NIGHT",
    "DAWN",
    "DUSK",
    "DAY",
)

#: Common slugline synonyms mapped onto the enum. Real scripts are not tidy.
TIME_ALIASES = {
    "MORNING": "DAY",
    "AFTERNOON": "DAY",
    "EVENING": "NIGHT",
    "LATER": "MOMENTS LATER",
    "SAME": "CONTINUOUS",
    "SUNRISE": "DAWN",
    "SUNSET": "DUSK",
    "MAGIC HOUR": "DUSK",
}

_SCENE_HEADING = re.compile(
    r"^(?P<prefix>INT\.?/EXT\.?|EXT\.?/INT\.?|I/E\.?|INT\.?|EXT\.?)"
    r"(?P<rest>[\s.].*)$",
    re.IGNORECASE,
)

#: A forced scene heading in Fountain: a line beginning with a single period.
_FORCED_HEADING = re.compile(r"^\.(?![.\s])(?P<rest>.+)$")

#: Trailing scene number, e.g. "INT. KITCHEN - NIGHT  #14A#" or "... 14A"
_TRAILING_NUMBER = re.compile(r"\s*#(?P<num>[\w.\-]+)#\s*$")

_SCRIPT_DAY_HINT = re.compile(r"\bSCRIPT\s+DAY\s+(?P<day>\d+)\b", re.IGNORECASE)


# --- data --------------------------------------------------------------------


@dataclass
class ParsedScene:
    """One scene, before the tagger enriches it."""

    scene_number: str
    int_ext: str
    set_name: str
    time_of_day: str
    page_start: float
    eighths: int
    body: str
    script_day_hint: int | None = None

    def to_schema(self) -> dict[str, Any]:
        """Return the schema-shaped dict, with tagger fields left absent.

        `synopsis` and `elements` are required by the schema, so they are
        seeded with placeholders the tagger overwrites. If the tagger fails
        for a scene, these are what survive — a valid document with an
        honest empty tag list, not an invalid one.
        """
        return {
            "scene_number": self.scene_number,
            "int_ext": self.int_ext,
            "set_name": self.set_name,
            "time_of_day": self.time_of_day,
            "page_start": round(self.page_start, 2),
            "eighths": self.eighths,
            "synopsis": self.set_name.title(),
            "elements": [],
        }


@dataclass
class ParseResult:
    script: dict[str, Any]
    scenes: list[ParsedScene]
    warnings: list[dict[str, Any]] = field(default_factory=list)


# --- helpers -----------------------------------------------------------------


def _normalise_int_ext(prefix: str) -> str:
    """Map a slugline prefix onto the schema's int_ext enum."""
    p = prefix.upper().replace(".", "").replace(" ", "")
    if p in {"INT/EXT", "EXT/INT", "I/E"}:
        return "INT/EXT"
    if p == "EXT":
        return "EXT"
    return "INT"


def _split_slug(rest: str) -> tuple[str, str | None]:
    """Split the post-prefix slugline into (set_name, time_of_day).

    Fountain convention separates set from time with " - ", but em dashes and
    double hyphens both appear in the wild.
    """
    cleaned = rest.strip().lstrip(".").strip()
    cleaned = re.sub(r"\s*[—–]\s*|\s+--\s+", " - ", cleaned)

    parts = [p.strip() for p in cleaned.split(" - ") if p.strip()]
    if not parts:
        return "", None

    tail = parts[-1].upper()

    # Exact match on the enum or a known alias.
    if tail in TIME_ALIASES:
        return " - ".join(parts[:-1]) or parts[-1], TIME_ALIASES[tail]
    for tod in TIME_OF_DAY:
        if tail == tod:
            return " - ".join(parts[:-1]) or parts[-1], tod

    # Suffix match, e.g. "LATER THAT NIGHT" or "NIGHT (FLASHBACK)".
    for tod in TIME_OF_DAY:
        if re.search(rf"\b{re.escape(tod)}\b", tail):
            return " - ".join(parts[:-1]) or parts[-1], tod

    # No recognisable time of day — the whole thing is the set name.
    return " - ".join(parts), None


def _eighths_for(line_count: int) -> int:
    """Convert body line count to page eighths, floored at one."""
    pages = line_count / LINES_PER_PAGE
    return max(1, round(pages * EIGHTHS_PER_PAGE))


def _parse_title_page(block: str) -> dict[str, Any]:
    """Read Fountain title-page key/value pairs."""
    meta: dict[str, Any] = {}
    key = None
    for raw in block.splitlines():
        if not raw.strip():
            continue
        match = re.match(r"^(?P<key>[A-Za-z ]+):\s*(?P<val>.*)$", raw)
        if match:
            key = match.group("key").strip().lower()
            meta[key] = match.group("val").strip()
        elif key and raw.startswith((" ", "\t")):
            meta[key] = f"{meta[key]} {raw.strip()}".strip()
    return meta


# --- entry point -------------------------------------------------------------


def split_scenes(text: str, source_format: str = "fountain") -> ParseResult:
    """Split a Fountain screenplay into scenes.

    Args:
        text: Raw screenplay text.
        source_format: Recorded on the output; one of the schema's
            source_format enum values.

    Returns:
        A ParseResult carrying script metadata, scenes in script order, and
        any warnings the UI should surface.
    """
    warnings: list[dict[str, Any]] = []

    # Title page is everything before the first "===" fence, when present.
    meta: dict[str, Any] = {}
    body = text
    if "===" in text:
        head, _, body = text.partition("===")
        meta = _parse_title_page(head)
        body = body.lstrip("=\n")

    lines = body.splitlines()

    # Locate scene headings first, then slice bodies between them. Doing it in
    # two passes keeps the line arithmetic honest.
    heading_indices: list[tuple[int, str, str]] = []  # (line_no, prefix, rest)
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        forced = _FORCED_HEADING.match(line)
        if forced:
            heading_indices.append((i, "INT", forced.group("rest")))
            continue

        match = _SCENE_HEADING.match(line)
        if match:
            heading_indices.append((i, match.group("prefix"), match.group("rest")))

    if not heading_indices:
        warnings.append(
            {
                "code": "no_scenes_found",
                "message": (
                    "No scene headings found. Fountain expects sluglines "
                    "beginning INT. or EXT., or a forced heading starting "
                    "with a period."
                ),
            }
        )
        return ParseResult(
            script=_script_meta(meta, 0.0, source_format), scenes=[], warnings=warnings
        )

    scenes: list[ParsedScene] = []
    cursor_page = 1.0

    for idx, (line_no, prefix, rest) in enumerate(heading_indices):
        end = (
            heading_indices[idx + 1][0]
            if idx + 1 < len(heading_indices)
            else len(lines)
        )
        scene_body = "\n".join(lines[line_no:end]).strip()

        # An explicit #14A# number wins over positional numbering.
        explicit = _TRAILING_NUMBER.search(rest)
        if explicit:
            scene_number = explicit.group("num")
            rest = _TRAILING_NUMBER.sub("", rest)
        else:
            scene_number = str(idx + 1)

        set_name, tod = _split_slug(rest)

        if not set_name:
            set_name = "UNKNOWN SET"
            warnings.append(
                {
                    "code": "unreadable_slugline",
                    "message": f"Could not read a set name from: {lines[line_no].strip()!r}",
                    "scene_number": scene_number,
                }
            )

        if tod is None:
            tod = "UNSPECIFIED"
            warnings.append(
                {
                    "code": "missing_time_of_day",
                    "message": (
                        f"No time of day in slugline {lines[line_no].strip()!r}. "
                        "Night work and turnaround cannot be scheduled for this scene."
                    ),
                    "scene_number": scene_number,
                }
            )

        # Body line count excludes the heading itself.
        eighths = _eighths_for(max(1, end - line_no - 1))

        day_hint = _SCRIPT_DAY_HINT.search(scene_body)

        scenes.append(
            ParsedScene(
                scene_number=scene_number,
                int_ext=_normalise_int_ext(prefix),
                set_name=set_name.upper(),
                time_of_day=tod,
                page_start=cursor_page,
                eighths=eighths,
                body=scene_body,
                script_day_hint=int(day_hint.group("day")) if day_hint else None,
            )
        )

        cursor_page += eighths / EIGHTHS_PER_PAGE

    total_pages = round(sum(s.eighths for s in scenes) / EIGHTHS_PER_PAGE, 2)
    return ParseResult(
        script=_script_meta(meta, total_pages, source_format),
        scenes=scenes,
        warnings=warnings,
    )


def _script_meta(
    meta: dict[str, Any], total_pages: float, source_format: str
) -> dict[str, Any]:
    """Build the schema's `script` object, omitting absent optional keys."""
    out: dict[str, Any] = {
        "title": meta.get("title") or "Untitled",
        "total_pages": total_pages,
        "source_format": source_format,
        "parsed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    author = meta.get("author") or meta.get("authors")
    # A placeholder left in a template is worse than no author at all.
    if author and not re.fullmatch(r"\[.*\]", author.strip()):
        out["author"] = author
    return out
