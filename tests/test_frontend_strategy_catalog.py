"""真实后端策略目录须可由 Vue 编辑器打开，避免样例隐藏协议差异。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_vue_editor_accepts_builtin_strategy_catalog():
    from app.strategies.management import get_strategy_payload

    node = shutil.which("node")
    if not node:
        pytest.skip("Vue 目录兼容验证需要 Node.js")
    payload = get_strategy_payload()
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [node, "--input-type=module", "-e", """
import fs from 'node:fs';
import { structureErrors } from './web/src/services/strategyEditor.js';
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const errors = payload.strategies.flatMap(strategy =>
  structureErrors(strategy, payload.modules, payload.limits).map(error => `${strategy.id}: ${error}`)
);
process.stdout.write(JSON.stringify(errors));
"""],
        cwd=root,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout) == []
