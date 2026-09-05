from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class Project(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    scene_count: int = 0
    source_filename: str | None = None
    source_path: str | None = None
    breakdown_json: str
    progress_stage: str = "parse"
    progress_pct: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProjectSummary(SQLModel):
    id: int
    title: str
    status: str
    scene_count: int
    created_at: datetime