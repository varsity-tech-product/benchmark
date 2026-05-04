/*
 * X08 — Momentum Strategy with Wrong Order Type (Debug Task)
 *
 * Strategy:
 *   - Symbol: BTCUSDT perpetual future
 *   - Timeframe: Daily bars
 *   - Signal: ROCP(20) > 5% -> buy; ROCP(20) < -5% -> sell
 *   - Direction: Long-only
 *   - Sizing: 100% of portfolio (single position)
 *
 * BUG: Uses LimitOrder at current price instead of MarketOrder.
 *   In a trending market, the limit order is placed at today's closing
 *   price but by the next bar the price has already moved.  For a
 *   momentum-up signal the price typically moves higher, so a buy-limit
 *   at the old price never fills.  Similarly, sell-limit orders during
 *   downtrends are placed above where the market trades next.
 *   The result: very few (or zero) fills and the strategy sits idle.
 *
 * FIX: Replace LimitOrder(..., Securities[_btc].Price) with
 *      MarketOrder(_btc, qty) or use SetHoldings(_btc, 1.0m).
 *
 * LEAN API: QCAlgorithm, AddCrypto, ROCP, LimitOrder, MarketOrder
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
    public class OrderTypeBug : QCAlgorithm
    {
        // -- Configuration --
        private const int RocPeriod = 20;
        private const decimal MomentumThreshold = 0.05m;  // 5%
        private const decimal InitialCash = 100_000m;

        // -- State --
        private Symbol _btc;
        private RateOfChangePercent _rocp;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            // Subscribe to a single crypto future
            var crypto = AddCrypto("BTCUSDT", Resolution.Daily, Market.Binance);
            _btc = crypto.Symbol;

            // Create the ROCP indicator
            _rocp = ROCP(_btc, RocPeriod, Resolution.Daily);

            // Proper warm-up so ROCP is ready on bar 1
            SetWarmUp(RocPeriod, Resolution.Daily);

            Log($"X08 initialized: BTCUSDT with ROCP({RocPeriod}), threshold={MomentumThreshold:P0}");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;
            if (!_rocp.IsReady) return;
            if (!data.ContainsKey(_btc)) return;

            var momentum = _rocp.Current.Value;

            if (momentum > MomentumThreshold && !Portfolio[_btc].Invested)
            {
                // Strong upward momentum -> buy
                var price = Securities[_btc].Price;
                var qty = Math.Floor(Portfolio.TotalPortfolioValue / price);
                if (qty > 0)
                {
                    // BUG: Using LimitOrder at current price instead of MarketOrder.
                    // In a momentum-up regime, by the time this limit order could fill
                    // on the next bar, price has typically moved higher and the buy
                    // limit sits below the market, never getting hit.
                    LimitOrder(_btc, qty, price);
                    Log($"BUY LIMIT (BUG): momentum={momentum:P2}, price={price}, qty={qty}");
                }
            }
            else if (momentum < -MomentumThreshold && Portfolio[_btc].Invested)
            {
                // Strong downward momentum -> sell to flatten
                var holdings = Portfolio[_btc].Quantity;
                var price = Securities[_btc].Price;

                // BUG: Using LimitOrder at current price instead of MarketOrder.
                // In a momentum-down regime, price drops further by next bar,
                // so the sell-limit above the market never fills either.
                LimitOrder(_btc, -holdings, price);
                Log($"SELL LIMIT (BUG): momentum={momentum:P2}, price={price}, qty={-holdings}");
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
            else if (orderEvent.Status == OrderStatus.Canceled)
            {
                Log($"CANCELED: {orderEvent.Symbol} | Tag={orderEvent.Message}");
            }
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"X08 complete — Final value: {Portfolio.TotalPortfolioValue}, " +
                $"Total trades: {TradeBuilder.ClosedTrades.Count}");
        }
    }
}
