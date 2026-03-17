/*
 * E04 — EMA Crossover Strategy (Fixed Version)
 *
 * Strategy:
 *   - Symbol: BTCUSDT perpetual future
 *   - Timeframe: Daily bars
 *   - Signal: EMA(20) > EMA(50) → go long; EMA(20) < EMA(50) → flatten
 *   - Direction: Long-only
 *   - Sizing: 100% of portfolio (single position)
 *   - Warm-up: SetWarmUp(50, Resolution.Daily) + IsWarmingUp guard
 *
 * This is the corrected version of E04_compound_bug.cs with all three
 * bugs fixed: signal direction, warm-up, and position sizing.
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, EMA, SetWarmUp
 */

using System;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Orders;

namespace QuantTutorBench
{
    public class E04CompoundFixed : QCAlgorithm
    {
        // -- Configuration --
        private const int FastPeriod = 20;
        private const int SlowPeriod = 50;
        private const decimal InitialCash = 100_000m;

        // -- State --
        private Symbol _btc;
        private ExponentialMovingAverage _emaFast;
        private ExponentialMovingAverage _emaSlow;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var crypto = AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
            _btc = crypto.Symbol;

            _emaFast = EMA(_btc, FastPeriod, Resolution.Daily);
            _emaSlow = EMA(_btc, SlowPeriod, Resolution.Daily);

            // FIX 2: Proper warm-up so EMAs are seeded with pre-start history
            SetWarmUp(SlowPeriod, Resolution.Daily);

            Log($"E04 initialized: BTCUSDT with EMA({FastPeriod}/{SlowPeriod})");
        }

        public override void OnData(Slice data)
        {
            // FIX 2 (continued): Guard against processing during warm-up
            if (IsWarmingUp) return;
            if (!_emaFast.IsReady || !_emaSlow.IsReady) return;
            if (!data.ContainsKey(_btc)) return;

            var price = data[_btc].Close;
            var fastValue = _emaFast.Current.Value;
            var slowValue = _emaSlow.Current.Value;

            // FIX 1: Correct signal direction — bullish crossover (fast > slow)
            if (fastValue > slowValue && !Portfolio[_btc].Invested)
            {
                // FIX 3: Proper position sizing — 100% instead of 200%
                SetHoldings(_btc, 1.0m);
                Log($"LONG: price={price}, EMA({FastPeriod})={fastValue}, EMA({SlowPeriod})={slowValue}");
            }
            else if (fastValue < slowValue && Portfolio[_btc].Invested)
            {
                Liquidate(_btc, tag: "EMA crossover exit");
                Log($"FLAT: price={price}, EMA({FastPeriod})={fastValue}, EMA({SlowPeriod})={slowValue}");
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
            Log($"E04 complete — Final value: {Portfolio.TotalPortfolioValue}, " +
                $"Total trades: {TradeBuilder.ClosedTrades.Count}");
        }
    }
}
