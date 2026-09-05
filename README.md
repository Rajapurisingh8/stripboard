# Stripboard

**Upload a screenplay. Get a first AD's script breakdown.**

Stripboard splits a screenplay into scenes, tags every production element, groups the scenes into shooting days, estimates cost, and flags continuity risk — then outputs a **stripboard** and a **day out of days**, the two documents a production office already knows how to read.

A first assistant director does this by hand. On a feature it takes two to three days, and it is the document every other department's schedule is derived from.

## Try it

**[Live demo →](https://stripboard.replit.app)**

No account, no upload required. Click **Open demo project** and you get a complete breakdown of *The Last Shift*, an original five-scene screenplay included in this repo.

To run it on your own material, click **New breakdown** and drop in a `.fountain`, `.txt`, or `.pdf`.

## What it produces

**Breakdown** — every scene with its slugline, page eighths, a one-line synopsis, and its tagged production elements across nineteen categories (cast, background, stunts, vehicles, props, set dressing, wardrobe, makeup/hair, practical and visual effects, animals and handlers, music, sound, special equipment, greenery, security, additional labour). Every element carries the verbatim quote from the script that justifies it, so a human can check the machine rather than trust it.

**Stripboard** — scenes grouped into shooting days, colour-coded by the standard production board convention: white for interior day, yellow for exterior day, blue for interior night, green for exterior night. Company moves and night work are badged. Each day carries a plain-English rationale for why those scenes are together.

**Day Out of Days** — cast against shooting days in real DOOD notation: `SW` start work, `W` work, `WF` work finish, `SWF` single day, `H` hold. Hold days are totalled separately, because a held day is a day the production pays for and does not shoot.

**Budget** — line items derived from the breakdown against a visible rate card. Every line carries a `basis` string naming exactly what produced it: *"6 cast day(s) across 3 role(s), deduplicated by shooting day."* An estimate a producer cannot trace is an estimate they cannot use.

**Continuity** — cross-scene flags at three severities, each naming the scenes it spans.

## Architecture

Six stages, streamed to the browser over Server-Sent Events so the UI shows real progress rather than a spinner.

```
parse ─→ split ─→ tag ─→ schedule ─→ budget ─→ qa
```

| Module | Model? | Responsibility |
|---|:---:|---|
| `scene_parser.py` | — | Fountain sluglines → scenes, page eighths, positions |
| `element_tagger.py` | **Gemini** | Per scene, concurrent: elements, synopsis, script day, location, confidence |
| `entity_resolution.py` | — | Canonicalises element names across scenes |
| `scheduler.py` | — | Groups scenes into shooting days |
| `budget.py` | — | Cast days from the schedule, line items from a rate card |
| `continuity.py` | **Gemini** | One cross-script pass for continuity and scheduling risk |
| `pipeline.py` | — | Orchestration, progress events, schema validation |

### Agents where judgment is needed, deterministic code where correctness is provable

Two of the six stages call a model. That is a deliberate choice, not a shortcut.

Fountain sluglines are structured text — parsing them with a model would be slower, costlier, and less correct than reading them directly. Page eighths and cast days are arithmetic; a reviewer can check that eighths sum and that a cast member appearing in two scenes on one shooting day is owed one day, not two. Neither benefits from inference.

Element tagging and continuity are different. *Is a walk-in door that "hangs open, breathing cold fog" a set-dressing item, a practical effect, or both?* *Does extinguisher powder in a character's hair in scene 3 survive into scene 4, and are those scenes scheduled on different days?* Those need reading comprehension and cross-scene memory.

The result is **six model calls for a five-scene script rather than thirty** — a breakdown that finishes in seconds, and a repo where the interesting code is actually the interesting part.

### The scheduler groups; it does not optimise

Grouping is by practical location and shooting condition, in script order within a day. Dawn and dusk are treated as distinct from night, because they are hard windows of twenty or thirty minutes and cannot be absorbed into a night block — getting that wrong produces a schedule that looks efficient and is unshootable.

A real optimiser weighs cast availability, daylight, location holds and turnaround against each other. A half-built one produces schedules a first AD can immediately disprove. Grouping is what a human does on day one, it is explainable, and every strip carries a rationale you can argue with.

### What this project taught me

The tagger's prompt says, in plain language, that the same person must carry an identical name in every scene, *because day-out-of-days is built by matching those strings*.

The model **mostly** complied. Which is worse than never complying: `MARISOL` and `MARISOL VEGA` both survived, and three stages downstream the day out of days showed five cast members for three characters — a wrong document that looked entirely plausible.

**Anything downstream arithmetic depends on has to be enforced in code, not requested in a prompt.** `entity_resolution.py` is that rule as code: it merges names whose identifying words are a subset of another's, within a single category, and logs every merge. Conservative on purpose — a wrong merge is harder to notice than a missed one.

### Failure behaviour

A stage failing degrades the document; it does not fail the run. A breakdown with no continuity flags is still useful. A breakdown with one untagged scene is still worth reading. Every degradation appears in a `warnings` array the UI surfaces, and every response is validated against `schema/scene-element.schema.json` with violations logged loudly — schema drift between six agents and one contract is the failure this design is most exposed to, and it is silent unless something looks for it.

## Stack

React + Vite + TypeScript · Python 3.11 + FastAPI · SQLite via SQLModel · one process serving both.

Agents run on `google-genai` against **Vertex AI**, model `gemini-2.5-flash`, using structured output with an enforced response schema. `google-adk` is pinned for the agent tooling. **No other AI SDK appears anywhere in this repository** — not in `requirements.txt`, not in `package.json`, not commented out.

Built with **Replit Agent** and deployed on Replit.

## Running locally

```bash
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
uvicorn app.main:app --host 0.0.0.0 --port 5000
```

Environment:

| Variable | Purpose |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | GCP project with the Vertex AI API enabled |
| `GOOGLE_CLOUD_LOCATION` | Vertex region, e.g. `us-central1` |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Service-account key JSON, as a single-line string |
| `GEMINI_MODEL` | Optional override; defaults to `gemini-2.5-flash` |

The service account needs `roles/aiplatform.user`. On first boot the demo project is seeded by running the real pipeline over `demo/the-last-shift.fountain`.

Run the deterministic stages without spending a model call:

```bash
python tests/test_deterministic.py
```

It parses the real screenplay, runs the scheduler and budget over a fixed tagging result, and validates the whole document against the schema.

## The demo screenplay

`demo/the-last-shift.fountain` is an original screenplay written as test data. It deliberately exercises every breakdown category — a night exterior, a stunt, an animal, practical fire, a company move, and two script days across five scenes. It contains no third-party material.

## License

MIT. See [LICENSE](LICENSE).

---

Built for the Google Cloud Agentic Cinema Hackathon, Replit track.
