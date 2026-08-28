"""The `adp` import kind: a consensus/expert ADP list -> `adp_entry` rows.

The first kind, and the reason the pipeline exists in this shape. Published ADP is the one
external signal we can't get from an API (ESPN's own is the exception, and it's redraft-only),
so it arrives as a paste from Hashtag Basketball or a FantasyPros export, and it arrives
repeatedly through August as boards move.

Two consequences show up in the code below:

* **Season is part of the key.** August's list and last August's list are both interesting —
  the *move* is the dynasty signal — so an import never overwrites another season.
* **Auto-accept is generous.** ADP is a number we read off a board by eye; a wrong row costs a
  double-take, not a bad bet. Anything the matcher resolved gets written, and the names it
  couldn't place come back as the review list.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdpEntry
from app.ingest.parser import ValueColumn
from app.ingest.registry import (
    ImportKind,
    ResolvedRow,
    UpsertContext,
    UpsertCounts,
    accept_matcher_threshold,
    register,
)

# What the three `adp_entry` value columns are called in the wild. `rank` and `overall` are
# here because plenty of "ADP" exports are really ranking lists with a position number, and
# for our purposes a consensus rank *is* the market read — stored as ADP, clearly sourced.
ADP_COLUMNS = (
    ValueColumn(
        "adp",
        (
            "adp",
            "avg pick",
            "average pick",
            "avg draft position",
            "average draft position",
            "avg",
            "rank",
            "overall",
            "ovr",
            "pick",
        ),
        required=True,
    ),
    ValueColumn(
        "auction_value",
        ("auction value", "auction", "salary", "$", "value", "aav", "price"),
    ),
    ValueColumn(
        "percent_owned",
        ("percent owned", "% owned", "%own", "owned", "rostered", "own"),
    ),
)

_VALUE_FIELDS = ("adp", "auction_value", "percent_owned")


def upsert_adp(db: Session, rows: Sequence[ResolvedRow], context: UpsertContext) -> UpsertCounts:
    """Upsert one `adp_entry` per resolved player for this (source, season).

    Hand-written rather than an `ON CONFLICT`, so the same code runs on the SQLite the tests
    use and the Postgres everything else uses — the same rule the ESPN sync follows.

    A dry run takes exactly this path and mutates nothing, so the preview's created/updated
    /unchanged counts are the real ones. That is what makes "re-importing changes nothing"
    something you can see *before* committing.
    """
    counts = UpsertCounts()
    if not rows:
        return counts

    existing = {
        entry.player_id: entry
        for entry in db.scalars(
            select(AdpEntry).where(
                AdpEntry.source == context.source,
                AdpEntry.season == context.season,
                AdpEntry.player_id.in_([resolved.player_id for resolved in rows]),
            )
        )
    }

    for resolved in rows:
        values = {field: resolved.value(field) for field in _VALUE_FIELDS}
        entry = existing.get(resolved.player_id)

        if entry is None:
            counts.created += 1
            if not context.dry_run:
                db.add(
                    AdpEntry(
                        player_id=resolved.player_id,
                        source=context.source,
                        season=context.season,
                        **values,
                    )
                )
            continue

        # A column the file doesn't carry is left alone rather than nulled: importing a
        # rank-only list must not wipe the auction values a fuller export already gave us.
        changes = {
            field: value
            for field, value in values.items()
            if value is not None and getattr(entry, field) != value
        }
        if changes:
            counts.updated += 1
            if not context.dry_run:
                for field, value in changes.items():
                    setattr(entry, field, value)
                # `as_of` means "when these numbers last moved", not "when we last looked".
                entry.as_of = datetime.now(UTC)
        else:
            counts.unchanged += 1

    if not context.dry_run:
        db.flush()
    return counts


ADP_KIND = register(
    ImportKind(
        name="adp",
        label="Average draft position from one source, per player per season",
        columns=ADP_COLUMNS,
        upsert=upsert_adp,
        accept=accept_matcher_threshold,
    )
)
