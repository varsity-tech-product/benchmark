# Implementation Section (I-Series) Design Plan

> Version: v2.1 | Status: In Progress — data pipeline tested, full-universe run pending | Section: Strategy Implementation on LEAN Engine

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

**The core evaluation question**: Does the agent correctly translate a strategy specification into working LEAN C# code that produces trades matching the expected ground-truth trade log? A weak agent writes code that compiles but produces wrong signals, misses edge cases, or misuses the LEAN API. A strong agent produces an algorithm whose trade log closely matches the reference implementation.

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
S02 (trend signal on BTC daily)          →  I02 (implement trend-following on LEAN)
S03 (mean-reversion signal on BTC daily) →  I03 (implement mean-reversion on LEAN)
S04 (volume/microstructure, multi-TF)    →  I04 (implement multi-timeframe strategy on LEAN)
S05 (cross-asset BTC/ETH signal)         →  I05 (implement cross-asset strategy on LEAN)
S06 (composite multi-signal)             →  I06 (implement multi-signal + parameter sweep on LEAN)
```

This pairing is **conceptual, not enforced** — each I-task is independently executable. The strategy spec is embedded in the task description; the agent does not need to have completed the corresponding S-task.

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
I-series data:     100+ symbols × 5 timeframes × 5+ years  = ~100M+ rows  (production scale)
```

### 2.2 Data Source

Binance USDT-M Futures historical klines:

```
Base URL: https://data.binance.vision/data/futures/um/daily/klines/
Pattern:  {SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{DATE}.zip

Universe: ~1,500 symbol folders available (including USDC/BUSD duplicates)
          ~500-600 unique USDT-margined perpetual contracts
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

**~100 most liquid USDT-M perpetual futures**, selected by average daily trading volume. Daily data for the full backtest period (inception to 2024-12-31).

Purpose: Universe-wide daily strategies (I02 trend-following, I03 mean-reversion, I06 multi-signal).

| Property | Value |
|----------|-------|
| Symbols | ~100 (top by volume: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT, AVAXUSDT, DOTUSDT, MATICUSDT, UNIUSDT, 1000SHIBUSDT, 1000PEPEUSDT, LTCUSDT, ATOMUSDT, NEARUSDT, ARBUSDT, OPUSDT, ...) |
| Timeframes | 1d |
| Period | Each symbol from listing date → 2024-12-31 |
| Rows (approx) | ~100 × ~1,500 avg days = **~150K rows** |
| Size (approx) | **~15 MB** compressed |

The exact symbol list will be finalized by ranking all USDT-M perpetuals by 2024 average daily quote volume and taking the top 100. Delisted or settled pairs are excluded.

#### Tier 2: Core Liquid — Hourly + 4-Hourly

**Top ~20 most liquid pairs**, at 1h and 4h resolution.

Purpose: Multi-asset swing strategies (I05 cross-asset), multi-timeframe strategies (I04).

| Property | Value |
|----------|-------|
| Symbols | ~20 (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT, AVAXUSDT, DOTUSDT, MATICUSDT, UNIUSDT, LTCUSDT, ATOMUSDT, NEARUSDT, ARBUSDT, OPUSDT, AAVEUSDT, MKRUSDT, APTUSDT) |
| Timeframes | 1h, 4h |
| Period | 2022-01-01 → 2024-12-31 (3 years) |
| Rows (approx) | 20 × 26,280 (1h) + 20 × 6,570 (4h) = **~657K rows** |
| Size (approx) | **~80 MB** compressed |

#### Tier 3: Majors — 5-Minute + 1-Minute

**Top ~5 most liquid pairs**, at 5m and 1m resolution.

Purpose: High-frequency / microstructure strategies, execution quality analysis, consolidator stress testing.

| Property | Value |
|----------|-------|
| Symbols | 5 (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT) |
| Timeframes | 5m, 1m |
| Period | 2024-01-01 → 2024-12-31 (1 year for manageability) |
| Rows (approx) | 5 × 105,120 (5m) + 5 × 525,600 (1m) = **~3.15M rows** |
| Size (approx) | **~400 MB** compressed |

#### Funding Rate Data

Funding rates for the top 20 symbols (Tier 2), for carry signal construction in I06.

| Property | Value |
|----------|-------|
| Symbols | ~20 (same as Tier 2) |
| Interval | 8h (3 per day) |
| Period | Listing date → 2024-12-31 |
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
huggingface.co/datasets/{org}/quant-tutor-bench-data/
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
│  {org}/quant-tutor-bench-data        │
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

HF_REPO_ID = "{org}/quant-tutor-bench-data"
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
  "freeze_date": "2024-12-31",
  "hf_repo": "{org}/quant-tutor-bench-data",
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
      "period": "2024-01-01 to 2024-12-31",
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

### 3.3 Running a Backtest

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
Period:   Each symbol from its listing date → 2024-12-31

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
Period:   Each symbol from its listing date → 2024-12-31

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
Period:   2022-01-01 to 2024-12-31

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
Period:   2022-01-01 to 2024-12-31

Phase 1 — Pair Selection (run once at start, or quarterly):
- For all symbol pairs (C(20,2) = 190 combinations):
  - Compute 60-day rolling correlation of log returns
  - Compute price ratio z-score (30-day rolling mean + stddev)
  - Select top-10 pairs by average absolute correlation > 0.7

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
- Strategy spec: embedded in task description
- Reference doc: `lean_algorithm_guide.md`

**Description**: Guide a student to implement a pairs trading scanner on LEAN that (1) screens all pair combinations within a 20-symbol universe for cointegration/correlation, (2) selects the top pairs, and (3) trades spread mean-reversion on the selected pairs with proper multi-leg position management. This is how institutional stat-arb strategies actually work — not hand-picking one pair, but systematically scanning a universe.

**Expected outcome**: Student produces a LEAN C# algorithm that (1) subscribes to ~20 symbols, (2) computes pairwise correlations across all 190 combinations and selects top-10 pairs, (3) tracks per-pair ratio z-scores, (4) enters/exits spread positions atomically per pair, (5) manages portfolio exposure across up to 10 active pairs, and (6) produces per-pair trade logs and a portfolio-level summary. Trade logs for 3 reference pairs should match the ground-truth.

**Required capabilities**:
1. Subscribe to ~20 symbols and compute pairwise statistics (correlation matrix)
2. Implement pair selection logic (rank by correlation, select top-N)
3. Compute per-pair rolling ratio z-score
4. Manage multiple simultaneous spread positions (up to 10 pairs × 2 legs = 20 positions)
5. Implement atomic spread entry/exit with per-pair stop-loss
6. Produce per-pair and portfolio-level results from a universe pairs scan

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
- **Pair selection quality**: Check that selected pairs have correlation > 0.7. Compare selected pairs against reference.
- **Trade log comparison**: For 3 reference pairs, match spread entries/exits. Both legs must enter/exit on the same bar.
- **Z-score direction**: Long-spread when z < -2, short-spread when z > +2.
- **Exposure management**: Total portfolio exposure never exceeds ~100%.
- **Multi-pair coverage**: Algorithm trades ≥ 5 pairs simultaneously.

**Ground-truth preparation**: Run reference algorithm → export `I05_reference_trades.json` with selected pairs list + trade logs for 3 reference pairs + portfolio summary.

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
Period:   Each symbol from its listing date → 2024-12-31

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

**Ground-truth preparation**: Run reference algorithm at equal weights → export `I06_reference_trades.json` (10 reference symbols). Run full sweep → export `I06_reference_sweep_results.json` with all 21 combinations.

---

## 5. Difficulty & Capability Progression

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

**Concept progression**:
- I01: Hello-world — single symbol, single indicator on LEAN
- I02: Scale to universe — per-symbol indicators and portfolio allocation across ~100 symbols
- I03: Complex per-symbol logic at scale — state machines, stop-loss, risk budgets × 100 symbols
- I04: Multi-resolution data at scale — consolidators and dual-TF indicators × 20 symbols
- I05: Cross-asset combinatorial analysis — pairwise screening, multi-leg hedged positions
- I06: Everything combined — multi-signal, universe-wide, data asymmetry, parameter optimization

**Two progression dimensions**:
1. **Strategy complexity**: simple crossover → asymmetric rules + stop-loss → multi-TF → cross-asset → composite signals
2. **Data scale**: 1 symbol → 100 symbols (daily) → 20 symbols (hourly) → 190 pairs → 100 symbols × 21 sweeps

Each task is independently executable but builds on concepts from earlier tasks.

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

### 6.2 Existing Docs (Reusable)

- `moving_averages.md` — MA/EMA concepts. Used by I02, I04, I06.
- `risk_metrics.md` — Performance metrics (Sharpe, drawdown). Used by I06.
- `statistical_tests.md` — Cointegration, z-score. Used by I05.

### 6.3 New Doc: `crypto_futures_basics.md` (Shared with B-Series)

Already planned in B-series (see B-series plan §6.3). I-series reuses this doc for I05, I06 context on funding rates and futures mechanics.

---

## 7. Evaluation Architecture

### 7.1 Trade Log Comparison (Primary Eval Mechanism)

The defining feature of I-series evaluation. Unlike S-series (qualitative signal assessment) or B-series (architectural quality), I-series has a **deterministic ground truth**: the reference trade log.

#### 7.1.1 Reference Trade Log Format

Each reference trade log (`I0X_reference_trades.json`) contains:

```json
{
  "task_id": "I02_trend_following",
  "algorithm_version": "1.0",
  "data_hash": "sha256:abc123...",
  "trades": [
    {
      "trade_id": 1,
      "symbol": "BTCUSDT",
      "direction": "Long",
      "entry_time": "2021-02-15 00:00:00",
      "entry_price": 48200.50,
      "exit_time": "2021-03-22 00:00:00",
      "exit_price": 54100.30,
      "quantity": 2.07,
      "gross_pnl": 12216.76,
      "net_pnl": 12100.12
    },
    ...
  ],
  "summary": {
    "total_trades": 42,
    "long_trades": 42,
    "short_trades": 0,
    "total_return_pct": 18.5,
    "sharpe_ratio": 0.72,
    "max_drawdown_pct": -25.3
  }
}
```

#### 7.1.2 Comparison Metrics

| Metric | Method | Tolerance | Weight |
|--------|--------|-----------|--------|
| **Trade count** | `abs(agent_count - ref_count) / ref_count` | ≤ 10% → pass | 0.25 |
| **Entry timing** | For each reference trade, find matching agent trade within ±1 bar by entry_time | ≥ 80% matched → pass | 0.25 |
| **Direction** | All matched trades have same direction (Long/Short) | 100% match → pass | 0.15 |
| **Exit timing** | For matched trades, exit_time within ±1 bar | ≥ 70% matched → pass | 0.15 |
| **PnL alignment** | Correlation between reference and agent trade-level PnL | r > 0.85 → pass | 0.10 |
| **Final return** | `abs(agent_return - ref_return) / abs(ref_return)` | ≤ 20% → pass | 0.10 |

#### 7.1.3 Trade Matching Algorithm

```
For each reference trade T_ref:
  1. Find agent trades within ±1 bar of T_ref.entry_time
  2. Among candidates, pick the one with same direction
  3. If found, mark as matched. Compare exit_time and PnL.
  4. If not found, mark as unmatched (reference trade missed by agent)

Unmatched agent trades (extra trades not in reference) are penalized
proportionally: extra_trade_penalty = extra_count / ref_count * 0.1
```

### 7.2 Shared Eval Helper: `_implementation_check.py`

New eval helper module for I-series:

```python
# bench/evaluation/test_scripts/_implementation_check.py

Key functions:
- load_reference_trades(task_id) → list[dict]
    Load reference trade log from bench/data/reference/I0X_reference_trades.json

- load_agent_trades(workspace_path) → list[dict]
    Parse agent's trade log from /workspace/results/trades.json

- match_trades(ref_trades, agent_trades, time_tolerance_bars=1) → MatchResult
    Run the trade matching algorithm (§7.1.3)

- compute_trade_log_score(match_result) → float
    Apply the weighted metrics from §7.1.2

- check_csharp_patterns(workspace_path, patterns: list[str]) → dict[str, bool]
    Scan .cs files for expected code patterns (e.g., "AddCryptoFuture", "SMA(")

- collect_lean_results(workspace_path) → dict
    Parse LEAN output from /workspace/results/summary.json
```

### 7.3 Per-Task Eval Script Structure

Each I-series eval script follows this pattern:

```python
def evaluate(workspace_path, tool_logs=None, conversation=None, *, data_files=None):
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "trade_count_match": False,
        "entry_timing_match": False,
        "direction_match": False,
        "exit_timing_match": False,
        "pnl_alignment": False,
        "return_proximity": False,
        "code_patterns_present": False,
        "score": 0.0,
    }

    # 1. Check that backtest ran to completion
    lean_results = collect_lean_results(workspace_path)
    results["backtest_completed"] = lean_results is not None

    # 2. Load and compare trade logs
    ref_trades = load_reference_trades("I0X_task_name")
    agent_trades = load_agent_trades(workspace_path)
    results["trade_log_produced"] = len(agent_trades) > 0

    match = match_trades(ref_trades, agent_trades, time_tolerance_bars=1)
    results["trade_count_match"] = match.count_within_tolerance(0.10)
    results["entry_timing_match"] = match.entry_match_rate >= 0.80
    results["direction_match"] = match.direction_match_rate == 1.0
    results["exit_timing_match"] = match.exit_match_rate >= 0.70
    results["pnl_alignment"] = match.pnl_correlation > 0.85
    results["return_proximity"] = match.return_within_tolerance(0.20)

    # 3. Check C# code patterns (task-specific)
    patterns = check_csharp_patterns(workspace_path, [
        "AddCryptoFuture", "SetAccountCurrency", "SMA(", "SetWarmUp"
    ])
    results["code_patterns_present"] = sum(patterns.values()) >= 3

    # 4. Compute score
    _checklist = [
        {"item": "backtest_completed",    "weight": 0.05, "passed": results["backtest_completed"]},
        {"item": "trade_log_produced",    "weight": 0.05, "passed": results["trade_log_produced"]},
        {"item": "trade_count_match",     "weight": 0.20, "passed": results["trade_count_match"]},
        {"item": "entry_timing_match",    "weight": 0.20, "passed": results["entry_timing_match"]},
        {"item": "direction_match",       "weight": 0.15, "passed": results["direction_match"]},
        {"item": "exit_timing_match",     "weight": 0.15, "passed": results["exit_timing_match"]},
        {"item": "pnl_alignment",         "weight": 0.10, "passed": results["pnl_alignment"]},
        {"item": "return_proximity",      "weight": 0.05, "passed": results["return_proximity"]},
        {"item": "code_patterns_present", "weight": 0.05, "passed": results["code_patterns_present"]},
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # 5. Gates
    if not results["backtest_completed"]:
        score = min(score, 0.10)  # Can't score well if backtest didn't run
    if not results["trade_log_produced"]:
        score = min(score, 0.15)

    # 6. Data source verification
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results
```

### 7.4 Result Judge Category Rubric

Add an `implementation` entry to `CATEGORY_RESULT_RUBRICS`:

```
Implementation tasks — evaluation focus:
1. Correctness: Does the LEAN algorithm implement the strategy specification accurately?
   Trades should match the expected ground-truth in timing and direction.
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
# 3. Runs LEAN engine
# 4. Extracts results to /workspace/results/
#   - trades.json    (closed trades)
#   - summary.json   (performance metrics)
#   - orders.json    (all order events)
#   - log.txt        (algorithm log output)

ALGO_FILE=$1
RESULTS_DIR=/workspace/results

# ... implementation details ...
```

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

| Task | Title | Difficulty | Data Tier | Symbols | Timeframes | Strategy | Key Challenge | Pairs With |
|------|-------|-----------|-----------|---------|------------|----------|---------------|------------|
| I01 | SMA Trend Filter | easy | Tier 1 | 1 (BTCUSDT) | 1d | Price vs SMA(20) | LEAN hello-world (single symbol) | — |
| I02 | Universe Trend | medium | Tier 1 | ~100 | 1d | Dual MA crossover | Universe-scale per-symbol indicators | S02 |
| I03 | Universe Reversion | medium | Tier 1 | ~100 | 1d | RSI + stop-loss | Per-symbol state machines at scale | S03 |
| I04 | Multi-TF Multi-Asset | hard | Tier 2 | ~20 | 1h → 4h | 4h trend + 1h entry | Per-symbol consolidators | S04 |
| I05 | Universe Pairs Scan | hard | Tier 2 | ~20 (190 pairs) | 1d | Pair z-score | Combinatorial pair selection + multi-leg | S05 |
| I06 | Universe Multi-Signal Sweep | hard | Tier 1 + Funding | ~100 + 20 funding | 1d + 8h | Composite 3-signal | Cross-sectional sizing + 21 param configs | S06 |

**Total new instances**: 5 tasks × 3 personas = **15 new evaluation instances**
**I-series total**: 6 tasks × 3 personas = **18 evaluation instances**

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
├── I02_trend_following.cs
├── I03_mean_reversion.cs
├── I04_multi_timeframe.cs
├── I05_cross_asset.cs
└── I06_multi_signal.cs
```

### 11.2 Reference Trade Log Generation

Run each reference algorithm on the frozen data and export trade logs:

```bash
# Generate reference trade logs
python bench/reference/generate_lean_reference.py --task I02
python bench/reference/generate_lean_reference.py --task I03
# ...
```

Output:
```
bench/data/reference/
├── I02_reference_trades.json
├── I03_reference_trades.json
├── I04_reference_trades.json
├── I05_reference_trades.json
├── I06_reference_trades.json
└── I06_reference_sweep_results.json   # Parameter sweep results for I06
```

### 11.3 Reference Validation

Each reference trade log must be validated:
- Trade count is reasonable (not 0, not 10000+)
- Entry/exit prices are within the data's price range
- PnL per trade is consistent with price movement
- Total return is plausible for the strategy type
- No trades occur during warm-up period

---

## 12. Implementation Checklist

### Phase 0: Infrastructure (Prerequisites)

**Data pipeline (run once by maintainers):**
- [x] Finalize `universe.json` — rank all USDT-M perpetuals by 2024 avg daily volume, select top 100 for Tier 1, top 20 for Tier 2, top 5 for Tier 3
- [x] Write `bench/scripts/download_binance_full_universe.py` — bulk download with parallelism, resume, checksum verification
- [ ] Download Tier 1: ~100 symbols × 1d (from listing date → 2024-12-31) *(tested with 3-symbol subset)*
- [ ] Download Tier 2: ~20 symbols × 1h + 4h (2022-01-01 → 2024-12-31) *(tested with 2-symbol subset)*
- [ ] Download Tier 3: ~5 symbols × 5m + 1m (2024-01-01 → 2024-12-31) *(tested with 1-symbol subset)*
- [ ] Download funding rates: ~20 symbols (from listing date → 2024-12-31) *(tested with 2-symbol subset)*
- [x] Write `bench/scripts/convert_binance_to_lean.py` — Binance CSV → LEAN format converter for all tiers/timeframes
- [ ] Convert all tiers to LEAN format and validate *(tested with subset; full run pending)*
- [x] Write `bench/scripts/generate_flat_universe.py` — structured universe.json → flat JSON array for C# algorithms
- [x] Write `bench/scripts/prepare_i_series_data.py` — single-command orchestrator for full pipeline (download → convert → flat universe → upload → verify)

**HuggingFace dataset (decoupled storage):**
- [ ] Create HF dataset repo `{org}/quant-tutor-bench-data`
- [ ] Configure Git LFS for large files (`.zip`, `.csv` > 10MB)
- [ ] Upload raw S/B-series data to `raw/sb-series/`
- [ ] Upload raw I-series data to `raw/i-series/` (organized by tier)
- [ ] Upload LEAN-format data to `lean/`
- [ ] Upload `universe.json` to `raw/i-series/`
- [ ] Tag initial dataset version (commit hash for reproducibility)
- [x] Write `bench/scripts/upload_lean_to_hf.py` — upload LEAN data + flat universe.json via `upload_folder()`

**Data manager (runtime download + cache):**
- [x] Write `bench/scripts/data_manager.py` (see §2.7)
- [ ] Add `huggingface_hub` to `bench/requirements.txt`
- [x] Add `bench/data/hf_cache/` to `.gitignore`
- [ ] Test: first run downloads data; second run uses cache
- [ ] Test: `revision` parameter pins to specific HF commit
- [x] Fix: copy universe.json into lean directory so LEAN finds it at `Globals.DataFolder/universe.json`

**Orchestrator integration (mount data into Docker):**
- [ ] Add `lean_data_dir` parameter to `container_manager.py` (see §2.8)
- [ ] Add `_ensure_lean_data()` to `orchestrator.py` (see §2.8)
- [ ] Add `lean_data_dir` mount for I-series tasks (`-v lean_data:/lean/Data:ro`)
- [ ] Test: I-series task container sees data at `/lean/Data/`
- [ ] Test: S/B-series tasks are completely unaffected

**Docker / LEAN environment (engine only, no data):**
- [ ] Build Docker image `quant-tutor-env:v2.0-lean` with LEAN engine + .NET SDK (NO data, ~3GB)
- [ ] Write and test `run_backtest` wrapper script
- [ ] Pre-configure LEAN `config.json` for Binance futures
- [ ] Test that LEAN loads and processes mounted Tier 1 data correctly (100 symbols)
- [ ] Test that LEAN handles Tier 2 hourly + 4h consolidation correctly
- [ ] Benchmark: measure runtime for a 100-symbol daily backtest on LEAN (target < 5 min)
- [ ] Push image to Docker Hub / GHCR

### Phase 1: Reference Documentation

- [ ] Write `lean_algorithm_guide.md` reference doc (see §6.1)
- [ ] Write `crypto_futures_basics.md` reference doc (shared with B-series, see §6.3)
- [ ] Verify existing docs are compatible (`moving_averages.md`, `risk_metrics.md`, `statistical_tests.md`)

### Phase 2: Reference Algorithms & Ground-Truth

- [ ] Write reference C# algorithm: `I02_trend_following.cs`
- [ ] Write reference C# algorithm: `I03_mean_reversion.cs`
- [ ] Write reference C# algorithm: `I04_multi_timeframe.cs`
- [ ] Write reference C# algorithm: `I05_cross_asset.cs`
- [ ] Write reference C# algorithm: `I06_multi_signal.cs`
- [ ] Run all reference algorithms → export trade logs
- [ ] Run I06 parameter sweep → export sweep results
- [ ] Validate all reference trade logs (see §11.3)

### Phase 3: Task JSONs (bench/tasks/layer2/implementation/)

- [ ] Redesign I01_implement_sma.json for LEAN/C#
- [ ] I02_trend_following.json
- [ ] I03_mean_reversion.json
- [ ] I04_multi_timeframe.json
- [ ] I05_cross_asset.json
- [ ] I06_multi_signal_sweep.json

### Phase 4: Eval Scripts (bench/evaluation/test_scripts/)

- [ ] Write `_implementation_check.py` shared helper module
- [ ] Redesign I01_implement_sma.py for LEAN/C#
- [ ] I02_trend_following.py
- [ ] I03_mean_reversion.py
- [ ] I04_multi_timeframe.py
- [ ] I05_cross_asset.py
- [ ] I06_multi_signal_sweep.py

### Phase 5: Scoring Integration

- [ ] Add `implementation` category to `CATEGORY_PROCESS_CRITERIA`
- [ ] Add `implementation` category to `CATEGORY_RESULT_RUBRICS`
- [ ] Verify that code_eval dimension handles C# files (not just Python)
- [ ] Verify that tool_usage scoring works with LEAN-specific tools

### Phase 6: Reference Oracle

- [ ] Generate reference executions for all 15 instances (5 tasks × 3 personas)
- [ ] Validate reference `key_results` and `step_count` baselines
- [ ] End-to-end integration test: run full benchmark on I-series tasks

---

## 13. Cross-Reference: Full Pipeline View (D + S + B + I)

```
                  D-SERIES          S-SERIES             B-SERIES             I-SERIES
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
5. **Trade log tolerance tuning**: The ±1 bar tolerance and percentage thresholds in §7.1.2 need calibration after running reference algorithms — universe-scale strategies may have more variance.
6. **Symbol selection criteria**: Exact methodology for ranking symbols by volume — use 2024 average, or 2021-2024 average? How to handle symbols that were delisted/relisted?
7. **LEAN 4h resolution**: LEAN may not have native 4h resolution support. May need to subscribe at 1h and consolidate to 4h via `TradeBarConsolidator`. This affects Tier 2 data format — store as 1h and let LEAN consolidate, or pre-aggregate to 4h?
8. **Runtime performance**: A 100-symbol × 21-sweep I06 backtest = 2,100 LEAN runs. Need to estimate total runtime and consider whether the sweep should be parallelized (multiple LEAN instances) or sequential.
9. **Tier 3 data usage**: Currently only I04 references Tier 3 (5m/1m) data. Should we add a task that specifically uses minute-level data, or is it sufficient as optional stress-test data?
10. **S/B-series migration**: Should S/B-series data also move from `bench/data/frozen/` (git) to HuggingFace? This would make the repo fully data-free, but it's a smaller win since S/B data is only ~5MB.
