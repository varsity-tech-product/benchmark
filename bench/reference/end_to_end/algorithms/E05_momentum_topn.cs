/*
 * E05 — Momentum Top-N (Reference Algorithm)
 *
 * Strategy:
 *   - Universe: ~20 tier2 crypto-futures from universe.json
 *   - Timeframe: Daily bars
 *   - Signal: ROCP(20) — 20-day rate of change percentage
 *   - Selection: Rank by ROCP, long top-5 at equal weight (0.20 each)
 *   - Rebalancing: Daily
 *   - Direction: Long-only
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, ROCP, SetWarmUp
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
    public class E05MomentumTopn : QCAlgorithm
    {
        // ── Configuration ──
        private const int MaxSymbols = 20;
        private const int RocpPeriod = 20;
        private const int TopN = 5;
        private const decimal PositionWeight = 0.20m;
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private readonly Dictionary<Symbol, RateOfChangePercent> _rocp = new();

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
                var crypto = AddCryptoFuture(ticker, Resolution.Daily, Market.Binance);
                _rocp[crypto.Symbol] = ROCP(crypto.Symbol, RocpPeriod, Resolution.Daily);
            }

            SetWarmUp(RocpPeriod, Resolution.Daily);

            Log($"E05 initialized with {subset.Count} symbols, ROCP({RocpPeriod}), top-{TopN}");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            // Collect current ROCP values for symbols with data
            var rankings = new List<(Symbol sym, decimal rocp)>();
            foreach (var kvp in _rocp)
            {
                if (!kvp.Value.IsReady) continue;
                if (!data.ContainsKey(kvp.Key)) continue;
                rankings.Add((kvp.Key, kvp.Value.Current.Value));
            }

            if (rankings.Count == 0) return;

            // Rank by ROCP descending, select top-N
            var topSymbols = rankings
                .OrderByDescending(r => r.rocp)
                .Take(TopN)
                .Select(r => r.sym)
                .ToHashSet();

            // Rebalance: long top-N at equal weight, liquidate rest
            foreach (var kvp in _rocp)
            {
                if (topSymbols.Contains(kvp.Key))
                {
                    if (!Portfolio[kvp.Key].Invested ||
                        Math.Abs(Portfolio[kvp.Key].HoldingsValue / Portfolio.TotalPortfolioValue - PositionWeight) > 0.05m)
                    {
                        SetHoldings(kvp.Key, PositionWeight);
                    }
                }
                else if (Portfolio[kvp.Key].Invested)
                {
                    Liquidate(kvp.Key, tag: "Not in top-N");
                }
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

        public override void OnEndOfAlgorithm()
        {
            Log($"E05 complete — Final value: {Portfolio.TotalPortfolioValue}, " +
                $"Total trades: {TradeBuilder.ClosedTrades.Count}");
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
