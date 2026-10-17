"""The pure imputation module: OREB/DREB, PF, the DD/TD model and blend, clamps, calibration."""

import math

import pytest

from app.config import Settings
from app.projections.impute import (
    DEFAULT_PARAMS,
    IMPUTE_MIN_GP,
    IMPUTE_PRIOR_GP,
    IMPUTE_SD_K,
    METHOD_BLEND,
    METHOD_FROM_REB,
    METHOD_LEAGUE,
    METHOD_MODEL,
    METHOD_OWN,
    METHOD_POSITION,
    Baselines,
    CalibrationSample,
    ImputeParams,
    LastSeason,
    Rates,
    blend_weight,
    double_rates,
    fit_sd_k,
    impute_line,
    league_baselines,
)

BASELINES = Baselines(
    by_position={"C": Rates(oreb_share=0.30, pf_per_min=0.08), "PG": Rates(0.10, 0.06)},
    league=Rates(oreb_share=0.20, pf_per_min=0.07),
)

# A big man's projected line: no DD, TD, OREB, DREB or PF, as ESPN sends it.
BIG = {"PTS": 22.0, "REB": 12.0, "AST": 3.0, "STL": 1.0, "BLK": 2.0, "MIN": 32.0}
BIG_LAST = LastSeason(
    per_game={**BIG, "OREB": 4.0, "DREB": 8.0, "PF": 3.2, "DD": 0.7, "TD": 0.02},
    games=70.0,
)


def _impute(per_game=BIG, last=BIG_LAST, position="C", params=DEFAULT_PARAMS):
    return impute_line(
        per_game, last_season=last, position=position, baselines=BASELINES, params=params
    )


# --- defaults --------------------------------------------------------------------------------


def test_settings_defaults_match_the_module_constants():
    settings = Settings(_env_file=None)
    assert settings.impute_espn_projections is True
    assert settings.impute_params() == ImputeParams()
    assert (
        settings.impute_sd_k,
        settings.impute_prior_gp,
        settings.impute_min_gp,
    ) == (IMPUTE_SD_K, IMPUTE_PRIOR_GP, IMPUTE_MIN_GP)


def test_the_fitted_sd_k_is_pinned_roughly():
    """Fitted 2026-10-16 on the 2025-26 actuals (k = 1.341). A refit that moves it far is a
    different model of the league, and worth a deliberate change here."""
    assert 1.2 <= IMPUTE_SD_K <= 1.5


# --- rebounds --------------------------------------------------------------------------------


def test_oreb_from_his_own_share_and_dreb_is_the_rest():
    line = _impute()
    assert line.per_game["OREB"] == pytest.approx(12.0 * 4.0 / 12.0)
    assert line.per_game["DREB"] == pytest.approx(12.0 - line.per_game["OREB"])
    assert line.imputed["OREB"] == METHOD_OWN
    assert line.imputed["DREB"] == METHOD_FROM_REB


def test_oreb_from_the_position_baseline_without_enough_history():
    short = LastSeason(per_game=BIG_LAST.per_game, games=IMPUTE_MIN_GP - 1)
    for last in (short, None):
        line = _impute(last=last)
        assert line.per_game["OREB"] == pytest.approx(12.0 * 0.30)
        assert line.imputed["OREB"] == METHOD_POSITION


def test_an_unknown_position_uses_the_league():
    line = _impute(last=None, position=None)
    assert line.per_game["OREB"] == pytest.approx(12.0 * 0.20)
    assert line.imputed["OREB"] == METHOD_LEAGUE


def test_no_reb_no_rebound_split():
    line = _impute(per_game={"PTS": 20.0, "MIN": 30.0})
    assert "OREB" not in line.per_game and "DREB" not in line.per_game


# --- fouls -----------------------------------------------------------------------------------


def test_pf_from_his_own_rate_per_minute():
    line = _impute()
    assert line.per_game["PF"] == pytest.approx(32.0 * 3.2 / 32.0)
    assert line.imputed["PF"] == METHOD_OWN


def test_pf_from_the_position_without_history_and_absent_without_minutes():
    assert _impute(last=None).per_game["PF"] == pytest.approx(32.0 * 0.08)
    assert _impute(last=None).imputed["PF"] == METHOD_POSITION
    no_minutes = {k: v for k, v in BIG.items() if k != "MIN"}
    assert "PF" not in _impute(per_game=no_minutes).per_game


# --- the DD / TD model -----------------------------------------------------------------------


def test_all_means_zero_is_no_doubles():
    assert double_rates({}, 1.3) == (0.0, 0.0)
    assert double_rates(dict.fromkeys(("PTS", "REB", "AST", "STL", "BLK"), 0.0), 1.3) == (
        0.0,
        0.0,
    )


def test_two_stats_far_above_ten_with_tiny_sd_is_a_certain_double_double():
    dd, td = double_rates({"PTS": 30.0, "REB": 30.0}, 0.01)
    assert dd == pytest.approx(1.0)
    assert td == pytest.approx(0.0)
    dd, td = double_rates({"PTS": 30.0, "REB": 30.0, "AST": 30.0}, 0.01)
    assert (dd, td) == (pytest.approx(1.0), pytest.approx(1.0))


def test_the_model_matches_a_hand_computed_case():
    """PTS and REB exactly at 10 each reach it half the time; the rest are 0 -> DD = 1/4."""
    dd, td = double_rates({"PTS": 10.0, "REB": 10.0}, 1.0)
    assert dd == pytest.approx(0.25)
    assert td == 0.0
    # Three coin flips: P(>=2) = 1/2, P(>=3) = 1/8.
    dd, td = double_rates({"PTS": 10.0, "REB": 10.0, "AST": 10.0}, 1.0)
    assert (dd, td) == (pytest.approx(0.5), pytest.approx(0.125))


def test_the_blend_weight():
    assert blend_weight(None, 20.0) == 0.0
    assert blend_weight(0.0, 20.0) == 0.0
    assert blend_weight(20.0, 20.0) == pytest.approx(0.5)
    assert blend_weight(60.0, 20.0) == pytest.approx(0.75)


def test_dd_and_td_blend_his_own_rate_with_the_model():
    params = ImputeParams(sd_k=1.3, prior_gp=20.0)
    model_dd, model_td = double_rates(BIG, 1.3)
    line = _impute(params=params)
    w = 70.0 / 90.0
    assert line.per_game["DD"] == pytest.approx(w * 0.7 + (1 - w) * model_dd)
    assert line.per_game["TD"] == pytest.approx(w * 0.02 + (1 - w) * model_td)
    assert line.imputed["DD"] == line.imputed["TD"] == METHOD_BLEND


def test_a_rookie_is_the_model_alone():
    line = _impute(last=None, params=ImputeParams(sd_k=1.3))
    assert (line.per_game["DD"], line.per_game["TD"]) == pytest.approx(double_rates(BIG, 1.3))
    assert line.imputed["DD"] == line.imputed["TD"] == METHOD_MODEL


# --- never overwrite, clamps -----------------------------------------------------------------


def test_never_overwrites_a_stat_the_source_gave():
    given = {**BIG, "DD": 0.11, "OREB": 1.0, "PF": 9.0}
    line = _impute(per_game=given)
    assert line.per_game["DD"] == 0.11 and line.per_game["OREB"] == 1.0
    assert line.per_game["PF"] == 9.0
    assert set(line.imputed) == {"DREB", "TD"}
    assert line.per_game["DREB"] == pytest.approx(11.0)
    # Everything given comes back unchanged.
    assert all(line.per_game[name] == value for name, value in given.items())


def test_a_complete_line_imputes_nothing():
    full = {**BIG, "DD": 0.5, "TD": 0.1, "OREB": 3.0, "DREB": 9.0, "PF": 2.0}
    line = _impute(per_game=full)
    assert line.imputed == {} and line.per_game == full


def test_clamps_td_under_dd_and_oreb_under_reb():
    # A given DD below what TD would blend to: TD is held at DD.
    line = _impute(per_game={**BIG, "DD": 0.001}, last=LastSeason({**BIG, "TD": 0.9}, 80.0))
    assert line.per_game["TD"] <= line.per_game["DD"] == 0.001
    # An absurd own share (bad data: OREB > REB) can't push OREB past REB.
    weird = LastSeason({**BIG, "REB": 1.0, "OREB": 5.0}, 80.0)
    line = _impute(last=weird)
    assert line.per_game["OREB"] == pytest.approx(12.0)
    assert line.per_game["DREB"] == 0.0
    # Rates live in [0, 1].
    line = _impute(last=LastSeason({**BIG, "DD": 3.0, "TD": 2.0}, 1000.0))
    assert 0.0 <= line.per_game["TD"] <= line.per_game["DD"] <= 1.0


def test_qd_is_left_alone():
    assert "QD" not in _impute().per_game


# --- baselines -------------------------------------------------------------------------------


def test_league_baselines_are_gp_weighted_by_position():
    actuals = [
        ("C", LastSeason({"REB": 10.0, "OREB": 4.0, "PF": 3.0, "MIN": 30.0}, 80.0)),
        ("C", LastSeason({"REB": 10.0, "OREB": 2.0, "PF": 1.0, "MIN": 30.0}, 20.0)),
        ("PG", LastSeason({"REB": 4.0, "OREB": 0.4, "PF": 2.0, "MIN": 20.0}, 50.0)),
        ("SF", LastSeason({"REB": 9.0, "OREB": 9.0}, 0.0)),  # no games: ignored
    ]
    baselines = league_baselines(actuals)
    assert baselines.by_position["C"].oreb_share == pytest.approx((320 + 40) / (800 + 200))
    assert baselines.by_position["C"].pf_per_min == pytest.approx((240 + 20) / (2400 + 600))
    assert baselines.by_position["PG"].oreb_share == pytest.approx(0.1)
    assert "SF" not in baselines.by_position
    assert baselines.league.oreb_share == pytest.approx((320 + 40 + 20) / (800 + 200 + 200))


# --- calibration -----------------------------------------------------------------------------


def test_the_fitter_recovers_a_known_k_on_a_synthetic_population():
    true_k = 1.1
    samples = []
    for i in range(60):
        line = {
            "PTS": 6.0 + i * 0.4,
            "REB": 2.0 + (i * 7 % 13),
            "AST": 1.0 + (i * 5 % 9),
            "STL": 1.0,
            "BLK": 0.5 + (i % 3),
        }
        samples.append(CalibrationSample(line, double_rates(line, true_k)[0], 20.0 + i))
    fit = fit_sd_k(samples)
    assert fit.k == pytest.approx(true_k, abs=1e-3)
    assert fit.rmse == pytest.approx(0.0, abs=1e-4)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-6)
    assert fit.players == 60
    assert math.isfinite(fit.spread) and fit.spread > 0


def test_the_fitter_needs_games():
    with pytest.raises(ValueError):
        fit_sd_k([CalibrationSample({"PTS": 10.0}, 0.0, 0.0)])
