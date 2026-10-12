import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.strategies import validation as strategy_validation


def test_strategy_import_enabled_string_false_stays_disabled():
    strategy = strategy_validation.normalize_strategy({
        "name": "Disabled string step",
        "strategy_type": "sell",
        "steps": [
            {"module_id": "action.pause_auto_sell", "enabled": "false", "params": {}},
        ],
    })

    assert strategy["steps"][0]["enabled"] is False
