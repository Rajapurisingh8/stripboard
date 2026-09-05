# Stripboard

Stripboard is a script breakdown and scheduling shell for film production. It uploads Fountain, text, and PDF screenplays, simulates a six-stage analysis pipeline, and presents a full scene-and-element breakdown from a local fixture.

The analysis is intentionally stubbed. There are no AI calls in this repository.

## Setup

Requirements:

- Python 3.11
- Node.js 20+

Install dependencies and build the frontend:

```bash
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt
npm --prefix frontend install
npm run build
```

Run the combined application:

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 5000
```

Open the Replit web preview. The demo project is seeded on first boot and can be opened without an account.

## Configuration

The server checks these environment variables at startup and logs only whether each is present:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GEMINI_MODEL`
- `GOOGLE_APPLICATION_CREDENTIALS_JSON`

They are reserved for the future analysis implementation and are not used by the current fixture pipeline.

## Architecture

FastAPI serves both the `/api` routes and the built React frontend from one process. Project metadata and fixture breakdowns are stored in a single SQLite file under `data/`; uploaded source files are stored under `uploads/`.

The planned analysis pipeline lives in `app/agents/pipeline.py`:

1. **Parse** — read screenplay structure and source format.
2. **Split** — normalize sluglines into production scenes.
3. **Tag** — identify production elements and retain source quotes.
4. **Schedule** — group scenes into a practical shooting order.
5. **Budget** — estimate production ranges with explicit assumptions.
6. **QA** — check continuity and validate the output contract.

For now, the API walks through these stages over Server-Sent Events and returns `fixtures/breakdown_demo.json`. The fixture is validated in development against `schema/scene-element.schema.json`, so contract drift fails loudly.
