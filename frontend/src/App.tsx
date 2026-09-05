import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

type Project = {
  id: number;
  title: string;
  status: string;
  scene_count: number;
  created_at: string;
};

type Element = {
  category: string;
  name: string;
  quantity?: number | null;
  speaking?: boolean | null;
  notes?: string | null;
  source_quote?: string | null;
};

type Scene = {
  scene_number: string;
  int_ext: "INT" | "EXT" | "INT/EXT";
  set_name: string;
  location?: string | null;
  time_of_day: string;
  page_start: number;
  eighths: number;
  synopsis: string;
  body?: string;
  elements: Element[];
  continuity_flags?: { severity: string; issue: string }[];
};

type Breakdown = {
  script: { title: string; author?: string; total_pages: number; source_format?: string };
  scenes: Scene[];
  schedule?: ScheduleDay[];
  budget?: {
    cast_days?: { name: string; days: number; scene_numbers: string[] }[];
  };
  warnings?: { code: string; message: string }[];
};

type ScheduleDay = {
  day: number;
  scene_numbers: string[];
  location?: string | null;
  total_eighths?: number | null;
  company_move?: boolean;
  night_work?: boolean;
};

type ProgressEvent = {
  stage: Stage;
  status: "running" | "complete";
  message: string;
  pct: number;
};

type Stage = "parse" | "split" | "tag" | "schedule" | "budget" | "qa";
type View = "projects" | "upload" | "progress" | "breakdown";

const STAGES: { id: Stage; label: string }[] = [
  { id: "parse", label: "Parse script" },
  { id: "split", label: "Split scenes" },
  { id: "tag", label: "Tag elements" },
  { id: "schedule", label: "Build schedule" },
  { id: "budget", label: "Estimate budget" },
  { id: "qa", label: "Continuity QA" },
];

const categoryLabel = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

function highlightedSceneBody(body: string, quote?: string | null) {
  if (!quote) return body;
  let start = body.indexOf(quote);
  if (start < 0) start = body.toLocaleLowerCase().indexOf(quote.toLocaleLowerCase());
  if (start < 0) return body;
  const end = start + quote.length;
  return (
    <>
      {body.slice(0, start)}
      <mark>{body.slice(start, end)}</mark>
      {body.slice(end)}
    </>
  );
}

const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

function pageCount(eighths: number) {
  const pages = eighths / 8;
  return `${pages.toFixed(pages % 1 === 0 ? 0 : 2)} page${pages === 1 ? "" : "s"}`;
}

function scheduledScenes(breakdown: Breakdown) {
  const sceneByNumber = new Map(breakdown.scenes.map((scene) => [scene.scene_number, scene]));
  let previousLight: "day" | "night" = "day";
  return (breakdown.schedule ?? []).map((day) => ({
    ...day,
    scenes: day.scene_numbers.flatMap((number) => {
      const scene = sceneByNumber.get(number);
      if (!scene) return [];
      if (scene.time_of_day === "DAY") previousLight = "day";
      else if (["NIGHT", "DAWN", "DUSK"].includes(scene.time_of_day)) previousLight = "night";
      const setting = scene.int_ext === "EXT" ? "ext" : "int";
      return [{ scene, boardColor: `${setting}-${previousLight}` }];
    }),
  }));
}

function Stripboard({ breakdown }: { breakdown: Breakdown }) {
  const days = scheduledScenes(breakdown);
  if (!days.length) return <p className="schedule-empty">No shooting schedule is available.</p>;
  return (
    <section className="schedule-section">
      <div className="schedule-title">
        <div><p className="eyebrow">Production board</p><h2>Stripboard</h2></div>
        <div className="strip-legend" aria-label="Strip color legend">
          <span className="int-day">INT DAY</span><span className="ext-day">EXT DAY</span>
          <span className="int-night">INT NIGHT</span><span className="ext-night">EXT NIGHT</span>
        </div>
      </div>
      <div className="stripboard">
        {days.map((day) => {
          const totalEighths = day.total_eighths ?? day.scenes.reduce((sum, item) => sum + item.scene.eighths, 0);
          return (
            <section className="shooting-day" key={day.day}>
              <header className="day-banner">
                <strong>DAY {day.day} — {WEEKDAYS[(day.day - 1) % WEEKDAYS.length]}</strong>
                <span className="day-location">{day.location ?? "Location TBD"}</span>
                <span>{totalEighths}/8 · {pageCount(totalEighths)}</span>
                <span className="day-badges">
                  {day.company_move && <b>COMPANY MOVE</b>}
                  {day.night_work && <b>NIGHT</b>}
                </span>
              </header>
              <div className="day-strips">
                {day.scenes.map(({ scene, boardColor }) => (
                  <div className={`production-strip ${boardColor}`} key={scene.scene_number}>
                    <strong className="strip-scene">{scene.scene_number}</strong>
                    <span className="strip-setting">{scene.int_ext} / {scene.time_of_day}</span>
                    <strong className="strip-set">{scene.set_name}</strong>
                    <span className="strip-synopsis">{scene.synopsis}</span>
                    <strong className="strip-eighths">{scene.eighths}/8</strong>
                    <span className="strip-cast">{scene.elements.filter((element) => element.category === "cast").length} cast</span>
                  </div>
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </section>
  );
}

function DayOutOfDays({ breakdown }: { breakdown: Breakdown }) {
  const days = breakdown.schedule ?? [];
  const cast = breakdown.budget?.cast_days ?? [];
  if (!days.length || !cast.length) return <p className="schedule-empty">No cast day schedule is available.</p>;

  return (
    <section className="schedule-section">
      <div className="schedule-title"><div><p className="eyebrow">Cast schedule</p><h2>Day Out of Days</h2></div></div>
      <div className="dood-scroll">
        <table className="dood-grid">
          <thead>
            <tr>
              <th>Cast member</th>
              {days.map((day) => <th key={day.day}>D{day.day}<small>{WEEKDAYS[(day.day - 1) % WEEKDAYS.length]}</small></th>)}
              <th>Work</th><th>Hold</th>
            </tr>
          </thead>
          <tbody>
            {cast.map((member) => {
              const sceneNumbers = new Set(member.scene_numbers);
              const works = days.map((day) => day.scene_numbers.some((scene) => sceneNumbers.has(scene)));
              const first = works.indexOf(true);
              const last = works.lastIndexOf(true);
              const notations = works.map((worksToday, index) => {
                if (worksToday && first === last) return "SWF";
                if (worksToday && index === first) return "SW";
                if (worksToday && index === last) return "WF";
                if (worksToday) return "W";
                if (index > first && index < last) return "H";
                return "";
              });
              const holdDays = notations.filter((notation) => notation === "H").length;
              return (
                <tr key={member.name}>
                  <th>{member.name}</th>
                  {notations.map((notation, index) => (
                    <td className={notation ? `dood-${notation.toLowerCase()}` : ""} key={days[index].day}>{notation}</td>
                  ))}
                  <td className="dood-total">{works.filter(Boolean).length}</td>
                  <td className="dood-total">{holdDays}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="dood-key"><strong>SW</strong> Start Work · <strong>W</strong> Work · <strong>WF</strong> Work Finish · <strong>SWF</strong> Start Work Finish · <strong>H</strong> Hold</p>
    </section>
  );
}

function App() {
  const [view, setView] = useState<View>("projects");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [breakdown, setBreakdown] = useState<Breakdown | null>(null);
  const [error, setError] = useState("");

  const loadProjects = async () => {
    const response = await fetch("/api/projects");
    if (!response.ok) throw new Error("Could not load projects.");
    setProjects(await response.json());
  };

  useEffect(() => {
    loadProjects().catch((cause) => setError(cause.message));
  }, []);

  const openProject = async (id: number) => {
    setError("");
    const response = await fetch(`/api/projects/${id}`);
    if (!response.ok) {
      setError("Could not open this breakdown.");
      return;
    }
    setBreakdown(await response.json());
    setProjectId(id);
    setView("breakdown");
  };

  const openDemo = () => {
    const demo = projects.find((project) => project.status === "complete") ?? projects[0];
    if (demo) openProject(demo.id);
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="wordmark" onClick={() => setView("projects")}>STRIPBOARD</button>
        <nav aria-label="Primary">
          <button className={view === "projects" ? "active" : ""} onClick={() => setView("projects")}>Projects</button>
          <button className={view === "upload" ? "active" : ""} onClick={() => setView("upload")}>New breakdown</button>
        </nav>
      </header>
      {error && <div className="global-error" role="alert">{error}</div>}
      {view === "projects" && (
        <ProjectsScreen
          projects={projects}
          onOpen={openProject}
          onDemo={openDemo}
          onNew={() => setView("upload")}
          onDelete={async (id) => {
            await fetch(`/api/projects/${id}`, { method: "DELETE" });
            await loadProjects();
          }}
        />
      )}
      {view === "upload" && (
        <UploadScreen
          onCreated={(id) => {
            setProjectId(id);
            setView("progress");
          }}
        />
      )}
      {view === "progress" && projectId && (
        <ProgressScreen projectId={projectId} onComplete={() => openProject(projectId)} />
      )}
      {view === "breakdown" && breakdown && projectId && (
        <BreakdownScreen breakdown={breakdown} projectId={projectId} />
      )}
    </div>
  );
}

function ProjectsScreen({
  projects,
  onOpen,
  onDemo,
  onNew,
  onDelete,
}: {
  projects: Project[];
  onOpen: (id: number) => void;
  onDemo: () => void;
  onNew: () => void;
  onDelete: (id: number) => void;
}) {
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Production workspace</p>
          <h1>Script breakdowns</h1>
          <p>Review scene elements, source evidence, and scheduling inputs.</p>
        </div>
        <div className="heading-actions">
          <button className="button secondary" onClick={onDemo} disabled={!projects.length}>Open demo project</button>
          <button className="button primary" onClick={onNew}>New breakdown</button>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Project</th><th>Status</th><th>Scenes</th><th>Created</th><th><span className="sr-only">Actions</span></th></tr>
          </thead>
          <tbody>
            {projects.map((project) => (
              <tr key={project.id}>
                <td><button className="text-link" onClick={() => onOpen(project.id)}>{project.title}</button></td>
                <td><span className={`status status-${project.status}`}>{project.status}</span></td>
                <td>{project.scene_count}</td>
                <td>{new Date(project.created_at).toLocaleDateString()}</td>
                <td className="row-actions">
                  <button onClick={() => onOpen(project.id)}>Open</button>
                  <button onClick={() => onDelete(project.id)}>Delete</button>
                </td>
              </tr>
            ))}
            {!projects.length && <tr><td colSpan={5} className="empty">No projects yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </main>
  );
}

function UploadScreen({ onCreated }: { onCreated: (id: number) => void }) {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectFile = (candidate?: File) => {
    if (!candidate) return;
    const suffix = candidate.name.toLowerCase().split(".").pop();
    if (!["fountain", "txt", "pdf"].includes(suffix ?? "")) {
      setError("Upload a .fountain, .txt, or .pdf screenplay instead.");
      setFile(null);
      return;
    }
    if (candidate.size > 10 * 1024 * 1024) {
      setError("This file is over 10 MB. Upload a smaller screenplay.");
      setFile(null);
      return;
    }
    setError("");
    setFile(candidate);
    if (!title) setTitle(candidate.name.replace(/\.[^.]+$/, ""));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !title.trim()) {
      setError("Add a title and screenplay file.");
      return;
    }
    setSubmitting(true);
    const body = new FormData();
    body.append("title", title.trim());
    body.append("file", file);
    const response = await fetch("/api/projects", { method: "POST", body });
    const payload = await response.json();
    setSubmitting(false);
    if (!response.ok) {
      setError(payload.detail ?? "Upload failed.");
      return;
    }
    onCreated(payload.project_id);
  };

  return (
    <main className="page narrow">
      <p className="eyebrow">New project</p>
      <h1>Upload a screenplay</h1>
      <p>Upload a screenplay to analyze scenes, production elements, schedule, and budget.</p>
      <form onSubmit={submit}>
        <label className="field">
          <span>Project title</span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Untitled production" />
        </label>
        <div
          className="dropzone"
          tabIndex={0}
          role="button"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(event) => event.key === "Enter" && inputRef.current?.click()}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            selectFile(event.dataTransfer.files[0]);
          }}
        >
          <strong>{file ? file.name : "Drop screenplay here"}</strong>
          <span>{file ? `${(file.size / 1024).toFixed(0)} KB` : "or choose a file · fountain, txt, pdf · 10 MB max"}</span>
          <input ref={inputRef} hidden type="file" accept=".fountain,.txt,.pdf" onChange={(event) => selectFile(event.target.files?.[0])} />
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button primary" disabled={submitting}>{submitting ? "Uploading…" : "Start breakdown"}</button>
      </form>
    </main>
  );
}

function ProgressScreen({ projectId, onComplete }: { projectId: number; onComplete: () => void }) {
  const [events, setEvents] = useState<Partial<Record<Stage, ProgressEvent>>>({});
  const [pct, setPct] = useState(0);
  const [connectionMessage, setConnectionMessage] = useState("");

  useEffect(() => {
    const source = new EventSource(`/api/projects/${projectId}/events`);
    source.onopen = () => setConnectionMessage("");
    source.onmessage = (message) => {
      const event: ProgressEvent = JSON.parse(message.data);
      setEvents((current) => ({ ...current, [event.stage]: event }));
      setPct(event.pct);
      if (event.stage === "qa" && event.status === "complete") {
        source.close();
        window.setTimeout(onComplete, 500);
      }
    };
    source.onerror = () => setConnectionMessage("Connection interrupted. Reconnecting to the current stage…");
    return () => source.close();
  }, [projectId, onComplete]);

  return (
    <main className="page narrow">
      <p className="eyebrow">Processing project {projectId}</p>
      <h1>Building the breakdown</h1>
      <div className="progress-meter"><span style={{ width: `${pct}%` }} /></div>
      <p className="progress-number">{pct}% complete</p>
      {connectionMessage && <p className="form-error" role="status">{connectionMessage}</p>}
      <ol className="stage-list">
        {STAGES.map((stage, index) => {
          const event = events[stage.id];
          return (
            <li key={stage.id} className={event?.status ?? (index === 0 ? "waiting" : "")}>
              <span className="stage-mark">{event?.status === "complete" ? "✓" : event?.status === "running" ? "•" : index + 1}</span>
              <div><strong>{stage.label}</strong><p>{event?.message ?? "Waiting"}</p></div>
            </li>
          );
        })}
      </ol>
    </main>
  );
}

function BreakdownScreen({ breakdown, projectId }: { breakdown: Breakdown; projectId: number }) {
  const [tab, setTab] = useState<"breakdown" | "schedule">("breakdown");
  const [selected, setSelected] = useState(breakdown.scenes[0]?.scene_number ?? "");
  const [intExt, setIntExt] = useState("ALL");
  const [time, setTime] = useState("ALL");
  const [hoveredQuote, setHoveredQuote] = useState<string | null>(null);
  const [selectedQuote, setSelectedQuote] = useState<string | null>(null);
  const scene = breakdown.scenes.find((item) => item.scene_number === selected) ?? breakdown.scenes[0];
  const activeQuote = hoveredQuote ?? selectedQuote;
  const filtered = breakdown.scenes.filter((item) => (intExt === "ALL" || item.int_ext === intExt) && (time === "ALL" || item.time_of_day === time));

  const index = useMemo(() => {
    const map = new Map<string, { category: string; name: string; scenes: string[] }>();
    breakdown.scenes.forEach((item) => item.elements.forEach((element) => {
      const key = `${element.category}:${element.name}`;
      const current = map.get(key) ?? { category: element.category, name: element.name, scenes: [] };
      current.scenes.push(item.scene_number);
      map.set(key, current);
    }));
    return [...map.values()].sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
  }, [breakdown]);

  const grouped = useMemo(() => {
    const map = new Map<string, Element[]>();
    scene?.elements.forEach((element) => map.set(element.category, [...(map.get(element.category) ?? []), element]));
    return [...map.entries()];
  }, [scene]);

  return (
    <main className="breakdown-page">
      <div className="breakdown-heading">
        <div>
          {breakdown.script.author && <p className="eyebrow">{breakdown.script.author}</p>}
          <h1>{breakdown.script.title}</h1>
          <p>{breakdown.scenes.length} scenes · {breakdown.script.total_pages} pages</p>
        </div>
        <a className="button secondary" href={`/api/projects/${projectId}/export.csv`}>Export CSV</a>
      </div>
      <div className="tabs">
        <button className={tab === "breakdown" ? "active" : ""} onClick={() => setTab("breakdown")}>Breakdown</button>
        <button className={tab === "schedule" ? "active" : ""} onClick={() => setTab("schedule")}>Schedule</button>
      </div>
      {tab === "schedule" ? (
        <div className="schedule-views">
          <Stripboard breakdown={breakdown} />
          <DayOutOfDays breakdown={breakdown} />
        </div>
      ) : (
        <div className="three-pane">
          <aside className="scene-pane">
            <div className="pane-title"><h2>Scenes</h2><span>{filtered.length}</span></div>
            <div className="filters">
              <select aria-label="Interior or exterior" value={intExt} onChange={(event) => setIntExt(event.target.value)}>
                <option>ALL</option><option>INT</option><option>EXT</option><option>INT/EXT</option>
              </select>
              <select aria-label="Time of day" value={time} onChange={(event) => setTime(event.target.value)}>
                <option>ALL</option>{[...new Set(breakdown.scenes.map((item) => item.time_of_day))].map((value) => <option key={value}>{value}</option>)}
              </select>
            </div>
            <div className="scene-list">
              {filtered.map((item) => (
                <button key={item.scene_number} className={item.scene_number === scene.scene_number ? "selected" : ""} onClick={() => { setSelected(item.scene_number); setHoveredQuote(null); setSelectedQuote(null); }}>
                  <span className="scene-number">{item.scene_number}</span>
                  <span><strong>{item.int_ext}. {item.set_name}</strong><small>{item.time_of_day} · {item.eighths}/8</small></span>
                </button>
              ))}
            </div>
          </aside>
          <section className="scene-detail">
            <div className="scene-meta"><span>SCENE {scene.scene_number}</span><span>{scene.int_ext}. {scene.set_name} — {scene.time_of_day}</span><span>{scene.eighths}/8 page</span></div>
            <h2>{scene.synopsis}</h2>
            <div className="script-excerpt">
              {highlightedSceneBody(scene.body ?? "", activeQuote)}
            </div>
            <div className="element-groups">
              {grouped.map(([category, elements]) => (
                <section key={category}>
                  <h3>{categoryLabel(category)}</h3>
                  <div>{elements.map((element) => (
                    <button
                      type="button"
                      className={`element-tag${selectedQuote === element.source_quote && element.source_quote ? " selected" : ""}`}
                      key={element.name}
                      title={element.source_quote ?? "No source quote"}
                      onMouseEnter={() => setHoveredQuote(element.source_quote ?? null)}
                      onMouseLeave={() => setHoveredQuote(null)}
                      onFocus={() => setHoveredQuote(element.source_quote ?? null)}
                      onBlur={() => setHoveredQuote(null)}
                      onClick={() => setSelectedQuote((current) => current === element.source_quote ? null : element.source_quote ?? null)}
                    >
                      {element.name}{element.quantity && element.quantity > 1 ? ` ×${element.quantity}` : ""}
                    </button>
                  ))}</div>
                </section>
              ))}
            </div>
            {!!scene.continuity_flags?.length && (
              <div className="continuity"><strong>Continuity</strong>{scene.continuity_flags.map((flag) => <p key={flag.issue}>{flag.issue}</p>)}</div>
            )}
          </section>
          <aside className="index-pane">
            <div className="pane-title"><h2>Element index</h2><span>{index.length}</span></div>
            <div className="index-list">
              {index.map((item) => (
                <div key={`${item.category}:${item.name}`}>
                  <small>{categoryLabel(item.category)}</small>
                  <strong>{item.name}</strong>
                  <p>Scenes {item.scenes.join(", ")}</p>
                </div>
              ))}
            </div>
          </aside>
        </div>
      )}
    </main>
  );
}

export default App;