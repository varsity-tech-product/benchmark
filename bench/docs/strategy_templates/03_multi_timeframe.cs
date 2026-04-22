// Template 03: Multi-Timeframe Strategy (1h + 4h via Consolidation)
//
// Subscribes at hourly resolution and uses LEAN's Consolidate() to build
// 4-hour bars.  The 4h EMA determines trend direction; the 1h RSI
// provides entry timing.  Applied across multiple symbols.
//
// Key patterns demonstrated:
//   - Consolidate() to build higher-timeframe bars from lower-timeframe data
//   - Per-symbol state management with a Dictionary of SymbolState
//   - Manual indicator updates on consolidated bars
//   - Multi-timeframe signal logic (trend filter + entry trigger)
//   - Position sizing across multiple active positions

using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Consolidators;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using System;
using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Algorithm.CSharp
{
    public class Algorithm : QCAlgorithm
    {
        private const int EmaPeriod = 20;   // on 4h bars
        private const int RsiPeriod = 14;   // on 1h bars
        private const decimal RsiBuyThreshold = 35m;
        private const decimal RsiSellThreshold = 65m;
        private const int MaxPositions = 5;

        private Dictionary<Symbol, SymbolState> _state = new Dictionary<Symbol, SymbolState>();

        private class SymbolState
        {
            public ExponentialMovingAverage Ema4h { get; set; }
            public RelativeStrengthIndex Rsi1h { get; set; }
            public decimal Last4hClose { get; set; }
        }

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetCash(100000);

            var tickers = new[] {
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
            };

            foreach (var ticker in tickers)
            {
                // Subscribe at hourly resolution.
                var sym = AddCrypto(ticker, Resolution.Hour).Symbol;

                // Create per-symbol indicators.
                var ema4h = new ExponentialMovingAverage(EmaPeriod);
                var rsi1h = RSI(sym, RsiPeriod, MovingAverageType.Wilders, Resolution.Hour);

                _state[sym] = new SymbolState
                {
                    Ema4h = ema4h,
                    Rsi1h = rsi1h,
                };

                // Build 4h bars from 1h data using Consolidate().
                // The callback manually feeds the 4h EMA.
                // NOTE: explicit TradeBar type resolves the LEAN overload ambiguity.
                Consolidate<TradeBar>(sym, TimeSpan.FromHours(4), (TradeBar bar) =>
                {
                    if (_state.ContainsKey(bar.Symbol))
                    {
                        _state[bar.Symbol].Ema4h.Update(bar.EndTime, bar.Close);
                        _state[bar.Symbol].Last4hClose = bar.Close;
                    }
                });
            }

            // Warm up enough bars for both timeframes.
            // 4h EMA needs EmaPeriod * 4 hourly bars to be ready.
            SetWarmUp(EmaPeriod * 4 + 10, Resolution.Hour);
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            int openPositions = _state.Keys.Count(s => Portfolio[s].Invested);

            foreach (var kvp in _state)
            {
                var sym = kvp.Key;
                var st = kvp.Value;

                // Both indicators must be ready.
                if (!st.Ema4h.IsReady || !st.Rsi1h.IsReady) continue;
                if (Securities[sym].Price <= 0) continue;

                decimal price = Securities[sym].Price;
                bool uptrend = price > st.Ema4h.Current.Value;
                decimal rsi = st.Rsi1h.Current.Value;

                // Entry: uptrend on 4h + oversold on 1h
                if (uptrend && rsi < RsiBuyThreshold
                    && !Portfolio[sym].Invested
                    && openPositions < MaxPositions)
                {
                    decimal weight = 1.0m / MaxPositions;
                    SetHoldings(sym, weight);
                    openPositions++;
                    Log($"LONG {sym.Value} @ {price:F2}  EMA4h={st.Ema4h:F2}  RSI1h={rsi:F2}");
                }
                // Exit: trend reversal or overbought
                else if (Portfolio[sym].IsLong && (!uptrend || rsi > RsiSellThreshold))
                {
                    Liquidate(sym);
                    openPositions--;
                    Log($"EXIT {sym.Value} @ {price:F2}  EMA4h={st.Ema4h:F2}  RSI1h={rsi:F2}");
                }
            }
        }
    }
}
