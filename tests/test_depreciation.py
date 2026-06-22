import pytest

from gpu_econ.depreciation import ebitda_swing, sensitivity
from gpu_econ.inputs import H100, HOURS_PER_YEAR, GPUSpec, Scenario


def test_sensitivity_preserves_life_order() -> None:
    rows = sensitivity(Scenario(gpu=H100), lives=(6.0, 3.0, 5.0))

    assert [row.useful_life_years for row in rows] == [6.0, 3.0, 5.0]


def test_h100_three_year_depreciation_matches_hand_calculation() -> None:
    row = sensitivity(Scenario(gpu=H100), lives=(3.0,))[0]

    assert row.annual_depreciation_per_gpu == pytest.approx(30_000 * 0.90 / 3.0)
    assert row.depreciation_per_hour == pytest.approx(9_000 / HOURS_PER_YEAR)


def test_h100_six_year_depreciation_matches_hand_calculation() -> None:
    row = sensitivity(Scenario(gpu=H100), lives=(6.0,))[0]

    assert row.annual_depreciation_per_gpu == pytest.approx(30_000 * 0.90 / 6.0)
    assert row.depreciation_per_hour == pytest.approx(4_500 / HOURS_PER_YEAR)


def test_longer_life_lowers_total_cost_per_provisioned_hour() -> None:
    three_year, six_year = sensitivity(Scenario(gpu=H100), lives=(3.0, 6.0))

    assert six_year.total_cost_per_provisioned_hour < three_year.total_cost_per_provisioned_hour


def test_ebitda_swing_headline_h100_six_year_vs_three_year() -> None:
    swing = ebitda_swing(Scenario(gpu=H100), base_life=6.0, alt_life=3.0, fleet_size=1_000)

    assert swing["base_annual_depreciation_usd"] == pytest.approx(4_500_000)
    assert swing["alt_annual_depreciation_usd"] == pytest.approx(9_000_000)
    assert swing["ebitda_delta_usd"] == pytest.approx(-4_500_000)
    assert swing["ebitda_delta_usd"] < 0


def test_ebitda_swing_reverses_sign_when_base_life_is_shorter() -> None:
    swing = ebitda_swing(Scenario(gpu=H100), base_life=3.0, alt_life=6.0, fleet_size=1_000)

    assert swing["ebitda_delta_usd"] == pytest.approx(4_500_000)
    assert swing["ebitda_delta_usd"] > 0


def test_custom_residual_value_changes_depreciation_base() -> None:
    gpu = GPUSpec(
        name="TestGPU",
        capex_usd=10_000,
        power_kw=1.0,
        tokens_per_sec=1_000,
        residual_value_frac=0.20,
    )
    row = sensitivity(Scenario(gpu=gpu), lives=(2.0,))[0]

    assert row.annual_depreciation_per_gpu == pytest.approx(10_000 * 0.80 / 2.0)
    assert row.depreciation_per_hour == pytest.approx(4_000 / HOURS_PER_YEAR)


def test_sensitivity_does_not_mutate_original_scenario_gpu() -> None:
    scenario = Scenario(gpu=H100)

    sensitivity(scenario, lives=(3.0, 6.0))

    assert scenario.gpu == H100
    assert scenario.gpu.useful_life_years == H100.useful_life_years


def test_empty_lives_returns_no_rows() -> None:
    assert sensitivity(Scenario(gpu=H100), lives=()) == []
