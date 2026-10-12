from unittest.mock import MagicMock

import pytest


def _entry():
    return {
        "it": {
            "appid": 730,
            "contextid": "2",
            "assetid": "52667017535",
        },
        "list_price": 10.0,
        "reason": "test",
        "price_cents": 1000,
        "name": "AK-47 | Uncharted",
        "aid": "52667017535",
    }


def _context():
    ctx = MagicMock()
    ctx.is_stop_requested.return_value = False
    return ctx


def _messages(ctx):
    return [call.args[0] for call in ctx.log.call_args_list]


@pytest.mark.parametrize(
    ("response", "expected_detail"),
    [
        # _parse_listing_response 的三个分支各留一个代表：无响应 /
        # 不可解析的响应体（data 为 None，含非 JSON 文本与非对象 JSON）/
        # 返回类型异常。同一分支内的其余形状为同义输入，不再重复参数。
        (None, "无响应"),
        (
            {"status_code": 502, "text": "<html>bad gateway</html>"},
            "HTTP 502; body=<html>bad gateway</html>",
        ),
        (["unexpected"], "返回类型异常: list"),
    ],
)
def test_submit_listing_handles_invalid_response_shapes(
    monkeypatch,
    response,
    expected_detail,
):
    from app.workflows import sale as sell_pipeline

    ctx = _context()
    recorded = []
    monkeypatch.setattr(sell_pipeline, "list_item", lambda *_args: response)
    monkeypatch.setattr(sell_pipeline, "jittered_sleep", lambda *_args: None)
    monkeypatch.setattr(
        sell_pipeline,
        "_record_listing_success",
        lambda *_args: recorded.append(_args),
    )

    listed = sell_pipeline._submit_listings(
        ctx,
        [_entry()],
        object(),
        "session-id",
        0,
    )

    messages = _messages(ctx)
    assert listed == 0
    assert recorded == []
    assert any("上架响应异常" in message for message in messages)
    assert any(expected_detail in message for message in messages)


@pytest.mark.parametrize(
    "payload",
    [
        {"success": True},
        {"success": False, "message": "pending confirmation"},
        {"success": False, "message": "already have a listing"},
    ],
)
def test_submit_listing_preserves_success_variants(monkeypatch, payload):
    from app.workflows import sale as sell_pipeline

    ctx = _context()
    recorded = []
    monkeypatch.setattr(
        sell_pipeline,
        "list_item",
        lambda *_args: {
            "status_code": 200,
            "text": __import__("json").dumps(payload),
        },
    )
    monkeypatch.setattr(sell_pipeline, "jittered_sleep", lambda *_args: None)
    monkeypatch.setattr(
        sell_pipeline,
        "_record_listing_success",
        lambda *args: recorded.append(args),
    )

    listed = sell_pipeline._submit_listings(
        ctx,
        [_entry()],
        object(),
        "session-id",
        0,
    )

    assert listed == 1
    assert len(recorded) == 1


def test_submit_listing_retry_null_is_diagnostic_and_not_success(
    monkeypatch,
):
    from app.workflows import sale as sell_pipeline

    ctx = _context()
    responses = iter(
        [
            {
                "status_code": 200,
                "text": (
                    '{"success": false, '
                    '"message": "Please wait until your previous action completes"}'
                ),
            },
            {"status_code": 200, "text": "null"},
        ]
    )
    recorded = []
    monkeypatch.setattr(
        sell_pipeline,
        "list_item",
        lambda *_args: next(responses),
    )
    monkeypatch.setattr(sell_pipeline, "jittered_sleep", lambda *_args: None)
    monkeypatch.setattr(
        sell_pipeline,
        "_record_listing_success",
        lambda *args: recorded.append(args),
    )

    listed = sell_pipeline._submit_listings(
        ctx,
        [_entry()],
        object(),
        "session-id",
        0,
    )

    messages = _messages(ctx)
    assert listed == 0
    assert recorded == []
    assert any(
        "上架重试响应异常" in message and "HTTP 200; body=null" in message
        for message in messages
    )


def test_submit_listing_retry_success_is_recorded_once(monkeypatch):
    from app.workflows import sale as sell_pipeline

    ctx = _context()
    attempts = []
    responses = iter(
        [
            {
                "status_code": 200,
                "text": (
                    '{"success": false, '
                    '"message": "Please wait until your previous action completes"}'
                ),
            },
            {"status_code": 200, "text": '{"success": true}'},
        ]
    )
    recorded = []
    monkeypatch.setattr(
        sell_pipeline,
        "list_item",
        lambda *_args: attempts.append(1) or next(responses),
    )
    monkeypatch.setattr(sell_pipeline, "jittered_sleep", lambda *_args: None)
    monkeypatch.setattr(
        sell_pipeline,
        "_record_listing_success",
        lambda *args: recorded.append(args),
    )

    listed = sell_pipeline._submit_listings(
        ctx,
        [_entry()],
        object(),
        "session-id",
        0,
    )

    assert listed == 1
    # 首次响应要求等待后重试了一次，且成功只记录一次
    assert len(attempts) == 2
    assert len(recorded) == 1


@pytest.mark.parametrize(
    "response",
    [
        {"status_code": 200, "text": '{"success": "false"}'},
        {"status_code": 200, "text": '{"success": 1}'},
        {"status_code": 500, "text": '{"success": true}'},
        {
            "status_code": 503,
            "text": '{"message": "pending confirmation"}',
        },
    ],
)
def test_submit_listing_rejects_untrusted_success_shapes(
    monkeypatch,
    response,
):
    from app.workflows import sale as sell_pipeline

    ctx = _context()
    recorded = []
    monkeypatch.setattr(sell_pipeline, "list_item", lambda *_args: response)
    monkeypatch.setattr(sell_pipeline, "jittered_sleep", lambda *_args: None)
    monkeypatch.setattr(
        sell_pipeline,
        "_record_listing_success",
        lambda *args: recorded.append(args),
    )

    listed = sell_pipeline._submit_listings(
        ctx,
        [_entry()],
        object(),
        "session-id",
        0,
    )

    assert listed == 0
    assert recorded == []
    assert any("上架失败" in message for message in _messages(ctx))


def test_listing_response_logs_are_single_line_bounded_and_redacted(
    monkeypatch,
):
    from app.workflows import sale as sell_pipeline

    ctx = _context()
    sensitive_body = (
        "bad\r\n"
        "sessionid=secret-session; "
        'access_token="secret-token"; '
        "Authorization: Bearer secret-bearer "
        + ("x" * 500)
    )
    monkeypatch.setattr(
        sell_pipeline,
        "list_item",
        lambda *_args: {"status_code": 502, "text": sensitive_body},
    )
    monkeypatch.setattr(sell_pipeline, "jittered_sleep", lambda *_args: None)

    listed = sell_pipeline._submit_listings(
        ctx,
        [_entry()],
        object(),
        "session-id",
        0,
    )

    response_log = next(
        message for message in _messages(ctx) if "上架响应异常" in message
    )
    assert listed == 0
    assert "\r" not in response_log
    assert "\n" not in response_log
    assert "secret-session" not in response_log
    assert "secret-token" not in response_log
    assert "secret-bearer" not in response_log
    assert "sessionid=***" in response_log
    assert 'access_token="***' in response_log
    assert "Bearer ***" in response_log
    assert len(response_log) < 300


@pytest.mark.parametrize(
    ("raw", "ok", "parsed"),
    [
        # parse_sell_response 的返回契约分支：成功 / 已知失败（返回原始
        # 对象）/ 非对象 JSON（isinstance 为假）/ JSON 解析异常（except
        # 分支）。后两者在实现内走不同分支，各留一个代表。
        ('{"success": true}', True, {"success": True}),
        ('{"success": false}', False, {"success": False}),
        ("null", False, None),
        ("<html>bad gateway</html>", False, None),
    ],
)
def test_parse_sell_response_contract(raw, ok, parsed):
    from steam import parse_sell_response

    assert parse_sell_response(raw) == (ok, parsed)
