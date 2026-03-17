/*
 * X07 — Warm-Up Fix (EMA Crossover, Single Symbol)
 *
 * Strategy:
 *   - Symbol: BTCUSDT perpetual future
 *   - Timeframe: Daily bars
 *   - Signal: Price > EMA(20) AND EMA(20) > EMA(50) → go long 100%
 *             EMA(20) < EMA(50) → flatten
 *   - Direction: Long-only
 *   - Sizing: 100% of portfolio (single position)
 *
 * This is the FIXED version of the warm-up bug scenario.
 * FIX: SetWarmUp(50, Resolution.Daily) is called in Initialize(),
 *      and IsWarmingUp + IsReady guards are present in OnData().
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
    public class X07WarmupFixed : QCAlgorithm
    {
        // ── Configuration ──
        private const int EmaFastPeriod = 20;
        private const int EmaSlowPeriod = 50;
        private const decimal InitialCash = 100_000m;

        // ── State ──
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
            var crypto = AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
            _btc = crypto.Symbol;

            // Create EMA indicators
            _emaFast = EMA(_btc, EmaFastPeriod, Resolution.Daily);
            _emaSlow = EMA(_btc, EmaSlowPeriod, Resolution.Daily);

            // FIX: Warm up with enough bars for the slower indicator
            SetWarmUp(EmaSlowPeriod, Resolution.Daily);

            Log($"X07 initialized: BTCUSDT with EMA({EmaFastPeriod}/{EmaSlowPeriod}), warm-up enabled");
        }

        public override void OnData(Slice data)
        {
            // FIX: Skip data during warm-up period
            if (IsWarmingUp) return;

            // Also verify indicators are ready
            if (!_emaFast.IsReady || !_emaSlow.IsReady) return;

            if (!data.ContainsKey(_btc)) return;

            var price = data[_btc].Close;
            var emaFastValue = _emaFast.Current.Value;
            var emaSlowValue = _emaSlow.Current.Value;

            if (price > emaFastValue && emaFastValue > emaSlowValue
                && !Portfolio[_btc].Invested)
            {
                // Price above fast EMA and fast EMA above slow EMA — go long
                SetHoldings(_btc, 1.0m);
                Log($"LONG: price={price}, EMA({EmaFastPeriod})={emaFastValue}, " +
                    $"EMA({EmaSlowPeriod})={emaSlowValue}");
            }
            else if (emaFastValue < emaSlowValue && Portfolio[_btc].Invested)
            {
                // Fast EMA crossed below slow EMA — flatten
                Liquidate(symbol: _btc, tag: "EMA crossover exit");
                Log($"FLAT: EMA({EmaFastPeriod})={emaFastValue} < " +
                    $"EMA({EmaSlowPeriod})={emaSlowValue}");
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
