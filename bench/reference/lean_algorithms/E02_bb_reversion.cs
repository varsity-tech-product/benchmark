/*
 * E02 — Bollinger Band Mean Reversion (Reference Algorithm)
 *
 * Strategy:
 *   - Symbol: BTCUSDT perpetual future
 *   - Timeframe: Daily bars
 *   - Signal: Close < lower band → go long; Close > middle band → flatten
 *   - Indicator: BollingerBands(20, 2.0, MovingAverageType.Simple)
 *   - Direction: Long-only
 *   - Sizing: 100% of portfolio (single position)
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, BollingerBands, SetWarmUp
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
    public class E02BbReversion : QCAlgorithm
    {
        // ── Configuration ──
        private const int BbPeriod = 20;
        private const decimal BbStdDev = 2.0m;
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private Symbol _btc;
        private BollingerBands _bb;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var crypto = AddCryptoFuture("BTCUSDT", Resolution.Daily, Market.Binance);
            _btc = crypto.Symbol;

            _bb = BB(_btc, BbPeriod, BbStdDev, MovingAverageType.Simple, Resolution.Daily);

            SetWarmUp(BbPeriod, Resolution.Daily);

            Log($"E02 initialized: BTCUSDT with BB({BbPeriod}, {BbStdDev})");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;
            if (!_bb.IsReady) return;
            if (!data.ContainsKey(_btc)) return;

            var price = data[_btc].Close;
            var lowerBand = _bb.LowerBand.Current.Value;
            var middleBand = _bb.MiddleBand.Current.Value;

            if (price < lowerBand && !Portfolio[_btc].Invested)
            {
                // Price below lower band — mean reversion entry
                SetHoldings(_btc, 1.0m);
                Log($"LONG: price={price}, LowerBand={lowerBand}, MiddleBand={middleBand}");
            }
            else if (price > middleBand && Portfolio[_btc].Invested)
            {
                // Price above middle band — mean reversion exit
                Liquidate(symbol: _btc, tag: "Price > middle band exit");
                Log($"FLAT: price={price}, LowerBand={lowerBand}, MiddleBand={middleBand}");
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
            Log($"E02 complete — Final value: {Portfolio.TotalPortfolioValue}, " +
                $"Total trades: {TradeBuilder.ClosedTrades.Count}");
        }
    }
}
