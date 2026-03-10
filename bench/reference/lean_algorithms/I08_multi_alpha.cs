/*
 * I08 — Multi-Alpha Portfolio Composition
 *
 * Strategy:
 *   - Universe: ~100 tier1 crypto-futures from universe.json
 *   - Timeframe: Daily bars
 *   - Three independent AlphaModels:
 *       1. TrendAlpha:         SMA(20)/SMA(50) crossover
 *       2. MeanReversionAlpha: RSI(14) extremes (<30 → Up, >70 → Down)
 *       3. MomentumAlpha:      20-day ROC (>5% → Up, <-5% → Down)
 *   - Portfolio: Parameterized via GetParameter("portfolio_model"):
 *       "InsightWeighting" → InsightWeightingPortfolioConstructionModel
 *       "EqualWeighting"   → EqualWeightingPortfolioConstructionModel
 *   - Execution: ImmediateExecutionModel
 *
 * Key: Uses AddAlpha() (accumulates) not SetAlpha() (replaces).
 *
 * LEAN API: Algorithm Framework (AddAlpha, multiple AlphaModels,
 *           InsightWeightingPortfolioConstructionModel)
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
using QuantConnect.Orders;

namespace QuantTutorBench
{
    public class I08MultiAlpha : QCAlgorithm
    {
        // ── Configuration ──
        private const int MaxSymbols = 100;
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

            // Multiple alpha models (AddAlpha accumulates, SetAlpha replaces)
            AddAlpha(new TrendAlpha());
            AddAlpha(new MeanReversionAlpha());
            AddAlpha(new MomentumAlpha());

            // Portfolio construction based on parameter
            var portfolioModel = GetParameter("portfolio_model", "InsightWeighting");
            if (portfolioModel == "EqualWeighting")
            {
                SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
                Log("Portfolio model: EqualWeighting");
            }
            else
            {
                SetPortfolioConstruction(new InsightWeightingPortfolioConstructionModel());
                Log("Portfolio model: InsightWeighting");
            }

            SetExecution(new ImmediateExecutionModel());

            SetWarmUp(50, Resolution.Daily);

            Log($"I08 initialized with {subset.Count} symbols (of {tickers.Count}), " +
                $"3 alpha models (Trend + MeanReversion + Momentum), " +
                $"portfolio_model={portfolioModel}");
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

    /// <summary>
    /// Trend alpha: SMA(20)/SMA(50) crossover.
    /// Bullish when fast > slow, bearish when fast < slow.
    /// </summary>
    public class TrendAlpha : AlphaModel
    {
        private const int FastPeriod = 20;
        private const int SlowPeriod = 50;

        private class SymbolData
        {
            public Symbol Symbol;
            public SimpleMovingAverage SmaFast;
            public SimpleMovingAverage SmaSlow;
        }

        private readonly Dictionary<Symbol, SymbolData> _data = new();

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();

            foreach (var kvp in _data)
            {
                var sd = kvp.Value;
                if (!sd.SmaFast.IsReady || !sd.SmaSlow.IsReady) continue;
                if (!data.ContainsKey(sd.Symbol)) continue;

                if (sd.SmaFast.Current.Value > sd.SmaSlow.Current.Value)
                {
                    insights.Add(Insight.Up(sd.Symbol, TimeSpan.FromDays(3),
                        magnitude: 0.5, confidence: 0.6));
                }
                else
                {
                    insights.Add(Insight.Down(sd.Symbol, TimeSpan.FromDays(3),
                        magnitude: 0.5, confidence: 0.6));
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
                        SmaFast = algorithm.SMA(added.Symbol, FastPeriod, Resolution.Daily),
                        SmaSlow = algorithm.SMA(added.Symbol, SlowPeriod, Resolution.Daily),
                    };
                }
            }
            foreach (var removed in changes.RemovedSecurities)
                _data.Remove(removed.Symbol);
        }
    }

    /// <summary>
    /// Mean-reversion alpha: RSI(14) extremes.
    /// RSI < 30 → Up (oversold bounce expected), RSI > 70 → Down (overbought).
    /// </summary>
    public class MeanReversionAlpha : AlphaModel
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
                if (!sd.Rsi.IsReady) continue;
                if (!data.ContainsKey(sd.Symbol)) continue;

                var rsi = sd.Rsi.Current.Value;

                if (rsi < 30)
                {
                    insights.Add(Insight.Up(sd.Symbol, TimeSpan.FromDays(2),
                        magnitude: 0.8, confidence: 0.7));
                }
                else if (rsi > 70)
                {
                    insights.Add(Insight.Down(sd.Symbol, TimeSpan.FromDays(2),
                        magnitude: 0.8, confidence: 0.7));
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
                _data.Remove(removed.Symbol);
        }
    }

    /// <summary>
    /// Momentum alpha: 20-day rate of change.
    /// ROC > 5% → Up, ROC < -5% → Down.
    /// </summary>
    public class MomentumAlpha : AlphaModel
    {
        private const int RocPeriod = 20;

        private class SymbolData
        {
            public Symbol Symbol;
            public RateOfChangePercent Roc;
        }

        private readonly Dictionary<Symbol, SymbolData> _data = new();

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();

            foreach (var kvp in _data)
            {
                var sd = kvp.Value;
                if (!sd.Roc.IsReady) continue;
                if (!data.ContainsKey(sd.Symbol)) continue;

                var roc = sd.Roc.Current.Value;

                if (roc > 5m)
                {
                    insights.Add(Insight.Up(sd.Symbol, TimeSpan.FromDays(5),
                        magnitude: (double)Math.Abs(roc) / 100.0, confidence: 0.5));
                }
                else if (roc < -5m)
                {
                    insights.Add(Insight.Down(sd.Symbol, TimeSpan.FromDays(5),
                        magnitude: (double)Math.Abs(roc) / 100.0, confidence: 0.5));
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
                        Roc = algorithm.ROCP(added.Symbol, RocPeriod, Resolution.Daily),
                    };
                }
            }
            foreach (var removed in changes.RemovedSecurities)
                _data.Remove(removed.Symbol);
        }
    }
}
