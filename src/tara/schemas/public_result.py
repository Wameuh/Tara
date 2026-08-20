"""Versioned public result schema and safe internal-to-public projection."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .common import SCHEMA_VERSION, PortableMetadata, StrictSchema

SCHEMA_NAME = "tara.public_result"
SECTION_TYPE_ORDER = (
    "overview",
    "chronology",
    "characters",
    "quests",
    "combat",
    "locations",
    "items",
    "factions",
    "uncertainties",
    "generic",
)
SECTION_TYPES = frozenset(SECTION_TYPE_ORDER)
LEGACY_SECTION_TYPES = (
    ("resume_express", "overview"),
    ("executive_summary", "overview"),
    ("impacts", "generic"),
    ("key_points", "overview"),
    ("final_state", "generic"),
    ("chronology", "chronology"),
    ("characters", "characters"),
    ("quests", "quests"),
    ("combat", "combat"),
    ("locations", "locations"),
    ("items", "items"),
    ("factions", "factions"),
    ("uncertainties", "uncertainties"),
)
SectionId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
Text = Annotated[str, Field(min_length=1, max_length=20_000)]
MAX_PUBLIC_SOURCE_CHARS = 4 * 1024 * 1024
_PUBLIC_MARKDOWN = re.compile(
    r"```|(?m:^\s*(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+|(?:[-*_]\s*){3,}$))"
    r"|!?\[[^\]]*\]\([^\r\n)]*\)|\*\*|__|`"
    r"|(?<!\*)\*[^*\n]+\*(?!\*)|(?<!\w)_[^_\n]+_(?!\w)"
    r"|<!--[\s\S]*?-->|</?[A-Za-z][^>]*>"
)
_ABSOLUTE_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s]+|/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*)"
)


class ParagraphBlock(StrictSchema):
    type: Literal["paragraph"] = "paragraph"
    text: Text


class ListBlock(StrictSchema):
    type: Literal["list"] = "list"
    items: list[Text] = Field(min_length=1, max_length=200)


class OrderedListBlock(StrictSchema):
    type: Literal["orderedList"] = "orderedList"
    items: list[Text] = Field(min_length=1, max_length=200)


class KeyValueItem(StrictSchema):
    key: Annotated[str, Field(min_length=1, max_length=256)]
    value: Text


class KeyValueBlock(StrictSchema):
    type: Literal["keyValue"] = "keyValue"
    entries: list[KeyValueItem] = Field(min_length=1, max_length=200)


class TableBlock(StrictSchema):
    type: Literal["table"] = "table"
    headers: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        min_length=1, max_length=32
    )
    rows: list[list[Annotated[str, Field(max_length=10_000)]]] = Field(max_length=500)

    @model_validator(mode="after")
    def rectangular(self) -> TableBlock:
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("table rows must match headers")
        return self


class CalloutBlock(StrictSchema):
    type: Literal["callout"] = "callout"
    title: Annotated[str, Field(min_length=1, max_length=255)]
    text: Text


PublicBlock = (
    ParagraphBlock
    | ListBlock
    | OrderedListBlock
    | KeyValueBlock
    | TableBlock
    | CalloutBlock
)


class PublicSection(StrictSchema):
    section_id: SectionId
    section_type: Literal[
        "overview",
        "chronology",
        "characters",
        "quests",
        "combat",
        "locations",
        "items",
        "factions",
        "uncertainties",
        "generic",
    ]
    title: Annotated[str, Field(min_length=1, max_length=255)]
    blocks: list[PublicBlock] = Field(min_length=1, max_length=500)


class PublicResultContent(StrictSchema):
    title: Annotated[str, Field(min_length=1, max_length=255)]
    sections: list[PublicSection] = Field(min_length=1, max_length=100)

    @field_validator("sections")
    @classmethod
    def unique_ids(cls, sections: list[PublicSection]) -> list[PublicSection]:
        ids = [section.section_id for section in sections]
        if len(ids) != len(set(ids)):
            raise ValueError("section_id values must be unique")
        return sections


class PublicResult(StrictSchema):
    schema_name: Literal[SCHEMA_NAME] = SCHEMA_NAME
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    metadata: PortableMetadata = Field(default_factory=PortableMetadata)
    content: PublicResultContent

    @model_validator(mode="after")
    def reject_private_public_values(self) -> PublicResult:
        for value in _iter_public_strings(self.model_dump(mode="json")):
            if _PUBLIC_MARKDOWN.search(value):
                raise ValueError("public result contains raw Markdown")
            if _ABSOLUTE_PATH.search(value):
                raise ValueError("public result contains an absolute path")
        return self


_SLUG = re.compile(r"[^a-z0-9]+")


def _iter_public_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"prompt", "trace", "internal_id"}:
                raise ValueError("public result contains an internal field")
            yield from _iter_public_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_public_strings(item)


def stable_section_id(section_type: str, title: str, used: set[str]) -> str:
    """Return a bounded URL-fragment-safe ID independent from file paths."""
    prefix = section_type if section_type in SECTION_TYPES else "generic"
    bounded = title[:256]
    normalized = unicodedata.normalize("NFKD", bounded).encode("ascii", "ignore")
    normalized = _SLUG.sub("-", normalized.decode("ascii").casefold()).strip("-")[:48]
    base = f"{prefix}-{normalized}" if normalized else prefix
    base = base[:63].rstrip("-") or "generic"
    candidate, index = base, 2
    while candidate in used:
        suffix = f"-{index}"
        candidate = f"{base[: 64 - len(suffix)].rstrip('-')}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def publish_internal_result(
    title: str, sections: Iterable[object], *, language: str | None = None
) -> PublicResult:
    """Positive-allowlist projection from internal sections to public blocks."""
    used: set[str] = set()
    public: list[PublicSection] = []
    for index, raw in enumerate(sections):
        if index >= 100:
            raise ValueError("public result exceeds the maximum section count")
        raw_title = _bounded_public_source(
            getattr(raw, "title", None)
            if not isinstance(raw, dict)
            else raw.get("title")
        )
        raw_content = _bounded_public_source(
            getattr(raw, "content", None)
            if not isinstance(raw, dict)
            else raw.get("content")
        )
        raw_id = (
            getattr(raw, "section_id", None)
            if not isinstance(raw, dict)
            else raw.get("section_id")
        )
        section_type = _section_type(raw_id, raw_title)
        section_title = _public_line(raw_title or f"Section {index + 1}")[:255]
        section_title = section_title or "Section"
        section_id = stable_section_id(section_type, section_title, used)
        public.append(
            PublicSection(
                section_id=section_id,
                section_type=section_type,
                title=section_title,
                blocks=_markdown_to_blocks(raw_content),
            )
        )
    if not public:
        public = [
            PublicSection(
                section_id="overview",
                section_type="overview",
                title="Overview",
                blocks=[ParagraphBlock(text="No public summary is available.")],
            )
        ]
    public_title = _public_line(_bounded_public_source(title))[:255] or "Tara result"
    return PublicResult(
        metadata=PortableMetadata(language=language, producer="tara"),
        content=PublicResultContent(title=public_title, sections=public),
    )


def _bounded_public_source(value: object) -> str:
    """Reject oversized source values before Markdown processing or iteration."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if len(value) > MAX_PUBLIC_SOURCE_CHARS:
        raise ValueError("public result source text exceeds the maximum size")
    return value


def _section_type(raw_id: object, title: object) -> str:
    raw = str(raw_id or "").casefold()
    for legacy_id, section_type in LEGACY_SECTION_TYPES:
        if raw == legacy_id:
            return section_type
    return "generic"


def _markdown_to_blocks(text: str) -> list[PublicBlock]:
    lines: list[str] = []
    in_fence = False
    for raw_line in text.splitlines():
        if raw_line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(_public_line(raw_line))
    lines = [line for line in lines if line]
    items = [
        re.sub(r"^[-+*]\s+", "", line) for line in lines if re.match(r"^[-+*]\s+", line)
    ]
    ordered = [
        re.sub(r"^\d+[.)]\s*", "", line)
        for line in lines
        if re.match(r"^\d+[.)]\s*", line)
    ]
    prose = [
        line.lstrip("# ").strip()
        for line in lines
        if not re.match(r"^[-+*]\s+", line) and not re.match(r"^\d+[.)]\s*", line)
    ]
    blocks: list[PublicBlock] = []
    if prose:
        blocks.append(ParagraphBlock(text=" ".join(prose)[:20_000] or "No details."))
    if items:
        blocks.append(ListBlock(items=items[:200]))
    if ordered:
        blocks.append(OrderedListBlock(items=ordered[:200]))
    return blocks or [ParagraphBlock(text="No details.")]


def _public_line(line: str) -> str:
    """Keep plain prose only; code and filesystem-looking lines stay internal."""
    value = line.strip()
    if not value or value.startswith("```"):
        return ""
    value = _ABSOLUTE_PATH.sub("", value)
    if re.fullmatch(r"\s*(?:[-*_]\s*){3,}\s*", value):
        return ""
    value = re.sub(r"^#{1,6}\s*|^>\s*", "", value)
    value = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", value)
    value = re.sub(r"<!--[\s\S]*?-->|</?[A-Za-z][^>]*>", "", value)
    return value.strip()
