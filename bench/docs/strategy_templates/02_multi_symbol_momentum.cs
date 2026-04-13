// Template 02: Multi-Symbol Momentum (ROCP Ranking)
//
// Subscribes to ~20 symbols via a loop, ranks them by 20-day rate of change,
// goes long the top 5 and short the bottom 5 with equal weight.
//
// Key patterns demonstrated:
//   - AddCrypto in a foreach loop with variable symbols (not string literals)
//   - Loading universe from /data/universe.json
//   - Per-symbol indicator management via Dictionary
//   - data.Keys iteration for accessing arrived bars
//   - Securities[sym].Price for current prices
//   - Monthly rebalance via time check

using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Indicators;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace QuantConnect.Algorithm.CSharp
{
    public class Algorithm : QCAlgorithm
    {
        private const int TopN = 5;
        private const int RocPeriod = 20;

        private List<Symbol> _symbols = new List<Symbol>();
        private Dictionary<Symbol, RateOfChangePercent> _rocp = new Dictionary<Symbol, RateOfChangePercent>();
        private DateTime _lastRebalance = DateTime.MinValue;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetCash(100000);

            // Load universe from the task data file.
            var universePath = "/data/universe.json";
            string[] tickers;
            if (File.Exists(universePath))
            {
                var json = File.ReadAllText(universePath);
                // Simple JSON array parse — each element is a ticker string.
                tickers = json.Trim('[', ']', ' ', '\n', '\r')
                    .Split(',')
                    .Select(s => s.Trim().Trim('"'))
                    .Where(s => !string.IsNullOrEmpty(s))
                    .Take(20)   // cap to 20 for performance
                    .ToArray();
            }
            else
            {
                // Fallback: hardcoded top-tier symbols.
                tickers = new[] {
                    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
                    "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT",
                    "AAVEUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
                };
            }

            // Subscribe via loop — the harness shadow method intercepts each call.
            foreach (var ticker in tickers)
            {
                var sym = AddCrypto(ticker, Resolution.Daily).Symbol;
                _symbols.Add(sym);
                _rocp[sym] = ROCP(sym, RocPeriod, Resolution.Daily);
            }

            SetWarmUp(RocPeriod + 5, Resolution.Daily);
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            // Rebalance monthly.
            if (Time.Month == _lastRebalance.Month && Time.Year == _lastRebalance.Year)
                return;
            _lastRebalance = Time;

            // Rank symbols by ROCP (descending).
            var ranked = _symbols
                .Where(s => _rocp[s].IsReady && Securities[s].Price > 0)
                .OrderByDescending(s => _rocp[s].Current.Value)
                .ToList();

            if (ranked.Count < TopN * 2) return;

            var longs = new HashSet<Symbol>(ranked.Take(TopN));
            var shorts = new HashSet<Symbol>(ranked.Skip(ranked.Count - TopN));

            // Flatten positions not in the new portfolio.
            foreach (var sym in _symbols)
            {
                if (Portfolio[sym].Invested && !longs.Contains(sym) && !shorts.Contains(sym))
                    Liquidate(sym);
            }

            // Equal-weight longs and shorts.
            decimal weight = 1.0m / TopN;
            foreach (var sym in longs)
                SetHoldings(sym, weight);
            foreach (var sym in shorts)
                SetHoldings(sym, -weight);

            Log($"Rebalance: {longs.Count} longs, {shorts.Count} shorts, "
                + $"top={ranked.First().Value} bottom={ranked.Last().Value}");
        }
    }
}
