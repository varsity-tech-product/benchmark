/*
 * X08 — Order Type Fix (Momentum Strategy, Single Symbol)
 *
 * Strategy:
 *   - Symbol: BTCUSDT perpetual future
 *   - Timeframe: Daily bars
 *   - Signal: ROCP(20) > 5% → buy; ROCP(20) < -5% → sell
 *   - Direction: Long-only
 *   - Sizing: Fixed quantity via MarketOrder
 *
 * This is the FIXED version of the order-type bug scenario.
 * FIX: Uses MarketOrder instead of LimitOrder, ensuring fills
 *      execute immediately at the current market price.
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, ROCP, MarketOrder, SetWarmUp
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
    public class X08OrderTypeFixed : QCAlgorithm
    {
        // ── Configuration ──
        private const int RocpPeriod = 20;
        private const decimal MomentumThreshold = 0.05m;   // 5%
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private Symbol _btc;
        private RateOfChangePercent _rocp;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            // Subscribe to a single crypto future
            var crypto = AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
            _btc = crypto.Symbol;

            // Create the ROCP indicator
            _rocp = ROCP(_btc, RocpPeriod, Resolution.Daily);

            // Warm up so ROCP is ready on bar 1
            SetWarmUp(RocpPeriod, Resolution.Daily);

            Log($"X08 initialized: BTCUSDT with ROCP({RocpPeriod}), " +
                $"threshold=+/-{MomentumThreshold:P0}");
        }

        public override void OnData(Slice data)
        {
            // Skip data during warm-up period
            if (IsWarmingUp) return;

            if (!_rocp.IsReady) return;
            if (!data.ContainsKey(_btc)) return;

            var price = data[_btc].Close;
            var rocpValue = _rocp.Current.Value;

            if (rocpValue > MomentumThreshold && !Portfolio[_btc].Invested)
            {
                // Strong positive momentum — buy using MarketOrder
                var qty = Math.Floor(Portfolio.Cash / price);
                if (qty > 0)
                {
                    // FIX: Use MarketOrder instead of LimitOrder
                    MarketOrder(_btc, qty);
                    Log($"BUY: price={price}, ROCP={rocpValue:P2}, qty={qty}");
                }
            }
            else if (rocpValue < -MomentumThreshold && Portfolio[_btc].Invested)
            {
                // Strong negative momentum — sell
                Liquidate(symbol: _btc, tag: "Momentum exit");
                Log($"SELL: price={price}, ROCP={rocpValue:P2}");
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
            Log($"X08 complete — Final value: {Portfolio.TotalPortfolioValue}, " +
                $"Total trades: {TradeBuilder.ClosedTrades.Count}");
        }
    }
}
