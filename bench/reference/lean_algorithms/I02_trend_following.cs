/*
 * I02 — Dual Moving Average Crossover (Trend Following)
 *
 * Strategy:
 *   - Universe: ~100 crypto-futures from universe.json
 *   - Timeframe: Daily bars
 *   - Signal: SMA(10) crosses above SMA(30) → go long; SMA(10) crosses below SMA(30) → flatten
 *   - Direction: Long-only
 *   - Sizing: Equal-weight across all active positions
 *   - No stop-loss (pure trend signal)
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, Market.Binance
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
    public class I02TrendFollowing : QCAlgorithm
    {
        // ── Configuration ──
        private const int FastPeriod = 10;
        private const int SlowPeriod = 30;
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private readonly Dictionary<Symbol, SimpleMovingAverage> _fastSma = new();
        private readonly Dictionary<Symbol, SimpleMovingAverage> _slowSma = new();
        private readonly HashSet<Symbol> _universe = new();

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            // Load universe from universe.json mounted at /data or /lean/Data
            var universeSymbols = LoadUniverse();

            foreach (var ticker in universeSymbols)
            {
                try
                {
                    var symbol = AddCryptoFuture(ticker, Resolution.Daily, Market.Binance).Symbol;
                    _universe.Add(symbol);

                    _fastSma[symbol] = SMA(symbol, FastPeriod, Resolution.Daily);
                    _slowSma[symbol] = SMA(symbol, SlowPeriod, Resolution.Daily);
                }
                catch (Exception ex)
                {
                    Log($"Skipping {ticker}: {ex.Message}");
                }
            }

            // Warm up indicators before trading
            SetWarmUp(SlowPeriod, Resolution.Daily);

            Log($"I02 initialized with {_universe.Count} symbols, SMA({FastPeriod}/{SlowPeriod})");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            var longSignals = new List<Symbol>();

            foreach (var symbol in _universe)
            {
                if (!_fastSma[symbol].IsReady || !_slowSma[symbol].IsReady)
                    continue;

                if (!data.ContainsKey(symbol))
                    continue;

                var fast = _fastSma[symbol].Current.Value;
                var slow = _slowSma[symbol].Current.Value;

                if (fast > slow)
                {
                    // Bullish crossover territory — want to be long
                    longSignals.Add(symbol);
                }
                else if (fast < slow && Portfolio[symbol].Invested)
                {
                    // Bearish crossover — flatten position
                    Liquidate(symbol, "SMA bearish crossover");
                }
            }

            // Equal-weight sizing across all active long signals
            if (longSignals.Count > 0)
            {
                var targetWeight = 1.0m / longSignals.Count;
                foreach (var symbol in longSignals)
                {
                    SetHoldings(symbol, targetWeight);
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

        private List<string> LoadUniverse()
        {
            // Try multiple paths where universe.json may be staged
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

            // Fallback: small default universe
            Log("WARNING: universe.json not found, using default 10-symbol universe");
            return new List<string>
            {
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
            };
        }
    }
}
