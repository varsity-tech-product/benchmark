/*
 * X10 — Stale Universe Fixed (Listing Date + Volume Filtering)
 *
 * Bug context:
 *   The buggy version subscribes to all symbols from universe.json without
 *   checking whether they were actually listed at the algorithm start date.
 *   Symbols listed after 2022-01-01 produce NaN/zero price data during early
 *   bars, corrupting indicator calculations and momentum rankings. Additionally,
 *   the buggy version uses a stale end-of-period volume snapshot rather than
 *   a trailing average, causing look-ahead bias and unstable rankings.
 *
 * Fixes:
 *   1. Checks a hardcoded listing-date dictionary before subscribing. Symbols
 *      listed after the algorithm start date are skipped.
 *   2. Uses a trailing 20-day SMA of volume for filtering instead of a
 *      single end-of-period snapshot.
 *
 * Strategy:
 *   - Universe: First 30 symbols from universe.json (after listing filter)
 *   - Timeframe: Daily bars
 *   - Classic QCAlgorithm (NOT framework)
 *   - Daily rebalance in OnData
 *   - 20-day ROCP for momentum ranking
 *   - Long top 5, short bottom 5
 *   - Equal weight ±0.10 per position
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, ROCP, SMA, SetHoldings
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
    public class X10UniverseStaleFixed : QCAlgorithm
    {
        // ── Configuration ──
        private const int MaxSymbols = 30;
        private const int MomentumPeriod = 20;
        private const int VolumePeriod = 20;
        private const int TopN = 5;
        private const int BottomN = 5;
        private const decimal PositionWeight = 0.10m;
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private readonly Dictionary<Symbol, RateOfChangePercent> _rocp = new();
        private readonly Dictionary<Symbol, SimpleMovingAverage> _volumeSma = new();
        private readonly HashSet<Symbol> _universe = new();
        private DateTime _lastRebalance = DateTime.MinValue;

        /// <summary>
        /// Symbols listed on Binance Futures AFTER 2022-01-01.
        /// These are excluded from the universe at initialization to avoid
        /// NaN/zero price data corrupting indicators during early bars.
        /// Most major coins (BTC, ETH, BNB, SOL, XRP, ADA, DOGE, etc.)
        /// were listed well before 2022.
        /// </summary>
        private static readonly Dictionary<string, DateTime> LateListings = new()
        {
            { "APTUSDT",         new DateTime(2022, 10, 19) },
            { "ARBUSDT",         new DateTime(2023,  3, 23) },
            { "OPUSDT",          new DateTime(2022,  5, 31) },
            { "0GUSDT",          new DateTime(2024,  6, 27) },
            { "1000000BOBUSDT",  new DateTime(2024, 11, 15) },
            { "1000000MOGUSDT",  new DateTime(2024,  8, 29) },
            { "1000BONKUSDT",    new DateTime(2023, 11, 22) },
            { "1000CATUSDT",     new DateTime(2024, 12, 16) },
            { "1000CHEEMSUSDT",  new DateTime(2024, 11, 29) },
            { "1000PEPEUSDT",    new DateTime(2023,  5,  5) },
            { "1000RATSUSDT",    new DateTime(2024,  3,  5) },
            { "1000SATSUSDT",    new DateTime(2023, 12, 13) },
            { "1000WHYUSDT",     new DateTime(2024,  8, 20) },
        };

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var tickers = LoadUniverse();
            var candidates = tickers.Take(MaxSymbols).ToList();

            int skipped = 0;

            foreach (var ticker in candidates)
            {
                // FIX 1: Skip symbols listed after algorithm start date
                if (LateListings.TryGetValue(ticker, out var listingDate))
                {
                    if (listingDate > StartDate)
                    {
                        Log($"SKIP: {ticker} listed {listingDate:yyyy-MM-dd}, after start {StartDate:yyyy-MM-dd}");
                        skipped++;
                        continue;
                    }
                }

                var symbol = AddCryptoFuture(ticker, Resolution.Daily, Market.Binance).Symbol;
                _universe.Add(symbol);

                // Momentum indicator: 20-day rate of change percent
                _rocp[symbol] = ROCP(symbol, MomentumPeriod, Resolution.Daily);

                // FIX 2: Trailing 20-day volume SMA (not stale end-of-period snapshot)
                _volumeSma[symbol] = new SimpleMovingAverage(VolumePeriod);
            }

            SetWarmUp(MomentumPeriod + 5, Resolution.Daily);

            Log($"X10 FIXED initialized with {_universe.Count} symbols " +
                $"(skipped {skipped} late-listed), " +
                $"momentum ROCP({MomentumPeriod}), " +
                $"volume SMA({VolumePeriod}), " +
                $"long top-{TopN} / short bottom-{BottomN}, " +
                $"weight ±{PositionWeight}");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            // Daily rebalance — only once per day
            if (data.Time.Date <= _lastRebalance.Date) return;
            _lastRebalance = data.Time;

            // Update volume SMAs manually with daily volume
            foreach (var symbol in _universe)
            {
                if (data.Bars.ContainsKey(symbol))
                {
                    _volumeSma[symbol].Update(data.Time, data.Bars[symbol].Volume);
                }
            }

            // Collect symbols with ready indicators and non-zero trailing volume
            var scored = new List<(Symbol symbol, decimal rocp)>();

            foreach (var symbol in _universe)
            {
                if (!_rocp[symbol].IsReady) continue;
                if (!_volumeSma[symbol].IsReady) continue;
                if (!data.ContainsKey(symbol)) continue;

                // Filter: require minimum trailing volume
                if (_volumeSma[symbol].Current.Value <= 0) continue;

                scored.Add((symbol, _rocp[symbol].Current.Value));
            }

            if (scored.Count < TopN + BottomN)
                return;

            // Rank by momentum
            var ranked = scored.OrderByDescending(x => x.rocp).ToList();
            var longSymbols = ranked.Take(TopN).Select(x => x.symbol).ToHashSet();
            var shortSymbols = ranked.Skip(ranked.Count - BottomN).Select(x => x.symbol).ToHashSet();
            var targetSymbols = longSymbols.Union(shortSymbols).ToHashSet();

            // Flatten positions no longer in target
            foreach (var symbol in _universe)
            {
                if (Portfolio[symbol].Invested && !targetSymbols.Contains(symbol))
                {
                    SetHoldings(symbol, 0m);
                }
            }

            // Set target positions
            foreach (var symbol in longSymbols)
            {
                SetHoldings(symbol, PositionWeight);
            }
            foreach (var symbol in shortSymbols)
            {
                SetHoldings(symbol, -PositionWeight);
            }
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
}
