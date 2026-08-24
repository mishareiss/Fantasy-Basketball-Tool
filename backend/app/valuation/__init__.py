"""Value engine (stub).

Computes both value horizons per player: current-year (win-now) and dynasty (projection run
through an age/longevity curve).

The curve's input is now available: `Player.age`, populated from nba.com birthdates by
`app.ages` and computed at a fixed `Settings.age_as_of` so the same player is the same age on
every run.
"""
