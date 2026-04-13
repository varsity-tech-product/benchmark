# LEAN Strategy Templates for 12-col Backtest System

Strategy templates demonstrating the correct patterns for writing LEAN C#
algorithms that run on the benchmark's 12-col custom data pipeline.

## How it works

The backtest harness **automatically intercepts** all `AddCrypto()` and
`AddCryptoFuture()` calls at compile time (via C# method hiding) and
routes them through the 12-col data reader.  You write standard LEAN
code and the harness handles the data plumbing.

## Data access patterns

| Pattern | Works? | Notes |
|---------|--------|-------|
| `AddCrypto("BTCUSDT", Resolution.Daily)` | Yes | String literal |
| `AddCrypto(sym, Resolution.Hour)` | Yes | Variable |
| `foreach (var s in list) AddCrypto(s, ...)` | Yes | Loop |
| `AddCryptoFuture(sym, ...)` | Yes | Redirects to AddCrypto |
| `data.Keys` / `foreach (var kvp in data)` | Yes | Iterate all bars |
| `data["BTCUSDT"]` | Yes | Generic accessor |
| `Securities["BTCUSDT"].Price` | Yes | Last close price |
| `Securities[sym].Close` | Yes | Same as Price |
| `SetHoldings(sym, weight)` | Yes | Portfolio operations |
| `Portfolio[sym].Invested` | Yes | Position check |
| `data.Bars["BTCUSDT"]` | **No** | Custom data is not in Bars |
| `data.Bars.ContainsKey(sym)` | **No** | Always false |

## Available resolutions

| Resolution | Data coverage |
|------------|--------------|
| `Resolution.Daily` | 635 symbols, 2022-2025 |
| `Resolution.Hour` | 635 symbols, 2022-2025 |
| `Resolution.Minute` | 5 symbols, 2022-2025 |

## Algorithm Framework caveats

When using LEAN's Algorithm Framework (SetAlpha / SetPortfolioConstruction):

- `OnSecuritiesChanged` does **not** fire for securities added in Initialize().
  Discover symbols lazily from `data.Keys` in `Update()` instead.
- LEAN's indicator auto-feed (`algo.EMA(sym, period)`) does **not** work.
  Create standalone indicators (`new ExponentialMovingAverage(period)`) and
  update them manually in `Update()` with `Securities[sym].Price`.
- See `04_algorithm_framework.cs` for the working pattern.

## Injected by the harness (do NOT add manually)

- `SetAccountCurrency("USDT")` -- injected at top of Initialize
- Fee model (maker 0.02%, taker 0.05%) -- injected at end of Initialize
- `Settings.TradingDaysPerYear = 365` -- injected at end of Initialize

## Templates

| File | Pattern | Complexity |
|------|---------|-----------|
| `01_single_symbol_sma.cs` | Single symbol, SMA crossover | Beginner |
| `02_multi_symbol_momentum.cs` | Multi-symbol loop, ROCP ranking | Intermediate |
| `03_multi_timeframe.cs` | Dual timeframe with Consolidate() | Intermediate |
| `04_algorithm_framework.cs` | AlphaModel + Framework components | Advanced |

## Running a template

Inside the Docker container:

```bash
run_backtest /workspace/Algorithm.cs --symbol BTCUSDT
```
