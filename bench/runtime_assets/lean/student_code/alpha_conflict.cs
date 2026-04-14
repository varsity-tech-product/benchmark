/*
 * X09 - Alpha Conflict (Framework Bug)
 *
 * Strategy:
 *   - Universe: 10 crypto symbols from universe.json
 *   - Timeframe: Daily bars
 *   - Two AlphaModels:
 *       1. TrendAlpha: EMA(10)/EMA(30) crossover
 *       2. ReversionAlpha: contrarian fade of the same EMA spread
 *   - Portfolio: AccumulativeInsightPortfolioConstructionModel
 *   - Execution: ImmediateExecutionModel
 *
 * BUG: Both alphas emit equally active but opposing insights on the
 *      same symbols with the same horizon. Under
 *      AccumulativeInsightPortfolioConstructionModel, the active
 *      insights net toward zero, producing very few or no trades.
 *
 * NOTE: On the standalone 12-col pipeline, these alpha models use the
 * subscribed symbol list plus manually updated indicators instead of
 * relying on OnSecuritiesChanged() auto-registration.
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
using QuantConnect.Indicators;
using QuantConnect.Orders;

namespace QuantTutorBench
{
    public class AlphaConflict : QCAlgorithm
    {
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
            var symbols = new List<Symbol>();

            foreach (var ticker in subset)
            {
                var security = AddCrypto(ticker, Resolution.Daily, Market.Binance);
                symbols.Add(security.Symbol);
            }

            AddAlpha(new TrendAlpha(symbols));
            AddAlpha(new ReversionAlpha(symbols));

            // BUG: AccumulativeInsightPortfolioConstructionModel aggregates all
            // active insights for a symbol. Since TrendAlpha and ReversionAlpha
            // emit equal-and-opposite signals, the net target stays near zero.
            SetPortfolioConstruction(new AccumulativeInsightPortfolioConstructionModel());
            SetExecution(new ImmediateExecutionModel());
            SetWarmUp(30, Resolution.Daily);

            Log(
                $"X09 AlphaConflict initialized with {symbols.Count} symbols, " +
                "TrendAlpha(EMA 10/30) + ReversionAlpha(contrarian EMA spread) -> Accumulative"
            );
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status == OrderStatus.Filled)
            {
                Log(
                    $"TRADE: {orderEvent.Symbol} | " +
                    $"Direction={orderEvent.Direction} | " +
                    $"Qty={orderEvent.FillQuantity} | " +
                    $"Price={orderEvent.FillPrice} | " +
                    $"Tag={orderEvent.Message}"
                );
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

    public class TrendAlpha : AlphaModel
    {
        private const int FastPeriod = 10;
        private const int SlowPeriod = 30;

        private class SymbolData
        {
            public ExponentialMovingAverage EmaFast;
            public ExponentialMovingAverage EmaSlow;
        }

        private readonly List<Symbol> _symbols;
        private readonly Dictionary<Symbol, SymbolData> _data = new();

        public TrendAlpha(IEnumerable<Symbol> symbols)
        {
            _symbols = symbols.ToList();
        }

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();

            foreach (var symbol in _symbols)
            {
                if (!_data.ContainsKey(symbol))
                {
                    _data[symbol] = new SymbolData
                    {
                        EmaFast = new ExponentialMovingAverage(FastPeriod),
                        EmaSlow = new ExponentialMovingAverage(SlowPeriod),
                    };
                }

                if (!data.ContainsKey(symbol) || !algorithm.Securities.ContainsKey(symbol))
                {
                    continue;
                }

                var price = algorithm.Securities[symbol].Price;
                if (price <= 0)
                {
                    continue;
                }

                var sd = _data[symbol];
                sd.EmaFast.Update(algorithm.Time, price);
                sd.EmaSlow.Update(algorithm.Time, price);

                if (!sd.EmaFast.IsReady || !sd.EmaSlow.IsReady)
                {
                    continue;
                }

                var direction = sd.EmaFast > sd.EmaSlow
                    ? InsightDirection.Up
                    : InsightDirection.Down;

                // BUG: this active insight is exactly opposed by ReversionAlpha,
                // and the accumulative PCM nets the pair toward zero.
                insights.Add(Insight.Price(symbol, TimeSpan.FromDays(5), direction, 0.5, 0.6));
            }

            return insights;
        }
    }

    public class ReversionAlpha : AlphaModel
    {
        private const int FastPeriod = 10;
        private const int SlowPeriod = 30;

        private class SymbolData
        {
            public ExponentialMovingAverage EmaFast;
            public ExponentialMovingAverage EmaSlow;
        }

        private readonly List<Symbol> _symbols;
        private readonly Dictionary<Symbol, SymbolData> _data = new();

        public ReversionAlpha(IEnumerable<Symbol> symbols)
        {
            _symbols = symbols.ToList();
        }

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();

            foreach (var symbol in _symbols)
            {
                if (!_data.ContainsKey(symbol))
                {
                    _data[symbol] = new SymbolData
                    {
                        EmaFast = new ExponentialMovingAverage(FastPeriod),
                        EmaSlow = new ExponentialMovingAverage(SlowPeriod),
                    };
                }

                if (!data.ContainsKey(symbol) || !algorithm.Securities.ContainsKey(symbol))
                {
                    continue;
                }

                var price = algorithm.Securities[symbol].Price;
                if (price <= 0)
                {
                    continue;
                }

                var sd = _data[symbol];
                sd.EmaFast.Update(algorithm.Time, price);
                sd.EmaSlow.Update(algorithm.Time, price);

                if (!sd.EmaFast.IsReady || !sd.EmaSlow.IsReady)
                {
                    continue;
                }

                var direction = sd.EmaFast > sd.EmaSlow
                    ? InsightDirection.Down
                    : InsightDirection.Up;

                insights.Add(Insight.Price(symbol, TimeSpan.FromDays(5), direction, 0.5, 0.6));
            }

            return insights;
        }
    }
}
