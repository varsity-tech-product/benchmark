/*
 * I04 — Multi-Timeframe Momentum
 *
 * Strategy:
 *   - Universe: ~20 crypto-futures (top liquidity subset)
 *   - Timeframes: 4h bars (trend) + 1h bars (entry timing)
 *   - Signal:
 *       - 4h EMA(20) slope positive → bullish bias
 *       - 1h RSI(14) < 40 while 4h bullish → long entry (pullback buy)
 *       - 1h RSI(14) > 60 while in position → exit
 *       - 4h EMA slope turns negative → flatten regardless
 *   - Direction: Long-only
 *   - Sizing: Equal-weight across active positions
 *   - Uses Consolidate() for multi-timeframe bar aggregation
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, Consolidate(), Market.Binance
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
    public class I04MultiTimeframe : QCAlgorithm
    {
        // ── Configuration ──
        private const int EmaPeriod = 20;
        private const int RsiPeriod = 14;
        private const decimal RsiEntryThreshold = 40m;
        private const decimal RsiExitThreshold = 60m;
        private const decimal InitialCash = 100_000m;
        private const int MaxSymbols = 20;

        // ── Per-symbol state ──
        private class SymbolData
        {
            public Symbol Symbol;
            public ExponentialMovingAverage Ema4h;
            public RelativeStrengthIndex Rsi1h;
            public decimal PrevEma4h;
            public bool Ema4hBullish;
        }

        private readonly Dictionary<Symbol, SymbolData> _symbolData = new();

        public override void Initialize()
        {
            SetStartDate(2023, 1, 1);
            SetEndDate(2023, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var tickers = LoadUniverse().Take(MaxSymbols).ToList();

            foreach (var ticker in tickers)
            {
                var security = AddCryptoFuture(ticker, Resolution.Hour, Market.Binance);
                var symbol = security.Symbol;

                var sd = new SymbolData
                {
                    Symbol = symbol,
                    Ema4h = new ExponentialMovingAverage($"{ticker}_EMA4h", EmaPeriod),
                    Rsi1h = RSI(symbol, RsiPeriod, MovingAverageType.Wilders, Resolution.Hour),
                    PrevEma4h = 0m,
                    Ema4hBullish = false,
                };

                // Consolidate hourly bars into 4-hour bars for EMA
                Consolidate(symbol, TimeSpan.FromHours(4), (TradeBar bar) =>
                {
                    sd.Ema4h.Update(bar.EndTime, bar.Close);
                    if (sd.Ema4h.IsReady)
                    {
                        var currentEma = sd.Ema4h.Current.Value;
                        sd.Ema4hBullish = sd.PrevEma4h > 0 && currentEma > sd.PrevEma4h;
                        sd.PrevEma4h = currentEma;
                    }
                });

                _symbolData[symbol] = sd;
            }

            // Warm up: need EmaPeriod * 4h = 80 hours + RSI warm-up
            SetWarmUp(TimeSpan.FromHours(EmaPeriod * 4 + RsiPeriod));

            Log($"I04 initialized with {tickers.Count} symbols, " +
                $"4h EMA({EmaPeriod}) + 1h RSI({RsiPeriod})");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            var longCandidates = new List<Symbol>();

            foreach (var kvp in _symbolData)
            {
                var symbol = kvp.Key;
                var sd = kvp.Value;

                if (!sd.Rsi1h.IsReady || !sd.Ema4h.IsReady)
                    continue;

                if (!data.ContainsKey(symbol))
                    continue;

                var rsi = sd.Rsi1h.Current.Value;
                var holding = Portfolio[symbol];

                // ── Exit conditions ──
                if (holding.Invested)
                {
                    // Exit if 4h trend turns bearish OR RSI signals overbought
                    if (!sd.Ema4hBullish || rsi > RsiExitThreshold)
                    {
                        Liquidate(symbol, $"Exit: EMA4h_bull={sd.Ema4hBullish}, RSI={rsi:F1}");
                        continue;
                    }
                }

                // ── Entry conditions ──
                if (!holding.Invested && sd.Ema4hBullish && rsi < RsiEntryThreshold)
                {
                    longCandidates.Add(symbol);
                }
            }

            // Equal-weight sizing for new entries
            if (longCandidates.Count > 0)
            {
                // Count currently invested positions
                var currentPositions = _symbolData.Keys.Count(s => Portfolio[s].Invested);
                var totalAfterEntry = currentPositions + longCandidates.Count;

                if (totalAfterEntry > 0)
                {
                    var targetWeight = 1.0m / totalAfterEntry;
                    foreach (var symbol in longCandidates)
                    {
                        SetHoldings(symbol, targetWeight, false,
                            $"MTF entry: 4h EMA bullish, 1h RSI={_symbolData[symbol].Rsi1h.Current.Value:F1}");
                    }
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

            Log("WARNING: universe.json not found, using default universe");
            return new List<string>
            {
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
                "LINKUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
                "FILUSDT", "AAVEUSDT", "APTUSDT", "ARBUSDT", "OPUSDT"
            };
        }
    }
}
