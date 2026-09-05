import json, os, tempfile
from google import genai
from google.genai import types

project = os.environ["GOOGLE_CLOUD_PROJECT"]
location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if raw:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(json.loads(raw), f); f.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name

client = genai.Client(vertexai=True, project=project, location=location)
print("client ok:", project, location)

names = []
try:
    for m in client.models.list():
        n = (getattr(m, "name", "") or "").rsplit("/", 1)[-1]
        if "gemini" in n:
            names.append(n)
    print("\nmodels visible:")
    for n in sorted(set(names)):
        print("  -", n)
except Exception as e:
    print("models.list failed:", type(e).__name__, e)

model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
print("\ncalling", model)
r = client.models.generate_content(
    model=model,
    contents=("Return JSON listing the production elements in this scene, "
              "each with category and name. Categories: cast, animals, props, vehicles.\n\n"
              "INT. DINER KITCHEN - NIGHT\n"
              "MARISOL scrapes the flat top. A SHEPHERD MIX pushes through "
              "the door, dragging a leash."),
    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
)
print(r.text)
print("\nGATE PASSED")