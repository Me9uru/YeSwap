import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.configuration.schema import DEFAULTS, _validate_ranges, merge, validate_and_fill


# ── merge() 深度合并测试 ──────────────────────────────────────────────────

def test_merge_深层合并不影响未指定key():
    # 用户只改了max_discount，target_balance不能消失
    defaults = {"pipeline": {"target_balance": 100, "max_discount": 0.9}}
    result = merge(defaults, {"pipeline": {"max_discount": 0.85}})
    assert result["pipeline"]["target_balance"] == 100
    assert result["pipeline"]["max_discount"] == 0.85


def test_merge_非dict会整体覆盖():
    result = merge({"x": {"a": 1}}, {"x": 42})
    assert result["x"] == 42


# ── validate_and_fill() 类型转换测试 ─────────────────────────────────────

def test_validate_字符串转float():
    result = validate_and_fill({"pipeline": {"max_discount": "0.7"}}, DEFAULTS)
    assert isinstance(result["pipeline"]["max_discount"], float)
    assert result["pipeline"]["max_discount"] == 0.7


def test_validate_字符串转int():
    result = validate_and_fill({"pipeline": {"iflow_top_n": "30"}}, DEFAULTS)
    assert result["pipeline"]["iflow_top_n"] == 30

    # 无法解析的值回退到默认值（例如时段配置里被清空的 start_time_hour）
    fallback = validate_and_fill({"pipeline": {"start_time_hour": None}}, DEFAULTS)
    assert fallback["pipeline"]["start_time_hour"] == (
        DEFAULTS["pipeline"]["start_time_hour"]
    )


def test_validate_iflow_min_price_zero_is_preserved():
    result = validate_and_fill({"iflow": {"min_price": 0}}, DEFAULTS)
    assert result["iflow"]["min_price"] == 0


# ── _validate_ranges() 范围校验 ───────────────────────────────────────────

@pytest.mark.parametrize(
    ("overrides", "section", "key", "check"),
    [
        (
            {"pipeline": {"max_discount": 1.5}},
            "pipeline",
            "max_discount",
            lambda v: v <= 1.0,
        ),
        (
            {"stability": {"cv_threshold": 0.0}},
            "stability",
            "cv_threshold",
            lambda v: v > 0.0,
        ),
        (
            {"buff": {"price_tolerance": -1.0}},
            "buff",
            "price_tolerance",
            lambda v: v >= 0.0,
        ),
    ],
    ids=["max_discount上限", "cv_threshold下限", "price_tolerance不能为负"],
)
def test_range_越界值被限制(overrides, section, key, check):
    cfg = merge(DEFAULTS, overrides)
    result = _validate_ranges(cfg)
    assert check(result[section][key])


def test_range_inventory_refresh_seconds_too_short_is_lifted_to_ten_minutes():
    cfg = merge(DEFAULTS, {"inventory": {"refresh_seconds": 60}})
    result = _validate_ranges(cfg)
    assert result["inventory"]["refresh_seconds"] == 600
