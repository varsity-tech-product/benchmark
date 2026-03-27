/*
 * I06 — Multi-Signal Composite (Trend + Reversion + Carry)
 *
 * Strategy:
 *   - Universe: ~100 crypto-futures from universe.json
 *   - Three independent signal components:
 *       1. Trend:     SMA(20) vs SMA(60) crossover → +1 / -1
 *       2. Reversion: RSI(14) z-score from 50 → mean-reversion signal
 *       3. Carry:     Real 8h funding rate (daily avg), |rate|>0.01% → ±1
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
        private decimal TrendWeight = 0.40m;
        private decimal ReversionWeight = 0.30m;
        private decimal CarryWeight = 0.30m;
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
        }

        private readonly Dictionary<Symbol, SymbolData> _data = new();
        private DateTime _lastRebalance = DateTime.MinValue;

        // Funding rate data: symbol ticker → (date → daily avg funding rate)
        private readonly Dictionary<string, Dictionary<DateTime, decimal>> _fundingRates = new();

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            // Read weights from parameters (for sweep), with defaults matching hardcoded values
            var twStr = GetParameter("trend_weight");
            if (!string.IsNullOrEmpty(twStr)) TrendWeight = decimal.Parse(twStr);
            var rwStr = GetParameter("reversion_weight");
            if (!string.IsNullOrEmpty(rwStr)) ReversionWeight = decimal.Parse(rwStr);
            var cwStr = GetParameter("carry_weight");
            if (!string.IsNullOrEmpty(cwStr)) CarryWeight = decimal.Parse(cwStr);

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
                };

                _data[symbol] = sd;

                // Load funding rate data for this symbol
                LoadFundingData(ticker);
            }

            SetWarmUp(SmaPeriodSlow + 1, Resolution.Daily);

            Log($"I06 initialized with {_data.Count} symbols (of {tickers.Count} in universe), " +
                $"SMA({SmaPeriodFast}/{SmaPeriodSlow}) + RSI({RsiPeriod}) + funding, " +
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

                if (!sd.SmaFast.IsReady || !sd.SmaSlow.IsReady || !sd.Rsi.IsReady)
                    continue;

                if (!data.ContainsKey(sd.Symbol))
                    continue;

                // Signal 1: Trend (SMA crossover)
                decimal trendSignal = sd.SmaFast.Current.Value > sd.SmaSlow.Current.Value
                    ? 1.0m
                    : -1.0m;

                // Signal 2: Reversion (RSI distance from 50, inverted)
                var rsiDeviation = (50m - sd.Rsi.Current.Value) / 50m;
                decimal reversionSignal = Math.Max(-1m, Math.Min(1m, rsiDeviation * 2m));

                // Signal 3: Carry (real funding rate)
                // Positive funding → longs pay shorts → short bias (-1)
                // Negative funding → shorts pay longs → long bias (+1)
                decimal carrySignal = 0m;
                var ticker = sd.Symbol.Value.Replace(" ", "");
                if (_fundingRates.TryGetValue(ticker, out var rates) &&
                    rates.TryGetValue(Time.Date, out var fundingRate))
                {
                    if (fundingRate > 0.0001m)
                        carrySignal = -1.0m;
                    else if (fundingRate < -0.0001m)
                        carrySignal = 1.0m;
                }

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

        private void LoadFundingData(string ticker)
        {
            // Search for funding CSV in multiple locations
            var paths = new[]
            {
                $"/CustomAlgo/funding/{ticker}_funding.csv",
                $"/data/funding/{ticker}_funding.csv",
                Path.Combine(Globals.DataFolder, "funding", $"{ticker}_funding.csv"),
            };

            string csvPath = null;
            foreach (var p in paths)
            {
                if (File.Exists(p)) { csvPath = p; break; }
            }

            if (csvPath == null) return;

            var dailyRates = new Dictionary<DateTime, List<decimal>>();
            foreach (var line in File.ReadLines(csvPath).Skip(1))
            {
                var parts = line.Split(',');
                if (parts.Length < 2 || string.IsNullOrWhiteSpace(parts[0])) continue;

                if (!long.TryParse(parts[0].Trim(), out var tsMs)) continue;
                if (!decimal.TryParse(parts[1].Trim(), System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture, out var rate)) continue;

                var dt = DateTimeOffset.FromUnixTimeMilliseconds(tsMs).UtcDateTime.Date;
                if (!dailyRates.ContainsKey(dt))
                    dailyRates[dt] = new List<decimal>();
                dailyRates[dt].Add(rate);
            }

            // Average the 8-hour settlements into daily
            var avgRates = new Dictionary<DateTime, decimal>();
            foreach (var kvp in dailyRates)
            {
                avgRates[kvp.Key] = kvp.Value.Average();
            }

            _fundingRates[ticker] = avgRates;
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
