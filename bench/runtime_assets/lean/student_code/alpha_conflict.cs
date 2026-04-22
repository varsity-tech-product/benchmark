/*
 * X09 — Alpha Conflict (Framework Bug)
 *
 * Strategy:
 *   - Universe: 10 crypto-futures from universe.json
 *   - Timeframe: Daily bars
 *   - Two AlphaModels:
 *       1. TrendAlpha:     EMA(10)/EMA(30) crossover
 *       2. ReversionAlpha: RSI(14) overbought/oversold
 *   - Portfolio: EqualWeightingPortfolioConstructionModel
 *   - Execution: ImmediateExecutionModel
 *
 * BUG: Both alphas emit insights with the SAME confidence (0.6).
 *      Under EqualWeightingPortfolioConstructionModel, insights from
 *      different alphas get equal weight. When TrendAlpha says Up and
 *      ReversionAlpha says Down for the same symbol (e.g., strong uptrend
 *      pushes RSI > 70), the opposing insights cancel out, producing
 *      near-zero target allocation. This results in very few or no trades.
 *
 *      Fix: Use InsightWeightingPortfolioConstructionModel and assign
 *      different confidence levels, or use a custom PCM that resolves
 *      conflicts via a priority/scoring system.
 *
 * LEAN API: Algorithm Framework (AddAlpha, EqualWeighting conflict)
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
    public class AlphaConflict : QCAlgorithm
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
                AddCrypto(ticker, Resolution.Daily, Market.Binance);
            }

            // Two alpha models that frequently disagree
            AddAlpha(new TrendAlpha());
            AddAlpha(new ReversionAlpha());

            // BUG: EqualWeightingPortfolioConstructionModel treats all insights
            // equally. When TrendAlpha emits Up and ReversionAlpha emits Down
            // for the same symbol (both with confidence 0.6), the portfolio
            // construction model averages them out → near-zero allocation →
            // almost no trades are placed.
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());

            SetExecution(new ImmediateExecutionModel());

            SetWarmUp(30, Resolution.Daily);

            Log($"X09 AlphaConflict initialized with {subset.Count} symbols, " +
                $"TrendAlpha(EMA 10/30) + ReversionAlpha(RSI 14) → EqualWeighting");
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
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
            };
        }
    }

    // ────────────────────────────────────────────────────────────────────────
    // Alpha Model 1: EMA(10)/EMA(30) Trend Crossover
    // ────────────────────────────────────────────────────────────────────────

    /// <summary>
    /// Trend-following alpha: EMA(10)/EMA(30) crossover.
    /// Emits Up when fast EMA > slow EMA, Down otherwise.
    /// Uses magnitude 0.5 and confidence 0.6.
    /// </summary>
    public class TrendAlpha : AlphaModel
    {
        private const int FastPeriod = 10;
        private const int SlowPeriod = 30;

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

                // BUG: confidence is always 0.6 — same as ReversionAlpha.
                // Under EqualWeighting, opposing insights cancel out.
                insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(5),
                    direction, 0.5, 0.6));

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

    // ────────────────────────────────────────────────────────────────────────
    // Alpha Model 2: RSI(14) Mean Reversion
    // ────────────────────────────────────────────────────────────────────────

    /// <summary>
    /// Mean-reversion alpha: RSI(14) extremes.
    /// RSI > 70 (overbought) → Down, RSI < 30 (oversold) → Up.
    /// Uses magnitude 0.8 and confidence 0.6 (same as TrendAlpha — this is the bug).
    /// </summary>
    public class ReversionAlpha : AlphaModel
    {
        private const int RsiPeriod = 14;

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

                if (!sd.Rsi.IsReady)
                    continue;
                if (!data.ContainsKey(sd.Symbol))
                    continue;

                var rsi = sd.Rsi.Current.Value;

                if (rsi > 70)
                {
                    // Overbought → expect reversion down
                    // BUG: confidence 0.6 matches TrendAlpha, so when trend
                    // is Up (strong uptrend pushes RSI > 70), the two insights
                    // cancel under EqualWeighting.
                    insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(3),
                        InsightDirection.Down, 0.8, 0.6));
                }
                else if (rsi < 30)
                {
                    // Oversold → expect reversion up
                    // Similarly cancels when trend says Down during oversold conditions.
                    insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(3),
                        InsightDirection.Up, 0.8, 0.6));
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
