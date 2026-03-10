# Implementation Section (I-Series) Design Plan

> Version: v2.3 | Status: In Progress — I01–I06 classic approach built, multi-layer behavioral eval implemented, I07–I10 Algorithm Framework tasks designed | Section: Strategy Implementation on LEAN Engine

---

## 1. Section Philosophy

### 1.1 What I-Series Tests

I-series tests the agent's ability to teach **strategy implementation on a production-grade backtest engine** — translating a clear strategy specification into working code that runs on the QuantConnect LEAN engine and produces a verifiable trade log.

The agent under test is **not** building the engine (that's B-series) or discovering the strategy idea (that's S-series). Both are given. The test is: can the agent guide a student to correctly implement a well-defined strategy as a LEAN C# algorithm, run it at scale on real market data, and produce trade logs that align with the strategy specification?

```
┌─────────────────────────────────────────────────────┐
│              Given: LEAN Engine (black-box)           │
│  Pre-installed, pre-configured, data pre-loaded.     │
│  Agent only knows how to USE it, not how it works.   │
│  Interface: write Algorithm.cs → run → get results   │
└──────────────────────┬──────────────────────────────┘
                       │ agent writes C# algorithm
                       ▼
┌─────────────────────────────────────────────────────┐
│              Given: Strategy Specification            │
│  Clear entry/exit rules, parameters, asset(s),       │
│  position sizing logic. No ambiguity — the agent     │
│  translates spec → code, not discovers the idea.     │
└──────────────────────┬──────────────────────────────┘
                       │ agent implements & runs
                       ▼
┌─────────────────────────────────────────────────────┐
│              Output: Trade Log + Performance          │
│  LEAN produces structured trade log (JSON/CSV).      │
│  Compared against ground-truth reference trade log   │
│  to evaluate implementation correctness.             │
└─────────────────────────────────────────────────────┘
```

**The core evaluation question**: Does the agent correctly translate a strategy specification into working LEAN C# code that is **behaviorally equivalent** to the reference implementation? Evaluation measures this through signal agreement, position overlap, portfolio metrics, and relaxed trade similarity — not exact trade identity. A weak agent writes code that compiles but produces wrong signals, misses edge cases, or misuses the LEAN API. A strong agent produces an algorithm whose positions and performance closely match the strategy specification.

### 1.2 Why C# on LEAN

All other layer-2 sections (D, S, B) use Python. I-series deliberately uses **C# on LEAN** for the following reasons:

1. **LEAN is natively C#**: The engine runs C# algorithms without the Python.NET bridge, providing full API access and production-grade performance for large-scale backtests.
2. **Real-world skill**: Production quant teams using LEAN write C# strategies. Testing the agent's ability to work in C# on an industrial engine is a distinct and valuable capability.
3. **Black-box discipline**: By providing LEAN as a pre-configured engine, I-series isolates the implementation skill. The agent must learn the LEAN API (e.g., `QCAlgorithm`, `Initialize()`, `OnData()`, `AddCryptoFuture()`, `SetHoldings()`) rather than building infrastructure from scratch.
4. **Deterministic evaluation**: LEAN's structured output (order events, closed trades JSON) enables precise trade-log comparison against a ground-truth reference — far more rigorous than pattern-matching Python code.

### 1.3 Position in the Quant Workflow Pipeline

I-series sits **downstream of both S-series and B-series** in the quant pipeline. S-series produces validated strategy ideas. B-series builds the backtest system. I-series takes both as inputs and produces backtest results.

```
D (Data)  →  S (Strategy Research)  →  B (Backtest Engine)  →  I (Implementation)  →  X (Debug)  →  E (End-to-End)
  │               │                       │                       │
"Get and        "Discover and            "Build the system       "Given the system
 understand      formalize the            to validate             AND the strategy,
 the data"       alpha idea"              strategies"             implement & run it"
```

| Section | Focus | Relationship to I-series |
|---------|-------|--------------------------|
| **S-series** (Strategy Research) | Signal discovery, hypothesis testing | S-series is the **upstream idea producer** — its output (strategy spec) is I-series' input. S-series does rough Python-based PnL checks; I-series does rigorous LEAN-based validation. |
| **B-series** (Backtest Engineering) | Building the backtest system | B-series builds engines from scratch in Python; I-series **uses** a pre-built engine (LEAN) in C#. Different skills: engineering vs application. |
| **D-series** (Data Analysis) | Data loading, cleaning, exploration | D-series prepares data skills; I-series uses pre-loaded data in LEAN format. |
| **X-series** (Debug) | Finding and fixing bugs | X-series could include debugging LEAN algorithm errors — natural downstream of I-series. |
| **E-series** (End-to-End) | Complete pipeline | E-series combines all steps; I-series isolates the implementation step only. |

### 1.3.1 The S→I Handoff (Conceptual Pairing)

Each I-task receives a strategy specification that is conceptually the **output of S-series research**. The pairing is:

```
Classic approach (I01–I06): S-series strategy → I-series LEAN implementation
S02 (trend signal on BTC daily)          →  I02 (implement trend-following on LEAN)
S03 (mean-reversion signal on BTC daily) →  I03 (implement mean-reversion on LEAN)
S04 (volume/microstructure, multi-TF)    →  I04 (implement multi-timeframe strategy on LEAN)
S05 (cross-asset BTC/ETH signal)         →  I05 (implement cross-asset strategy on LEAN)
S06 (composite multi-signal)             →  I06 (implement multi-signal + parameter sweep on LEAN)

Framework approach (I07–I10): Classic I-task → Framework re-implementation
I02 (classic trend-following)            →  I07 (refactor to AlphaModel + framework pipeline)
I05/I06 (multi-signal manual)            →  I08 (multi-alpha + portfolio construction models)
I03 (manual stop-loss/risk)              →  I09 (framework risk management models)
I06 (manual parameter sweep)             →  I10 (LEAN optimizer engine + optional Bayesian)
```

This pairing is **conceptual, not enforced** — each I-task is independently executable. The strategy spec is embedded in the task description; the agent does not need to have completed the corresponding S-task. I07–I10 pair with classic I-tasks rather than S-tasks, since they test the same strategy concepts with a different architectural approach.

### 1.4 LEAN as a Black-Box

The agent should treat LEAN as a **fully working black-box**:

- **Pre-installed**: LEAN engine is installed in the Docker sandbox, ready to run.
- **Pre-configured**: Data paths, brokerage model (Binance futures), resolution, and account settings are pre-configured.
- **Data pre-loaded**: Binance futures data is already converted to LEAN format and placed in the correct directory.
- **Simple interface**: The agent only needs to:
  1. Write a C# algorithm file (inheriting `QCAlgorithm`)
  2. Run a command like `lean backtest MyAlgorithm` or `dotnet run`
  3. Read the output trade log and performance summary

The agent does **NOT** need to:
- Configure LEAN's `config.json` or data paths
- Understand LEAN's internal architecture
- Convert data formats
- Set up the C# build environment

A reference document (`lean_algorithm_guide.md`, see §6) will be provided to teach the LEAN C# API basics.

---

## 2. Data Preparation

### 2.1 Data Scale Philosophy: S/B-Series vs I-Series

S-series and B-series use **small curated datasets** (a few symbols, limited timeframes) because those sections teach foundational skills — the data is just a vehicle for learning concepts.

I-series is fundamentally different. LEAN is an **industrial-grade backtest engine** designed to process entire markets. The tasks should reflect this scale: strategies applied across the **full Binance futures universe**, not just one or two symbols. This is what distinguishes I-series from toy-scale exercises — we let LEAN cook.

```
S/B-series data:   2 symbols × 1-2 timeframes × 4 years   = ~40K rows    (learning scale)
I-series data:     670+ symbols × 5 timeframes × 4 years   = ~100M+ rows  (production scale)
```

### 2.2 Data Source

Binance USDT-M Futures historical klines:

```
Base URL: https://data.binance.vision/data/futures/um/daily/klines/
Pattern:  {SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{DATE}.zip

Universe: ~811 symbol folders available (including delivery contracts)
          ~671 unique USDT-margined perpetual contracts
History:  BTCUSDT/ETHUSDT from 2019-12-31; most pairs from 2020-2021 onward
```

### 2.3 Representative Timeframes

Binance offers 15 intervals (1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1mo). Many are redundant. We select **5 representative timeframes** covering the full strategy horizon spectrum:

| Timeframe | Strategy Horizon | Why Selected | Skip Alternatives |
|-----------|-----------------|--------------|-------------------|
| **1m** | Microstructure / execution | Highest resolution; order flow, execution quality analysis | — |
| **5m** | Short-term intraday | Standard intraday swing; cleaner than 1m, still high-frequency | 3m (too close to 5m) |
| **1h** | Intraday / overnight swing | Most common intraday timeframe for crypto; natural consolidator base | 15m, 30m (between 5m and 1h) |
| **4h** | Medium-term swing | Popular among crypto traders; bridges intraday and daily | 2h, 6h, 8h, 12h (redundant) |
| **1d** | Daily systematic | Backbone of most systematic strategies; lowest noise | 3d, 1w, 1mo (derivable from daily) |

### 2.4 Symbol Universe Tiers

Not all symbols need all timeframes. We organize into **three tiers** by liquidity, with different timeframe coverage:

#### Tier 1: Full Universe — Daily Only

**All USDT-M perpetual futures** discovered from Binance Data Vision (`data.binance.vision`). This is a point-in-time S3 archive of ALL historical klines — including delisted symbols — eliminating survivorship bias and manual curation.

Purpose: Universe-wide daily strategies (I02 trend-following, I03 mean-reversion, I06 multi-signal).

| Property | Value |
|----------|-------|
| Symbols | ~670 (all USDT-M perpetuals from Data Vision, no cherry-picking) |
| Timeframes | 1d |
| Period | 2022-01-01 to 2025-12-31 |
| Rows (approx) | ~670 × ~1,000 avg days = **~670K rows** |
| Size (approx) | **~70 MB** compressed |

The symbol list is auto-discovered by querying the S3 listing at `data.binance.vision` and filtering to perpetual USDT-margined contracts (no delivery contracts like `BTCUSDT_250627`). See `bench/scripts/discover_binance_universe.py`.

#### Tier 2: Core Liquid — Hourly + 4-Hourly

**Top ~20 most liquid pairs**, at 1h and 4h resolution.

Purpose: Multi-asset swing strategies (I05 cross-asset), multi-timeframe strategies (I04).

| Property | Value |
|----------|-------|
| Symbols | ~20 (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT, AVAXUSDT, DOTUSDT, MATICUSDT, UNIUSDT, LTCUSDT, ATOMUSDT, NEARUSDT, ARBUSDT, OPUSDT, AAVEUSDT, MKRUSDT, APTUSDT) |
| Timeframes | 1h, 4h |
| Period | 2022-01-01 → 2025-12-31 (4 years) |
| Rows (approx) | 20 × 26,280 (1h) + 20 × 6,570 (4h) = **~657K rows** |
| Size (approx) | **~80 MB** compressed |

#### Tier 3: Majors — 5-Minute + 1-Minute

**Top ~5 most liquid pairs**, at 5m and 1m resolution.

Purpose: High-frequency / microstructure strategies, execution quality analysis, consolidator stress testing.

| Property | Value |
|----------|-------|
| Symbols | 5 (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT) |
| Timeframes | 5m, 1m |
| Period | 2024-01-01 → 2025-12-31 (2 years) |
| Rows (approx) | 5 × 105,120 (5m) + 5 × 525,600 (1m) = **~3.15M rows** |
| Size (approx) | **~400 MB** compressed |

#### Funding Rate Data

Funding rates for the top 20 symbols (Tier 2), for carry signal construction in I06.

| Property | Value |
|----------|-------|
| Symbols | ~20 (same as Tier 2) |
| Interval | 8h (3 per day) |
| Period | Listing date → 2025-12-31 |
| Rows (approx) | 20 × ~3,000 = **~60K rows** |
| Source | Binance REST API (`/fapi/v1/fundingRate`) |

#### Summary: Total I-Series Data

| Tier | Symbols | Timeframes | Rows | Size | I-Tasks |
|------|---------|------------|------|------|---------|
| Tier 1 (Full Universe) | ~100 | 1d | ~150K | ~15 MB | I02, I03, I06 |
| Tier 2 (Core Liquid) | ~20 | 1h, 4h | ~657K | ~80 MB | I04, I05 |
| Tier 3 (Majors) | ~5 | 5m, 1m | ~3.15M | ~400 MB | I04 (stress test) |
| Funding | ~20 | 8h | ~60K | ~5 MB | I06 |
| **Total** | | | **~4M rows** | **~500 MB** | |

This is orders of magnitude larger than S/B-series data (~40K rows) but well within LEAN's capacity and HuggingFace storage limits.

### 2.5 Data Pipeline: Raw → LEAN Format → HuggingFace

I-series requires a **three-stage data preparation pipeline** (run once by benchmark maintainers, not at eval time):

```
Stage 1: Download raw Binance klines + funding rates at scale
         ↓
Stage 2: Convert to LEAN format (directory structure + CSV schema + zip packaging)
         ↓
Stage 3: Upload both raw and LEAN-format to HuggingFace dataset repo
```

#### Stage 1: Bulk Download

Extend `bench/scripts/download_binance_klines.py` (currently handles S/B-series datasets only) to support:
- Bulk download of all Tier 1/2/3 symbols and timeframes
- Parallel downloads for speed (100 symbols × 1,500 days = 150K files for Tier 1 daily alone)
- Resume capability (skip already-downloaded files)
- Checksum verification using Binance's `.CHECKSUM` files

New script: `bench/scripts/download_binance_full_universe.py`

#### Stage 2: LEAN Format Conversion

LEAN expects data in a specific directory structure and CSV format:

```
Data/
└── crypto/
    └── binance/
        ├── daily/
        │   ├── btcusdt.zip          # Low-res: single zip per symbol
        │   ├── ethusdt.zip
        │   ├── bnbusdt.zip
        │   └── ... (100 symbols)
        ├── hour/
        │   ├── btcusdt.zip
        │   ├── ethusdt.zip
        │   └── ... (20 symbols)
        ├── 4hour/                   # LEAN may need custom resolution handling
        │   └── ...
        ├── 5minute/
        │   └── btcusdt/
        │       ├── 20240101_trade.zip    # High-res: one zip per day
        │       ├── 20240102_trade.zip
        │       └── ...
        └── minute/
            └── btcusdt/
                ├── 20240101_trade.zip
                └── ...
```

**LEAN TradeBar CSV format** (low-resolution: daily, hourly):
```
Date (YYYYMMDD HH:MM), Open (scaled), High (scaled), Low (scaled), Close (scaled), Volume
```

**Conversion script**: `bench/scripts/convert_binance_to_lean.py`
- Reads raw Binance CSVs (from Stage 1)
- Converts timestamps to LEAN's expected format
- Scales prices per LEAN's internal representation for crypto
- Packages into `.zip` files in the correct directory structure
- Handles all 5 timeframes and all symbol tiers
- Validates output against LEAN's expected schema

This script is **benchmark infrastructure** — it is NOT a task for the agent.

#### Stage 3: HuggingFace Upload

Both raw and LEAN-format data are uploaded to HuggingFace using Git LFS:

```
huggingface.co/datasets/Varsity-Tech/quant-tutor-bench-data/
├── raw/
│   ├── sb-series/                    # Small curated datasets for S/B-series
│   │   ├── BTCUSDT_1d_2021_2024.csv
│   │   ├── ETHUSDT_1d_2021_2024.csv
│   │   └── ...
│   └── i-series/                     # Full-universe raw data for I-series
│       ├── tier1_daily/              # ~100 symbols, daily
│       │   ├── BTCUSDT_1d.csv
│       │   ├── ETHUSDT_1d.csv
│       │   └── ...
│       ├── tier2_hourly/             # ~20 symbols, 1h + 4h
│       │   ├── BTCUSDT_1h.csv
│       │   ├── BTCUSDT_4h.csv
│       │   └── ...
│       ├── tier3_minute/             # ~5 symbols, 5m + 1m
│       │   ├── BTCUSDT_5m_2024.csv
│       │   ├── BTCUSDT_1m_2024.csv
│       │   └── ...
│       ├── funding/                  # ~20 symbols, 8h
│       │   ├── BTCUSDT_funding.csv
│       │   └── ...
│       └── universe.json             # Symbol list, listing dates, tier assignments
└── lean/                             # Pre-converted LEAN-format data
    └── crypto/
        └── binance/
            ├── daily/                # Tier 1: ~100 zips
            ├── hour/                 # Tier 2: ~20 zips
            ├── minute/              # Tier 3: daily zip files per symbol
            └── ...
```

### 2.6 Decoupled Architecture: Dataset ↔ Eval System

**Core principle**: The dataset and the evaluation system are **fully decoupled**. The dataset lives on HuggingFace. The eval system (Docker + LEAN + orchestrator) lives in this repo. They connect at runtime via a data manager that downloads from HF and mounts into Docker.

```
┌─────────────────────────────────────┐
│         HuggingFace Dataset          │
│  Varsity-Tech/quant-tutor-bench-data        │
│                                      │
│  ├── raw/sb-series/   (S/B data)     │
│  ├── raw/i-series/    (I raw data)   │
│  ├── lean/            (LEAN format)  │
│  └── universe.json                   │
└──────────────┬──────────────────────┘
               │ huggingface_hub.snapshot_download()
               │ (first run only, then cached)
               ▼
┌─────────────────────────────────────┐
│         Local Cache (gitignored)     │
│  bench/data/                         │
│  ├── frozen/           (S/B, small,  │
│  │                      may stay     │
│  │                      in git)      │
│  └── hf_cache/         (I-series,    │
│      ├── lean/          downloaded   │
│      │   └── crypto/    from HF,     │
│      │       └── ...    gitignored)  │
│      └── universe.json               │
└──────────────┬──────────────────────┘
               │ Docker volume mount (-v)
               ▼
┌─────────────────────────────────────┐
│     Docker Container                 │
│     quant-tutor-env:v2.0-lean        │
│     (LEAN engine + .NET SDK only,    │
│      NO data baked in — ~3GB)        │
│                                      │
│  /lean/Data/ ← mount hf_cache/lean/ │
│  /data/      ← mount frozen/ or      │
│                universe.json         │
│  /workspace/ ← mount temp workspace  │
│  /docs/      ← mount reference docs  │
└─────────────────────────────────────┘
```

**Why decoupled?**

| Concern | Baked-in-image approach | Decoupled approach |
|---------|------------------------|-------------------|
| Image size | ~10-15GB (LEAN + data) | ~3GB (LEAN only) |
| Data update | Rebuild + re-push entire image | Just update HF dataset repo |
| Image update (LEAN version) | Re-push with all data | Rebuild image alone (~3GB) |
| First-run cost | Slow image pull (~15GB) | Fast image pull (~3GB) + data download (~500MB, once) |
| Reproducibility | Data pinned to image tag | Data pinned to HF dataset commit hash |
| Sharing | Must share huge image | Image is small; data downloads automatically |
| S/B-series impact | None (they use different image) | None (they still use frozen/ directly) |

### 2.7 Data Manager: `bench/scripts/data_manager.py`

A thin wrapper around `huggingface_hub` that ensures data is available locally before running I-series tasks.

```python
"""Data manager for downloading and caching HuggingFace datasets.

Usage:
    from scripts.data_manager import ensure_data

    # Before running I-series tasks:
    paths = ensure_data(series="i")
    # paths.lean_data  → local path to LEAN-format data (mount as /lean/Data/)
    # paths.universe   → local path to universe.json (mount as /data/universe.json)

    # Before running S/B-series tasks:
    paths = ensure_data(series="sb")
    # paths.frozen_data → local path to frozen CSVs (mount as /data/)
"""

import os
from dataclasses import dataclass
from pathlib import Path
from huggingface_hub import snapshot_download

HF_REPO_ID = "Varsity-Tech/quant-tutor-bench-data"
DEFAULT_CACHE_DIR = Path(__file__).parent.parent / "data" / "hf_cache"


@dataclass
class DataPaths:
    lean_data: str | None = None       # LEAN-format data dir (for I-series)
    frozen_data: str | None = None     # Raw frozen CSVs (for S/B-series)
    universe: str | None = None        # universe.json path


def ensure_data(
    series: str = "i",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    hf_repo: str = HF_REPO_ID,
    revision: str | None = None,       # Pin to specific HF commit for reproducibility
) -> DataPaths:
    """Download data from HuggingFace if not cached locally.

    Args:
        series: "i" for I-series (LEAN format), "sb" for S/B-series (raw CSVs).
        cache_dir: Local directory for caching downloaded data.
        hf_repo: HuggingFace dataset repo ID.
        revision: Optional HF commit hash for reproducible runs.

    Returns:
        DataPaths with local paths to the downloaded data.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if series == "i":
        lean_dir = cache_dir / "lean"
        universe_path = cache_dir / "universe.json"

        if not lean_dir.exists() or not universe_path.exists():
            snapshot_download(
                repo_id=hf_repo,
                repo_type="dataset",
                allow_patterns=["lean/**", "raw/i-series/universe.json"],
                local_dir=str(cache_dir),
                revision=revision,
            )
            # Move universe.json to expected location
            src = cache_dir / "raw" / "i-series" / "universe.json"
            if src.exists() and not universe_path.exists():
                src.rename(universe_path)

        return DataPaths(
            lean_data=str(lean_dir),
            universe=str(universe_path),
        )

    elif series == "sb":
        frozen_dir = cache_dir / "raw" / "sb-series"

        if not frozen_dir.exists():
            snapshot_download(
                repo_id=hf_repo,
                repo_type="dataset",
                allow_patterns=["raw/sb-series/**"],
                local_dir=str(cache_dir),
                revision=revision,
            )

        return DataPaths(frozen_data=str(frozen_dir))

    else:
        raise ValueError(f"Unknown series: {series!r}. Use 'i' or 'sb'.")
```

### 2.8 Orchestrator Integration

The existing orchestrator mounts `bench/data/frozen/` as `/data:ro` in the Docker container (see `container_manager.py:95`). For I-series, we extend this with LEAN data mounts.

**Changes to `container_manager.py`**:

```python
def create_container(
    self,
    task_id: str,
    data_dir: str,
    docs_dir: str,
    student_code_dir: Optional[str] = None,
    sandbox_image: Optional[str] = None,
    network_enabled: bool = False,
    lean_data_dir: Optional[str] = None,     # NEW: LEAN data mount
) -> ContainerInfo:
    # ... existing logic ...

    if self.use_docker:
        mounts = [
            f"-v {workspace}:/workspace",
            f"-v {data_dir}:/data:ro",
            f"-v {docs_dir}:/docs:ro",
        ]
        # NEW: Mount LEAN data for I-series tasks
        if lean_data_dir:
            mounts.append(f"-v {lean_data_dir}:/lean/Data:ro")

        # ... rest of existing logic ...
```

**Changes to `orchestrator.py`**:

```python
from scripts.data_manager import ensure_data

class Orchestrator:
    def __init__(self, ...):
        # ... existing init ...
        self._lean_data_paths = None    # Lazy-loaded

    def _ensure_lean_data(self) -> DataPaths:
        """Download LEAN data from HF if not cached. Called once, cached."""
        if self._lean_data_paths is None:
            self._lean_data_paths = ensure_data(series="i")
        return self._lean_data_paths

    def run_task(self, task, persona, ...):
        # ... existing logic ...

        lean_data_dir = None
        if task.category == "implementation":
            paths = self._ensure_lean_data()
            lean_data_dir = paths.lean_data
            # Use universe.json as data_dir instead of frozen/
            data_dir = os.path.dirname(paths.universe)

        container = self.container_manager.create_container(
            task_id=task.task_id,
            data_dir=data_dir,
            docs_dir=docs_dir,
            sandbox_image=task.environment.get("sandbox_image"),
            network_enabled=task.environment.get("network_enabled", False),
            lean_data_dir=lean_data_dir,    # NEW
        )
```

**Key properties of this integration**:

1. **Lazy download**: Data is only downloaded when the first I-series task runs. S/B-series tasks never trigger a download.
2. **Cached**: Once downloaded, data is reused across all I-series tasks and across benchmark runs.
3. **Reproducible**: The `revision` parameter can pin to a specific HF commit hash.
4. **Non-invasive**: S/B-series flow is completely unchanged — they still use `bench/data/frozen/` directly.
5. **Gitignored**: The cache directory (`bench/data/hf_cache/`) is gitignored — no large files in the repo.

### 2.9 `universe.json` — Symbol Metadata

A manifest file listing all symbols with metadata, used by both download scripts and reference algorithm generation:

```json
{
  "version": "1.0",
  "freeze_date": "2025-12-31",
  "hf_repo": "Varsity-Tech/quant-tutor-bench-data",
  "hf_revision": "abc123def456...",
  "tiers": {
    "tier1": {
      "description": "Full universe — daily resolution",
      "timeframes": ["1d"],
      "symbols": [
        {"symbol": "BTCUSDT", "listing_date": "2019-12-31", "avg_daily_volume_usdt": 25000000000},
        {"symbol": "ETHUSDT", "listing_date": "2019-12-31", "avg_daily_volume_usdt": 12000000000},
        ...
      ]
    },
    "tier2": {
      "description": "Core liquid — hourly + 4-hourly",
      "timeframes": ["1h", "4h"],
      "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "..."]
    },
    "tier3": {
      "description": "Majors — 5-minute + 1-minute",
      "timeframes": ["5m", "1m"],
      "period": "2024-01-01 to 2025-12-31",
      "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
    },
    "funding": {
      "description": "Funding rates — 8-hourly",
      "symbols": "same as tier2"
    }
  }
}
```

### 2.10 Data in the Docker Sandbox

For I-series tasks, the Docker container sees:

```
/lean/                            # LEAN engine (baked into image)
/lean/Data/                       # ← MOUNTED from hf_cache/lean/ (read-only)
    └── crypto/binance/
        ├── daily/                # Tier 1: ~100 symbols
        │   ├── btcusdt.zip
        │   ├── ethusdt.zip
        │   └── ... (100 zips)
        ├── hour/                 # Tier 2: ~20 symbols
        ├── minute/              # Tier 3: ~5 symbols (daily zip files)
        └── ...
/lean/Launcher/                   # LEAN launcher (baked into image)
/workspace/                       # ← MOUNTED temp dir (read-write)
    └── Algorithm.cs              # (agent writes this)
/data/                            # ← MOUNTED from hf_cache/ (read-only)
    └── universe.json             # Symbol list, tiers, listing dates
/docs/                            # ← MOUNTED from bench/docs/reference/ (read-only)
    └── lean_algorithm_guide.md
```

**What's IN the Docker image** (baked in, ~3GB):
- LEAN engine (C# build)
- .NET SDK 8.0
- `run_backtest` wrapper script
- LEAN config.json (pre-configured for Binance futures)
- No data

**What's MOUNTED at runtime** (from local cache of HF data):
- `/lean/Data/` ← LEAN-format market data
- `/data/` ← universe.json + any metadata
- `/workspace/` ← agent's working directory
- `/docs/` ← reference documents

**Key difference from S/B-series**: The agent does NOT have direct access to raw CSV files. All data access happens through LEAN's `AddCryptoFuture()` API. The `universe.json` tells the agent which symbols are available and at which resolutions.

### 2.11 `.gitignore` Additions

```
# I-series data cache (downloaded from HuggingFace at runtime)
bench/data/hf_cache/
```

---

## 3. LEAN Algorithm Interface

### 3.1 C# Algorithm Structure

Every I-series task requires the agent to write a C# algorithm following this structure:

```csharp
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Indicators;

namespace QuantConnect.Algorithm.CSharp
{
    public class MyStrategy : QCAlgorithm
    {
        // Declare symbols, indicators, state variables
        private Symbol _btc;
        private SimpleMovingAverage _fastMa;
        private SimpleMovingAverage _slowMa;

        public override void Initialize()
        {
            // 1. Set backtest period
            SetStartDate(2021, 1, 1);
            SetEndDate(2024, 12, 31);

            // 2. Set account currency and cash
            SetAccountCurrency("USDT");
            SetCash(100000);

            // 3. Subscribe to data
            var crypto = AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
            _btc = crypto.Symbol;

            // 4. Create indicators
            _fastMa = SMA(_btc, 10);
            _slowMa = SMA(_btc, 30);

            // 5. Warm up indicators
            SetWarmUp(30);
        }

        public override void OnData(Slice slice)
        {
            if (IsWarmingUp) return;
            if (!slice.Bars.ContainsKey(_btc)) return;

            // Strategy logic
            if (_fastMa > _slowMa && !Portfolio[_btc].IsLong)
            {
                SetHoldings(_btc, 1.0);
            }
            else if (_fastMa < _slowMa && Portfolio[_btc].IsLong)
            {
                Liquidate(_btc);
            }
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status == OrderStatus.Filled)
            {
                Log($"Order filled: {orderEvent}");
            }
        }
    }
}
```

### 3.2 Key LEAN API Elements for I-Series

The agent must learn and use these LEAN API elements:

| API Element | Purpose | Tasks |
|-------------|---------|-------|
| `AddCryptoFuture(ticker, resolution, market)` | Subscribe to crypto futures data | All |
| `SetAccountCurrency("USDT")` | Set account to USDT for Binance futures | All |
| `SMA()`, `RSI()`, `EMA()`, `MACD()` etc. | Built-in indicators | I02, I03, I04, I06 |
| `SetHoldings(symbol, percentage)` | Set position as % of portfolio | All |
| `MarketOrder(symbol, quantity)` | Place market order | I03, I04, I05 |
| `LimitOrder(symbol, quantity, price)` | Place limit order | I04, I05 |
| `Liquidate(symbol)` | Close position | All |
| `Portfolio[symbol].IsLong / .IsShort` | Check current position | All |
| `Portfolio[symbol].UnrealizedProfit` | Check PnL | I05, I06 |
| `Securities[symbol].Price` | Current price | All |
| `Consolidate(symbol, resolution, handler)` | Multi-timeframe data | I04 |
| `AddCryptoFuture()` (multiple calls) | Multi-asset | I05 |
| `Schedule.On()` | Scheduled events | I06 |
| `SetWarmUp(period)` | Indicator warm-up | All |
| `Log()` / `Debug()` | Logging for trade log | All |
| `OnOrderEvent()` | Order fill callback | All |

### 3.3 LEAN Algorithm Framework API (I07–I10)

I01–I06 use the **classic approach** where all logic lives in `Initialize()` + `OnData()`. LEAN also provides a higher-level **Algorithm Framework** that decomposes strategies into pluggable modules. I07–I10 test the agent's ability to use this framework.

#### 3.3.1 Framework Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    QCAlgorithm                           │
│                                                         │
│  Initialize() {                                         │
│      SetAlpha(myAlphaModel);                            │
│      SetPortfolioConstruction(myPortfolioModel);        │
│      SetRiskManagement(myRiskModel);                    │
│      SetExecution(myExecutionModel);                    │
│  }                                                      │
│                                                         │
│  Data → Alpha → Insights → Portfolio → Targets          │
│                              → Risk → Adjusted Targets  │
│                                 → Execution → Orders    │
└─────────────────────────────────────────────────────────┘
```

Unlike the classic approach (where everything is manually wired in `OnData()`), the framework handles the pipeline automatically. The agent writes modular components, and LEAN orchestrates them.

#### 3.3.2 Key Framework API Elements

| API Element | Purpose | Tasks |
|-------------|---------|-------|
| **Alpha Models** | | |
| `SetAlpha(IAlphaModel)` | Register signal generator | I07, I08, I09 |
| `AddAlpha(IAlphaModel)` | Register additional alpha (multi-alpha) | I08 |
| `class MyAlpha : AlphaModel` | Custom alpha model base class | I07, I08, I09 |
| `Insight.Up(symbol, duration, ...)` | Emit bullish signal | I07, I08, I09 |
| `Insight.Down(symbol, duration, ...)` | Emit bearish signal | I07, I08, I09 |
| `Insight.Flat(symbol)` | Emit neutral signal | I07, I08, I09 |
| `InsightDirection`, `InsightType` | Signal metadata enums | I07, I08, I09 |
| `Insight.Magnitude`, `.Confidence` | Expected return, signal strength | I08 |
| **Portfolio Construction Models** | | |
| `SetPortfolioConstruction(IPortfolioConstructionModel)` | Register portfolio model | I07, I08, I09 |
| `EqualWeightingPortfolioConstructionModel` | Equal allocation across active insights | I07, I09 |
| `InsightWeightingPortfolioConstructionModel` | Weight by insight confidence/magnitude | I08 |
| `MeanVarianceOptimizationPortfolioConstructionModel` | Mean-variance optimization | I08 |
| `BlackLittermanOptimizationPortfolioConstructionModel` | Black-Litterman model | I08 |
| `PortfolioTarget` | Position target (symbol + quantity) | I08, I09 |
| **Risk Management Models** | | |
| `SetRiskManagement(IRiskManagementModel)` | Register risk model | I09 |
| `AddRiskManagement(IRiskManagementModel)` | Register additional risk model | I09 |
| `MaximumDrawdownPerSecurity(maxDrawdown)` | Cap per-symbol drawdown | I09 |
| `TrailingStopRiskManagementModel(maxTrailingStop)` | Trailing stop on positions | I09 |
| `MaximumSectorExposureRiskManagementModel` | Limit sector/group exposure | I09 |
| `class MyRisk : RiskManagementModel` | Custom risk model base class | I09 |
| **Execution Models** | | |
| `SetExecution(IExecutionModel)` | Register execution model | I07, I08, I09 |
| `ImmediateExecutionModel` | Execute targets immediately at market | I07, I08, I09 |
| `VolumeWeightedAveragePriceExecutionModel` | VWAP execution | — (optional) |
| **Optimization** | | |
| `SetParameter(name, value)` | Declare tunable parameter | I10 |
| `GetParameter(name)` | Read parameter value at runtime | I10 |
| LEAN Optimizer CLI / API | Run parameter optimization sweeps | I10 |

#### 3.3.3 Classic vs. Framework: Same Strategy, Different Architecture

The same strategy can be implemented in both styles. For example, an EMA crossover:

**Classic (I02 style):**
```csharp
public override void OnData(Slice data) {
    if (_fastEma > _slowEma && !Portfolio[_btc].IsLong)
        SetHoldings(_btc, 1.0);
    else if (_fastEma < _slowEma && Portfolio[_btc].IsLong)
        Liquidate(_btc);
}
```

**Framework (I07 style):**
```csharp
// Alpha model — only emits signals
public class EmaCrossoverAlpha : AlphaModel {
    public override IEnumerable<Insight> Update(QCAlgorithm algo, Slice data) {
        if (_fastEma > _slowEma)
            yield return Insight.Up(symbol, TimeSpan.FromDays(1));
        else if (_fastEma < _slowEma)
            yield return Insight.Down(symbol, TimeSpan.FromDays(1));
    }
}

// Algorithm — wires modules together
public override void Initialize() {
    SetAlpha(new EmaCrossoverAlpha());
    SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
    SetExecution(new ImmediateExecutionModel());
}
```

The framework approach separates signal generation from position sizing and execution — each module is independently testable, replaceable, and composable. I07–I10 test whether the agent can work at this architectural level.

### 3.4 Running a Backtest

The agent runs backtests via a simple wrapper command:

```bash
# Write algorithm to workspace
file_write /workspace/Algorithm.cs "<C# code>"

# Run backtest (wrapper script handles LEAN plumbing)
shell_exec "run_backtest /workspace/Algorithm.cs"

# Read results
file_read /workspace/results/trades.json
file_read /workspace/results/summary.json
```

The `run_backtest` wrapper script (pre-installed in the sandbox) handles:
- Copying the algorithm into the LEAN project structure
- Building the C# project
- Running the LEAN engine
- Extracting trade log and performance summary to `/workspace/results/`

---

## 4. Task Designs

### 4.0 Existing Task: I01 — Implement SMA

**Status**: **Redesigned** to LEAN C#. I01 is now the simplest LEAN strategy — a single-symbol (BTCUSDT) SMA(20) trend filter on daily bars. Price > SMA → long 100%; Price < SMA → flatten. This serves as the "hello world" for LEAN C# development, naturally leading into I02's multi-symbol scaling.

**Scope**: Subscribe to BTCUSDT daily futures via `AddCryptoFuture`, create `SMA(20)` indicator, implement price-vs-SMA entry/exit in `OnData`, handle warm-up with `SetWarmUp`, and produce a trade log. Evaluation uses the same trade-log matching infrastructure as I02-I06.

**Files**: `I01_implement_sma.json` (task), `I01_implement_sma.py` (eval), `I01_implement_sma.cs` (reference algorithm), `I01_reference_trades.json` (placeholder).

---

### 4.1 I02 — Universe-Wide Trend-Following on LEAN

**Difficulty**: medium
**Category**: implementation
**Pairs with**: S02 (trend-following research)

**Core idea**: Given a clear trend-following strategy specification, implement it on LEAN and run it across the **full Tier 1 universe (~100 symbols)** at daily resolution. This tests both LEAN API basics and the agent's ability to work at universe scale — looping over symbols, managing per-asset indicators, and aggregating cross-universe results.

**Strategy specification** (provided to the agent):
```
Strategy: Dual Moving Average Crossover — Universe-Wide
Assets:   All symbols in universe.json Tier 1 (~100 USDT-M perpetual futures)
Resolution: Daily
Period:   Each symbol from its listing date → 2025-12-31

Rules (applied independently per symbol):
- Compute 10-day SMA (fast) and 30-day SMA (slow) of close prices
- ENTRY LONG:  fast SMA crosses above slow SMA → go long
- EXIT LONG:   fast SMA crosses below slow SMA → liquidate
- No short positions
- Warm-up: 30 bars before first trade per symbol

Position sizing: Equal-weight across active positions.
  Each symbol gets 1/N of portfolio where N = number of currently active symbols.
  Rebalance on each new entry/exit.

Output required:
- Per-symbol trade log (trades.json)
- Per-symbol performance summary (return, Sharpe, max drawdown, trade count)
- Universe-level aggregated summary:
    - How many symbols are profitable vs unprofitable
    - Distribution of per-symbol Sharpe ratios
    - Total portfolio return (equal-weighted)
```

**Materials provided**:
- Data: Tier 1 universe (~100 symbols, daily) pre-loaded in LEAN format
- `universe.json` with symbol list and listing dates
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`

**Description**: Guide a student to implement a dual moving average crossover strategy as a LEAN C# algorithm that runs across the entire Binance futures universe. Unlike a single-symbol backtest, the student must dynamically subscribe to ~100 symbols, manage per-symbol indicator state, implement equal-weight position sizing across active positions, and produce both per-symbol and universe-level aggregated results. This is how institutional trend-following strategies are actually tested.

**Expected outcome**: Student produces a working LEAN C# algorithm that (1) reads `universe.json` or iterates over available symbols to subscribe to ~100 futures via `AddCryptoFuture`, (2) creates SMA indicators per symbol, (3) implements crossover entry/exit independently per symbol, (4) manages equal-weight portfolio allocation across active positions, (5) runs to completion across the full universe, and (6) produces per-symbol trade logs and an aggregated universe summary. The per-symbol trade logs for a sample of 10 reference symbols should match the ground-truth.

**Required capabilities**:
1. Write a LEAN C# algorithm that dynamically subscribes to many symbols
2. Manage per-symbol indicator state (Dictionary of SMA pairs keyed by Symbol)
3. Implement crossover detection logic applied independently per symbol
4. Handle equal-weight position sizing across a variable number of active positions
5. Handle warm-up correctly per symbol (different listing dates = different warm-up timing)
6. Produce and interpret universe-level aggregated results

**Student openings**:
- **beginner_no_finance**: "I have access to a trading engine called LEAN with data for about 100 crypto futures. I want to test a moving average strategy on all of them at once. I've never written C# before — where do I start?"
- **intermediate_developer**: "I need to implement a dual MA crossover strategy on LEAN across ~100 crypto futures symbols simultaneously. How do I manage indicators and positions for that many symbols in a single algorithm?"
- **advanced_quant**: "I'm implementing a universe-wide trend-following backtest on LEAN for ~100 Binance futures. I need per-symbol SMA crossover signals, equal-weight portfolio construction, and aggregated universe statistics. What's the cleanest way to structure a multi-symbol algorithm?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md", "moving_averages.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `plot_chart`

**Eval strategy**:
- **Trade log comparison** (primary): For 10 reference symbols (e.g., BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT, AVAXUSDT, DOTUSDT), compare agent's per-symbol trades against reference. Match on trade count, entry/exit timing (±1 bar), direction.
- **Universe coverage**: Check that the algorithm subscribed to ≥ 80 symbols (not just a handful).
- **Aggregated output**: Check that universe summary exists with per-symbol statistics.
- **Code patterns**: `AddCryptoFuture` in a loop or bulk, `Dictionary<Symbol, ...>` for per-symbol state.

**Ground-truth preparation**: Run reference algorithm on full universe → export `I02_reference_trades.json` containing per-symbol trade logs for the 10 reference symbols + universe summary.

---

### 4.2 I03 — Universe-Wide Mean-Reversion on LEAN

**Difficulty**: medium
**Category**: implementation
**Pairs with**: S03 (mean-reversion research)

**Core idea**: Given a mean-reversion strategy specification with asymmetric entry/exit rules and a stop-loss, implement it across the **full Tier 1 universe (~100 symbols)** at daily resolution. This tests the agent's ability to handle complex per-symbol position management (entry at one condition, exit at another, stop-loss at a third) at scale.

**Strategy specification**:
```
Strategy: RSI Mean-Reversion with Stop-Loss — Universe-Wide
Assets:   All symbols in universe.json Tier 1 (~100 USDT-M perpetual futures)
Resolution: Daily
Period:   Each symbol from its listing date → 2025-12-31

Indicators (per symbol):
- 14-period RSI on close prices

Rules (applied independently per symbol):
- ENTRY LONG:  RSI crosses below 30 → go long
- EXIT LONG:   RSI crosses above 50 → liquidate
- STOP-LOSS:   If unrealized loss exceeds 5% of entry price → liquidate
- ENTRY SHORT: RSI crosses above 70 → go short
- EXIT SHORT:  RSI crosses below 50 → liquidate
- STOP-LOSS:   Same 5% stop on short side
- No overlapping positions per symbol (must exit before entering opposite direction)

Position sizing: Equal-risk allocation.
  Each symbol gets max 2% of portfolio per position.
  Total exposure capped at 100% (max ~50 concurrent positions).

Output required:
- Per-symbol trade log with exit reason tagged (RSI_exit vs stop_loss)
- Per-symbol performance summary
- Universe-level summary:
    - Win rate across all trades (all symbols combined)
    - Stop-loss trigger frequency (% of trades exited by stop vs signal)
    - Long vs short trade distribution
    - Total portfolio return
```

**Materials provided**:
- Data: Tier 1 universe (~100 symbols, daily) pre-loaded in LEAN format
- `universe.json` with symbol list
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`

**Description**: Guide a student to implement an RSI mean-reversion strategy with asymmetric entry/exit thresholds and stop-loss across the full Binance futures universe on LEAN. Unlike I02's simple crossover, this task requires per-symbol entry price tracking, long/short state machines, stop-loss logic, and risk-based position sizing that limits exposure per symbol. At universe scale, the student must manage ~100 independent position state machines concurrently.

**Expected outcome**: Student produces a LEAN C# algorithm that (1) subscribes to ~100 symbols and creates per-symbol RSI indicators, (2) implements RSI entry/exit and stop-loss logic independently per symbol, (3) manages per-symbol position state (flat/long/short) with entry price tracking, (4) implements risk-based position sizing (2% per position, capped total), (5) tags exit reasons (RSI signal vs stop-loss) in the trade log, and (6) produces universe-level aggregated statistics. Per-symbol trades for 10 reference symbols should match the ground-truth.

**Required capabilities**:
1. Manage per-symbol RSI indicators and position state at scale (~100 symbols)
2. Implement asymmetric entry/exit logic with per-symbol state machine
3. Track entry prices per symbol and compute stop-loss conditions
4. Handle long/short positions without overlap, independently per symbol
5. Implement risk-based position sizing (per-symbol cap + total exposure cap)
6. Tag exit reasons and produce universe-level aggregated statistics

**Student openings**:
- **beginner_no_finance**: "I want to test a buy-low-sell-high strategy with stop-losses on all the crypto futures in my dataset — about 100 of them. How do I manage all those positions at once on LEAN?"
- **intermediate_developer**: "I'm implementing a universe-wide RSI mean-reversion strategy on LEAN. Each symbol needs its own state machine for long/short/flat with stop-loss tracking. How do I structure this for ~100 symbols?"
- **advanced_quant**: "I need a universe-wide RSI mean-reversion implementation on LEAN with per-symbol asymmetric thresholds, 5% stop-loss, 2% risk budget per position, and tagged exit reasons across ~100 crypto futures. What's the cleanest architecture?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md", "moving_averages.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `compute_indicator`, `plot_chart`

**Eval strategy**:
- **Trade log comparison**: For 10 reference symbols, match trades against reference. Key checks: correct long vs short trade counts, stop-loss triggers on correct bars, RSI-based exits on correct bars.
- **Stop-loss verification**: Across the universe, a meaningful fraction of trades should exit via stop-loss (tagged exit reason check).
- **Position sizing**: No single position exceeds ~2% of portfolio at entry.
- **Universe coverage**: Algorithm subscribed to ≥ 80 symbols.
- **Aggregated output**: Universe-level summary with win rate, stop-loss frequency, long/short distribution.

**Ground-truth preparation**: Run reference algorithm on full universe → export `I03_reference_trades.json` with 10 reference symbol trade logs + universe summary.

---

### 4.3 I04 — Multi-Timeframe Strategy on LEAN

**Difficulty**: hard
**Category**: implementation
**Pairs with**: S04 (volume/microstructure alpha)

**Core idea**: Given a strategy that uses 4h bars for trend direction and 1h bars for entry timing, implement it on LEAN across the **Tier 2 universe (~20 symbols)** using data consolidators. This tests the agent's ability to work with multiple data resolutions simultaneously across multiple symbols — a common production requirement.

**Strategy specification**:
```
Strategy: Multi-Timeframe Trend Entry — Multi-Asset
Assets:   All symbols in universe.json Tier 2 (~20 core liquid futures)
Resolution: Subscribe at 1h, consolidate to 4h
Period:   2022-01-01 to 2025-12-31

Data (per symbol):
- 4h bars (via consolidator): for trend direction (20-period EMA slope on 4h)
- 1h bars (native): for entry timing (RSI on 1h)

Rules (applied independently per symbol):
- TREND FILTER: 20-period EMA slope on 4h positive → bullish, negative → bearish
- ENTRY LONG:   In bullish regime AND 1h RSI < 35 → go long
- EXIT LONG:    1h RSI > 65 OR 4h EMA slope turns negative → liquidate
- ENTRY SHORT:  In bearish regime AND 1h RSI > 65 → go short
- EXIT SHORT:   1h RSI < 35 OR 4h EMA slope turns positive → liquidate
- Max one position per symbol at a time

Position sizing: Equal-weight across active positions, max 10% per symbol.

Output required:
- Per-symbol trade log
- Per-symbol performance breakdown
- Universe summary: which symbols were most/least profitable, average holding period
```

**Materials provided**:
- Data: Tier 2 universe (~20 symbols, 1h + 4h) pre-loaded in LEAN format
- `universe.json` with symbol list
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`

**Description**: Guide a student to implement a multi-timeframe strategy on LEAN that uses 4h bars for trend direction and 1h bars for precise entry timing, applied across ~20 liquid crypto futures. The student must learn to use LEAN's `Consolidate()` to build 4h bars from 1h data, manage per-symbol dual-timeframe indicator state, and ensure 4h indicators only update on 4h bar completion. Running this at scale across 20 symbols with 3 years of hourly data tests both correctness and performance.

**Expected outcome**: Student produces a LEAN C# algorithm that (1) subscribes to ~20 symbols at hourly resolution and consolidates to 4h bars per symbol, (2) computes 20-period EMA on 4h bars and RSI on 1h bars per symbol, (3) implements multi-timeframe entry/exit logic independently per symbol, (4) ensures 4h indicators update only on 4h bar completion, (5) manages per-symbol position state, and (6) produces per-symbol trade logs and universe summary. Trade logs for 5 reference symbols should match the ground-truth.

**Required capabilities**:
1. Subscribe to multiple symbols at hourly resolution and use `Consolidate()` to create 4h bars per symbol
2. Manage per-symbol dual-timeframe indicator state (Dictionary of EMA + RSI per symbol)
3. Implement multi-timeframe logic: 4h trend filter gates 1h entry signals, per symbol
4. Ensure 4h indicators only update on 4h bar completion (avoid mid-period artifacts)
5. Manage per-symbol position state with position sizing across multiple active positions
6. Produce per-symbol and universe-level results from a multi-timeframe multi-asset backtest

**Student openings**:
- **beginner_no_finance**: "I heard professional traders use multiple timeframes. I have hourly data for 20 crypto futures and I want to use a 4-hour trend to guide my hourly entries. How do I set this up on LEAN?"
- **intermediate_developer**: "I need to implement a multi-timeframe strategy on LEAN across ~20 symbols: 4h EMA for trend, 1h RSI for entries. How do I set up consolidators for multiple symbols simultaneously?"
- **advanced_quant**: "I'm implementing a multi-timeframe multi-asset alpha on LEAN: 4h EMA slope as regime filter, 1h RSI for entries, across 20 futures. I need per-symbol consolidators and dual-resolution indicator state. What's the right architecture?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md", "moving_averages.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `compute_indicator`, `plot_chart`

**Eval strategy**:
- **Trade log comparison**: For 5 reference symbols (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT), match trades against reference. Tolerance ±1 hourly bar.
- **Consolidator correctness**: Check that code uses `Consolidate()` or dual-resolution subscription per symbol.
- **Indicator resolution**: Verify EMA is computed on 4h bars (not 1h).
- **Regime filter**: Per symbol, trades should only occur in correct regime (longs in bullish, shorts in bearish).
- **Multi-asset coverage**: Algorithm subscribed to ≥ 15 symbols.

**Ground-truth preparation**: Run reference algorithm on Tier 2 universe → export `I04_reference_trades.json` with 5 reference symbol trade logs + universe summary.

---

### 4.4 I05 — Cross-Asset Pairs Scanning on LEAN

**Difficulty**: hard
**Category**: implementation
**Pairs with**: S05 (cross-asset alpha)

**Core idea**: Given a pairs/spread trading strategy specification, implement it on LEAN across **all possible pairs within the Tier 2 universe (~20 symbols)**, automatically scanning for the best cointegrated pairs and trading them. This tests the agent's ability to handle combinatorial cross-asset analysis, dynamic pair selection, and multi-leg hedged positions at scale.

**Strategy specification**:
```
Strategy: Universe Pairs Scanner + Spread Trading
Assets:   All symbols in universe.json Tier 2 (~20 core liquid futures, daily resolution)
Period:   2022-01-01 to 2025-12-31

Phase 1 — Pair Selection (provided via I05_candidate_pairs.json):
- Pre-computed: all C(20,2) = 190 pairs scored by 60-day rolling correlation of log returns
- Top-10 pairs with avg |correlation| > 0.7 are provided in the candidate pairs file
- Student loads the file and uses the ranked pairs (no brute-force scan needed)

Phase 2 — Trading (applied to selected pairs):
- For each selected pair (A, B):
  - Price ratio: A_close / B_close
  - 30-day SMA of ratio
  - 30-day standard deviation of ratio
  - Z-score: (ratio - SMA) / StdDev

  Rules:
  - ENTRY LONG SPREAD:   Z-score < -2.0 → buy A (5% portfolio), sell B (5% portfolio)
  - ENTRY SHORT SPREAD:  Z-score > +2.0 → sell A (5% portfolio), buy B (5% portfolio)
  - EXIT:                |Z-score| < 0.5 → liquidate both legs
  - STOP-LOSS:           |Z-score| > 3.5 → liquidate both (divergence protection)
  - Max one spread position per pair at a time

Position sizing: 5% per leg per pair. Max total exposure: 100% (up to 10 pairs × 2 legs × 5%).

Output required:
- Selected pairs list with correlation scores
- Per-pair trade log
- Per-pair performance summary (spread return, Sharpe, max drawdown)
- Portfolio-level summary (aggregate return, number of active pairs over time)
```

**Materials provided**:
- Data: Tier 2 universe (~20 symbols, daily) pre-loaded in LEAN format
- `universe.json` with symbol list
- `I05_candidate_pairs.json` — pre-computed top-10 correlated pairs (60-day rolling correlation of log returns, ranked). Generated by `generate_reference_signals.py --task I05`. Eliminates the C(20,2)=190 pair brute-force scan so the task focuses on spread trading implementation.
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`

**Description**: Guide a student to implement a pairs trading strategy on LEAN that (1) uses a pre-computed candidate pairs file listing top-10 correlated pairs from the 20-symbol universe, (2) implements spread mean-reversion trading on the selected pairs with z-score entry/exit rules, and (3) manages multi-leg positions with proper exposure caps. The student should understand pair selection concepts but does not need to compute pairwise statistics from scratch. This is how institutional stat-arb strategies work — the pair discovery phase is provided so the task focuses on implementation of spread trading and multi-pair portfolio management.

**Expected outcome**: Student produces a LEAN C# algorithm that (1) loads candidate pairs from I05_candidate_pairs.json, (2) subscribes to the relevant symbols, (3) tracks per-pair ratio z-scores, (4) enters/exits spread positions atomically per pair, (5) manages portfolio exposure across up to 10 active pairs, and (6) produces per-pair trade logs and a portfolio-level summary. Trade logs for 3 reference pairs should match the ground-truth.

**Required capabilities**:
1. Load and parse pre-computed candidate pairs from I05_candidate_pairs.json
2. Subscribe to symbols from the candidate pairs and understand pair selection concepts
3. Compute per-pair rolling ratio z-score
4. Manage multiple simultaneous spread positions (up to 10 pairs × 2 legs = 20 positions)
5. Implement atomic spread entry/exit with per-pair stop-loss
6. Produce per-pair and portfolio-level results

**Student openings**:
- **beginner_no_finance**: "I have price data for 20 crypto futures and I heard you can trade pairs that move together. How do I find the best pairs and trade them automatically on LEAN?"
- **intermediate_developer**: "I need to implement a pairs trading scanner on LEAN that screens all pair combinations across 20 symbols, selects the best ones, and trades spread mean-reversion. How do I structure the pair selection and multi-leg position management?"
- **advanced_quant**: "I'm implementing a universe stat-arb strategy on LEAN: screen C(20,2) pairs for correlation, select top-10, trade z-score mean-reversion with atomic spread entries. I need proper multi-pair portfolio management with exposure caps. What's the architecture?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md", "statistical_tests.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `compute_statistics`, `plot_chart`

**Eval strategy**:
- **Candidate pairs usage**: Check that agent loads and uses I05_candidate_pairs.json (or demonstrates pair selection logic).
- **Trade log comparison**: For 3 reference pairs, match spread entries/exits. Both legs must enter/exit on the same bar.
- **Z-score direction**: Long-spread when z < -2, short-spread when z > +2.
- **Exposure management**: Total portfolio exposure never exceeds ~100%.
- **Multi-pair coverage**: Algorithm trades ≥ 5 pairs simultaneously.

**Ground-truth preparation**: Run reference algorithm → export `I05_reference_trades.json` with selected pairs list + trade logs for 3 reference pairs + portfolio summary. `I05_candidate_pairs.json` is also generated alongside reference signals.

---

### 4.5 I06 — Universe Multi-Signal with Parameter Sweep on LEAN

**Difficulty**: hard
**Category**: implementation
**Pairs with**: S06 (multi-signal combination)

**Core idea**: Given a composite strategy that combines multiple signals (trend + mean-reversion + funding carry) with tunable weights, implement it across the **full Tier 1 universe (~100 symbols)** and run a parameter sweep to find the best weight combination. This is the capstone I-series task — combining universe-scale implementation, multi-signal composition, funding rate integration, and systematic parameter optimization.

**Strategy specification**:
```
Strategy: Composite Signal with Parameter Sweep — Universe-Wide
Assets:   All symbols in universe.json Tier 1 (~100 USDT-M perpetual futures, daily)
          Funding rate data for Tier 2 subset (~20 symbols)
Period:   Each symbol from its listing date → 2025-12-31

Signals (computed per symbol, each normalized to [-1, +1]):
- Signal A (Trend):     sign(close - 50-day SMA) → +1 if above, -1 if below
- Signal B (Reversion): -(RSI_14 - 50) / 50     → +1 when oversold, -1 when overbought
- Signal C (Carry):     -sign(funding_rate)       → +1 when funding negative
                         (only for symbols with funding data; others use wC=0 and
                          redistribute weight to wA, wB proportionally)

Composite signal per symbol:
  S_i = wA * SignalA_i + wB * SignalB_i + wC * SignalC_i
  where wA + wB + wC = 1.0, each weight ∈ [0, 1]

Rules (per symbol):
- If S_i > +0.3  → go long with position size proportional to S_i
- If S_i < -0.3  → go short with position size proportional to |S_i|
- If |S_i| ≤ 0.3 → flat (liquidate)
- Rebalance daily

Position sizing: Cross-sectional allocation.
  Target position per symbol = S_i / sum(|S_j| for all active j) × leverage_target
  Total portfolio leverage capped at 3.0x gross.

Parameter sweep:
  Sweep weights (wA, wB, wC) over grid: each from 0.0 to 1.0 in steps of 0.2,
  subject to wA + wB + wC = 1.0. (21 valid combinations)
  For each combination, run full universe backtest.
  Report: Sharpe, max drawdown, annual return, turnover for each combination.
  Identify the top-3 weight combinations by Sharpe.

Output required:
- Sweep results table (21 rows × metrics)
- Best-config detailed trade log (per-symbol)
- Best-config universe summary (sector-like breakdown by listing cohort)
```

**Materials provided**:
- Data: Tier 1 universe (~100 symbols, daily) + Tier 2 funding rates (~20 symbols) pre-loaded in LEAN format
- `universe.json` with symbol list and tier assignments
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`

**Description**: Guide a student to implement a composite multi-signal strategy across the full Binance futures universe on LEAN, then run a systematic parameter sweep over signal weights. The student must implement three signal components per symbol, handle the asymmetry where only ~20 symbols have funding data, implement cross-sectional position sizing (not per-symbol independent sizing), and structure a sweep workflow that runs 21 backtests. This is the most complex I-series task, testing everything: multi-signal construction, universe-scale, data asymmetry, portfolio construction, and systematic optimization.

**Expected outcome**: Student produces (1) a LEAN C# algorithm that computes all three signals per symbol, handles missing funding data gracefully, and implements cross-sectional position sizing, (2) a parameter sweep mechanism running 21 weight combinations, (3) a sweep results table with Sharpe/drawdown/return/turnover per combination, and (4) detailed results for the best configuration. The equal-weight (0.33/0.33/0.33) trade log for 10 reference symbols should match the ground-truth.

**Required capabilities**:
1. Implement three signal components per symbol with proper normalization at universe scale
2. Handle data asymmetry (funding data available for only ~20 of ~100 symbols)
3. Implement cross-sectional position sizing (signal strength relative to universe, not independent)
4. Combine signals with configurable weights and structure a 21-run parameter sweep
5. Manage daily rebalancing across ~100 symbols with leverage constraints
6. Produce structured sweep results and identify optimal configurations

**Student openings**:
- **beginner_no_finance**: "I want to combine different trading signals and test many parameter combinations across all the crypto futures in my dataset. I also have 'funding rate' data for some of them. How do I set this up on LEAN?"
- **intermediate_developer**: "I'm implementing a composite signal strategy on LEAN across ~100 crypto futures, combining trend, mean-reversion, and carry signals. I need to run a parameter sweep over the signal weights. How should I structure the universe-wide algorithm and the sweep workflow?"
- **advanced_quant**: "I'm building a universe-wide multi-signal alpha on LEAN: trend, reversion, and carry across ~100 futures with cross-sectional sizing and leverage constraints. Funding data is only available for 20 symbols. I need a grid search over signal weights. What's the architecture?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info", "search_docs"],
  "docs_available": ["lean_algorithm_guide.md", "moving_averages.md", "risk_metrics.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `compute_indicator`, `analyze_backtest_results`, `plot_chart`

**Eval strategy**:
- **Base-case trade log comparison**: Equal-weight (0.33/0.33/0.33) per-symbol trade logs for 10 reference symbols compared against reference.
- **Parameter sweep completeness**: Check that all 21 valid weight combinations are tested.
- **Sweep results structure**: Output includes weight combination + Sharpe + drawdown + return + turnover per run.
- **Top-3 identification**: Agent correctly identifies the best weight combinations (within tolerance of reference).
- **Funding data handling**: Symbols without funding data should not have carry signal contribution.
- **Portfolio constraints**: Gross leverage never exceeds 3.0x.

**Runtime budget**: 21 sequential LEAN backtests × ~2 min each + compilation overhead ≈ 45 min total. `timeout_minutes: 45` in the task JSON. Each individual backtest is capped at `LEAN_RUN_TIMEOUT` seconds (default 300s) via `run_backtest.sh`. Exit code 124 indicates a timeout kill.

**Ground-truth preparation**: Run reference algorithm at equal weights → export `I06_reference_trades.json` (10 reference symbols). Run full sweep → export `I06_reference_sweep_results.json` with all 21 combinations.

---

### 4.6 I07 — Alpha Model Architecture on LEAN

**Difficulty**: medium
**Category**: implementation

**Core idea**: Given the same trend-following strategy concept from I02, re-implement it using LEAN's **Algorithm Framework** instead of the classic `OnData()` approach. The agent must write a custom `AlphaModel` that emits `Insight` objects, wire it to a built-in `PortfolioConstructionModel` and `ExecutionModel`, and understand how the Insight→Target→Order pipeline works. This tests a fundamentally different skill: framework comprehension and modular composition vs. writing everything from scratch.

**Strategy specification**:
```
Strategy: EMA Crossover via Algorithm Framework — Multi-Asset
Assets:   All symbols in universe.json Tier 2 (~20 core liquid futures)
Resolution: Daily
Period:   2022-01-01 to 2025-12-31

Architecture: LEAN Algorithm Framework (NOT classic OnData approach)

Alpha Model — EmaCrossoverAlphaModel (custom):
  Per symbol:
  - Compute 10-day EMA (fast) and 30-day EMA (slow)
  - When fast EMA crosses above slow → emit Insight.Up(symbol, TimeSpan.FromDays(5),
      magnitude: (fastEma - slowEma) / slowEma,
      confidence: 1.0)
  - When fast EMA crosses below slow → emit Insight.Down(symbol, TimeSpan.FromDays(5),
      magnitude: abs(fastEma - slowEma) / slowEma,
      confidence: 1.0)
  - Insights expire after 5 days (re-emitted if signal persists)

Portfolio Construction: EqualWeightingPortfolioConstructionModel (built-in)
  - Allocates equally across all active insights

Execution: ImmediateExecutionModel (built-in)
  - Executes portfolio targets immediately at market price

Risk Management: None (deferred to I09)

Output required:
- Per-symbol trade log (same format as I02)
- Insight emission log (timestamp, symbol, direction, magnitude, confidence, duration)
- Universe-level summary (same metrics as I02)
- Trade log should be broadly comparable to I02 reference
  (not identical — framework execution timing differs slightly)
```

**Materials provided**:
- Data: Tier 2 universe (~20 symbols, daily) pre-loaded in LEAN format
- `universe.json` with symbol list
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`, `algorithm_framework_guide.md`

**Description**: Guide a student to re-implement a trend-following strategy using LEAN's Algorithm Framework instead of the classic `OnData()` approach. The student must write a custom `AlphaModel` class that emits `Insight` objects with direction, magnitude, confidence, and duration, then wire it to LEAN's built-in `EqualWeightingPortfolioConstructionModel` and `ImmediateExecutionModel`. This tests whether the agent understands LEAN's modular architecture — separating signal generation from position sizing from order execution — rather than implementing everything monolithically.

**Expected outcome**: Student produces a LEAN C# algorithm that (1) inherits `QCAlgorithm` and uses `SetAlpha()`, `SetPortfolioConstruction()`, `SetExecution()` in `Initialize()`, (2) implements a custom `EmaCrossoverAlphaModel` class inheriting `AlphaModel` that emits `Insight` objects per symbol, (3) uses built-in `EqualWeightingPortfolioConstructionModel` and `ImmediateExecutionModel`, (4) correctly manages per-symbol EMA indicator state within the alpha model, and (5) produces trade logs and an insight emission log.

**Required capabilities**:
1. Understand LEAN's Algorithm Framework architecture (Alpha → Portfolio → Risk → Execution pipeline)
2. Write a custom `AlphaModel` class with `Update()` method that returns `IEnumerable<Insight>`
3. Construct `Insight` objects with correct parameters (direction, duration, magnitude, confidence)
4. Wire framework modules together in `Initialize()` using `SetAlpha()`, `SetPortfolioConstruction()`, `SetExecution()`
5. Manage per-symbol indicator state within the alpha model (not in the main algorithm class)
6. Understand insight expiration and re-emission logic

**Student openings**:
- **beginner_no_finance**: "I heard LEAN has a modular framework where you separate your trading signals from position sizing. I already have a simple moving average strategy working — how do I refactor it into this framework? I've never used 'Alpha Models' or 'Insights' before."
- **intermediate_developer**: "I want to restructure my LEAN strategy using the Algorithm Framework — AlphaModel emitting Insights, built-in portfolio construction, etc. How do I write a custom AlphaModel and wire it into the framework?"
- **advanced_quant**: "I'm migrating from classic `OnData()` to the Algorithm Framework for composability. I need a custom `AlphaModel` with proper insight duration and magnitude, wired to `EqualWeightingPortfolioConstructionModel`. What's the cleanest architecture?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["lean_algorithm_guide.md", "algorithm_framework_guide.md", "moving_averages.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `search_docs`, `plot_chart`

**Eval strategy**:
- **Framework architecture check**: Code must contain `SetAlpha(`, `SetPortfolioConstruction(`, `SetExecution(`. Must NOT have strategy logic in `OnData()`.
- **Alpha model class**: A class inheriting `AlphaModel` with `Update()` method that yields `Insight` objects.
- **Insight quality**: Insights have valid direction, non-zero magnitude, duration ≥ 1 day.
- **Trade log comparison**: For 5 reference symbols, trade timing and direction should broadly match I07 reference (±2 bars tolerance, since framework execution timing differs from classic).
- **Insight log**: Insight emission log exists and contains structured records.

**Ground-truth preparation**: Run reference algorithm → export `I07_reference_trades.json` (5 reference symbol trade logs) + `I07_reference_insights.json` (insight emission log).

---

### 4.7 I08 — Multi-Alpha Portfolio Construction on LEAN

**Difficulty**: hard
**Category**: implementation

**Core idea**: Run **multiple alpha models simultaneously** within LEAN's Algorithm Framework and use a **portfolio construction model** to combine their competing signals into optimal portfolio weights. This tests the agent's ability to work with LEAN's built-in portfolio optimization models and understand how multiple insight streams are aggregated into portfolio targets.

**Strategy specification**:
```
Strategy: Multi-Alpha with Portfolio Optimization — Universe-Wide
Assets:   All symbols in universe.json Tier 1 (~100 USDT-M perpetual futures)
Resolution: Daily
Period:   Each symbol from its listing date → 2025-12-31

Architecture: LEAN Algorithm Framework with multiple alpha models

Alpha Model 1 — TrendAlpha (custom):
  Per symbol:
  - Compute 20-day SMA and 50-day SMA
  - SMA_20 > SMA_50 → Insight.Up(symbol, 3 days, magnitude=0.5, confidence=0.6)
  - SMA_20 < SMA_50 → Insight.Down(symbol, 3 days, magnitude=0.5, confidence=0.6)

Alpha Model 2 — MeanReversionAlpha (custom):
  Per symbol:
  - Compute 14-day RSI
  - RSI < 30 → Insight.Up(symbol, 2 days, magnitude=0.8, confidence=0.7)
  - RSI > 70 → Insight.Down(symbol, 2 days, magnitude=0.8, confidence=0.7)
  - 30 ≤ RSI ≤ 70 → no insight (neutral)

Alpha Model 3 — MomentumAlpha (custom):
  Per symbol:
  - Compute 20-day Rate of Change (ROC = (close - close_20) / close_20)
  - ROC > 0.05 → Insight.Up(symbol, 5 days, magnitude=abs(ROC), confidence=0.5)
  - ROC < -0.05 → Insight.Down(symbol, 5 days, magnitude=abs(ROC), confidence=0.5)

Portfolio Construction: Compare two models
  Run 1: InsightWeightingPortfolioConstructionModel
    - Weights positions by insight confidence × magnitude
  Run 2: EqualWeightingPortfolioConstructionModel
    - Equal allocation across active insights

Execution: ImmediateExecutionModel (built-in)

Risk Management: None (keep focus on multi-alpha + portfolio construction)

Position constraints:
  - Max 5% per symbol
  - Total gross exposure capped at 2.0x

Output required:
- Per-alpha insight count and hit rate
- Per-symbol trade log (for both portfolio construction runs)
- Comparison: InsightWeighting vs EqualWeighting performance
  (Sharpe, return, max drawdown, turnover)
- Universe-level summary for each run
```

**Materials provided**:
- Data: Tier 1 universe (~100 symbols, daily) pre-loaded in LEAN format
- `universe.json` with symbol list
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`, `algorithm_framework_guide.md`

**Description**: Guide a student to build a multi-alpha strategy on LEAN that runs three independent alpha models simultaneously (trend, mean-reversion, momentum), each emitting insights with different magnitudes, confidences, and durations. The student must then compare two portfolio construction models — `InsightWeightingPortfolioConstructionModel` (weights by confidence × magnitude) and `EqualWeightingPortfolioConstructionModel` — to see how signal aggregation affects portfolio performance. This tests the most powerful feature of LEAN's framework: composable, independent signal generators feeding into interchangeable portfolio optimizers.

**Expected outcome**: Student produces (1) three custom alpha model classes, each inheriting `AlphaModel`, (2) a main algorithm that registers all three via `AddAlpha()`, (3) two separate runs using different `PortfolioConstructionModel` implementations, (4) per-alpha insight statistics, (5) per-symbol trade logs for both runs, and (6) a comparison of the two portfolio construction approaches.

**Required capabilities**:
1. Write three independent `AlphaModel` classes with different indicator logic and insight parameters
2. Register multiple alphas via `AddAlpha()` (not `SetAlpha()` which replaces)
3. Understand how LEAN aggregates insights from multiple alpha models (insight collection, conflict resolution)
4. Configure and use `InsightWeightingPortfolioConstructionModel` (understands confidence × magnitude weighting)
5. Run two separate backtests with different portfolio construction models and compare results
6. Track per-alpha contribution metrics (insight counts, directional accuracy)

**Student openings**:
- **beginner_no_finance**: "I have three different trading strategies — one based on trends, one on buying oversold assets, and one on momentum. I want to run all three at the same time and have LEAN figure out the best way to combine them. How do I set this up?"
- **intermediate_developer**: "I need to implement three alpha models in LEAN's framework — trend, mean-reversion, and momentum — and compare `InsightWeightingPortfolioConstructionModel` vs `EqualWeightingPortfolioConstructionModel` for combining them. How do I structure the multi-alpha setup?"
- **advanced_quant**: "I'm building a multi-alpha framework on LEAN with three signal sources across ~100 futures. I want to compare insight-weighted vs equal-weighted portfolio construction. What's the architecture for composable alpha models with interchangeable portfolio optimizers?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["lean_algorithm_guide.md", "algorithm_framework_guide.md", "moving_averages.md", "risk_metrics.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `search_docs`, `compute_indicator`, `analyze_backtest_results`, `plot_chart`

**Eval strategy**:
- **Multi-alpha architecture**: Code contains ≥ 3 classes inheriting `AlphaModel`, registered via `AddAlpha()`.
- **Insight emission**: Each alpha emits insights with distinct magnitude/confidence values matching the spec.
- **Portfolio model comparison**: Two separate backtest runs exist with different portfolio construction models.
- **Trade log comparison**: For 10 reference symbols under `InsightWeighting`, trades broadly match reference (±2 bars, since framework timing differs).
- **Comparison output**: A structured comparison of Sharpe/return/drawdown/turnover between the two runs.
- **Universe coverage**: Algorithm subscribed to ≥ 80 symbols.

**Ground-truth preparation**: Run reference algorithm with both portfolio models → export `I08_reference_trades_iw.json` (InsightWeighting, 10 reference symbols) + `I08_reference_trades_ew.json` (EqualWeighting, 10 reference symbols) + `I08_reference_comparison.json` (side-by-side metrics).

---

### 4.8 I09 — Risk Management Models on LEAN

**Difficulty**: hard
**Category**: implementation

**Core idea**: Add LEAN's dedicated **risk management layer** to a framework-based strategy. Risk models sit between portfolio construction and execution — they can modify, scale down, or veto portfolio targets before orders are placed. The agent must use built-in risk models and write a custom one, then compare strategy performance with and without the risk layer. This tests understanding of a critical production concept: risk management as a separate, composable module.

**Strategy specification**:
```
Strategy: Framework Strategy with Risk Management — Multi-Asset
Assets:   All symbols in universe.json Tier 2 (~20 core liquid futures)
Resolution: Hourly (1h) — more granular data for intraday risk events
Period:   2022-01-01 to 2025-12-31

Architecture: LEAN Algorithm Framework with risk management models

Alpha Model — TrendFollowingAlpha (custom, reuse from I07 adapted to hourly):
  Per symbol:
  - Compute 24-period EMA (fast) and 72-period EMA (slow) on hourly bars
  - Fast > Slow → Insight.Up(symbol, TimeSpan.FromHours(24))
  - Fast < Slow → Insight.Down(symbol, TimeSpan.FromHours(24))

Portfolio Construction: EqualWeightingPortfolioConstructionModel (built-in)

Execution: ImmediateExecutionModel (built-in)

Risk Management — Three configurations to compare:

  Run 1 (No Risk): No risk management model
    - Baseline performance

  Run 2 (Built-in Risk Models):
    - MaximumDrawdownPerSecurity(0.05m)
      → Liquidate any position that draws down > 5% from peak
    - TrailingStopRiskManagementModel(0.03m)
      → 3% trailing stop on all positions

  Run 3 (Custom Risk Model):
    - Write a custom MaxGroupExposureRiskManagementModel:
      → Group symbols by market cap tier (large/mid/small from universe.json)
      → Cap each group's gross exposure at 40% of portfolio
      → If a group exceeds 40%, scale down all positions in that group proportionally
    - Combined with TrailingStopRiskManagementModel(0.03m) via AddRiskManagement()

Output required:
- Per-symbol trade log for all 3 runs
- Per-run performance summary (Sharpe, max drawdown, return, trade count)
- Comparison table: No Risk vs Built-in Risk vs Custom Risk
  (focus on: max drawdown improvement, Sharpe change, number of risk-triggered exits)
- Risk event log: timestamp, symbol, risk model that triggered, action taken
```

**Materials provided**:
- Data: Tier 2 universe (~20 symbols, hourly) pre-loaded in LEAN format
- `universe.json` with symbol list and market cap tier assignments
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`, `algorithm_framework_guide.md`

**Description**: Guide a student to add LEAN's risk management layer to a framework-based strategy. The student must understand that risk models intercept portfolio targets *after* portfolio construction but *before* execution — they can modify position sizes, liquidate positions, or block new entries based on risk limits. The student runs three configurations: no risk management, built-in risk models (`MaximumDrawdownPerSecurity` + `TrailingStopRiskManagementModel`), and a custom risk model (group exposure limits). Comparing the three reveals how risk management trades return for drawdown reduction.

**Expected outcome**: Student produces (1) a framework algorithm with `SetAlpha()`, `SetPortfolioConstruction()`, `SetExecution()`, and `SetRiskManagement()` / `AddRiskManagement()`, (2) three separate backtest runs with different risk configurations, (3) a custom `MaxGroupExposureRiskManagementModel` class inheriting `RiskManagementModel`, (4) a comparison table showing how risk models affect drawdown, Sharpe, and return, and (5) a risk event log showing when and why each risk model triggered.

**Required capabilities**:
1. Use `SetRiskManagement()` and `AddRiskManagement()` to register risk models in the framework
2. Configure built-in risk models: `MaximumDrawdownPerSecurity`, `TrailingStopRiskManagementModel`
3. Write a custom `RiskManagementModel` class with `ManageRisk()` method that returns adjusted `PortfolioTarget` list
4. Understand that risk models receive the portfolio construction targets and can modify them before execution
5. Stack multiple risk models via `AddRiskManagement()` (they apply sequentially)
6. Run three backtest configurations and produce a structured comparison
7. Log risk-triggered events (which model fired, on which symbol, what action was taken)

**Student openings**:
- **beginner_no_finance**: "My trading strategy makes money but sometimes has scary drops. I heard LEAN has built-in risk management that can automatically cut losses. How do I add stop-losses and drawdown limits without changing my strategy logic?"
- **intermediate_developer**: "I want to add risk management to my LEAN framework strategy — trailing stops, max drawdown per symbol, and a custom exposure limit model. How do I write and register risk management models in the framework?"
- **advanced_quant**: "I'm adding a risk management layer to my framework strategy on LEAN. I need to compare no-risk vs `MaximumDrawdownPerSecurity` + `TrailingStopRiskManagementModel` vs a custom group-exposure model. How do I structure the comparison?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["lean_algorithm_guide.md", "algorithm_framework_guide.md", "risk_metrics.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `search_docs`, `compute_indicator`, `analyze_backtest_results`, `plot_chart`

**Eval strategy**:
- **Risk model registration**: Code contains `SetRiskManagement(` or `AddRiskManagement(` with valid risk model classes.
- **Built-in models used**: `MaximumDrawdownPerSecurity` and `TrailingStopRiskManagementModel` appear in code.
- **Custom risk model**: A class inheriting `RiskManagementModel` with `ManageRisk()` method implementing group exposure logic.
- **Three-run comparison**: Three separate backtest runs with different risk configurations exist.
- **Drawdown improvement**: Run 2 and Run 3 should show lower max drawdown than Run 1.
- **Trade log comparison**: For 5 reference symbols under Run 2, trades match reference (±2 bars).
- **Risk event log**: Structured log of risk-triggered events exists.

**Ground-truth preparation**: Run reference algorithm in all 3 configurations → export `I09_reference_trades_norisk.json`, `I09_reference_trades_builtin.json`, `I09_reference_trades_custom.json` (5 reference symbols each) + `I09_reference_comparison.json`.

---

### 4.9 I10 — Parameter Optimization on LEAN

**Difficulty**: hard
**Category**: implementation

**Core idea**: Use LEAN's **built-in parameter optimization infrastructure** to systematically tune strategy parameters, replacing the manual shell-loop approach from I06. The agent must declare parameters via `SetParameter()` / `GetParameter()`, configure LEAN's optimization runner, and optionally integrate an external optimizer (e.g., Optuna) for Bayesian optimization. This tests the agent's ability to use LEAN's optimization tooling rather than reinventing parameter sweeps.

**Strategy specification**:
```
Strategy: Parameterized Trend Strategy with Optimization — Multi-Asset
Assets:   All symbols in universe.json Tier 2 (~20 core liquid futures)
Resolution: Daily
Period:   2022-01-01 to 2025-12-31

Architecture: LEAN Algorithm Framework with parameterized alpha model

Alpha Model — ParameterizedTrendAlpha (custom):
  Tunable parameters:
  - fast_period: EMA fast period (range: 5–30, step: 5)
  - slow_period: EMA slow period (range: 20–100, step: 10)
  - signal_threshold: minimum EMA spread to emit insight (range: 0.0–0.02, step: 0.005)
  Constraints:
  - fast_period < slow_period (invalid combinations skipped)

  Per symbol:
  - Compute EMA(fast_period) and EMA(slow_period)
  - spread = (fast_ema - slow_ema) / slow_ema
  - If spread > signal_threshold → Insight.Up(symbol, 5 days)
  - If spread < -signal_threshold → Insight.Down(symbol, 5 days)

Portfolio Construction: EqualWeightingPortfolioConstructionModel (built-in)
Execution: ImmediateExecutionModel (built-in)
Risk Management: TrailingStopRiskManagementModel(0.03m) (built-in)

Optimization:

  Phase 1 — Grid Search (LEAN Optimizer):
  - Use LEAN's optimization engine to sweep all valid parameter combinations
  - Parameter grid: fast_period × slow_period × signal_threshold
    (6 × 9 × 5 = 270 raw combinations, ~180 valid after constraint filtering)
  - Optimization target: maximize Sharpe ratio
  - Report: parameter combination + Sharpe + return + max drawdown for each run

  Phase 2 — Bayesian Optimization (Optional, Bonus):
  - Integrate Optuna (or similar) to run Bayesian optimization over the same parameter space
  - Budget: 50 trials (vs 180 for grid search)
  - Compare: does Bayesian find a comparable optimum with fewer evaluations?

Output required:
- Grid search results table (180 rows × metrics)
- Top-5 parameter combinations by Sharpe
- Detailed trade log for the best parameter combination
- (Optional) Bayesian optimization results with convergence curve
- Comparison: grid search best vs Bayesian best vs equal-weight baseline
```

**Materials provided**:
- Data: Tier 2 universe (~20 symbols, daily) pre-loaded in LEAN format
- `universe.json` with symbol list
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`, `algorithm_framework_guide.md`

**Description**: Guide a student to use LEAN's parameter optimization infrastructure to tune a trend-following strategy. Unlike I06's manual shell-loop approach, the student declares parameters using `SetParameter()` / `GetParameter()` and uses LEAN's optimization engine to sweep the parameter grid. Optionally, the student integrates Optuna for Bayesian optimization to find good parameters with fewer evaluations. This tests the agent's knowledge of LEAN's optimization tooling and the conceptual understanding of grid search vs. Bayesian optimization.

**Expected outcome**: Student produces (1) a parameterized LEAN algorithm using `GetParameter()` to read strategy parameters at runtime, (2) a LEAN optimization configuration that defines the parameter grid and optimization target, (3) grid search results for ~180 valid parameter combinations, (4) identification of the top-5 configurations by Sharpe, (5) detailed trade log for the best configuration, and optionally (6) a Bayesian optimization run using Optuna with a convergence comparison.

**Required capabilities**:
1. Use `GetParameter()` in the algorithm to read parameter values at runtime (not hardcoded)
2. Configure LEAN's optimization engine (parameter ranges, steps, constraints, optimization target)
3. Run a grid search over ~180 valid parameter combinations using LEAN's optimizer
4. Parse optimization results and identify optimal configurations
5. (Optional) Integrate Optuna or similar external optimizer — write a Python wrapper that calls LEAN per trial
6. Understand grid search vs. Bayesian optimization trade-offs (exhaustive vs. sample-efficient)

**Student openings**:
- **beginner_no_finance**: "I have a moving average strategy and I want to find the best settings — like how many days for the fast and slow averages. I heard LEAN can automatically test many combinations. How do I set that up?"
- **intermediate_developer**: "I need to run parameter optimization on my LEAN strategy — I want to sweep EMA periods and signal thresholds. How do I use `SetParameter()`/`GetParameter()` and LEAN's optimization engine instead of a manual loop?"
- **advanced_quant**: "I'm setting up parameter optimization for a trend strategy on LEAN. I want to compare LEAN's grid search against Bayesian optimization via Optuna. How do I structure the parameterized algorithm and the optimization workflow?"

**Environment**:
```json
{
  "data_files": ["universe.json"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["lean_algorithm_guide.md", "algorithm_framework_guide.md", "risk_metrics.md"],
  "sandbox_image": "quant-tutor-env:v2.0-lean",
  "network_enabled": false
}
```

**Convenient tools**: `search_docs`, `compute_indicator`, `analyze_backtest_results`, `plot_chart`

**Eval strategy**:
- **Parameter API usage**: Code uses `GetParameter()` (not hardcoded values) for tunable parameters.
- **Optimization configuration**: A valid optimization config exists (parameter ranges, target metric).
- **Grid search completeness**: ≥ 150 valid parameter combinations tested (out of ~180).
- **Results structure**: Output includes parameter values + Sharpe + return + drawdown per combination.
- **Top-5 identification**: Agent identifies the best 5 configurations (Sharpe within 10% of reference top-5).
- **Best-config trade log**: For the best parameter combination, trade log for 5 reference symbols matches reference (±2 bars).
- **(Bonus) Bayesian optimization**: If Optuna is used, convergence curve shows improvement over random sampling.

**Runtime budget**: ~180 sequential LEAN backtests × ~1-2 min each ≈ up to 360 min for full grid search. Recommended `timeout_minutes: 60` (assuming LEAN Optimizer parallelizes internally). Each individual backtest is capped at `LEAN_RUN_TIMEOUT` seconds (default 300s) via `run_backtest.sh`. Exit code 124 indicates a timeout kill. For Bayesian optimization (Phase 2), the 50-trial budget should complete much faster.

**Ground-truth preparation**: Run reference grid search → export `I10_reference_grid_results.json` (all ~180 combinations). Run best config → export `I10_reference_trades.json` (5 reference symbols). Optionally run Optuna → export `I10_reference_bayesian.json`.

---

## 5. Difficulty & Capability Progression

### 5.1 Classic Approach (I01–I06)

```
I01  Implement SMA on LEAN            easy      Hello-world LEAN C# algorithm
 │                                               (single indicator, single asset, stock data)
 ▼
I02  Universe Trend-Following         medium    MA crossover across ~100 symbols
 │                                               (per-symbol indicators, equal-weight sizing,
 │                                                universe aggregation)
 ▼
I03  Universe Mean-Reversion          medium    RSI + stop-loss across ~100 symbols
 │                                               (per-symbol state machines, risk-based sizing,
 │                                                tagged exit reasons)
 ▼
I04  Multi-Timeframe Multi-Asset      hard      1h + 4h across ~20 symbols
 │                                               (per-symbol consolidators, dual-resolution indicators,
 │                                                Tier 2 data scale)
 ▼
I05  Universe Pairs Scanner           hard      Pair selection + spread trading across ~20 symbols
 │                                               (C(20,2) pair screening, multi-leg positions,
 │                                                exposure management)
 ▼
I06  Universe Multi-Signal + Sweep    hard      3 signals × ~100 symbols × 21 param configs
                                                 (cross-sectional sizing, funding data asymmetry,
                                                  systematic parameter optimization)
```

### 5.2 Algorithm Framework Approach (I07–I10)

```
I07  Alpha Model Architecture         medium    Refactor trend strategy to Algorithm Framework
 │                                               (custom AlphaModel, Insight emission,
 │                                                built-in PortfolioConstructionModel)
 ▼
I08  Multi-Alpha Portfolio Construct.  hard      3 alpha models × ~100 symbols
 │                                               (composable alpha models, insight aggregation,
 │                                                InsightWeighting vs EqualWeighting comparison)
 ▼
I09  Risk Management Models           hard      Framework strategy + risk layer × ~20 symbols
 │                                               (built-in risk models, custom RiskManagementModel,
 │                                                3-way comparison: no risk vs built-in vs custom)
 ▼
I10  Parameter Optimization Engine    hard      Parameterized alpha × ~180 grid combinations
                                                 (LEAN optimizer, GetParameter(), optional Optuna
                                                  Bayesian optimization comparison)
```

### 5.3 Full Progression Map

```
CLASSIC APPROACH (I01–I06)                  FRAMEWORK APPROACH (I07–I10)
──────────────────────────                  ────────────────────────────
"Write trading logic from scratch"          "Use LEAN's modular architecture"

I01  SMA hello-world (easy)
I02  Universe trend (medium)           ──►  I07  Same strategy, framework arch (medium)
I03  Mean-reversion + risk (medium)    ──►  I09  Framework + risk models (hard)
I04  Multi-TF consolidators (hard)
I05  Pairs scanner (hard)             ──►  I08  Multi-alpha + portfolio opt (hard)
I06  Multi-signal + sweep (hard)       ──►  I10  LEAN optimizer engine (hard)
```

**Concept progression (I01–I06)**:
- I01: Hello-world — single symbol, single indicator on LEAN
- I02: Scale to universe — per-symbol indicators and portfolio allocation across ~100 symbols
- I03: Complex per-symbol logic at scale — state machines, stop-loss, risk budgets × 100 symbols
- I04: Multi-resolution data at scale — consolidators and dual-TF indicators × 20 symbols
- I05: Cross-asset combinatorial analysis — pairwise screening, multi-leg hedged positions
- I06: Everything combined — multi-signal, universe-wide, data asymmetry, parameter optimization

**Concept progression (I07–I10)**:
- I07: Framework introduction — refactor classic strategy into Alpha→Portfolio→Execution pipeline
- I08: Multi-alpha composition — run multiple signal generators, compare portfolio construction models
- I09: Risk management layer — built-in + custom risk models that intercept before execution
- I10: Optimization infrastructure — LEAN's optimizer replaces manual sweep; optional Bayesian comparison

**Three progression dimensions** (updated):
1. **Strategy complexity**: simple crossover → asymmetric rules + stop-loss → multi-TF → cross-asset → composite signals
2. **Data scale**: 1 symbol → 100 symbols (daily) → 20 symbols (hourly) → 190 pairs → 100 symbols × 21 sweeps
3. **Architecture maturity**: manual `OnData()` → modular Alpha/Portfolio/Risk/Execution framework → optimizer integration

The classic series (I01–I06) tests **implementation skill** — can the agent translate a spec into working code. The framework series (I07–I10) tests **architectural comprehension** — can the agent use LEAN's production architecture to compose, manage risk, and optimize strategies.

Each task is independently executable but builds on concepts from earlier tasks. I07–I10 are designed to be attempted after I01–I06, but this is a recommendation, not a hard dependency.

---

## 6. Reference Documentation

### 6.1 New Doc Required: `lean_algorithm_guide.md`

The primary reference doc for I-series. Must be **general-purpose** (not task-specific), covering LEAN C# algorithm development.

Suggested structure:
```markdown
# LEAN C# Algorithm Development Guide

## 1. Algorithm Structure
- Inheriting from QCAlgorithm
- Initialize() and OnData() lifecycle
- Compilation and execution model

## 2. Data Subscription
- AddCryptoFuture(ticker, resolution, market)
- SetAccountCurrency("USDT")
- Resolution options: Daily, Hour, Minute, Second, Tick
- Multiple subscriptions

## 3. Built-In Indicators
- SMA, EMA, RSI, MACD, Bollinger Bands
- Creating: SMA(symbol, period)
- Accessing: indicator.Current.Value, indicator.IsReady
- Warm-up: SetWarmUp(period)

## 4. Order Management
- SetHoldings(symbol, percentage): target portfolio %
- MarketOrder(symbol, quantity): market order
- LimitOrder(symbol, quantity, price): limit order
- Liquidate(symbol): close position
- Liquidate(): close all positions

## 5. Portfolio State
- Portfolio[symbol].IsLong / .IsShort / .Invested
- Portfolio[symbol].Quantity, .AveragePrice
- Portfolio[symbol].UnrealizedProfit, .UnrealizedProfitPercent
- Portfolio.TotalPortfolioValue

## 6. Multi-Timeframe: Consolidators
- Consolidate(symbol, Resolution.Daily, handler)
- TradeBarConsolidator usage
- Registering indicators with consolidators

## 7. Multi-Asset Strategies
- Multiple AddCryptoFuture calls
- Iterating over multiple symbols in OnData
- Per-asset position management

## 8. Events and Logging
- OnOrderEvent(OrderEvent): order fill notification
- Log(message): write to backtest log
- Debug(message): write debug info
- Schedule.On(): time-based events

## 9. Parameter Optimization
- SetParameter(name, value): define parameters
- Accessing parameters in Initialize()
- Running multiple backtests with different parameters

## 10. Running Backtests
- Command: run_backtest /workspace/Algorithm.cs
- Output: /workspace/results/trades.json, summary.json
- Interpreting results

## 11. Common Patterns
- Crossover detection: previous vs current indicator values
- Entry price tracking: store price on fill via OnOrderEvent
- Stop-loss: check unrealized PnL each bar
- Position state machine: enum { Flat, Long, Short }

## 12. Common Pitfalls
- Forgetting SetWarmUp → indicators not ready
- Not checking IsWarmingUp in OnData
- Not checking slice.Bars.ContainsKey → KeyNotFoundException
- Using Resolution.Daily with hourly data → missing bars
```

### 6.2 New Doc Required: `algorithm_framework_guide.md`

Reference doc for I07–I10 covering LEAN's Algorithm Framework. Complements `lean_algorithm_guide.md` (which covers the classic approach).

Suggested structure:
```markdown
# LEAN Algorithm Framework Guide

## 1. Classic vs Framework Approach
- When to use classic OnData() vs Algorithm Framework
- Framework benefits: modularity, composability, testability
- Framework pipeline: Alpha → Portfolio → Risk → Execution

## 2. Alpha Models
- AlphaModel base class and Update() method
- Insight objects: direction, magnitude, confidence, duration
- Insight.Up(), Insight.Down(), Insight.Flat()
- Multiple alpha models: AddAlpha() vs SetAlpha()
- Per-symbol indicator management within alpha models
- Insight expiration and re-emission

## 3. Portfolio Construction Models
- IPortfolioConstructionModel interface
- Built-in models:
  - EqualWeightingPortfolioConstructionModel
  - InsightWeightingPortfolioConstructionModel
  - MeanVarianceOptimizationPortfolioConstructionModel
  - BlackLittermanOptimizationPortfolioConstructionModel
- PortfolioTarget: symbol + quantity
- How insights are converted to portfolio targets
- Custom portfolio construction models

## 4. Risk Management Models
- IRiskManagementModel interface
- Built-in models:
  - MaximumDrawdownPerSecurity
  - TrailingStopRiskManagementModel
  - MaximumSectorExposureRiskManagementModel
- Risk models intercept targets AFTER portfolio construction, BEFORE execution
- Stacking multiple risk models via AddRiskManagement()
- Writing custom risk models: ManageRisk() method

## 5. Execution Models
- IExecutionModel interface
- ImmediateExecutionModel (most common)
- VolumeWeightedAveragePriceExecutionModel
- Custom execution models

## 6. Parameter Optimization
- GetParameter(name): reading parameters at runtime
- LEAN Optimizer: grid search configuration
- Optimization targets: Sharpe, return, drawdown
- Parameter constraints and filtering
- External optimizer integration (Optuna, Hyperopt)

## 7. Wiring It Together
- Complete Initialize() example with all four modules
- Data flow: how insights flow through the pipeline
- Debugging: logging at each pipeline stage
- Common pitfalls:
  - SetAlpha() replaces; AddAlpha() adds
  - Insight duration must be positive
  - Risk models can silently liquidate positions
  - Portfolio construction runs on insight changes, not every bar

## 8. Framework vs Classic: Migration Patterns
- Refactoring OnData() logic into AlphaModel.Update()
- Moving position sizing from code to PortfolioConstructionModel
- Moving stop-loss from OnData() to RiskManagementModel
- When to stick with classic (complex state machines, exotic logic)
```

### 6.3 Existing Docs (Reusable)

- `moving_averages.md` — MA/EMA concepts. Used by I02, I04, I06, I07, I08, I10.
- `risk_metrics.md` — Performance metrics (Sharpe, drawdown). Used by I06, I08, I09, I10.
- `statistical_tests.md` — Cointegration, z-score. Used by I05.

### 6.4 New Doc: `crypto_futures_basics.md` (Shared with B-Series)

Already planned in B-series (see B-series plan §6.3). I-series reuses this doc for I05, I06 context on funding rates and futures mechanics.

---

## 7. Evaluation Architecture

### 7.1 Multi-Layer Behavioral Evaluation

The I-series uses **behavioral equivalence** rather than strict trade identity matching. LEAN's fill model, warmup handling, and indicator state can shift trades by 1-2 bars even in pure backtesting, creating false negatives with strict matching. Instead, evaluation compares four layers of behavioral similarity:

```
Signal agreement       0.40   (deterministic Python signals vs agent positions)
Position overlap       0.30   (reference LEAN positions vs agent positions)
Performance metrics    0.20   (Sharpe, return, drawdown proximity)
Trade similarity       0.10   (relaxed trade matcher, 2-bar tolerance)
```

**Key insight**: Reference signals are **deterministic** — they depend only on data + formula, not the LEAN engine. By computing signals in Python from raw market data (`bench/reference/generate_reference_signals.py`), we get a ground truth free from simulator quirks. Evaluation then compares the agent's LEAN-produced positions against these deterministic signals.

#### 7.1.1 Reference Data Files (per task)

Each task has two reference data files beyond the existing trade log:

```
bench/data/reference/
├── I0X_reference_trades.json      # Existing: LEAN round-trip trades
├── I0X_reference_signals.json     # NEW: deterministic Python signals
└── I0X_reference_summary.json     # NEW: standardized performance metrics
```

Reference positions are **not stored as files** — they are reconstructed from `reference_trades.json` at evaluation time via `reconstruct_positions()`. This avoids reference drift and version mismatches between trades and positions.

**Signal file schema** (`I0X_reference_signals.json`):
```json
{
  "task_id": "I01",
  "resolution": "daily",
  "start_date": "2022-01-01",
  "end_date": "2025-12-31",
  "warmup_periods": 20,
  "signals": {
    "BTCUSDT": [
      {"date": "2024-02-21", "signal": 1},
      {"date": "2024-02-22", "signal": 1},
      {"date": "2024-03-17", "signal": -1}
    ]
  }
}
```

**Position file schema** (`I0X_reference_positions.json`):
```json
{
  "task_id": "I01",
  "positions": {
    "BTCUSDT": [
      {"date": "2024-02-02", "quantity": 2.315},
      {"date": "2024-02-03", "quantity": 2.315}
    ]
  }
}
```

**Summary file schema** (`I0X_reference_summary.json`):
```json
{
  "task_id": "I01",
  "metrics": {
    "total_return_pct": 78.104,
    "sharpe_ratio": 1.727,
    "max_drawdown_pct": 31.9,
    "total_trades": 15,
    "win_rate": 0.40
  }
}
```

#### 7.1.2 Signal Generation (per task)

Signals are computed in pure Python/pandas by `bench/reference/generate_reference_signals.py`:

| Task | Strategy Formula | Signal Definition |
|------|-----------------|-------------------|
| I01 | SMA(20) on daily close | `+1 if close > SMA else -1` |
| I02 | Per-symbol dual SMA(10)/SMA(30) | `+1 if fast > slow else -1` |
| I03 | RSI(14) | `+1 if RSI<30, -1 if RSI>70, 0 otherwise` |
| I04 | EMA(20) on 4h + RSI(14) on 1h | Composite: trend dominates on disagreement |
| I05 | Pair log-spread z-score (20-day) | `+1/-1 if |z|>2, 0 if |z|<0.5` |
| I06 | 0.4×trend + 0.3×reversion + 0.3×carry | Composite with ±0.1 threshold |

Raw data source: `bench/data/raw/i-series/` — minute data is aggregated to daily to fill gaps in the daily CSVs (same logic as `convert_binance_to_lean.py --fill-daily-from-minute`).

#### 7.1.3 Layer Scoring Functions

All layer scores are **continuous [0.0, 1.0]**, not binary pass/fail:

**Signal agreement** (`score_signal_agreement`):
Per (date, symbol): compare reference signal direction vs `sign(agent_position)`.
- `+1.0` if directions match
- `+0.3` if one is zero (missed signal or unnecessary flat — penalizes more than a half-credit)
- `+0.0` if directions oppose
Returns weighted mean across all dates/symbols.

**Position overlap** (`score_position_overlap`):
Split into two components to avoid false positives from tiny positions:
- **Direction agreement (0.70)**: `sign(ref) == sign(agent)` — fraction of days where direction matches.
- **Size similarity (0.30)**: `min(|ref|, |agent|) / max(|ref|, |agent|)` — only computed when both are non-zero.
Final score: `0.70 * direction_score + 0.30 * size_score`. Range [0.0, 1.0].

**Performance** (`score_performance`):
Compare Sharpe, return, drawdown, trade count. Each sub-metric: `proximity = 1 - |ref - agent| / max(|ref|, |agent|, ε)`. Equal-weighted average.

**Trade similarity** (`score_trade_similarity`):
Continuous version of existing `match_trades()` with **2-bar tolerance** (relaxed from 1-bar). Combines count similarity (0.25), entry rate (0.25), direction rate (0.15), exit rate (0.15), PnL correlation (0.20).

#### 7.1.4 Weight Redistribution

When layers are unavailable (e.g., no agent orders, no reference signals), weights are redistributed proportionally among available layers:

```python
scale = 1.0 / sum(available_weights)
composite = sum(score_i * weight_i * scale for i in available_layers)
```

This ensures the composite stays in [0.0, 1.0] regardless of how many layers are active.

#### 7.1.5 Discrimination Validation (I01 baseline)

| Test Case | Signal | Position | Performance | Trade | Composite |
|-----------|--------|----------|-------------|-------|-----------|
| Self-test (reference as agent) | 0.658 | 1.000 | 1.000 | 1.000 | **0.863** |
| Shifted by 30 days | 0.445 | 0.543 | ~0.85 | ~0.60 | **0.603** |
| Inverted direction | 0.174 | 0.300 | 1.000 | ~0.35 | **~0.38** |
| Empty workspace | — | — | — | — | **0.000** |

The self-test signal score (0.658) is intentionally below 1.0: the I01 strategy is long-only (goes flat when signal=-1, not short), so agent position=0 when signal=-1 yields only 0.3 credit. The position overlap's direction+size split means inverted gives 0.30 (size matches but direction doesn't).

### 7.2 Shared Eval Helper: `_implementation_check.py`

Shared eval helper module for I-series. Contains both the original trade-matching functions (preserved for backward compatibility) and the new behavioral scoring layer:

```python
# bench/evaluation/test_scripts/_implementation_check.py

# ── Original functions (preserved) ──
- load_reference_trades(task_id) → list[dict]
- load_agent_trades(workspace_path) → list[dict]
- match_trades(ref_trades, agent_trades, time_tolerance_bars, resolution) → MatchResult
- compute_trade_log_score(match_result) → float
- check_csharp_patterns(workspace_path, patterns) → dict[str, bool]
- collect_lean_results(workspace_path) → dict | None
- collect_artifact_text(workspace_path, tool_logs) → str
- has_any(text, keywords) → bool
- has_regex(text, patterns) → bool

# ── New reference loaders ──
- load_reference_signals(task_id) → dict
- load_reference_positions(task_id) → dict
    Reconstructs from reference trades at runtime (no separate positions file).
- load_reference_summary(task_id) → dict

# ── New agent data extraction ──
- load_agent_orders(workspace_path) → list[dict]
    Parse orders.json, normalize PascalCase, filter to filled orders.
- reconstruct_positions(orders_or_trades, start_date, end_date) → dict[str, list[dict]]
    Build daily position series. From orders: cumulative fill tracking.
    From trades: entry_time→exit_time spans. Forward-filled daily.
- load_agent_summary(workspace_path) → dict
    Parse summary.json into standardized metrics dict.

# ── New layer scoring (continuous 0.0–1.0) ──
- score_signal_agreement(ref_signals, agent_positions) → float
- score_position_overlap(ref_positions, agent_positions) → float
- score_performance(ref_summary, agent_summary) → float
- score_trade_similarity(match_result) → float

# ── New composite scoring ──
@dataclass
class BehavioralResult:
    signal_score, position_score, performance_score, trade_score: float
    signal_weight=0.40, position_weight=0.30, performance_weight=0.20, trade_weight=0.10
    composite_score: float
    layers_available: list[str]

- compute_behavioral_score(task_id, workspace_path, resolution) → BehavioralResult
    Main entry: loads all data, scores each layer, redistributes weights, returns composite.
```

### 7.3 Per-Task Eval Script Structure

Each I-series eval script uses the behavioral scoring system with task-specific checks:

```python
def evaluate(workspace_path, tool_logs=None, conversation=None, *, data_files=None):
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,        # behavioral.signal_score >= 0.60
        "position_overlap": False,        # behavioral.position_score >= 0.60
        "performance_match": False,       # behavioral.performance_score >= 0.50
        "trade_count_match": False,       # behavioral.trade_score >= 0.40
        "code_patterns": False,
        # ... task-specific items ...
        "score": 0.0,
    }

    # 1. Backtest completion + trade log checks (same as before)
    # 2. Behavioral scoring
    behavioral = compute_behavioral_score("I0X", workspace_path, resolution="daily")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    # ... etc ...

    # 3. Task-specific checks (code patterns, indicators, etc.)
    # 4. Scoring with continuous behavioral weight
    _checklist = [
        {"item": "backtest_completed",  "weight": 0.05, "passed": results["backtest_completed"]},
        {"item": "trade_log_produced",  "weight": 0.05, "passed": results["trade_log_produced"]},
        {"item": "behavioral_score",    "weight": W,    "score": behavioral.composite_score},
        # ... task-specific items ...
    ]
    # Items with "score" key use score directly; items with "passed" use 1.0/0.0
    score = sum(
        c["weight"] * c.get("score", 1.0 if c.get("passed") else 0.0)
        for c in _checklist
    )

    # 5. Gates + data source verification (same as before)
    results["behavioral_composite"] = round(behavioral.composite_score, 4)
    results["behavioral_layers"] = behavioral.layers_available
    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results
```

#### Per-Task Weight Allocation

| Task | behavioral_score | Task-specific items | Gate items |
|------|-----------------|---------------------|------------|
| I01 | 0.60 | code_patterns 0.10, sma_indicator_used 0.10 | backtest 0.05, trades 0.05 |
| I02 | 0.55 | code_patterns 0.05, universe_coverage 0.15, universe_summary 0.05 | backtest 0.05, trades 0.05 |
| I03 | 0.50 | code_patterns 0.05, stop_loss 0.08, long_short_both 0.07, exit_tagged 0.05 | backtest 0.05, trades 0.05 |
| I04 | 0.55 | code_patterns 0.05, consolidator 0.10, dual_resolution 0.10 | backtest 0.05, trades 0.05 |
| I05 | 0.50 | code_patterns 0.05, pair_selection 0.08, multi_leg 0.07, exposure_cap 0.05 | backtest 0.05, trades 0.05 |
| I06 | 0.45 | code_patterns 0.05, sweep 0.10, top_configs 0.05, funding 0.05 | backtest 0.05, trades 0.05 |

Tasks with more task-specific checks (I03, I05, I06) allocate less to behavioral score, since the task-specific items capture implementation quality that behavioral scoring cannot.

### 7.4 Result Judge Category Rubric

Add an `implementation` entry to `CATEGORY_RESULT_RUBRICS`:

```
Implementation tasks — evaluation focus:
1. Behavioral Equivalence: Does the agent's algorithm produce positions that align with
   the strategy's deterministic signals? (scored continuously via 4-layer behavioral eval:
   signal agreement, position overlap, performance proximity, trade similarity)
2. Completeness: Does the algorithm handle all specified rules (entry, exit, stop-loss,
   position sizing)?
3. API Usage: Does the code use LEAN C# API correctly (indicators, orders, portfolio state)?
4. Execution: Does the backtest run to completion and produce valid output?
5. Interpretation: Does the agent explain the results meaningfully (trade count, return, etc.)?
```

### 7.5 Process Reasonableness Criteria

Add to `CATEGORY_PROCESS_CRITERIA`:

```
implementation: "understand strategy spec → set up LEAN algorithm structure →
                implement indicators → implement entry/exit logic →
                handle edge cases (warm-up, position state) →
                run backtest → inspect trade log → verify against spec →
                iterate if trades don't match"
```

Score caps for missing critical artifacts:
- No `.cs` file in workspace → cap at 0.10
- `.cs` file exists but backtest didn't run → cap at 0.20
- Backtest ran but no trades produced → cap at 0.30
- Trades produced but trade count off by > 50% from reference → cap at 0.50

---

## 8. Docker Sandbox: `quant-tutor-env:v2.0-lean`

### 8.1 Sandbox Requirements

A new Docker image is required for I-series, extending the base `quant-tutor-env:v2.0`:

```dockerfile
FROM quant-tutor-env:v2.0

# Install .NET SDK (required for LEAN C# compilation)
RUN apt-get update && apt-get install -y dotnet-sdk-8.0

# Install LEAN engine
RUN git clone https://github.com/QuantConnect/Lean.git /lean
WORKDIR /lean
RUN dotnet build QuantConnect.Lean.sln

# Pre-configure LEAN
COPY lean-config.json /lean/Launcher/config.json

# Install wrapper script
COPY run_backtest.sh /usr/local/bin/run_backtest
RUN chmod +x /usr/local/bin/run_backtest

# Pre-load converted data
COPY lean-data/ /lean/Data/

WORKDIR /workspace
```

### 8.2 `run_backtest` Wrapper Script

The wrapper script abstracts LEAN plumbing:

```bash
#!/bin/bash
# Usage: run_backtest /workspace/Algorithm.cs
#
# 1. Copies Algorithm.cs into LEAN project
# 2. Builds the C# project
# 3. Runs LEAN engine (with per-run timeout)
# 4. Extracts results to /workspace/results/
#   - trades.json    (closed trades)
#   - summary.json   (performance metrics)
#   - orders.json    (all order events)
#   - log.txt        (algorithm log output)

ALGO_FILE=$1
RESULTS_DIR=/workspace/results

# Per-backtest timeout (default 5 min, override via LEAN_RUN_TIMEOUT env var)
LEAN_RUN_TIMEOUT="${LEAN_RUN_TIMEOUT:-300}"

# ... implementation details ...
# LEAN engine is wrapped with: timeout "$LEAN_RUN_TIMEOUT" dotnet run ...
# Exit code 124 = timeout killed
```

**Runtime enforcement**: The `timeout` bash command wraps the LEAN engine invocation. Tasks that run multiple backtests (I06: 21 runs, I10: ~180 runs) can override `LEAN_RUN_TIMEOUT` to adjust the per-run cap. Exit code 124 is mapped to a recognizable "timed out" error in the log.

### 8.3 Pre-configured LEAN Config

The LEAN `config.json` is pre-configured for Binance futures:

```json
{
  "environment": "backtesting",
  "algorithm-type-name": "Algorithm",
  "algorithm-language": "CSharp",
  "data-folder": "/lean/Data",
  "results-destination-folder": "/workspace/results",
  "log-handler": "QuantConnect.Logging.FileLogHandler",
  "brokerage-model": "BinanceFuturesBrokerageModel",
  "account-currency": "USDT"
}
```

---

## 9. Persona Considerations

### 9.1 All I-Tasks Use Three Personas

The implementation skill scales naturally with persona level:

| Persona | What they focus on | Tutor should adapt by... |
|---------|-------------------|--------------------------|
| **beginner_no_finance** | "What is LEAN? What is C#? How do I even start?" | Explaining C# syntax basics, LEAN algorithm lifecycle, step-by-step code writing |
| **intermediate_developer** | "I know programming but not C# or LEAN. How do I translate this strategy spec?" | Focusing on LEAN API specifics, strategy translation patterns, debugging compile errors |
| **advanced_quant** | "I want a clean, efficient implementation with proper edge-case handling." | Discussing indicator edge cases, warm-up nuances, position state correctness, execution timing |

### 9.2 Student Opening Design

Per v2.0 guidelines: one entry point per persona, no capability enumeration.

```
GOOD (beginner, I02):
  "I have access to a trading engine called LEAN and some Bitcoin data.
   I want to test a moving average crossover strategy but I've never
   written C# before. Can you help me get started?"

BAD (beginner, I02):
  "I want to write a QCAlgorithm in C# that uses AddCryptoFuture to
   subscribe to BTCUSDT, creates SMA indicators with windows 10 and 30,
   implements crossover detection in OnData, and handles warm-up."
```

---

## 10. Task Summary Table

### Classic Approach (I01–I06)

| Task | Title | Difficulty | Data Tier | Symbols | Timeframes | Strategy | Key Challenge | Pairs With |
|------|-------|-----------|-----------|---------|------------|----------|---------------|------------|
| I01 | SMA Trend Filter | easy | Tier 1 | 1 (BTCUSDT) | 1d | Price vs SMA(20) | LEAN hello-world (single symbol) | — |
| I02 | Universe Trend | medium | Tier 1 | ~100 | 1d | Dual MA crossover | Universe-scale per-symbol indicators | S02 |
| I03 | Universe Reversion | medium | Tier 1 | ~100 | 1d | RSI + stop-loss | Per-symbol state machines at scale | S03 |
| I04 | Multi-TF Multi-Asset | hard | Tier 2 | ~20 | 1h → 4h | 4h trend + 1h entry | Per-symbol consolidators | S04 |
| I05 | Universe Pairs Scan | hard | Tier 2 | ~20 (190 pairs) | 1d | Pair z-score | Combinatorial pair selection + multi-leg | S05 |
| I06 | Universe Multi-Signal Sweep | hard | Tier 1 + Funding | ~100 + 20 funding | 1d + 8h | Composite 3-signal | Cross-sectional sizing + 21 param configs | S06 |

### Algorithm Framework Approach (I07–I10)

| Task | Title | Difficulty | Data Tier | Symbols | Timeframes | Strategy | Key Challenge | Pairs With |
|------|-------|-----------|-----------|---------|------------|----------|---------------|------------|
| I07 | Alpha Model Architecture | medium | Tier 2 | ~20 | 1d | EMA crossover via AlphaModel | Framework pipeline: Insight→Target→Order | I02 (framework refactor) |
| I08 | Multi-Alpha Portfolio Construction | hard | Tier 1 | ~100 | 1d | 3 alphas (trend+RSI+momentum) | Multi-alpha composition + portfolio model comparison | S06 |
| I09 | Risk Management Models | hard | Tier 2 | ~20 | 1h | Trend + risk layer | Built-in + custom risk models, 3-way comparison | I03 (framework risk) |
| I10 | Parameter Optimization Engine | hard | Tier 2 | ~20 | 1d | Parameterized trend | LEAN optimizer + optional Bayesian (Optuna) | I06 (framework optimization) |

**I-series total**: 10 tasks × 3 personas = **30 evaluation instances**

---

## 11. Ground-Truth Reference Preparation

### 11.1 Reference Algorithm Development

For each I-task, write a **canonical C# algorithm** that:
1. Exactly implements the strategy specification
2. Runs on the frozen LEAN-format data
3. Produces a deterministic trade log

These reference algorithms live in:
```
bench/reference/lean_algorithms/
├── I01_implement_sma.cs          # Classic approach
├── I02_trend_following.cs
├── I03_mean_reversion.cs
├── I04_multi_timeframe.cs
├── I05_cross_asset.cs
├── I06_multi_signal.cs
├── I07_alpha_model.cs            # Algorithm Framework approach
├── I08_multi_alpha.cs
├── I09_risk_management.cs
└── I10_parameter_optimization.cs
```

### 11.2 Reference Trade Log Generation

Run each reference algorithm on the frozen data and export trade logs:

```bash
# Generate reference trade logs — classic approach
python bench/reference/generate_lean_reference.py --task I01
python bench/reference/generate_lean_reference.py --task I02
# ... (also auto-generates signals, positions, summary via generate_reference_signals.py)

# Generate reference trade logs — framework approach
python bench/reference/generate_lean_reference.py --task I07
python bench/reference/generate_lean_reference.py --task I08
# ...
```

### 11.2b Reference Signal & Position Generation

**Reproducibility seed**: Each reference signal JSON includes a `"seed": null` field documenting that reference signals are fully deterministic (pure math, no randomization). The `seed` field provides a hook for future use — if a task needs randomized reference data, the seed would be set to an integer value. In the task schema (`QuantTutorTask`), an optional `seed: int` field overrides the default `hash(task_id_run_index)` used for distractor tool selection. When `seed` is absent or null, the default hashing behavior applies.

Deterministic reference signals, positions, and summaries for behavioral evaluation:

```bash
# Generate all signal/position/summary files (no Docker needed — pure Python)
python bench/reference/generate_reference_signals.py --task all
# Also called automatically by generate_lean_reference.py after each LEAN backtest
```

Output:
```
bench/data/reference/
├── I01_reference_trades.json              # LEAN round-trip trades
├── I01_reference_signals.json             # Deterministic Python signals (316 signals)
├── I01_reference_positions.json           # Daily positions from LEAN orders (201 entries)
├── I01_reference_summary.json             # Standardized metrics (Sharpe 0.168, 32.9% return)
├── I02_reference_trades.json
├── I02_reference_signals.json             # 676 signals (3 symbols × SMA(10)/SMA(30))
├── I02_reference_positions.json
├── I02_reference_summary.json
├── I03_reference_signals.json             # 321 signals (RSI(14))
├── I04_reference_signals.json             # 335 signals (EMA(20) 4h + RSI(14) 1h)
├── I05_candidate_pairs.json               # Pre-computed top-10 correlated pairs for I05
├── I05_reference_signals.json             # 390 signals (pair z-score, 2 symbols)
├── I06_reference_signals.json             # 316 signals (composite 3-signal)
├── I06_reference_sweep_results.json       # Parameter sweep results for I06
├── I07_reference_trades.json              # Framework approach
├── I07_reference_insights.json            # Insight emission log
├── I08_reference_trades_iw.json           # InsightWeighting run
├── I08_reference_trades_ew.json           # EqualWeighting run
├── I08_reference_comparison.json          # Side-by-side metrics
├── I09_reference_trades_norisk.json       # No risk management
├── I09_reference_trades_builtin.json      # Built-in risk models
├── I09_reference_trades_custom.json       # Custom risk model
├── I09_reference_comparison.json          # 3-way comparison
├── I10_reference_trades.json              # Best config trade log
└── I10_reference_grid_results.json        # ~180 grid search results
```

### 11.3 Reference Validation

Each reference trade log must be validated:
- Trade count is reasonable (not 0, not 10000+)
- Entry/exit prices are within the data's price range
- PnL per trade is consistent with price movement
- Total return is plausible for the strategy type
- No trades occur during warm-up period

Each reference signal file must be validated:
- Signal count matches expected date range minus warmup
- Signals are in {-1, 0, +1} only
- Self-test: `compute_behavioral_score()` with reference workspace scores > 0.85

---

## 12. Implementation Checklist

### Phase 0: Infrastructure (Prerequisites)

**Data pipeline (run once by maintainers):**
- [x] Finalize `universe.json` — rank all USDT-M perpetuals by 2024 avg daily volume, select top 100 for Tier 1, top 20 for Tier 2, top 5 for Tier 3
- [x] Write `bench/scripts/download_binance_full_universe.py` — bulk download with parallelism, resume, checksum verification
- [x] Download Tier 1: ~100 symbols × 1d (from listing date → 2025-12-31)
- [x] Download Tier 2: ~20 symbols × 1h + 4h (2022-01-01 → 2025-12-31)
- [x] Download Tier 3: ~5 symbols × 5m + 1m (2024-01-01 → 2025-12-31)
- [x] Download funding rates: ~20 symbols (from listing date → 2025-12-31)
- [x] Write `bench/scripts/convert_binance_to_lean.py` — Binance CSV → LEAN format converter for all tiers/timeframes
- [x] Convert all tiers to LEAN format and validate
- [x] Write `bench/scripts/generate_flat_universe.py` — structured universe.json → flat JSON array for C# algorithms
- [x] Write `bench/scripts/prepare_i_series_data.py` — single-command orchestrator for full pipeline (download → convert → flat universe → upload → verify)

**HuggingFace dataset (decoupled storage):**
- [x] Create HF dataset repo `Varsity-Tech/quant-tutor-bench-data`
- [ ] Configure Git LFS for large files (`.zip`, `.csv` > 10MB)
- [ ] Upload raw S/B-series data to `raw/sb-series/`
- [ ] Upload raw I-series data to `raw/i-series/` (organized by tier)
- [x] Upload LEAN-format data to `lean/` *(test subset: 3 symbols, 739 zips uploaded)*
- [x] Upload `universe.json` to `raw/i-series/` *(flat format for C# algorithms)*
- [ ] Tag initial dataset version (commit hash for reproducibility)
- [x] Write `bench/scripts/upload_lean_to_hf.py` — upload LEAN data + flat universe.json via `upload_folder()`

**Data manager (runtime download + cache):**
- [x] Write `bench/scripts/data_manager.py` (see §2.7)
- [x] Add `huggingface_hub` to `bench/requirements.txt`
- [x] Add `bench/data/hf_cache/` to `.gitignore`
- [ ] Test: first run downloads data; second run uses cache
- [ ] Test: `revision` parameter pins to specific HF commit
- [x] Fix: copy universe.json into lean directory so LEAN finds it at `Globals.DataFolder/universe.json`

**Orchestrator integration (mount data into Docker):**
- [x] Add `lean_data_dir` parameter to `container_manager.py` (see §2.8)
- [x] Add `_ensure_lean_data()` to `orchestrator.py` (see §2.8)
- [x] Add `lean_data_dir` mount for I-series tasks (`-v lean_data:/lean/Data:ro`)
- [ ] Test: I-series task container sees data at `/lean/Data/` *(mount chain verified in code review; Docker runtime test pending)*
- [x] Verify: S/B-series tasks unaffected — LEAN mount only triggers when `"lean" in sandbox_image`

**Docker / LEAN environment (engine only, no data):**
- [x] Build Docker image `quant-tutor-env:v2.0-lean` with LEAN engine + .NET SDK (NO data, ~3GB) — `docker/Dockerfile.lean`
- [x] Write and test `run_backtest` wrapper script — `docker/run_backtest.sh`
- [x] Pre-configure LEAN `config.json` for Binance futures — `docker/lean-config.json`
- [ ] Test that LEAN loads and processes mounted Tier 1 data correctly (100 symbols)
- [ ] Test that LEAN handles Tier 2 hourly + 4h consolidation correctly
- [ ] Benchmark: measure runtime for a 100-symbol daily backtest on LEAN (target < 5 min)
- [ ] Push image to Docker Hub / GHCR

### Phase 1: Reference Documentation

- [x] Write `lean_algorithm_guide.md` reference doc (see §6.1)
- [ ] Write `algorithm_framework_guide.md` reference doc (see §6.2) — covers Alpha/Portfolio/Risk/Execution models
- [x] Write `crypto_futures_basics.md` reference doc (shared with B-series, see §6.4)
- [x] Verify existing docs are compatible (`moving_averages.md`, `risk_metrics.md`, `statistical_tests.md`)

### Phase 2: Reference Algorithms & Ground-Truth (Classic: I01–I06)

- [x] Write reference C# algorithm: `I01_implement_sma.cs`
- [x] Write reference C# algorithm: `I02_trend_following.cs`
- [x] Write reference C# algorithm: `I03_mean_reversion.cs`
- [x] Write reference C# algorithm: `I04_multi_timeframe.cs`
- [x] Write reference C# algorithm: `I05_cross_asset.cs`
- [x] Write reference C# algorithm: `I06_multi_signal.cs`
- [ ] Run all reference algorithms → export trade logs *(reference JSONs exist but need re-generation with flat universe.json)*
- [ ] Run I06 parameter sweep → export sweep results *(reference JSON exists)*
- [ ] Validate all reference trade logs (see §11.3)
- [x] Write `bench/reference/generate_reference_signals.py` — deterministic signal computation from raw data (I01–I06)
- [x] Generate reference signals for I01 (316 signals, SMA(20) on daily BTC)
- [x] Generate reference signals for I02–I06 (all tasks, all formulas)
- [x] Generate reference positions for I01 (201 daily position entries from LEAN trades)
- [x] Generate reference summaries for I01–I06 (standardized metrics)
- [x] Validate I01 self-test: behavioral composite = 0.897 (> 0.85 threshold)

### Phase 2b: Reference Algorithms & Ground-Truth (Framework: I07–I10)

- [ ] Write reference C# algorithm: `I07_alpha_model.cs` — EMA crossover via AlphaModel + EqualWeighting
- [ ] Write reference C# algorithm: `I08_multi_alpha.cs` — 3 alpha models + InsightWeighting/EqualWeighting comparison
- [ ] Write reference C# algorithm: `I09_risk_management.cs` — Framework strategy + 3 risk configurations
- [ ] Write reference C# algorithm: `I10_parameter_optimization.cs` — Parameterized alpha + LEAN optimizer
- [ ] Run I07 → export `I07_reference_trades.json` + `I07_reference_insights.json`
- [ ] Run I08 (both portfolio models) → export `I08_reference_trades_iw.json`, `I08_reference_trades_ew.json`, `I08_reference_comparison.json`
- [ ] Run I09 (3 risk configs) → export `I09_reference_trades_norisk.json`, `I09_reference_trades_builtin.json`, `I09_reference_trades_custom.json`, `I09_reference_comparison.json`
- [ ] Run I10 grid search → export `I10_reference_grid_results.json` + `I10_reference_trades.json`
- [ ] Validate all framework reference trade logs

### Phase 3: Task JSONs (bench/tasks/layer2/implementation/)

- [x] Redesign I01_implement_sma.json for LEAN/C#
- [x] I02_trend_following.json
- [x] I03_mean_reversion.json
- [x] I04_multi_timeframe.json
- [x] I05_cross_asset.json
- [x] I06_multi_signal_sweep.json
- [ ] I07_alpha_model.json
- [ ] I08_multi_alpha.json
- [ ] I09_risk_management.json
- [ ] I10_parameter_optimization.json

### Phase 4: Eval Scripts (bench/evaluation/test_scripts/)

- [x] Write `_implementation_check.py` shared helper module
- [x] Redesign I01_implement_sma.py for LEAN/C#
- [x] I02_trend_following.py
- [x] I03_mean_reversion.py
- [x] I04_multi_timeframe.py
- [x] I05_cross_asset.py
- [x] I06_multi_signal_sweep.py
- [x] Add multi-layer behavioral scoring to `_implementation_check.py`:
  - [x] Reference loaders: `load_reference_signals`, `load_reference_positions`, `load_reference_summary`
  - [x] Agent extractors: `load_agent_orders`, `reconstruct_positions`, `load_agent_summary`
  - [x] Layer scorers: `score_signal_agreement`, `score_position_overlap`, `score_performance`, `score_trade_similarity`
  - [x] `BehavioralResult` dataclass + `compute_behavioral_score()` entry point with weight redistribution
- [x] Migrate I01–I06 eval scripts to use `compute_behavioral_score()` with per-task weight allocation
- [x] Validate: self-test, shifted-data, inverted-direction, empty-workspace tests all pass
- [ ] I07_alpha_model.py — framework architecture checks + insight log validation
- [ ] I08_multi_alpha.py — multi-alpha registration + portfolio model comparison checks
- [ ] I09_risk_management.py — risk model registration + 3-way comparison checks
- [ ] I10_parameter_optimization.py — GetParameter() usage + grid completeness checks
- [ ] Extend `_implementation_check.py` with framework-specific helpers (insight log parsing, multi-run comparison)

### Phase 5: Scoring Integration

- [ ] Add `implementation` category to `CATEGORY_PROCESS_CRITERIA`
- [ ] Add `implementation` category to `CATEGORY_RESULT_RUBRICS`
- [ ] Verify that code_eval dimension handles C# files (not just Python)
- [ ] Verify that tool_usage scoring works with LEAN-specific tools

### Phase 6: Reference Oracle

- [ ] Generate reference executions for all 30 instances (10 tasks × 3 personas)
- [ ] Validate reference `key_results` and `step_count` baselines
- [ ] End-to-end integration test: run full benchmark on I-series tasks

---

## 13. Cross-Reference: Full Pipeline View (D + S + B + I)

```
                  D-SERIES          S-SERIES             B-SERIES             I-SERIES (CLASSIC)
                  (Data)            (Research)           (Engine)             (Implementation)
                  Python            Python               Python               C# on LEAN
                  ──────            ──────               ──────               ──────
                  1-2 symbols       1-2 symbols          1-2 symbols          FULL UNIVERSE
                  sample data       sample data          sample data          100+ symbols, multi-TF

                  D01–D11           S01 MA (easy)        B01 Metrics (easy)   I01 SMA (easy)
                  Data loading,     [stock data]         [stock data]         [stock data, LEAN hello-world]
                  cleaning,
                  exploration       S02 Trend (med)  →   B02 Engine (med)  →  I02 Trend (med)
                                    explore trends       build 3-layer arch   MA crossover × 100 symbols
                                    on BTC daily         on BTC daily         on Tier 1 universe daily

                                    S03 Reversion (med)→  B03 Look-Ahead (med)→ I03 Reversion (med)
                                    explore reversion    prove no leak        RSI + stop × 100 symbols
                                    on BTC daily         on BTC hourly        on Tier 1 universe daily

                                    S04 Volume (hard) →  B05 Execution (hard)→ I04 Multi-TF (hard)
                                    microstructure       slippage, fees       4h trend + 1h entry
                                    on BTC multi-TF      on BTC multi-TF      × 20 symbols (Tier 2)

                                    S05 Cross-Asset (h)→ B04 Multi-Asset (h)→ I05 Pairs Scan (hard)
                                    BTC/ETH dynamics     synchronized replay  190 pair scan + spread
                                    on 2 symbols         on 2 symbols         on 20 symbols (Tier 2)

                                    S06 Multi-Sig (h)  → B06 Walk-Fwd (hard)→ I06 Multi-Sig Sweep (hard)
                                    composite alpha      rolling IS/OOS       3 signals × 100 symbols
                                    on BTC daily         on BTC daily         × 21 param configs

                  ──────            ──────               ──────               ──────
                  Scale:            Scale:               Scale:               Scale:
                  ~10K rows         ~10K rows            ~10K rows            ~4M rows
                  (learning)        (learning)           (learning)           (production)

                  Output:           Output:              Output:              Output:
                  Clean data,       Validated signal,    Production engine,   Trade log matching
                  understanding     IC, rough Sharpe     no look-ahead,       ground-truth reference
                                                         execution sim        at universe scale
```

### 13.1 Algorithm Framework Extension (I07–I10)

I07–I10 extend the I-series with LEAN's Algorithm Framework, adding a new dimension:

```
I-SERIES (CLASSIC)                    I-SERIES (FRAMEWORK)
Manual OnData() approach              Modular Alpha/Portfolio/Risk/Execution
──────────────────────                ──────────────────────────────────────

I02 Trend (medium)              ──►   I07 Alpha Model Architecture (medium)
  MA crossover in OnData()              Same strategy refactored to AlphaModel
                                        + EqualWeightingPortfolioConstruction

I05 Pairs + I06 Multi-Sig      ──►   I08 Multi-Alpha Portfolio (hard)
  Manual multi-signal code              3 composable AlphaModels +
                                        InsightWeighting vs EqualWeighting

I03 Reversion + stop-loss      ──►   I09 Risk Management Models (hard)
  Manual stop-loss in OnData()          MaxDrawdown + TrailingStop + custom
                                        risk model (group exposure limits)

I06 Manual param sweep         ──►   I10 Parameter Optimization (hard)
  External shell loop                   LEAN optimizer + optional Optuna
                                        Bayesian optimization comparison
```

**What the framework series adds**:
- I07–I10 do NOT test new strategies — they test **architectural comprehension** of LEAN's production framework
- Same underlying concepts (trend, risk, optimization) but implemented via LEAN's modular pipeline
- Tests whether the agent knows which built-in model to use, how modules connect, how to compose and configure
- Complements I01–I06's focus on raw implementation skill

---

## 14. Open Questions & Decisions

### 14.1 Resolved

- **Engine**: LEAN (QuantConnect), open-source → confirmed
- **Language**: C# (native LEAN, no Python.NET overhead) → confirmed
- **Data storage**: HuggingFace (raw + LEAN format), decoupled from eval system → confirmed
- **Data scale**: Full Binance futures universe, not toy datasets → confirmed
- **Timeframes**: 5 representative (1m, 5m, 1h, 4h, 1d) → confirmed
- **Symbol tiers**: Tier 1 (~100, daily), Tier 2 (~20, hourly+4h), Tier 3 (~5, minute) → confirmed
- **Evaluation**: Trade log comparison against ground-truth → confirmed
- **LEAN as black-box**: Agent only writes Algorithm.cs + runs → confirmed
- **Architecture**: Dataset on HF, Docker image has engine only (~3GB), data mounted at runtime → confirmed
- **Data mount**: `hf_cache/lean/` → `/lean/Data:ro` via Docker volume mount → confirmed

### 14.2 To Be Resolved

1. **LEAN version pinning**: Which LEAN version to freeze? Latest stable release at build time?
2. **Funding rate data in LEAN**: LEAN may not natively support custom funding rate data. I06 may need a custom data reader or a pre-computed funding column merged into kline data. Need to prototype.
3. ~~**I01 redesign timeline**: Should I01 be redesigned before or after I02–I06 are built?~~ **Resolved**: I01 redesigned to LEAN C# single-symbol SMA; added to `TASK_ALGO_MAP` and `class_name_map` in `generate_lean_reference.py`.
4. **HuggingFace access**: Public or private dataset repo? If private, how to distribute access tokens for CI/CD and collaborators?
5. ~~**Trade log tolerance tuning**: The ±1 bar tolerance and percentage thresholds in §7.1.2 need calibration after running reference algorithms — universe-scale strategies may have more variance.~~ **Resolved**: Replaced binary trade-log matching (strict ±1 bar) with multi-layer behavioral evaluation (§7.1). Trade similarity now uses relaxed 2-bar tolerance and contributes only 0.10 weight. Primary evaluation uses deterministic Python signals (0.40 weight) and position overlap (0.30 weight), which are robust to LEAN engine quirks. I01 self-test validates composite = 0.897.
6. **Symbol selection criteria**: Exact methodology for ranking symbols by volume — use 2024 average, or 2021-2024 average? How to handle symbols that were delisted/relisted?
7. **LEAN 4h resolution**: LEAN may not have native 4h resolution support. May need to subscribe at 1h and consolidate to 4h via `TradeBarConsolidator`. This affects Tier 2 data format — store as 1h and let LEAN consolidate, or pre-aggregate to 4h?
8. **Runtime performance**: A 100-symbol × 21-sweep I06 backtest = 2,100 LEAN runs. Need to estimate total runtime and consider whether the sweep should be parallelized (multiple LEAN instances) or sequential.
9. **Tier 3 data usage**: Currently only I04 references Tier 3 (5m/1m) data. Should we add a task that specifically uses minute-level data, or is it sufficient as optional stress-test data?
10. **S/B-series migration**: Should S/B-series data also move from `bench/data/frozen/` (git) to HuggingFace? This would make the repo fully data-free, but it's a smaller win since S/B data is only ~5MB.
11. **Algorithm Framework API stability**: LEAN's Algorithm Framework API (AlphaModel, PortfolioConstructionModel, etc.) may have breaking changes between LEAN versions. Need to verify I07–I10 reference implementations against the pinned LEAN version.
12. **Framework execution timing**: The Algorithm Framework processes insights and generates orders through a pipeline, which may produce slightly different trade timing compared to classic `OnData()` direct execution. Need to calibrate trade-log comparison tolerance for I07–I10 (currently set to ±2 bars vs ±1 bar for classic).
13. **LEAN optimizer availability in Docker**: LEAN's optimization engine may require additional configuration or a separate entry point beyond `run_backtest`. Need to verify that the Docker sandbox supports optimization runs for I10, and whether the wrapper script needs extension.
14. **Multi-run eval architecture**: I08 (2 portfolio models), I09 (3 risk configurations), and I10 (~180 grid search) require multiple backtest runs per task. The eval scripts need to handle multi-run output directories. Consider a naming convention like `/workspace/results/run_1/`, `/workspace/results/run_2/`, etc.
15. **Custom risk model testability**: I09's custom `MaxGroupExposureRiskManagementModel` requires group/tier assignments from `universe.json`. Need to verify that the framework risk model can access algorithm state (universe metadata) during `ManageRisk()`.
16. **Optuna in Docker**: I10's optional Bayesian optimization requires Optuna (Python). The LEAN Docker image is C#-focused. Need to decide: (a) pre-install Optuna in the Docker image, (b) have the agent install it at runtime, or (c) provide a Python-C# bridge script that calls LEAN per Optuna trial.
