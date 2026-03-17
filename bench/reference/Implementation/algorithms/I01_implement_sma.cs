/*
 * I01 — Simple Moving Average Trend Filter (Single Symbol)
 *
 * Strategy:
 *   - Symbol: BTCUSDT perpetual future
 *   - Timeframe: Daily bars
 *   - Signal: Price > SMA(20) → go long 100%; Price < SMA(20) → flatten
 *   - Direction: Long-only
 *   - Sizing: 100% of portfolio (single position)
 *   - No stop-loss (pure SMA filter)
 *
 * This is the simplest LEAN strategy — a single-symbol "hello world"
 * that validates the end-to-end backtest pipeline before scaling to
 * multi-symbol strategies in I02+.
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, SMA, SetWarmUp
 */

using System;
using System.IO;
using Newtonsoft.Json;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Orders;

namespace QuantTutorBench
{
    public class I01ImplementSma : QCAlgorithm
    {
        // ── Configuration ──
        private const int SmaPeriod = 20;
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private Symbol _btc;
        private SimpleMovingAverage _sma;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            // Subscribe to a single crypto future
            var crypto = AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
            _btc = crypto.Symbol;

            // Create the SMA indicator
            _sma = SMA(_btc, SmaPeriod, Resolution.Daily);

            // Warm up so SMA is ready on bar 1
            SetWarmUp(SmaPeriod, Resolution.Daily);

            Log($"I01 initialized: BTCUSDT with SMA({SmaPeriod})");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;
            if (!_sma.IsReady) return;
            if (!data.ContainsKey(_btc)) return;

            var price = data[_btc].Close;
            var smaValue = _sma.Current.Value;

            if (price > smaValue && !Portfolio[_btc].Invested)
            {
                // Price above SMA — go long
                SetHoldings(_btc, 1.0m);
                Log($"LONG: price={price}, SMA={smaValue}");
            }
            else if (price < smaValue && Portfolio[_btc].Invested)
            {
                // Price below SMA — flatten
                Liquidate(symbol: _btc, tag: "Price < SMA exit");
                Log($"FLAT: price={price}, SMA={smaValue}");
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
            Log($"I01 complete — Final value: {Portfolio.TotalPortfolioValue}, " +
                $"Total trades: {TradeBuilder.ClosedTrades.Count}");
        }
    }
}
