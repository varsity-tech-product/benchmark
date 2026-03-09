# LEAN C# Algorithm Development Guide

A comprehensive reference for developing crypto-futures backtesting algorithms on Binance using the QuantConnect LEAN engine. This guide covers the full lifecycle from data subscription through execution and analysis.

---

## 1. Algorithm Structure

Every LEAN algorithm inherits from `QCAlgorithm`. The engine calls two primary methods:

- **`Initialize()`** -- called once at startup. Set dates, cash, subscriptions, indicators, and parameters here.
- **`OnData(Slice data)`** -- called on every new data point (bar or tick). This is where trading logic lives.

LEAN compiles C# source files at runtime inside the Docker container. The algorithm class must be `public` and reside in the `QuantConnect.Algorithm.CSharp` namespace.

### Minimal skeleton

```csharp
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;

namespace QuantConnect.Algorithm.CSharp
{
    public class MyAlgorithm : QCAlgorithm
    {
        public override void Initialize()
        {
            SetStartDate(2023, 1, 1);
            SetEndDate(2024, 1, 1);
            SetAccountCurrency("USDT");
            SetCash("USDT", 100000);

            AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
        }

        public override void OnData(Slice data)
        {
            if (!data.ContainsKey("BTCUSDT")) return;

            var price = data["BTCUSDT"].Close;
            Log($"BTC close: {price}");
        }
    }
}
```

### Lifecycle overview

1. Engine loads and compiles the `.cs` file.
2. `Initialize()` runs -- sets universe, indicators, warm-up period.
3. If `SetWarmUp()` was called, the engine feeds historical data silently (indicators update but orders are blocked).
4. `OnData()` fires for each slice in the date range.
5. At `SetEndDate`, the engine liquidates remaining positions and writes results.

---

## 2. Data Subscription

### AddCryptoFuture

Use `AddCryptoFuture` to subscribe to Binance perpetual futures:

```csharp
var btc = AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
var eth = AddCryptoFuture("ETHUSDT", Resolution.Hour, Market.Binance);
```

The returned `CryptoFuture` object contains the `Symbol` property used throughout the API.

### SetAccountCurrency

Binance futures are margined in USDT. Always call this before `SetCash`:

```csharp
SetAccountCurrency("USDT");
SetCash("USDT", 100000);
```

### Resolution options

| Resolution           | Enum                    | Typical use case                    |
|----------------------|-------------------------|-------------------------------------|
| Tick                 | `Resolution.Tick`       | Microstructure research             |
| Second               | `Resolution.Second`     | HFT prototyping                     |
| Minute               | `Resolution.Minute`     | Intraday strategies                 |
| Hour                 | `Resolution.Hour`       | Swing trading, multi-asset rotation |
| Daily                | `Resolution.Daily`      | Trend following, cross-sectional    |

### Multiple subscriptions

You can subscribe to as many symbols as needed. Each subscription creates its own data stream:

```csharp
var symbols = new[] { "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT" };
foreach (var sym in symbols)
{
    AddCryptoFuture(sym, Resolution.Daily, Market.Binance);
}
```

---

## 3. Built-In Indicators

LEAN provides a rich library of technical indicators. All indicators auto-update when registered to a symbol.

### Creating indicators

```csharp
private SimpleMovingAverage _smaFast;
private SimpleMovingAverage _smaSlow;
private ExponentialMovingAverage _ema;
private RelativeStrengthIndex _rsi;
private MovingAverageConvergenceDivergence _macd;
private BollingerBands _bb;

public override void Initialize()
{
    // ... subscriptions ...

    _smaFast = SMA("BTCUSDT", 20, Resolution.Daily);
    _smaSlow = SMA("BTCUSDT", 50, Resolution.Daily);
    _ema     = EMA("BTCUSDT", 21, Resolution.Daily);
    _rsi     = RSI("BTCUSDT", 14, MovingAverageType.Wilders, Resolution.Daily);
    _macd    = MACD("BTCUSDT", 12, 26, 9, MovingAverageType.Exponential, Resolution.Daily);
    _bb      = BB("BTCUSDT", 20, 2, MovingAverageType.Simple, Resolution.Daily);
}
```

### Accessing indicator values

```csharp
public override void OnData(Slice data)
{
    if (!_smaFast.IsReady || !_smaSlow.IsReady) return;

    var fastVal  = _smaFast.Current.Value;
    var slowVal  = _smaSlow.Current.Value;
    var rsiVal   = _rsi.Current.Value;
    var macdVal  = _macd.Current.Value;
    var macdSig  = _macd.Signal.Current.Value;
    var macdHist = _macd.Histogram.Current.Value;
    var bbUpper  = _bb.UpperBand.Current.Value;
    var bbLower  = _bb.LowerBand.Current.Value;
    var bbMiddle = _bb.MiddleBand.Current.Value;
}
```

### Warm-up

Indicators need historical data before they produce valid readings. Use `SetWarmUp` to feed them before live trading begins:

```csharp
// Warm up 50 bars so a 50-period SMA is ready on bar 1
SetWarmUp(50, Resolution.Daily);
```

During warm-up, `IsWarmingUp` returns `true` and orders are rejected. Always guard:

```csharp
public override void OnData(Slice data)
{
    if (IsWarmingUp) return;
    // safe to trade
}
```

---

## 4. Order Management

### SetHoldings (target-weight orders)

The simplest approach -- specify a portfolio fraction:

```csharp
// Allocate 100% of portfolio to long BTC
SetHoldings("BTCUSDT", 1.0m);

// Go 50% long BTC, 50% long ETH
SetHoldings("BTCUSDT", 0.5m);
SetHoldings("ETHUSDT", 0.5m);

// Go short 100%
SetHoldings("BTCUSDT", -1.0m);

// Flatten position (equivalent to Liquidate)
SetHoldings("BTCUSDT", 0m);
```

### MarketOrder

Place a market order for a specific quantity:

```csharp
// Buy 0.5 BTC contracts
MarketOrder("BTCUSDT", 0.5m);

// Sell (short) 1.0 ETH contracts
MarketOrder("ETHUSDT", -1.0m);
```

### LimitOrder

Place a limit order at a specified price:

```csharp
var price = Securities["BTCUSDT"].Price;
// Buy limit 2% below current price
LimitOrder("BTCUSDT", 0.5m, price * 0.98m);
```

### Liquidate

Close all positions in a symbol or the entire portfolio:

```csharp
// Liquidate one symbol
Liquidate("BTCUSDT");

// Liquidate everything
Liquidate();
```

---

## 5. Portfolio State

Check current position status through the `Portfolio` dictionary:

```csharp
var holding = Portfolio["BTCUSDT"];

// Position direction
bool isLong  = holding.IsLong;    // quantity > 0
bool isShort = holding.IsShort;   // quantity < 0
bool invested = holding.Invested; // quantity != 0

// Position details
decimal qty       = holding.Quantity;         // signed quantity
decimal avgPrice  = holding.AveragePrice;     // entry price
decimal unrealPnl = holding.UnrealizedProfit;  // current P&L
decimal realPnl   = holding.LastTradeProfit;   // last closed P&L

// Current market price
decimal curPrice = Securities["BTCUSDT"].Price;
```

### Portfolio-level state

```csharp
decimal totalValue  = Portfolio.TotalPortfolioValue;
decimal cash        = Portfolio.Cash;
decimal margin      = Portfolio.TotalMarginUsed;
bool    anyInvested = Portfolio.Invested; // true if any position is open
```

---

## 6. Multi-Timeframe Consolidators

When your subscription is at a fine resolution (e.g., minute) but your strategy needs coarser bars (e.g., hourly or 4-hourly), use consolidators.

### Using Consolidate()

The simplest API -- pass a `TimeSpan` or `CalendarType`:

```csharp
public override void Initialize()
{
    AddCryptoFuture("BTCUSDT", Resolution.Minute, Market.Binance);

    // Consolidate minute bars into 4-hour bars
    Consolidate("BTCUSDT", TimeSpan.FromHours(4), OnFourHourBar);

    // Consolidate into daily bars
    Consolidate("BTCUSDT", Resolution.Daily, OnDailyBar);
}

private void OnFourHourBar(TradeBar bar)
{
    Log($"4H bar: O={bar.Open} H={bar.High} L={bar.Low} C={bar.Close}");
}

private void OnDailyBar(TradeBar bar)
{
    Log($"Daily bar: {bar.Time} Close={bar.Close}");
}
```

### TradeBarConsolidator (manual approach)

For more control, create a consolidator explicitly:

```csharp
private TradeBarConsolidator _consolidator4h;
private SimpleMovingAverage _sma4h;

public override void Initialize()
{
    var btc = AddCryptoFuture("BTCUSDT", Resolution.Minute, Market.Binance);

    _consolidator4h = new TradeBarConsolidator(TimeSpan.FromHours(4));
    _consolidator4h.DataConsolidated += On4hBar;
    SubscriptionManager.AddConsolidator(btc.Symbol, _consolidator4h);

    // Register an indicator on the consolidated timeframe
    _sma4h = new SimpleMovingAverage(20);
    RegisterIndicator(btc.Symbol, _sma4h, _consolidator4h);

    SetWarmUp(TimeSpan.FromDays(5));
}

private void On4hBar(object sender, TradeBar bar)
{
    if (!_sma4h.IsReady) return;
    Log($"4H SMA(20) = {_sma4h.Current.Value}");
}
```

### Registering indicators on consolidated timeframes

Use `RegisterIndicator` to wire an indicator to a consolidator so it updates on the coarser timeframe:

```csharp
var rsi4h = new RelativeStrengthIndex(14, MovingAverageType.Wilders);
RegisterIndicator("BTCUSDT", rsi4h, _consolidator4h);
```

---

## 7. Multi-Asset Strategies

### Subscribing to multiple symbols

```csharp
private List<Symbol> _symbols = new List<Symbol>();

public override void Initialize()
{
    SetStartDate(2023, 1, 1);
    SetEndDate(2024, 1, 1);
    SetAccountCurrency("USDT");
    SetCash("USDT", 100000);

    var tickers = new[] {
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT"
    };

    foreach (var ticker in tickers)
    {
        var crypto = AddCryptoFuture(ticker, Resolution.Daily, Market.Binance);
        _symbols.Add(crypto.Symbol);
    }
}
```

### Iterating symbols in OnData

```csharp
public override void OnData(Slice data)
{
    if (IsWarmingUp) return;

    foreach (var symbol in _symbols)
    {
        if (!data.ContainsKey(symbol)) continue;

        var bar = data[symbol];
        // Apply per-asset logic here
    }
}
```

### Equal-weight allocation

```csharp
public override void OnData(Slice data)
{
    if (IsWarmingUp) return;

    var weight = 1.0m / _symbols.Count;
    foreach (var symbol in _symbols)
    {
        if (!data.ContainsKey(symbol)) continue;
        SetHoldings(symbol, weight);
    }
}
```

### Cross-sectional momentum example

```csharp
private Dictionary<Symbol, RateOfChange> _rocs = new Dictionary<Symbol, RateOfChange>();

public override void Initialize()
{
    // ... subscriptions ...
    foreach (var symbol in _symbols)
    {
        _rocs[symbol] = ROC(symbol, 20, Resolution.Daily);
    }
    SetWarmUp(25, Resolution.Daily);
}

public override void OnData(Slice data)
{
    if (IsWarmingUp) return;

    // Rank by momentum
    var ranked = _symbols
        .Where(s => data.ContainsKey(s) && _rocs[s].IsReady)
        .OrderByDescending(s => _rocs[s].Current.Value)
        .ToList();

    // Go long top 3, flat the rest
    var topN = ranked.Take(3).ToHashSet();
    foreach (var symbol in _symbols)
    {
        if (topN.Contains(symbol))
            SetHoldings(symbol, 1.0m / 3);
        else
            Liquidate(symbol);
    }
}
```

---

## 8. Events and Logging

### OnOrderEvent

Called whenever an order changes state (submitted, filled, cancelled, etc.):

```csharp
public override void OnOrderEvent(OrderEvent orderEvent)
{
    if (orderEvent.Status == OrderStatus.Filled)
    {
        Log($"FILLED: {orderEvent.Symbol} qty={orderEvent.FillQuantity} " +
            $"price={orderEvent.FillPrice} direction={orderEvent.Direction}");
    }
}
```

### Log and Debug

```csharp
// Log() writes to the backtest log file (always captured)
Log($"Portfolio value: {Portfolio.TotalPortfolioValue}");

// Debug() writes to the debug console (visible in live mode)
Debug($"Signal triggered for {symbol}");

// Error() logs an error-level message
Error("Unexpected state: no data for BTCUSDT");
```

### Schedule.On

Execute code at specific times, independent of data events:

```csharp
public override void Initialize()
{
    // ... subscriptions ...

    // Rebalance every Monday at midnight UTC
    Schedule.On(DateRules.Every(DayOfWeek.Monday),
                TimeRules.At(0, 0),
                Rebalance);

    // Run at market open every day
    Schedule.On(DateRules.EveryDay(),
                TimeRules.At(0, 0),
                DailyRoutine);
}

private void Rebalance()
{
    Log("Weekly rebalance triggered");
    // rebalancing logic
}

private void DailyRoutine()
{
    Log($"Daily check -- portfolio value: {Portfolio.TotalPortfolioValue}");
}
```

### OnEndOfAlgorithm

Called once when the backtest finishes:

```csharp
public override void OnEndOfAlgorithm()
{
    Log($"Final portfolio value: {Portfolio.TotalPortfolioValue}");
    Log($"Total trades: {TradeBuilder.ClosedTrades.Count}");
}
```

---

## 9. Parameter Optimization

LEAN supports named parameters that can be swept across values for optimization.

### Setting parameters

```csharp
public override void Initialize()
{
    var fastPeriod = GetParameter("fast-period", 10);
    var slowPeriod = GetParameter("slow-period", 50);
    var rsiThreshold = GetParameter("rsi-threshold", 30);

    _smaFast = SMA("BTCUSDT", fastPeriod, Resolution.Daily);
    _smaSlow = SMA("BTCUSDT", slowPeriod, Resolution.Daily);
}
```

### Passing parameters at runtime

Parameters are passed via the LEAN configuration or command line. In our Docker setup, they are injected through the config:

```json
{
  "parameters": {
    "fast-period": "10",
    "slow-period": "50",
    "rsi-threshold": "30"
  }
}
```

### Parameter sweep pattern

To run a parameter sweep, modify the config and re-run the container for each combination. The harness automates this by varying parameter values and collecting results from each run.

---

## 10. Running Backtests

### The run_backtest command

The benchmark harness launches LEAN inside Docker. The typical flow:

1. The harness writes the algorithm `.cs` file into the workspace.
2. It mounts the data directory and config into the container.
3. It runs the LEAN engine container.
4. Results are written to the results directory.

### Output files

After a backtest completes, the results directory contains:

| File                        | Contents                                        |
|-----------------------------|-------------------------------------------------|
| `results.json`              | Full backtest results (orders, equity curve, statistics) |
| `log.txt`                   | All `Log()` output from the algorithm           |
| `orders.json`               | Detailed order-by-order execution log            |

### Interpreting results

Key statistics in `results.json`:

```
TotalPerformance.TradeStatistics:
  - TotalNumberOfTrades
  - WinRate
  - AverageProfit
  - AverageLoss
  - ProfitLossRatio

Statistics:
  - Total Trades
  - Sharpe Ratio
  - Compounding Annual Return
  - Max Drawdown
  - Net Profit
  - Total Fees
```

### Reading results programmatically

```python
import json

with open("results/results.json") as f:
    results = json.load(f)

stats = results["Statistics"]
sharpe = float(stats["Sharpe Ratio"])
max_dd = float(stats["Max Drawdown"].replace("%", ""))
net_profit = float(stats["Net Profit"].replace("%", "").replace(",", ""))
```

---

## 11. Common Patterns

### Moving average crossover detection

```csharp
private SimpleMovingAverage _smaFast;
private SimpleMovingAverage _smaSlow;
private decimal _prevFast;
private decimal _prevSlow;

public override void OnData(Slice data)
{
    if (IsWarmingUp || !_smaFast.IsReady || !_smaSlow.IsReady) return;

    var fast = _smaFast.Current.Value;
    var slow = _smaSlow.Current.Value;

    // Bullish crossover: fast crosses above slow
    if (_prevFast <= _prevSlow && fast > slow)
    {
        SetHoldings("BTCUSDT", 1.0m);
        Log("Bullish crossover -- going long");
    }
    // Bearish crossover: fast crosses below slow
    else if (_prevFast >= _prevSlow && fast < slow)
    {
        Liquidate("BTCUSDT");
        Log("Bearish crossover -- closing position");
    }

    _prevFast = fast;
    _prevSlow = slow;
}
```

### Entry price tracking and stop-loss

```csharp
private decimal _entryPrice;
private decimal _stopLossPct = 0.05m; // 5% stop

public override void OnData(Slice data)
{
    if (IsWarmingUp) return;
    if (!data.ContainsKey("BTCUSDT")) return;

    var price = data["BTCUSDT"].Close;

    if (!Portfolio["BTCUSDT"].Invested)
    {
        // Entry logic
        if (ShouldEnterLong())
        {
            SetHoldings("BTCUSDT", 1.0m);
            _entryPrice = price;
        }
    }
    else if (Portfolio["BTCUSDT"].IsLong)
    {
        // Stop-loss check
        var drawdown = (price - _entryPrice) / _entryPrice;
        if (drawdown <= -_stopLossPct)
        {
            Liquidate("BTCUSDT");
            Log($"Stop-loss triggered at {price} (entry was {_entryPrice})");
        }
    }
}
```

### Position state machine

```csharp
private enum PositionState { Flat, Long, Short }
private PositionState _state = PositionState.Flat;

public override void OnData(Slice data)
{
    if (IsWarmingUp) return;
    if (!data.ContainsKey("BTCUSDT")) return;

    var signal = ComputeSignal(data);

    switch (_state)
    {
        case PositionState.Flat:
            if (signal > 0.5m)
            {
                SetHoldings("BTCUSDT", 1.0m);
                _state = PositionState.Long;
            }
            else if (signal < -0.5m)
            {
                SetHoldings("BTCUSDT", -1.0m);
                _state = PositionState.Short;
            }
            break;

        case PositionState.Long:
            if (signal < 0m)
            {
                Liquidate("BTCUSDT");
                _state = PositionState.Flat;
            }
            break;

        case PositionState.Short:
            if (signal > 0m)
            {
                Liquidate("BTCUSDT");
                _state = PositionState.Flat;
            }
            break;
    }
}
```

### Trailing stop

```csharp
private decimal _highWaterMark;
private decimal _trailingStopPct = 0.03m; // 3% trailing stop

public override void OnData(Slice data)
{
    if (IsWarmingUp) return;
    if (!data.ContainsKey("BTCUSDT")) return;

    var price = data["BTCUSDT"].Close;

    if (Portfolio["BTCUSDT"].IsLong)
    {
        // Update high water mark
        if (price > _highWaterMark)
            _highWaterMark = price;

        // Check trailing stop
        var dropFromPeak = (_highWaterMark - price) / _highWaterMark;
        if (dropFromPeak >= _trailingStopPct)
        {
            Liquidate("BTCUSDT");
            Log($"Trailing stop hit: peak={_highWaterMark}, exit={price}");
        }
    }
}
```

---

## 12. Common Pitfalls

### Forgetting SetWarmUp

**Problem:** Indicators produce NaN or zero values at the start of the backtest because they have no history.

**Fix:** Always call `SetWarmUp` with enough bars for your longest-period indicator:

```csharp
// If your slowest indicator is SMA(50), warm up at least 50 bars
SetWarmUp(50, Resolution.Daily);
```

### Not checking IsWarmingUp

**Problem:** Orders placed during warm-up are silently rejected, or indicators are read before they are ready, leading to incorrect signals.

**Fix:** Guard the top of `OnData`:

```csharp
public override void OnData(Slice data)
{
    if (IsWarmingUp) return;
    // ...
}
```

### Missing ContainsKey check

**Problem:** `KeyNotFoundException` crash when accessing `data["SYMBOL"]` for a symbol that has no data on that bar (weekends, holidays, pre-listing).

**Fix:** Always check before accessing:

```csharp
if (!data.ContainsKey("BTCUSDT")) return;
var bar = data["BTCUSDT"];
```

For multi-asset strategies, check each symbol individually:

```csharp
foreach (var symbol in _symbols)
{
    if (!data.ContainsKey(symbol)) continue;
    // process bar
}
```

### Resolution mismatches

**Problem:** Subscribing at `Resolution.Daily` but creating an indicator with `Resolution.Hour` -- the indicator never updates because no hourly data is coming in.

**Fix:** Match indicator resolution to subscription resolution, or use consolidators to go from finer to coarser:

```csharp
// WRONG: subscribed daily, indicator expects hourly
AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
var sma = SMA("BTCUSDT", 20, Resolution.Hour); // will NOT update

// RIGHT: match resolutions
AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
var sma = SMA("BTCUSDT", 20, Resolution.Daily); // updates correctly

// RIGHT: subscribe fine, consolidate up
AddCryptoFuture("BTCUSDT", Resolution.Minute, Market.Binance);
// then use consolidator for hourly/daily indicators
```

### Using string vs Symbol

**Problem:** Mixing up the string ticker (`"BTCUSDT"`) with the `Symbol` object. Some APIs accept both, others require the `Symbol`.

**Fix:** Store the `Symbol` from `AddCryptoFuture` and use it consistently:

```csharp
var btc = AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
Symbol btcSymbol = btc.Symbol;

// Both work for most APIs:
SetHoldings("BTCUSDT", 1.0m);
SetHoldings(btcSymbol, 1.0m);

// But Symbol is required for some operations:
RegisterIndicator(btcSymbol, myIndicator, myConsolidator);
```

### Accessing Securities before subscription

**Problem:** Trying to read `Securities["BTCUSDT"].Price` before calling `AddCryptoFuture` throws an exception.

**Fix:** Always subscribe in `Initialize()` before accessing in `OnData()`.

### Not accounting for fees

**Problem:** Strategies look profitable in backtest but fail in practice because trading fees eat the edge.

**Fix:** The `BinanceFuturesBrokerageModel` applies realistic fee schedules automatically. Be aware:
- Binance futures taker fee: ~0.04%
- Binance futures maker fee: ~0.02%
- Frequent rebalancing on low-alpha signals can be destroyed by fees

### Overfitting to the warm-up period

**Problem:** The algorithm's first few trades after warm-up use indicators that were computed on a limited history window.

**Fix:** Set warm-up to be at least 2x your longest indicator period. Check `_indicator.IsReady` even after warm-up completes, as a belt-and-suspenders guard.

### Decimal precision

**Problem:** Using `double` instead of `decimal` for prices and quantities causes floating-point errors.

**Fix:** LEAN uses `decimal` throughout. Always use the `m` suffix for literal values:

```csharp
// WRONG
SetHoldings("BTCUSDT", 0.5);   // double -- implicit cast, may lose precision

// RIGHT
SetHoldings("BTCUSDT", 0.5m);  // decimal -- exact
```
