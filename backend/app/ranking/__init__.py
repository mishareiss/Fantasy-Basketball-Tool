"""Ranking logic (stub).

The *storage* now exists: `app.db.models.ranking` holds `RankingSet` / `RankingEntry`, and
`app.ingest.ranking` imports a board into them (a set is replaced wholesale, not merged). What
is still stubbed is everything computed on top — consensus ADP blending, the projected-value
set, and the personal composite model (weighted blend, manual overrides, learn-from-edits),
plus tiers and versioned snapshots. See FEATURE_SPEC 5-6.
"""
