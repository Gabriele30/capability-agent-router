"""Pydantic models for deterministic repository intelligence."""

from pathlib import Path

from pydantic import BaseModel, Field


class GitState(BaseModel):
    """A concise snapshot of a repository's Git working tree."""

    available: bool
    is_repository: bool = False
    branch: str | None = None
    modified_files: list[str] = Field(default_factory=list)
    staged_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.modified_files or self.staged_files or self.untracked_files)


class LanguageStats(BaseModel):
    """Counts of source files grouped by detected language."""

    counts: dict[str, int] = Field(default_factory=dict)


class ProjectSignals(BaseModel):
    """Manifest and build-system signals found at repository root."""

    systems: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)


class RepositoryState(BaseModel):
    """The domain representation returned by the repository scanner."""

    root: Path
    name: str
    git: GitState
    tracked_file_count: int = 0
    languages: LanguageStats = Field(default_factory=LanguageStats)
    project_signals: ProjectSignals = Field(default_factory=ProjectSignals)
