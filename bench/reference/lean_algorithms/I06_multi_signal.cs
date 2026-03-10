/*
 * I06 — Multi-Signal Composite (Trend + Reversion + Carry)
 *
 * Strategy:
 *   - Universe: ~100 crypto-futures from universe.json
 *   - Three independent signal components:
 *       1. Trend:     SMA(20) vs SMA(60) crossover → +1 / -1
 *       2. Reversion: RSI(14) z-score from 50 → mean-reversion signal
 *       3. Carry:     Funding rate proxy (1d momentum as carry stand-in) → +1 / -1
 *   - Composite: weighted sum of signals (0.40 trend + 0.30 reversion + 0.30 carry)
 *   - Cross-sectional sizing: rank composite scores, go long top quintile,
 *     short bottom quintile, size proportional to |composite|
 *   - Rebalance: Daily
 *   - Max positions: 20 long + 20 short
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
    public class I06MultiSignal : QCAlgorithm
    {
        // ── Configuration ──
        private const int SmaPeriodFast = 20;
        private const int SmaPeriodSlow = 60;
        private const int RsiPeriod = 14;
        private const decimal TrendWeight = 0.40m;
        private const decimal ReversionWeight = 0.30m;
        private const decimal CarryWeight = 0.30m;
        private const int MaxLongPositions = 20;
        private const int MaxShortPositions = 20;
        private const decimal InitialCash = 100_000m;

        // ── Per-symbol indicators ──
        private class SymbolData
        {
            public Symbol Symbol;
            public SimpleMovingAverage SmaFast;
            public SimpleMovingAverage SmaSlow;
            public RelativeStrengthIndex Rsi;
            public RateOfChangePercent Momentum1d;
        }

        private readonly Dictionary<Symbol, SymbolData> _data = new();
        private DateTime _lastRebalance = DateTime.MinValue;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var tickers = LoadUniverse();

            foreach (var ticker in tickers)
            {
                var symbol = AddCryptoFuture(ticker, Resolution.Daily, Market.Binance).Symbol;

                var sd = new SymbolData
                {
                    Symbol = symbol,
                    SmaFast = SMA(symbol, SmaPeriodFast, Resolution.Daily),
                    SmaSlow = SMA(symbol, SmaPeriodSlow, Resolution.Daily),
                    Rsi = RSI(symbol, RsiPeriod, MovingAverageType.Wilders, Resolution.Daily),
                    Momentum1d = ROCP(symbol, 1, Resolution.Daily),
                };

                _data[symbol] = sd;
            }

            SetWarmUp(SmaPeriodSlow + 1, Resolution.Daily);

            Log($"I06 initialized with {_data.Count} symbols (of {tickers.Count} in universe), " +
                $"SMA({SmaPeriodFast}/{SmaPeriodSlow}) + RSI({RsiPeriod}) + ROCP(1), " +
                $"weights=({TrendWeight}/{ReversionWeight}/{CarryWeight})");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            // Daily rebalance only
            if (Time.Date == _lastRebalance) return;
            _lastRebalance = Time.Date;

            // ── Compute composite signal for each symbol ──
            var signals = new List<(Symbol symbol, decimal composite)>();

            foreach (var kvp in _data)
            {
                var sd = kvp.Value;

                if (!sd.SmaFast.IsReady || !sd.SmaSlow.IsReady ||
                    !sd.Rsi.IsReady || !sd.Momentum1d.IsReady)
                    continue;

                if (!data.ContainsKey(sd.Symbol))
                    continue;

                // Signal 1: Trend (SMA crossover)
                decimal trendSignal = sd.SmaFast.Current.Value > sd.SmaSlow.Current.Value
                    ? 1.0m
                    : -1.0m;

                // Signal 2: Reversion (RSI distance from 50, inverted)
                // RSI < 50 → positive reversion signal (expect bounce)
                // RSI > 50 → negative reversion signal (expect pullback)
                var rsiDeviation = (50m - sd.Rsi.Current.Value) / 50m;
                decimal reversionSignal = Math.Max(-1m, Math.Min(1m, rsiDeviation * 2m));

                // Signal 3: Carry proxy (1-day momentum as funding rate stand-in)
                decimal carrySignal = sd.Momentum1d.Current.Value > 0 ? 1.0m : -1.0m;

                // Composite weighted sum
                var composite = TrendWeight * trendSignal
                              + ReversionWeight * reversionSignal
                              + CarryWeight * carrySignal;

                signals.Add((sd.Symbol, composite));
            }

            if (signals.Count == 0) return;

            // ── Cross-sectional ranking ──
            var ranked = signals.OrderByDescending(s => s.composite).ToList();
            int quintileSize = Math.Max(1, ranked.Count / 5);

            // Top quintile → long, bottom quintile → short
            var longSymbols = ranked.Take(Math.Min(quintileSize, MaxLongPositions)).ToList();
            var shortSymbols = ranked.Skip(ranked.Count - Math.Min(quintileSize, MaxShortPositions)).ToList();

            // Build target portfolio
            var targetWeights = new Dictionary<Symbol, decimal>();

            // Size proportional to |composite| within each side
            decimal longTotalAbs = longSymbols.Sum(s => Math.Abs(s.composite));
            decimal shortTotalAbs = shortSymbols.Sum(s => Math.Abs(s.composite));

            if (longTotalAbs > 0)
            {
                foreach (var (symbol, composite) in longSymbols)
                {
                    // 50% of capital to long side, sized by signal strength
                    targetWeights[symbol] = 0.5m * Math.Abs(composite) / longTotalAbs;
                }
            }

            if (shortTotalAbs > 0)
            {
                foreach (var (symbol, composite) in shortSymbols)
                {
                    // 50% of capital to short side, sized by signal strength
                    targetWeights[symbol] = -0.5m * Math.Abs(composite) / shortTotalAbs;
                }
            }

            // ── Rebalance: liquidate stale, set new targets ──
            // Flatten positions not in the new target
            foreach (var kvp in _data)
            {
                if (Portfolio[kvp.Key].Invested && !targetWeights.ContainsKey(kvp.Key))
                {
                    Liquidate(kvp.Key, "Not in target portfolio");
                }
            }

            // Set target holdings
            foreach (var kvp in targetWeights)
            {
                SetHoldings(kvp.Key, kvp.Value);
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
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
            };
        }
    }
}
