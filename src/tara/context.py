"""User-provided context loading for Tara analysis runs."""

from __future__ import annotations

import locale
import logging
from dataclasses import dataclass, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)

ALLOWED_CONTEXT_SUFFIXES = {".md", ".txt"}
GENERAL_CONTEXT_CHAR_LIMIT = 20_000
PRIOR_CONTEXT_CHAR_LIMIT = 50_000
SECRET_LINE_MARKERS = ("api_key", "token", "password", "secret")


@dataclass(frozen=True, slots=True)
class LoadedTextContext:
    """A loaded text context file with traceable path metadata."""

    path: Path | None
    text: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def path_str(self) -> str | None:
        """Return a serializable path string when a file was loaded."""
        return str(self.path) if self.path is not None else None


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """General and prior context loaded for one Tara analysis run."""

    general: LoadedTextContext
    prior: LoadedTextContext

    @property
    def warning_count(self) -> int:
        """Return the number of context-loading warnings."""
        return len(self.general.warnings) + len(self.prior.warnings)


def load_analysis_context(
    *,
    general_path: Path | None,
    prior_path: Path | None,
) -> AnalysisContext:
    """Load optional general and prior context files."""
    return AnalysisContext(
        general=load_text_context(
            general_path,
            label="context",
            char_limit=GENERAL_CONTEXT_CHAR_LIMIT,
        ),
        prior=load_text_context(
            prior_path,
            label="prior-context",
            char_limit=PRIOR_CONTEXT_CHAR_LIMIT,
        ),
    )


def load_text_context(
    path: Path | None,
    *,
    label: str,
    char_limit: int,
) -> LoadedTextContext:
    """Load an optional text context file with extension and size handling."""
    if path is None:
        return LoadedTextContext(path=None, text=None)
    if path.suffix.lower() not in ALLOWED_CONTEXT_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_CONTEXT_SUFFIXES))
        raise ValueError(f"{label} file must use one of these extensions: {allowed}")
    resolved = path.resolve()
    if not resolved.is_file():
        warning = f"{label} file does not exist and will be ignored: {resolved}"
        LOGGER.warning(warning)
        return LoadedTextContext(path=None, text=None, warnings=(warning,))
    text = _read_text_with_fallback(resolved)
    warnings: list[str] = []
    if len(text) > char_limit:
        text = text[:char_limit].rstrip() + "\n\n[truncated]\n"
        warnings.append(f"{label} file was truncated to {char_limit} characters.")
    return LoadedTextContext(path=resolved, text=text, warnings=tuple(warnings))


def resolve_context_path(
    value: str | Path | None,
    *,
    base_dir: Path | None,
) -> Path | None:
    """Resolve a context path, using the config directory for relative values."""
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute() or base_dir is None:
        return path
    return base_dir / path


def write_context_debug_file(path: Path, context: AnalysisContext) -> None:
    """Write redacted loaded context text to a debug artifact."""
    parts: list[str] = ["# Tara context debug\n"]
    parts.append("## Prior context\n")
    parts.append(_redact_context_text(context.prior.text or "(not loaded)"))
    parts.append("\n## General context\n")
    parts.append(_redact_context_text(context.general.text or "(not loaded)"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def _read_text_with_fallback(path: Path) -> str:
    """Read text as UTF-8, falling back to the system preferred encoding."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fallback = locale.getpreferredencoding(False)
        return path.read_text(encoding=fallback)


def _redact_context_text(text: str) -> str:
    """Redact lines that appear to contain credentials or tokens."""
    redacted: list[str] = []
    for line in text.splitlines():
        lower = line.casefold()
        if any(marker in lower for marker in SECRET_LINE_MARKERS):
            redacted.append("[redacted sensitive context line]")
        else:
            redacted.append(line)
    return "\n".join(redacted)
