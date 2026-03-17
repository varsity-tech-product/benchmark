# LEAN Algorithm Framework Guide

## Overview

LEAN's **Algorithm Framework** replaces the monolithic `OnData()` approach with a modular pipeline. Instead of writing all logic in one method, you define separate models for each responsibility:

```
Alpha Model → Portfolio Construction → Risk Management → Execution
```

Each model is a pluggable component that can be swapped, combined, or customized independently.

## Classic vs Framework Comparison

| Aspect | Classic (`OnData`) | Framework |
|---|---|---|
| Signal generation | Manual in `OnData()` | `AlphaModel.Update()` emits `Insight` objects |
| Position sizing | Manual `SetHoldings()` | `PortfolioConstructionModel` converts Insights → targets |
| Risk controls | Manual checks in `OnData()` | `RiskManagementModel.ManageRisk()` adjusts targets |
| Order routing | `SetHoldings()` / `MarketOrder()` | `ExecutionModel` handles order submission |
| Composability | Single monolith | Mix-and-match models |

## Wiring Models in Initialize()

```csharp
public class Algorithm : QCAlgorithm
{
    public override void Initialize()
    {
        // ... dates, cash, universe setup ...

        // Single alpha model
        SetAlpha(new MyAlphaModel());

        // Multiple alpha models (accumulates, does not replace)
        AddAlpha(new TrendAlpha());
        AddAlpha(new MeanReversionAlpha());

        // Portfolio construction
        SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());

        // Risk management: Set replaces, Add composes
        SetRiskManagement(new MaximumDrawdownPercentPerSecurity(0.05m));
        AddRiskManagement(new TrailingStopRiskManagementModel(0.03m));

        // Execution
        SetExecution(new ImmediateExecutionModel());
    }
}
```

Use `SetPortfolioConstruction(...)` to replace the current portfolio construction model.

## Alpha Model + Insight

An Alpha Model generates trading signals as `Insight` objects.

### Insight.Price (primary API)

The most flexible way to create insights is `Insight.Price(...)`. Use `InsightDirection` for direction and set `weight:` explicitly when an insight-weighted portfolio model should consume the signal:

```csharp
public class MyAlphaModel : AlphaModel
{
    public override IEnumerable<Insight> Update(
        QCAlgorithm algorithm, Slice data)
    {
        var insights = new List<Insight>();

        // Bullish insight using Insight.Price
        insights.Add(Insight.Price(
            symbol,
            TimeSpan.FromDays(5),           // duration
            InsightDirection.Up,            // direction enum
            0.05,                           // magnitude
            0.8,                            // confidence
            weight: 0.4                     // target weight used by InsightWeighting PCM
        ));

        // Bearish insight
        insights.Add(Insight.Price(
            symbol,
            TimeSpan.FromDays(3),
            InsightDirection.Down,
            0.03,
            0.6,
            weight: 0.25
        ));

        return insights;
    }
}
```

### Insight.Up / Insight.Down (convenience shortcuts)

Shorthand methods that set the direction automatically:

```csharp
// Equivalent to Insight.Price(symbol, duration, InsightDirection.Up, ...)
insights.Add(Insight.Up(symbol, TimeSpan.FromDays(5), magnitude: 0.05, confidence: 0.8));

// Equivalent to Insight.Price(symbol, duration, InsightDirection.Down, ...)
insights.Add(Insight.Down(symbol, TimeSpan.FromDays(3), magnitude: 0.03, confidence: 0.6));
```

### OnSecuritiesChanged (dynamic universe handling)

Override `OnSecuritiesChanged` in your AlphaModel to register/deregister indicators when assets enter or leave the universe. This is required for multi-asset framework algorithms:

```csharp
public class EmaCrossoverAlphaModel : AlphaModel
{
    private Dictionary<Symbol, ExponentialMovingAverage> _fastEma = new();
    private Dictionary<Symbol, ExponentialMovingAverage> _slowEma = new();

    public override void OnSecuritiesChanged(
        QCAlgorithm algorithm, SecurityChanges changes)
    {
        foreach (var security in changes.AddedSecurities)
        {
            var symbol = security.Symbol;
            _fastEma[symbol] = algorithm.EMA(symbol, 10, Resolution.Daily);
            _slowEma[symbol] = algorithm.EMA(symbol, 30, Resolution.Daily);
        }

        foreach (var security in changes.RemovedSecurities)
        {
            var symbol = security.Symbol;
            _fastEma.Remove(symbol);
            _slowEma.Remove(symbol);
        }
    }

    public override IEnumerable<Insight> Update(
        QCAlgorithm algorithm, Slice data)
    {
        var insights = new List<Insight>();
        foreach (var kvp in _fastEma)
        {
            var symbol = kvp.Key;
            if (!kvp.Value.IsReady || !_slowEma[symbol].IsReady) continue;
            if (!data.ContainsKey(symbol)) continue;

            var direction = kvp.Value > _slowEma[symbol]
                ? InsightDirection.Up : InsightDirection.Down;

            insights.Add(Insight.Price(
                symbol, TimeSpan.FromDays(1), direction, 0.01, 0.5));
        }
        return insights;
    }
}
```

### Insight Properties

| Property | Type | Description |
|---|---|---|
| `Direction` | `InsightDirection` | `.Up`, `.Down`, or `.Flat` |
| `Period` | `TimeSpan` | How long the insight is valid |
| `Magnitude` | `double?` | Expected return magnitude |
| `Confidence` | `double?` | Confidence in the prediction (0-1) |
| `Weight` | `double?` | Target weight used by `InsightWeightingPortfolioConstructionModel` |

## Portfolio Construction Models

Built-in models that convert Insights into portfolio targets:

### EqualWeightingPortfolioConstructionModel

Assigns equal weight to all active insights, regardless of magnitude or confidence.

```csharp
SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
```

### InsightWeightingPortfolioConstructionModel

Weights positions from `Insight.Weight` on the last active insight per symbol. Insights without `Weight` are ignored:

```csharp
SetPortfolioConstruction(new InsightWeightingPortfolioConstructionModel(Resolution.Daily));
```

Example:

```csharp
insights.Add(Insight.Price(
    symbol,
    TimeSpan.FromDays(3),
    InsightDirection.Up,
    magnitude: 0.03,
    confidence: 0.7,
    weight: 0.35
));
```

If multiple alpha models emit insights for the same symbol, the built-in portfolio construction models operate on the last active insight for that symbol. Use explicit weights, signal gating, or model ordering intentionally.

## Risk Management Models

### MaximumDrawdownPercentPerSecurity

Liquidates a position if its drawdown exceeds a threshold:

```csharp
AddRiskManagement(new MaximumDrawdownPercentPerSecurity(0.05m)); // 5% max drawdown per position
```

### TrailingStopRiskManagementModel

Applies a trailing stop-loss to each position:

```csharp
AddRiskManagement(new TrailingStopRiskManagementModel(0.03m)); // 3% trailing stop
```

### Custom Risk Management Model

```csharp
public class MyRiskModel : RiskManagementModel
{
    private readonly decimal _maxExposure;

    public MyRiskModel(decimal maxExposure)
    {
        _maxExposure = maxExposure;
    }

    public override IEnumerable<IPortfolioTarget> ManageRisk(
        QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        var adjustedTargets = new List<IPortfolioTarget>();

        // Example: scale down if total exposure exceeds threshold
        var totalExposure = algorithm.Portfolio.TotalHoldingsValue
            / algorithm.Portfolio.TotalPortfolioValue;

        if (totalExposure > _maxExposure)
        {
            var scale = _maxExposure / totalExposure;
            foreach (var target in targets)
            {
                adjustedTargets.Add(
                    new PortfolioTarget(target.Symbol,
                        (int)(target.Quantity * scale)));
            }
            return adjustedTargets;
        }

        return targets; // No adjustment needed
    }
}
```

## Execution Models

### ImmediateExecutionModel

Submits market orders immediately for all targets:

```csharp
SetExecution(new ImmediateExecutionModel());
```

This is the standard choice for backtesting — no order splitting or timing logic.

## Parameter Optimization with GetParameter()

Use `GetParameter()` to read runtime parameters from `config.json`. Current LEAN C# source includes typed overloads for `string`, `int`, `double`, and `decimal` defaults:

```csharp
public override void Initialize()
{
    // Typed overloads are available in current LEAN
    int fastPeriod = GetParameter("fast_period", 10);
    int slowPeriod = GetParameter("slow_period", 30);
    decimal threshold = GetParameter("signal_threshold", 0.01m);

    SetAlpha(new ParameterizedAlpha(fastPeriod, slowPeriod, threshold));
}
```

Parameters are set in `config.json`:
```json
{
    "parameters": {
        "fast_period": "15",
        "slow_period": "50",
        "signal_threshold": "0.02"
    }
}
```

This enables running the same algorithm with different configurations for parameter sweeps and optimization.

## Multiple Alpha Models

Use `AddAlpha()` (not `SetAlpha()`) to register multiple alpha models. All models run in parallel and their insights are merged:

```csharp
AddAlpha(new TrendAlpha());        // SMA crossover signals
AddAlpha(new MeanReversionAlpha()); // RSI-based signals
AddAlpha(new MomentumAlpha());     // ROC-based signals
```

The portfolio construction model receives insights from all alphas and determines position sizing based on the combined signal set.

## Required Using Directives

Framework algorithms need the standard LEAN usings plus framework-specific ones:

```csharp
// Standard (same as classic algorithms)
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Orders;

// Framework-specific
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Risk;

// For SecurityChanges in OnSecuritiesChanged
using QuantConnect.Data.UniverseSelection;
```
