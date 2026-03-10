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
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Risk;

public class MyFrameworkAlgorithm : QCAlgorithm
{
    public override void Initialize()
    {
        // ... dates, cash, universe setup ...

        // Single alpha model
        SetAlpha(new MyAlphaModel());

        // Multiple alpha models (accumulates, does not replace)
        AddAlpha(new TrendAlpha());
        AddAlpha(new MeanReversionAlpha());

        // Portfolio construction (note: LEAN spelling is "Construction" — no "i")
        SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());

        // Risk management (can add multiple)
        SetRiskManagement(new MaximumDrawdownPerSecurity(0.05m));
        AddRiskManagement(new TrailingStopRiskManagementModel(0.03m));

        // Execution
        SetExecution(new ImmediateExecutionModel());
    }
}
```

**Important**: The method is `SetPortfolioConstruction` (not `SetPortfolioConstruction`) — LEAN's API has this exact spelling without the "i" in "Construction".

## Alpha Model + Insight

An Alpha Model generates trading signals as `Insight` objects:

```csharp
using QuantConnect.Algorithm.Framework.Alphas;

public class MyAlphaModel : AlphaModel
{
    public override IEnumerable<Insight> Update(
        QCAlgorithm algorithm, Slice data)
    {
        var insights = new List<Insight>();

        // Emit a bullish insight
        insights.Add(Insight.Up(
            symbol,
            TimeSpan.FromDays(5),      // duration (how long the signal is valid)
            magnitude: 0.05,            // expected move magnitude
            confidence: 0.8             // confidence level 0-1
        ));

        // Emit a bearish insight
        insights.Add(Insight.Down(
            symbol,
            TimeSpan.FromDays(3),
            magnitude: 0.03,
            confidence: 0.6
        ));

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
| `Weight` | `double?` | Relative weight vs other insights |

## Portfolio Construction Models

Built-in models that convert Insights into portfolio targets:

### EqualWeightingPortfolioConstructionModel

Assigns equal weight to all active insights, regardless of magnitude or confidence.

```csharp
SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
```

### InsightWeightingPortfolioConstructionModel

Weights positions by insight properties (confidence × magnitude):

```csharp
SetPortfolioConstruction(new InsightWeightingPortfolioConstructionModel());
```

## Risk Management Models

### MaximumDrawdownPerSecurity

Liquidates a position if its drawdown exceeds a threshold:

```csharp
AddRiskManagement(new MaximumDrawdownPerSecurity(0.05m)); // 5% max drawdown per position
```

### TrailingStopRiskManagementModel

Applies a trailing stop-loss to each position:

```csharp
AddRiskManagement(new TrailingStopRiskManagementModel(0.03m)); // 3% trailing stop
```

### Custom Risk Management Model

```csharp
using QuantConnect.Algorithm.Framework.Risk;

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

Use `GetParameter()` to read runtime parameters from `config.json`:

```csharp
public override void Initialize()
{
    // Read parameters with defaults
    int fastPeriod = int.Parse(GetParameter("fast_period", "10"));
    int slowPeriod = int.Parse(GetParameter("slow_period", "30"));
    decimal threshold = decimal.Parse(GetParameter("signal_threshold", "0.01"));

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

```csharp
// Core framework
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Risk;

// For PortfolioTarget in custom risk models
using QuantConnect.Algorithm.Framework.Portfolio;
```
