/*
 * X09 — Alpha Conflict Fixed (Confidence-Weighted Resolution)
 *
 * Bug context:
 *   The buggy version uses EqualWeightingPortfolioConstructionModel with two
 *   conflicting alpha models (Trend + Reversion). Under equal weighting, when
 *   both alphas fire on the same symbol with opposite directions, insights
 *   cancel out and produce zero or near-zero target holdings → no trades.
 *
 * Fix:
 *   Switch to InsightWeightingPortfolioConstructionModel(Resolution.Daily).
 *   TrendAlpha emits confidence=0.8, ReversionAlpha emits confidence=0.4.
 *   Under insight-weighting, the higher-confidence trend signal dominates,
 *   resolving the cancellation and producing actual trades.
 *
 * Strategy:
 *   - Universe: First 10 symbols from universe.json
 *   - Timeframe: Daily bars
 *   - Two AlphaModels via AddAlpha():
 *       1. TrendAlpha:     EMA(10)/EMA(30) crossover, magnitude 0.5, confidence 0.8
 *       2. ReversionAlpha: RSI(14) extremes, magnitude 0.8, confidence 0.4
 *   - Portfolio: InsightWeightingPortfolioConstructionModel(Resolution.Daily)
 *   - Execution: ImmediateExecutionModel
 *   - WarmUp: 30 daily bars
 *
 * LEAN API: Algorithm Framework (AddAlpha, InsightWeightingPortfolioConstructionModel)
 */

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Orders;

namespace QuantTutorBench
{
    public class X09AlphaConflictFixed : QCAlgorithm
    {
        // ── Configuration ──
        private const int MaxSymbols = 10;
        private const decimal InitialCash = 100_000m;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var tickers = LoadUniverse();
            var subset = tickers.Take(MaxSymbols).ToList();

            foreach (var ticker in subset)
            {
                AddCryptoFuture(ticker, Resolution.Daily, Market.Binance);
            }

            // Two conflicting alpha models (AddAlpha accumulates)
            AddAlpha(new X09TrendAlpha());
            AddAlpha(new X09ReversionAlpha());

            // FIX: Use InsightWeightingPortfolioConstructionModel instead of EqualWeighting.
            // TrendAlpha confidence (0.8) dominates ReversionAlpha confidence (0.4),
            // so when both fire on the same symbol, the trend signal wins.
            SetPortfolioConstruction(new InsightWeightingPortfolioConstructionModel(Resolution.Daily));

            SetExecution(new ImmediateExecutionModel());

            SetWarmUp(30, Resolution.Daily);

            Log($"X09 FIXED initialized with {subset.Count} symbols (of {tickers.Count}), " +
                $"TrendAlpha(conf=0.8) + ReversionAlpha(conf=0.4), " +
                $"InsightWeightingPCM → trend dominates on conflict");
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status == OrderStatus.Filled)
            {
                Log($"TRADE: {orderEvent.Symbol} | " +
                    $"Direction={orderEvent.Direction} | " +
                    $"Qty={orderEvent.FillQuantity} | " +
                    $"Price={orderEvent.FillPrice} | " +
                    $"Tag={orderEvent.Message}");
            }
        }

        private List<string> LoadUniverse()
        {
            var paths = new[]
            {
                Path.Combine(Globals.DataFolder, "universe.json"),
                "/data/universe.json",
                "/lean/Data/universe.json"
            };

            foreach (var path in paths)
            {
                if (File.Exists(path))
                {
                    var json = File.ReadAllText(path);
                    var symbols = JsonConvert.DeserializeObject<List<string>>(json);
                    Log($"Loaded {symbols.Count} symbols from {path}");
                    return symbols;
                }
            }

            Log("WARNING: universe.json not found, using default 10-symbol universe");
            return new List<string>
            {
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT"
            };
        }
    }

    /// <summary>
    /// Trend alpha: EMA(10)/EMA(30) crossover.
    /// Emits Up when fast > slow, Down when fast < slow.
    /// High confidence (0.8) — intended to dominate under insight-weighting.
    /// </summary>
    public class X09TrendAlpha : AlphaModel
    {
        private const int FastPeriod = 10;
        private const int SlowPeriod = 30;
        private const double Magnitude = 0.5;
        private const double Confidence = 0.8;

        private class SymbolData
        {
            public Symbol Symbol;
            public ExponentialMovingAverage EmaFast;
            public ExponentialMovingAverage EmaSlow;
            public InsightDirection LastDirection = InsightDirection.Flat;
        }

        private readonly Dictionary<Symbol, SymbolData> _data = new();

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();

            foreach (var kvp in _data)
            {
                var sd = kvp.Value;

                if (!sd.EmaFast.IsReady || !sd.EmaSlow.IsReady)
                    continue;

                if (!data.ContainsKey(sd.Symbol))
                    continue;

                var fast = sd.EmaFast.Current.Value;
                var slow = sd.EmaSlow.Current.Value;

                InsightDirection direction;
                if (fast > slow)
                    direction = InsightDirection.Up;
                else
                    direction = InsightDirection.Down;

                insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(5),
                    direction, Magnitude, Confidence));

                sd.LastDirection = direction;
            }

            return insights;
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
            foreach (var added in changes.AddedSecurities)
            {
                if (!_data.ContainsKey(added.Symbol))
                {
                    _data[added.Symbol] = new SymbolData
                    {
                        Symbol = added.Symbol,
                        EmaFast = algorithm.EMA(added.Symbol, FastPeriod, Resolution.Daily),
                        EmaSlow = algorithm.EMA(added.Symbol, SlowPeriod, Resolution.Daily),
                    };
                }
            }

            foreach (var removed in changes.RemovedSecurities)
            {
                _data.Remove(removed.Symbol);
            }
        }
    }

    /// <summary>
    /// Mean-reversion alpha: RSI(14) extremes.
    /// RSI > 70 → Down (overbought), RSI < 30 → Up (oversold).
    /// Low confidence (0.4) — defers to trend under insight-weighting.
    /// </summary>
    public class X09ReversionAlpha : AlphaModel
    {
        private const int RsiPeriod = 14;
        private const double Magnitude = 0.8;
        private const double Confidence = 0.4;

        private class SymbolData
        {
            public Symbol Symbol;
            public RelativeStrengthIndex Rsi;
        }

        private readonly Dictionary<Symbol, SymbolData> _data = new();

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();

            foreach (var kvp in _data)
            {
                var sd = kvp.Value;
                if (!sd.Rsi.IsReady) continue;
                if (!data.ContainsKey(sd.Symbol)) continue;

                var rsi = sd.Rsi.Current.Value;

                if (rsi > 70)
                {
                    insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(3),
                        InsightDirection.Down, Magnitude, Confidence));
                }
                else if (rsi < 30)
                {
                    insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(3),
                        InsightDirection.Up, Magnitude, Confidence));
                }
            }

            return insights;
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
            foreach (var added in changes.AddedSecurities)
            {
                if (!_data.ContainsKey(added.Symbol))
                {
                    _data[added.Symbol] = new SymbolData
                    {
                        Symbol = added.Symbol,
                        Rsi = algorithm.RSI(added.Symbol, RsiPeriod, MovingAverageType.Wilders, Resolution.Daily),
                    };
                }
            }

            foreach (var removed in changes.RemovedSecurities)
            {
                _data.Remove(removed.Symbol);
            }
        }
    }
}
