// Template 04: Algorithm Framework (Alpha + PCM + Risk + Execution)
//
// Uses LEAN's Algorithm Framework architecture with separate components:
//   - Custom AlphaModel emitting Insight objects
//   - EqualWeightingPortfolioConstructionModel
//   - MaximumDrawdownPercentPerSecurity risk model
//   - ImmediateExecutionModel
//
// Key patterns demonstrated:
//   - SetAlpha / SetPortfolioConstruction / SetRiskManagement / SetExecution
//   - Custom AlphaModel with lazy symbol discovery from data.Keys
//   - Insight.Price() for directional signals with magnitude and confidence
//   - Manual indicator updates (auto-feed not available on 12-col pipeline)
//   - Resolution.Daily on a multi-symbol universe
//
// NOTE on Framework + 12-col pipeline:
//   Securities added via AddCrypto in Initialize() do NOT trigger the
//   Framework's OnSecuritiesChanged callback on this pipeline.  The
//   AlphaModel discovers symbols lazily from data.Keys in Update()
//   instead.  Indicators must also be updated manually (see Update body).

using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Indicators;
using System;
using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Algorithm.CSharp
{
    public class Algorithm : QCAlgorithm
    {
        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetCash(100000);

            // Subscribe to symbols -- framework components operate on these.
            var tickers = new[] {
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
                "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT",
                "AAVEUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
            };

            foreach (var ticker in tickers)
                AddCrypto(ticker, Resolution.Daily);

            // Wire framework components.
            SetAlpha(new EmaCrossAlpha(fastPeriod: 10, slowPeriod: 30));
            // NOTE: LEAN spells it SetPortfolioConstruction (no 'i').
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
            SetRiskManagement(new MaximumDrawdownPercentPerSecurity(0.10m));
            SetExecution(new ImmediateExecutionModel());

            SetWarmUp(35, Resolution.Daily);
        }
    }

    /// <summary>
    /// EMA crossover alpha model.  Emits Up insight when fast EMA crosses
    /// above slow EMA, Down insight on cross below.
    ///
    /// Discovers symbols lazily from data.Keys (OnSecuritiesChanged does
    /// not fire for 12-col pipeline subscriptions) and updates indicators
    /// manually each bar.
    /// </summary>
    public class EmaCrossAlpha : AlphaModel
    {
        private readonly int _fast;
        private readonly int _slow;
        private readonly Dictionary<Symbol, SymbolData> _data = new Dictionary<Symbol, SymbolData>();

        public EmaCrossAlpha(int fastPeriod = 10, int slowPeriod = 30)
        {
            _fast = fastPeriod;
            _slow = slowPeriod;
        }

        public override IEnumerable<Insight> Update(QCAlgorithm algo, Slice data)
        {
            var insights = new List<Insight>();

            // Lazy symbol discovery: register any symbol that appears in
            // data.Keys but isn't tracked yet.  This replaces the standard
            // OnSecuritiesChanged flow which does not fire on this pipeline.
            foreach (var key in data.Keys)
            {
                if (!_data.ContainsKey(key))
                {
                    _data[key] = new SymbolData
                    {
                        Fast = new ExponentialMovingAverage(_fast),
                        Slow = new ExponentialMovingAverage(_slow),
                    };
                }
            }

            foreach (var kvp in _data)
            {
                var sym = kvp.Key;
                var sd = kvp.Value;

                // Manually feed indicators from the security price.
                // LEAN's auto-feed does not work with the 12-col custom
                // data pipeline, so we update explicitly each bar.
                if (!algo.Securities.ContainsKey(sym)) continue;
                var price = algo.Securities[sym].Price;
                if (price <= 0) continue;

                sd.Fast.Update(algo.Time, price);
                sd.Slow.Update(algo.Time, price);

                if (!sd.Fast.IsReady || !sd.Slow.IsReady) continue;

                bool bullish = sd.Fast > sd.Slow;
                bool wasLong = sd.WasLong;
                sd.WasLong = bullish;

                // Only emit on state change to avoid flooding.
                if (bullish && !wasLong)
                {
                    insights.Add(Insight.Price(
                        sym,
                        TimeSpan.FromDays(7),      // insight period
                        InsightDirection.Up,
                        0.02,                       // expected magnitude (2%)
                        0.6                         // confidence
                    ));
                }
                else if (!bullish && wasLong)
                {
                    insights.Add(Insight.Price(
                        sym,
                        TimeSpan.FromDays(7),
                        InsightDirection.Down,
                        0.02,
                        0.6
                    ));
                }
            }

            return insights;
        }

        private class SymbolData
        {
            public ExponentialMovingAverage Fast { get; set; }
            public ExponentialMovingAverage Slow { get; set; }
            public bool WasLong { get; set; }
        }
    }
}
