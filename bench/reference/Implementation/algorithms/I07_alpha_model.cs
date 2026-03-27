/*
 * I07 — Alpha Model Architecture (Framework Migration)
 *
 * Strategy:
 *   - Universe: full tier2 crypto-futures universe from universe.json
 *   - Timeframe: Daily bars
 *   - Signal: EMA(10) / EMA(30) crossover via AlphaModel
 *   - Direction: Long/Short via Insight.Up / Insight.Down
 *   - Portfolio: EqualWeightingPortfolioConstructionModel
 *   - Execution: ImmediateExecutionModel
 *   - No risk management layer
 *
 * LEAN API: Algorithm Framework (AlphaModel, Insight, SetAlpha,
 *           SetPortfolioConstruction, SetExecution)
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
    public class I07AlphaModel : QCAlgorithm
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

            var tickers = LoadUniverse();
            var subset = tickers.Take(MaxSymbols).ToList();

            foreach (var ticker in subset)
            {
                AddCryptoFuture(ticker, Resolution.Daily, Market.Binance);
            }

            // Framework wiring
            SetAlpha(new EmaCrossoverAlphaModel());
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
            SetExecution(new ImmediateExecutionModel());

            SetWarmUp(30, Resolution.Daily);

            Log($"I07 initialized with {subset.Count} symbols (of {tickers.Count} in universe), " +
                $"EMA(10/30) AlphaModel → EqualWeighting → ImmediateExecution");
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
    /// EMA(10) / EMA(30) crossover alpha model.
    /// Emits Insight.Up on bullish crossover, Insight.Down on bearish crossover.
    /// Re-emits on persist (while condition holds).
    /// </summary>
    public class EmaCrossoverAlphaModel : AlphaModel
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
                var spread = (fast - slow) / slow;

                InsightDirection direction;
                if (fast > slow)
                    direction = InsightDirection.Up;
                else
                    direction = InsightDirection.Down;

                // Emit on crossover edge or re-emit on persist
                if (direction != InsightDirection.Flat)
                {
                    var magnitude = (double)Math.Abs(spread);
                    var confidence = direction == sd.LastDirection ? 0.6 : 0.8;

                    insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(5),
                        direction, magnitude, confidence));
                }

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
                        EmaFast = algorithm.EMA(added.Symbol, FastPeriod, Resolution.Daily),
                        EmaSlow = algorithm.EMA(added.Symbol, SlowPeriod, Resolution.Daily),
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
