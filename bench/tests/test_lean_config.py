"""Tests for bench/docker/lean_config.py — the shared LEAN config helper.

Locks down the behaviour both the in-container session runner
(``bench/docker/run_backtest.sh``) and the host-side reference generator
(``bench/reference_generator/generate_lean_reference.py``) depend on. Any
divergence between the two callers reproduces the empty-``algorithm-location``
crash that blocked session backtests on prod (issue #33).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker"))

import lean_config  # noqa: E402


class TestResolveAlgorithmTypeName:
    def test_full_type_name_wins(self):
        assert (
            lean_config.resolve_algorithm_type_name(
                class_name="Ignored", full_type_name="My.Ns.Algo"
            )
            == "My.Ns.Algo"
        )

    def test_class_name_gets_default_namespace(self):
        assert (
            lean_config.resolve_algorithm_type_name(class_name="SmaBaseline")
            == "QuantConnect.Algorithm.CSharp.SmaBaseline"
        )

    def test_blank_inputs_fall_back_to_algorithm(self):
        assert (
            lean_config.resolve_algorithm_type_name()
            == "QuantConnect.Algorithm.CSharp.Algorithm"
        )


class TestApplySessionOverrides:
    def test_session_runner_shape_matches_run_backtest_sh(self):
        """The ``bench/docker/run_backtest.sh`` call shape — bare class name,
        explicit DLL + results dir, task parameters merged in."""
        cfg = {"environment": "backtesting", "parameters": {"pre-existing": "keep"}}

        result = lean_config.apply_session_overrides(
            cfg,
            class_name="SmaBaseline",
            dll_path="/lean/Launcher/bin/Debug/QuantConnect.Algorithm.CSharp.dll",
            results_dir="/workspace/results/sma_baseline",
            parameters={"fast": "10", "slow": "30"},
        )

        # Mutates in place and also returns the same dict.
        assert result is cfg
        assert cfg["algorithm-type-name"] == "QuantConnect.Algorithm.CSharp.SmaBaseline"
        assert (
            cfg["algorithm-location"]
            == "/lean/Launcher/bin/Debug/QuantConnect.Algorithm.CSharp.dll"
        )
        assert cfg["results-destination-folder"] == "/workspace/results/sma_baseline"

        # Default fees + custom-data-root appear; task params merge in;
        # pre-existing `parameters` is replaced wholesale because the session
        # runner always rewrites the block from the merged defaults.
        assert cfg["parameters"]["maker-fee-rate"] == "0.0002"
        assert cfg["parameters"]["taker-fee-rate"] == "0.0005"
        assert cfg["parameters"]["custom-data-root"] == "/data/custom/binance"
        assert cfg["parameters"]["fast"] == "10"
        assert cfg["parameters"]["slow"] == "30"
        assert "pre-existing" not in cfg["parameters"]

    def test_reference_generator_shape_preserves_fqn_and_ref_fees(self):
        """``bench/reference_generator/generate_lean_reference.py`` passes the
        already-fully-qualified class name via ``full_type_name`` and supplies
        its own symmetric 0.0005 fees."""
        cfg = {"environment": "backtesting", "start-date": "2022-01-01"}

        ref_fees = {"maker-fee-rate": "0.0005", "taker-fee-rate": "0.0005"}
        lean_config.apply_session_overrides(
            cfg,
            full_type_name="QuantTutorBench.MultiSignalSweep",
            dll_path="/CustomAlgo/bin/Debug/net10.0/CustomAlgo.dll",
            parameters=ref_fees,
        )

        # The reference FQN passes through unchanged.
        assert cfg["algorithm-type-name"] == "QuantTutorBench.MultiSignalSweep"
        assert cfg["algorithm-location"] == "/CustomAlgo/bin/Debug/net10.0/CustomAlgo.dll"

        # Reference overrides win over session defaults.
        assert cfg["parameters"]["maker-fee-rate"] == "0.0005"
        assert cfg["parameters"]["taker-fee-rate"] == "0.0005"
        assert cfg["parameters"]["custom-data-root"] == "/data/custom/binance"

        # Reference generator doesn't pass results_dir; helper must not touch
        # whatever the scaffolding already set.
        assert "results-destination-folder" not in cfg

    def test_empty_dll_path_leaves_algorithm_location_untouched(self):
        """Callers that seed ``algorithm-location`` elsewhere (e.g. a
        scaffolding default) should be safe to call the helper without it."""
        cfg = {"algorithm-location": "/some/preexisting.dll"}
        lean_config.apply_session_overrides(cfg, class_name="Algo")
        assert cfg["algorithm-location"] == "/some/preexisting.dll"

    def test_empty_parameters_still_gets_defaults(self):
        cfg: dict = {}
        lean_config.apply_session_overrides(cfg, class_name="Algo")
        assert cfg["parameters"]["maker-fee-rate"] == "0.0002"
        assert cfg["parameters"]["taker-fee-rate"] == "0.0005"
        assert cfg["parameters"]["custom-data-root"] == "/data/custom/binance"

    def test_task_params_can_override_default_fees(self):
        cfg: dict = {}
        lean_config.apply_session_overrides(
            cfg, class_name="Algo", parameters={"maker-fee-rate": "0.0001"}
        )
        assert cfg["parameters"]["maker-fee-rate"] == "0.0001"
        # Untouched defaults still present.
        assert cfg["parameters"]["taker-fee-rate"] == "0.0005"
