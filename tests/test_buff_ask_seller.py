import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buff.buyer import BuffBuyer
from buff import BuffWriteResultUnknown


def _buyer_with_responses(monkeypatch, responses):
    buyer = object.__new__(BuffBuyer)
    calls = []
    response_iter = iter(responses)

    def make_request(method, url, **kwargs):
        calls.append(
            {
                "method": method,
                "url": url,
                "payload": json.loads(kwargs["data"]),
            }
        )
        response = next(response_iter)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(buyer, "_make_request", make_request)
    return buyer, calls


def _requested_ids(calls):
    return [call["payload"]["bill_orders"][0] for call in calls]


@pytest.mark.parametrize(
    ("responses", "expected_result", "expected_ids"),
    [
        # 中间单号瞬时失败后重试：不会被静默跳过，也不会阻止后续单号。
        pytest.param(
            [
                {"code": "OK"},
                {"code": "TemporaryError", "error": "稍后重试"},
                {"code": "OK"},
                {"code": "OK"},
            ],
            True,
            ["bill-1", "bill-2", "bill-2", "bill-3"],
            id="transient-middle-failure-is-retried",
        ),
        # 中间单号永久失败：最后一个单号仍会被提示，但整体结果不能报成功。
        pytest.param(
            [
                {"code": "OK"},
                {"code": "Rejected"},
                {"code": "Rejected"},
                {"code": "Rejected"},
                {"code": "OK"},
            ],
            False,
            ["bill-1", "bill-2", "bill-2", "bill-2", "bill-3"],
            id="permanent-middle-failure-is-not-hidden",
        ),
    ],
)
def test_partial_success_is_not_reported_as_complete_success(
    monkeypatch,
    responses,
    expected_result,
    expected_ids,
):
    from buff import buyer as buyer_module

    buyer, calls = _buyer_with_responses(monkeypatch, responses)
    monkeypatch.setattr(buyer_module, "jittered_sleep", lambda _seconds: None)

    result = buyer.ask_seller_to_send(["bill-1", "bill-2", "bill-3"])

    assert result is expected_result
    assert _requested_ids(calls) == expected_ids


def test_order_ids_are_normalized_and_deduplicated(monkeypatch):
    from buff import buyer as buyer_module

    buyer, calls = _buyer_with_responses(
        monkeypatch,
        [{"code": "OK"}, {"code": "OK"}],
    )
    monkeypatch.setattr(buyer_module, "jittered_sleep", lambda _seconds: None)

    result = buyer.ask_seller_to_send(
        [" bill-1 ", "", None, "bill-1", 0, "bill-2"],
    )

    assert result is True
    assert _requested_ids(calls) == ["bill-1", "bill-2"]


def test_unknown_write_result_is_never_retried(monkeypatch):
    from buff import buyer as buyer_module

    unknown = BuffWriteResultUnknown(
        "timeout after send",
        method="POST",
        url="https://buff.invalid/ask",
    )
    buyer, calls = _buyer_with_responses(monkeypatch, [unknown])
    monkeypatch.setattr(
        buyer_module,
        "jittered_sleep",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("unknown writes must not be retried")
        ),
    )

    with pytest.raises(BuffWriteResultUnknown):
        buyer.ask_seller_to_send(["bill-1", "bill-2"])

    assert _requested_ids(calls) == ["bill-1"]
