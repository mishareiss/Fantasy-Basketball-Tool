"""Ingestion pipeline (stub).

CSV/paste imports with column mapping. The player-matching half is no longer a stub: every
imported row resolves through `app.matching`, which is where the name normalization, the fuzzy
matcher, and the `PlayerAlias` layer already live. What is still missing here is the pipeline
around it — parsing a pasted table, mapping its columns to a kind (adp | projection | ranking |
market_line), and upserting the resolved rows.
"""
