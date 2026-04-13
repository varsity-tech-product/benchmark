/*
 * E04 — EMA Crossover Strategy (Buggy Version)
 *
 * Strategy:
 *   - Symbol: BTCUSDT perpetual future
 *   - Timeframe: Daily bars
 *   - Signal: EMA(20) / EMA(50) crossover
 *   - Direction: Long-only
 *   - Sizing: SetHoldings
 *
 * This code compiles and runs, but produces poor results due to
 * multiple interacting bugs.
 *
 * LEAN API: QCAlgorithm, AddCrypto, EMA
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
    public class E04CompoundBug : QCAlgorithm
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

            // BUG 2: Missing SetWarmUp(SlowPeriod, Resolution.Daily);
            // Without warm-up, the EMAs start computing from the first bar
            // of the backtest window. The exponential memory begins from
            // zero rather than being seeded with pre-start historical data,
            // causing unreliable signals in the first ~50 bars.

            Log($"E04 initialized: BTCUSDT with EMA({FastPeriod}/{SlowPeriod})");
        }

        public override void OnData(Slice data)
        {
            // BUG 2 (continued): Missing "if (IsWarmingUp) return;" guard.
            // Even if SetWarmUp were added, without this guard the algorithm
            // would still process data during the warm-up period.

            if (!_emaFast.IsReady || !_emaSlow.IsReady) return;
            if (!data.ContainsKey(_btc)) return;

            var price = data[_btc].Close;
            var fastValue = _emaFast.Current.Value;
            var slowValue = _emaSlow.Current.Value;

            // BUG 1: Inverted signal — enters on BEARISH crossover (fast < slow)
            // instead of BULLISH crossover (fast > slow). This causes the
            // algorithm to go long when momentum is turning negative.
            if (fastValue < slowValue && !Portfolio[_btc].Invested)
            {
                // BUG 3: Excessive leverage — 200% position instead of 100%.
                // Combined with the inverted signal, this amplifies losses
                // from wrong-direction entries.
                SetHoldings(_btc, 2.0m);
                Log($"LONG: price={price}, EMA({FastPeriod})={fastValue}, EMA({SlowPeriod})={slowValue}");
            }
            else if (fastValue > slowValue && Portfolio[_btc].Invested)
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
