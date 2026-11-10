from fractions import Fraction as F

import pytest

from scripts.verify_closeout_portfolio import Asset, evaluate_allocation, load_assets, reference_optimum


ASSETS = [Asset("S1", F(1, 5), F(1, 10), F(1, 10), F(10))]


def test_reference_covers_zero_minimum_and_proportional_fee_regimes():
    # 单资产损失上限为 2，购买至 20；净收益 4 + 78*5% - 2。
    result = reference_optimum(ASSETS, capital=F(100), risk_limit=F(2))
    assert result["amounts"] == [F(20)]
    assert result["bank"] == F(78)
    assert result["objective"] == F(59, 10)
    assert result["regimes_examined"] == 3
    zero = reference_optimum(ASSETS, capital=F(100), risk_limit=F(0))
    assert zero["amounts"] == [F(0)]
    assert zero["bank"] == F(100)
    assert zero["objective"] == F(5)


def test_small_purchase_pays_minimum_fee_and_bank_interest():
    result = evaluate_allocation(ASSETS, [5], 94, capital=100, risk_limit=2, recorded_objective=4.7)
    assert result["passed"] is True
    assert result["fees"] == pytest.approx([1])
    assert result["objective"] == pytest.approx(4.7)
    assert result["max_risk"] == pytest.approx(0.5)


@pytest.mark.parametrize("amounts,bank,objective,reason", [
    ([5], 94.5, 5.225, "cash_budget"),
    ([30], 67, 6.35, "risk_limit"),
    ([20], 78, 7.9, "objective"),
    ([float("nan")], 100, 5, "finite"),
    ([-1], 101, 5, "nonnegative"),
])
def test_invalid_allocations_are_rejected(amounts, bank, objective, reason):
    result = evaluate_allocation(ASSETS, amounts, bank, capital=100, risk_limit=2, recorded_objective=objective)
    assert result["passed"] is False
    assert reason in result["failures"]


def test_modified_source_is_rejected(tmp_path):
    source = tmp_path / "changed.txt"
    source.write_text("S1 28 2.5 0 103", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA"):
        load_assets(source)
