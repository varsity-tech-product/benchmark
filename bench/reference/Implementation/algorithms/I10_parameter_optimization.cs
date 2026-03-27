/*
 * I10 — Parameter Optimization (Grid Search)
 *
 * Strategy:
 *   - Universe: full tier2 crypto-futures universe from universe.json
 *   - Timeframe: Daily bars
 *   - Signal: Parameterized EMA crossover with threshold filter
 *       - EMA(fast) / EMA(slow) crossover
 *       - Signal only if |EMA_fast - EMA_slow| / EMA_slow > threshold
 *   - Parameters (via GetParameter):
 *       fast_period:       5, 10, 15, 20, 25, 30
 *       slow_period:       20, 30, 40, 50, 60, 70, 80, 90, 100
 *       signal_threshold:  0.0, 0.005, 0.01, 0.015, 0.02
 *   - Portfolio: EqualWeightingPortfolioConstructionModel
 *   - Execution: ImmediateExecutionModel
 *   - Risk: TrailingStopRiskManagementModel(3%)
 *
 * LEAN API: Algorithm Framework (AlphaModel, GetParameter,
 *           TrailingStopRiskManagementModel)
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
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Orders;

namespace QuantTutorBench
{
    public class I10ParameterOptimization : QCAlgorithm
    {
        // ── Configuration ──
        private const int MaxSymbols = 20;
        private const decimal InitialCash = 100_000m;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            // Read parameters with defaults
            int fastPeriod = int.Parse(GetParameter("fast_period", "10"));
            int slowPeriod = int.Parse(GetParameter("slow_period", "30"));
            decimal signalThreshold = decimal.Parse(GetParameter("signal_threshold", "0.01"));

            var tickers = LoadUniverse();
            var subset = tickers.Take(MaxSymbols).ToList();

            foreach (var ticker in subset)
            {
                AddCryptoFuture(ticker, Resolution.Daily, Market.Binance);
            }

            // Framework wiring
            SetAlpha(new ParameterizedTrendAlpha(fastPeriod, slowPeriod, signalThreshold));
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
            SetExecution(new ImmediateExecutionModel());
            AddRiskManagement(new TrailingStopRiskManagementModel(0.03m));

            SetWarmUp(slowPeriod, Resolution.Daily);

            Log($"I10 initialized with {subset.Count} symbols, " +
                $"EMA({fastPeriod}/{slowPeriod}), threshold={signalThreshold}, " +
                $"TrailingStop=3%");
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
    /// Parameterized EMA crossover alpha with threshold filter.
    /// Only emits insights when |EMA_fast - EMA_slow| / EMA_slow > threshold.
    /// </summary>
    public class ParameterizedTrendAlpha : AlphaModel
    {
        private readonly int _fastPeriod;
        private readonly int _slowPeriod;
        private readonly decimal _threshold;

        private class SymbolData
        {
            public Symbol Symbol;
            public ExponentialMovingAverage EmaFast;
            public ExponentialMovingAverage EmaSlow;
            public InsightDirection LastDirection = InsightDirection.Flat;
        }

        private readonly Dictionary<Symbol, SymbolData> _data = new();

        public ParameterizedTrendAlpha(int fastPeriod, int slowPeriod, decimal threshold)
        {
            _fastPeriod = fastPeriod;
            _slowPeriod = slowPeriod;
            _threshold = threshold;
        }

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

                if (slow == 0) continue;

                var spread = (fast - slow) / slow;

                // Only emit if spread exceeds threshold
                if (Math.Abs(spread) <= _threshold)
                    continue;

                InsightDirection direction;
                if (fast > slow)
                    direction = InsightDirection.Up;
                else
                    direction = InsightDirection.Down;

                var magnitude = (double)Math.Abs(spread);
                var confidence = direction == sd.LastDirection ? 0.6 : 0.8;

                insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(5),
                    direction, magnitude, confidence));

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
                    var sd = new SymbolData
                    {
                        Symbol = added.Symbol,
                        EmaFast = algorithm.EMA(added.Symbol, _fastPeriod, Resolution.Daily),
                        EmaSlow = algorithm.EMA(added.Symbol, _slowPeriod, Resolution.Daily),
                    };
                    _data[added.Symbol] = sd;
                }
            }

            foreach (var removed in changes.RemovedSecurities)
            {
                _data.Remove(removed.Symbol);
            }
        }
    }
}
