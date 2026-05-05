import json
from pathlib import Path
from unittest.mock import patch


def test_get_lean_template_schema_is_generic():
    from server.core.tools.tools import CORE_TOOLS
    from server.tooling import get_canonical_tool_spec

    assert CORE_TOOLS["get_lean_template"]["params"] == {}

    spec = get_canonical_tool_spec("get_lean_template")
    assert spec.input_schema == {"type": "object", "properties": {}}
    assert "I02" not in json.dumps(spec.input_schema)


def test_get_lean_template_uses_session_context(monkeypatch):
    from server.core.tools.tools import get_lean_template

    monkeypatch.setenv(
        "QTB_LEAN_TEMPLATE_CONTEXT_JSON",
        json.dumps(
            {
                "category": "implementation",
                "template_type": "multi_symbol",
                "expects_universe": True,
                "sandbox_image": "quant-tutor-env:v2.2-lean",
                "user_code_available": False,
            }
        ),
    )

    payload = json.loads(get_lean_template())

    assert payload["template"] == "multi-symbol universe skeleton"
    assert "public class Algorithm : QCAlgorithm" in payload["code"]
    assert "File.ReadAllText(\"/data/universe.json\")" in payload["code"]
    assert payload["session"]["template_type"] == "multi_symbol"
    assert "task_id" not in payload["session"]
    assert "SetHoldings(symbol, _state[symbol].Regime * weight)" in payload["code"]
    assert "Leave brokerage model selection" in payload["rules"]


def test_lean_tasks_expose_template_tool_before_register():
    from server.api.session_api import SessionState

    bench_root = Path(__file__).resolve().parents[1]
    state = SessionState("session-for-tool-list", use_docker=False, bench_root=bench_root)
    state._run_task_id = "I01_implement_sma"

    names = {tool.name for tool in state.get_visible_tools()}

    assert "get_lean_template" in names
    assert "run_lean_backtest" in names


def test_run_lean_backtest_preflight_blocks_common_harness_mismatch(
    monkeypatch, tmp_path
):
    from server.core.tools import tools

    source = """
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Brokerages;

namespace QuantConnect.Algorithm.CSharp
{
    public class Algorithm : QCAlgorithm
    {
        public override void Initialize()
        {
            SetBrokerageModel(BrokerageName.BinanceFutures, AccountType.Margin);
            AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
        }

        public override void OnData(QuantConnect.Data.Slice data)
        {
            SetHoldings(Symbol("BTCUSDT"), 1.0m, true, "tag");
        }
    }
}
"""
    (tmp_path / "Algorithm.cs").write_text(source, encoding="utf-8")

    monkeypatch.setenv("QTB_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("QTB_MAX_BACKTEST_TRIALS", "5")
    monkeypatch.setenv(
        "QTB_LEAN_TEMPLATE_CONTEXT_JSON",
        json.dumps(
            {
                "category": "implementation",
                "template_type": "multi_symbol",
                "expects_universe": True,
            }
        ),
    )
    tools._trial_managers.clear()

    with patch.object(tools, "shell_exec") as shell_exec:
        result = tools.run_lean_backtest("Algorithm.cs")

    shell_exec.assert_not_called()
    assert result.startswith("Preflight failed:")
    assert "Leave brokerage model selection unset" in result
    assert "Use SetHoldings(symbol, targetWeight)" in result
    assert "Load the session universe file" in result
    assert not (tmp_path / ".backtest_runs.jsonl").exists()

    tools._trial_managers.clear()
