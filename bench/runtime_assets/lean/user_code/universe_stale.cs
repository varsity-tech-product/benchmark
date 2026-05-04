/*
 * X10 — Stale Universe / Survivorship Bias
 *
 * Strategy:
 *   - Universe: 30 crypto-futures from universe.json (static subscription)
 *   - Timeframe: Daily bars
 *   - Signal: 20-day momentum (ROCP) long/short ranking
 *   - Long top 5 by momentum, short bottom 5
 *   - Equal weight: +/-0.10 per position
 *
 * BUG 1 (Survivorship Bias): All 30 symbols are subscribed statically in
 *        Initialize() without checking whether each symbol was actually
 *        listed/trading at the backtest start date (2022-01-01). Some symbols
 *        in the universe may have been listed later (e.g., mid-2023), meaning
 *        the algorithm implicitly assumes they existed from the start. This is
 *        survivorship bias — the universe is chosen with hindsight knowledge
 *        of which symbols survived/exist today.
 *
 * BUG 2 (Look-Ahead Volume Ranking): The algorithm computes average volume
 *        over ALL available data (including future bars) when deciding which
 *        symbols to track. The _volumeAccumulator sums volume from the very
 *        first bar and divides by total bar count, meaning early-period
 *        rankings incorporate volume data from the future. A correct approach
 *        would use a rolling/trailing window (e.g., 20-day average volume).
 *
 * LEAN API: Classic QCAlgorithm, RateOfChangePercent, SetHoldings
 */

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

namespace QuantTutorBench
{
    public class UniverseStale : QCAlgorithm
    {
        // ── Configuration ──
        private const int MaxSymbols = 30;
        private const int MomentumPeriod = 20;
        private const int TopN = 5;
        private const decimal PositionSize = 0.10m;
        private const decimal InitialCash = 100_000m;

        // Indicators keyed by Symbol
        private readonly Dictionary<Symbol, RateOfChangePercent> _momentum = new();

        // BUG 2: Cumulative volume accumulators — these sum ALL volume from
        // the start of the backtest, so the "average" incorporates future data
        // when used for ranking in earlier periods.
        private readonly Dictionary<Symbol, decimal> _volumeAccumulator = new();
        private readonly Dictionary<Symbol, int> _volumeBarCount = new();

        // Track LEAN Symbol objects for rebalancing
        private readonly List<Symbol> _symbols = new();

        // Rebalance tracking
        private DateTime _lastRebalance = DateTime.MinValue;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var tickers = LoadUniverse();
            var subset = tickers.Take(MaxSymbols).ToList();

            // BUG 1: Static subscription of all symbols without checking listing dates.
            // Some of these symbols may not have existed on 2022-01-01. By subscribing
            // them here, we assume they were all available from the start, which is
            // survivorship bias. A correct approach would use AddUniverseSelection or
            // check each symbol's listing date before subscribing.
            foreach (var ticker in subset)
            {
                var security = AddCrypto(ticker, Resolution.Daily, Market.Binance);
                var symbol = security.Symbol;

                _symbols.Add(symbol);
                _momentum[symbol] = ROCP(symbol, MomentumPeriod, Resolution.Daily);
                _volumeAccumulator[symbol] = 0m;
                _volumeBarCount[symbol] = 0;
            }

            SetWarmUp(MomentumPeriod + 5, Resolution.Daily);

            Log($"X10 UniverseStale initialized with {subset.Count} symbols, " +
                $"ROCP({MomentumPeriod}) long/short top/bottom {TopN}");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            // Update cumulative volume for each symbol (BUG 2: includes all bars,
            // not just a trailing window — early-period rankings use future volume data)
            foreach (var symbol in _symbols)
            {
                if (data.ContainsKey(symbol) && data[symbol] != null)
                {
                    var bar = data[symbol] as TradeBar;
                    if (bar != null)
                    {
                        _volumeAccumulator[symbol] += bar.Volume;
                        _volumeBarCount[symbol] += 1;
                    }
                }
            }

            // Daily rebalance
            if (Time.Date <= _lastRebalance.Date) return;
            _lastRebalance = Time;

            // Filter to symbols with ready momentum indicators and sufficient volume data
            var ready = _symbols
                .Where(s => _momentum[s].IsReady && _volumeBarCount[s] > 0)
                .ToList();

            if (ready.Count < TopN * 2)
            {
                Log($"Only {ready.Count} symbols ready, need at least {TopN * 2} — skipping rebalance");
                return;
            }

            // Rank by momentum (ROCP value)
            var ranked = ready
                .OrderByDescending(s => _momentum[s].Current.Value)
                .ToList();

            // Long top N, short bottom N
            var longs = ranked.Take(TopN).ToHashSet();
            var shorts = ranked.TakeLast(TopN).ToHashSet();
            var active = longs.Union(shorts).ToHashSet();

            // Liquidate positions not in the new active set
            foreach (var symbol in _symbols)
            {
                if (!active.Contains(symbol) && Portfolio[symbol].Invested)
                {
                    Liquidate(symbol);
                }
            }

            // Set target allocations
            foreach (var symbol in longs)
            {
                SetHoldings(symbol, PositionSize);
            }
            foreach (var symbol in shorts)
            {
                SetHoldings(symbol, -PositionSize);
            }
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status == OrderStatus.Filled)
            {
                // Log average volume for context (BUG 2: this average includes
                // future data, so it's not a valid point-in-time metric)
                var avgVol = _volumeBarCount.ContainsKey(orderEvent.Symbol) && _volumeBarCount[orderEvent.Symbol] > 0
                    ? _volumeAccumulator[orderEvent.Symbol] / _volumeBarCount[orderEvent.Symbol]
                    : 0m;

                Log($"TRADE: {orderEvent.Symbol} | " +
                    $"Direction={orderEvent.Direction} | " +
                    $"Qty={orderEvent.FillQuantity} | " +
                    $"Price={orderEvent.FillPrice} | " +
                    $"AvgVolume={avgVol:F0}");
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
}
