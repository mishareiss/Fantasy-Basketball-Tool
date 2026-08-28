"""CSV/paste import endpoints — the HTTP half of `app.ingest`.

Two phases, mirroring the pipeline: `dry_run=true` (the default) previews and writes nothing;
`dry_run=false` commits. The preview response carries the whole row-by-row outcome including
candidates, which is what a future column-mapping / review UI will render, and what makes the
paste path usable from `curl` today.

The table arrives as text in a JSON body. A *file* is imported from the CLI
(`make import ... FILE=...`), which reads it and runs the same pipeline — deliberately, rather
than adding a multipart endpoint: `UploadFile`/`Form` require `python-multipart`, and this
task is meant to add no dependencies. When a browser upload is wanted, it is that one dep plus
a route that decodes the bytes as utf-8-sig and calls `run_import` exactly as below.
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.ingest import (
    ImportParseError,
    ImportSummary,
    UnknownKindError,
    accept_only_certain,
    kind_names,
    run_import,
)
from app.ingest.registry import KINDS, PLANNED_KINDS

router = APIRouter(prefix="/import", tags=["import"])


class ImportRequest(BaseModel):
    """A pasted table plus what it is."""

    source: str = Field(
        ..., min_length=1, description="Who published it: 'hashtag', 'fantasypros', ..."
    )
    text: str = Field(..., min_length=1, description="The CSV / TSV / pasted table itself")
    season: int | None = Field(
        None, description="Season the data is FOR; defaults to ESPN_SEASON from settings"
    )
    column_map: dict[str, str] | None = Field(
        None,
        description="Override column detection: field -> header or 1-based column number, "
        "e.g. {'name': 'PLAYER', 'adp': '3'}",
    )
    delimiter: str | None = Field(None, description="Force a delimiter instead of sniffing it")
    dry_run: bool = Field(True, description="Preview only. Set false to write.")
    strict: bool = Field(
        False,
        description="Hold fuzzy matches for confirmation instead of auto-accepting them",
    )


class RowOutcomeResponse(BaseModel):
    """One row of the file and what became of it."""

    line: int
    source_name: str
    status: str
    values: dict[str, float | None] = {}
    team: str | None = None
    positions: list[str] = []
    player_id: int | None = None
    player_name: str | None = None
    confidence: float = 0.0
    method: str = ""
    candidates: list[dict] = []
    note: str | None = None


class ImportResponse(BaseModel):
    """The preview or the receipt — same shape either way, `dry_run` says which."""

    kind: str
    source: str
    season: int
    dry_run: bool

    columns: dict[str, str] = {}
    delimiter: str

    rows_parsed: int
    rows_skipped_blank: int

    matched: int
    review: int
    unmatched: int
    duplicate: int
    invalid: int

    aliases_created: int
    aliases_existing: int

    rows_created: int
    rows_updated: int
    rows_unchanged: int

    rows: list[RowOutcomeResponse]


class KindInfo(BaseModel):
    """One import kind: implemented, or planned and what it's waiting on."""

    kind: str
    label: str
    implemented: bool
    value_columns: dict[str, list[str]] = {}
    required: list[str] = []


@router.get("/kinds", response_model=list[KindInfo])
def list_kinds() -> list[KindInfo]:
    """What can be imported today, and what is designed but not built (see PLANNED_KINDS)."""
    return [
        KindInfo(
            kind=kind.name,
            label=kind.label,
            implemented=True,
            value_columns={column.field: list(column.aliases) for column in kind.columns},
            required=list(kind.required_fields),
        )
        for kind in (KINDS[name] for name in kind_names())
    ] + [
        KindInfo(kind=name, label=note, implemented=False)
        for name, note in sorted(PLANNED_KINDS.items())
    ]


def _resolve_season(season: int | None) -> int:
    """The season the rows are for. Required — a stored row without one is uninterpretable."""
    resolved = season if season is not None else get_settings().espn_season
    if resolved is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "season is required (and ESPN_SEASON is unset): an ADP row without a season can't "
            "be compared to anything later.",
        )
    return resolved


def _to_response(summary: ImportSummary) -> ImportResponse:
    return ImportResponse(
        **{key: value for key, value in summary.__dict__.items() if key != "rows"},
        rows=[RowOutcomeResponse(**row.as_dict()) for row in summary.rows],
    )


def _run(db: Session, kind: str, **kwargs) -> ImportResponse:
    """Run the pipeline and translate its two failure modes into HTTP."""
    try:
        summary = run_import(db, kind=kind, **kwargs)
    except UnknownKindError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc.args[0])) from exc
    except ImportParseError as exc:
        # 422, not 400: the request is well-formed, its *content* isn't a table we can read.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _to_response(summary)


@router.post("/{kind}", response_model=ImportResponse)
def import_paste(
    kind: str = Path(..., description="What kind of data this is: 'adp' today"),
    payload: ImportRequest = Body(...),
    db: Session = Depends(get_db),
) -> ImportResponse:
    """Import a pasted table. Previews by default; set `dry_run=false` to write.

    Unmatched and review rows are never written — they come back in `rows` with their
    candidates. Resolve one with `POST /players/{espn_player_id}/aliases` and re-import; the
    alias makes it land as `method='alias'` at confidence 1.0.
    """
    return _run(
        db,
        kind,
        source=payload.source,
        season=_resolve_season(payload.season),
        text=payload.text,
        column_map=payload.column_map,
        delimiter=payload.delimiter,
        dry_run=payload.dry_run,
        accept=accept_only_certain if payload.strict else None,
    )
