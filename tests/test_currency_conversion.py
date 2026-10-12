import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 多字符 "$" 前缀必须先于裸 "$" 的 USD 兜底匹配，否则会被误判为美元。
        ("US$ 100", "USD"),
        ("HK$ 100", "HKD"),
        ("R$ 100", "BRL"),
        ("CL$ 100", "CLP"),
        # 人民币符号与代码同义，同样不能落入美元兜底。
        ("￥ 100", "CNY"),
        ("CN￥ 100", "CNY"),
        ("RMB 100", "CNY"),
        # 其余币种的符号与代码各覆盖一个代表写法。
        ("₹ 100", "INR"),
        ("₽ 100", "RUB"),
        ("€ 100", "EUR"),
        ("₺ 100", "TRY"),
        ("JP¥ 100", "JPY"),
        ("INR 100", "INR"),
        ("$ 100", "USD"),
        ("", None),
    ],
)
def test_detect_currency_reads_steam_symbols_and_codes(text, expected):
    from steam.client import detect_currency

    assert detect_currency(text) == expected


def test_apply_currency_uses_rate_map_for_non_usd_prices():
    from utils.money import apply_currency

    converted, currency = apply_currency([100.0, 125.5], "INR", rate_map={"INR": 0.07})

    assert currency == "CNY"
    assert converted == pytest.approx([7.0, 8.785])


def test_stability_currency_conversion_uses_exchange_rate_map(monkeypatch):
    import utils.money
    from analysis import stability

    monkeypatch.setattr(utils.money, "_load_exchange_rates", lambda: {"JPY": 0.05})

    converted, currency = stability._apply_currency([1000.0], "JPY", 7.2)

    assert currency == "CNY"
    assert converted == [50.0]
