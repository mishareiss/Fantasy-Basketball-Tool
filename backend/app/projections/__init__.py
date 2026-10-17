"""Projection sources and the work done on them before they are priced.

`impute` fills in the stats a source doesn't project (ESPN: DD, TD, OREB, DREB, PF) from each
player's last season. A pluggable `ProjectionSource` layer (imported CSVs, sportsbook props, a
custom model) is still to come.
"""
