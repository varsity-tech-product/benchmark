/*
 * X07 — EMA Crossover Without Warm-Up (Debug Task)
 *
 * Strategy:
 *   - Symbol: BTCUSDT perpetual future
 *   - Timeframe: Daily bars
 *   - Signal: Price > EMA(20) > EMA(50) -> go long; Price < EMA(20) < EMA(50) -> flatten
 *   - Direction: Long-only
 *   - Sizing: 100% of portfolio (single position)
 *
 * BUG: No SetWarmUp() call and no IsWarmingUp guard.
 *   Without warm-up, the EMA indicators receive data starting from bar 1.
 *   The IsReady check prevents trading before 50 bars have been seen, but
 *   the EMA values at bar 50 are calculated from an incomplete history —
 *   the exponential memory starts from zero rather than from a proper
 *   seed built on pre-start historical data.  This causes the strategy
 *   to produce different (wrong) signals during the first ~100 bars
 *   compared to a correctly warmed-up version.
 *
 * FIX: Add SetWarmUp(50, Resolution.Daily) in Initialize() and
 *      add "if (IsWarmingUp) return;" at the top of OnData().
 *
 * LEAN API: QCAlgorithm, AddCrypto, EMA, SetWarmUp
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
    public class WarmupBug : QCAlgorithm
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

            // Subscribe to a single crypto future
            var crypto = AddCrypto("BTCUSDT", Resolution.Daily, Market.Binance);
            _btc = crypto.Symbol;

            // Create EMA indicators
            _emaFast = EMA(_btc, FastPeriod, Resolution.Daily);
            _emaSlow = EMA(_btc, SlowPeriod, Resolution.Daily);

            // BUG: Missing SetWarmUp(SlowPeriod, Resolution.Daily);
            // Without this, the EMAs start computing from the first bar of the
            // backtest window instead of being seeded with pre-start history.

            Log($"X07 initialized: BTCUSDT with EMA({FastPeriod}/{SlowPeriod})");
        }

        public override void OnData(Slice data)
        {
            // BUG: Missing "if (IsWarmingUp) return;" guard.
            // Even if SetWarmUp were added, without this guard the algorithm
            // would still trade on partially-initialized indicator values.

            // This check prevents trading before SlowPeriod bars, but the
            // EMA values are still wrong because they weren't seeded with
            // historical data from before the start date.
            if (!_emaFast.IsReady || !_emaSlow.IsReady) return;
            if (!data.ContainsKey(_btc)) return;

            var price = data[_btc].Close;
            var fastValue = _emaFast.Current.Value;
            var slowValue = _emaSlow.Current.Value;

            if (price > fastValue && fastValue > slowValue && !Portfolio[_btc].Invested)
            {
                // Bullish alignment: price > fast EMA > slow EMA -> go long
                SetHoldings(_btc, 1.0m);
                Log($"LONG: price={price}, EMA({FastPeriod})={fastValue}, EMA({SlowPeriod})={slowValue}");
            }
            else if (price < fastValue && fastValue < slowValue && Portfolio[_btc].Invested)
            {
                // Bearish alignment: price < fast EMA < slow EMA -> flatten
                Liquidate(_btc, tag: "Bearish crossover exit");
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
            Log($"X07 complete — Final value: {Portfolio.TotalPortfolioValue}, " +
                $"Total trades: {TradeBuilder.ClosedTrades.Count}");
        }
    }
}
