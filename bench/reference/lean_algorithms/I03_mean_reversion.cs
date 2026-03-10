/*
 * I03 — RSI Mean Reversion
 *
 * Strategy:
 *   - Universe: ~100 crypto-futures from universe.json
 *   - Timeframe: Daily bars
 *   - Signal: RSI(14) for entry/exit
 *       - Long entry:  RSI < 30
 *       - Long exit:   RSI > 50
 *       - Short entry: RSI > 70
 *       - Short exit:  RSI < 50
 *   - Stop-loss: 5% trailing from entry
 *   - Sizing: 2% of portfolio per position (max ~50 positions)
 *   - Direction: Long + Short
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, Market.Binance
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
    public class I03MeanReversion : QCAlgorithm
    {
        // ── Configuration ──
        private const int RsiPeriod = 14;
        private const decimal RsiLongEntry = 30m;
        private const decimal RsiLongExit = 50m;
        private const decimal RsiShortEntry = 70m;
        private const decimal RsiShortExit = 50m;
        private const decimal StopLossPct = 0.05m;
        private const decimal PositionSizePct = 0.05m;
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private readonly Dictionary<Symbol, RelativeStrengthIndex> _rsi = new();
        private readonly Dictionary<Symbol, decimal> _entryPrice = new();
        private readonly HashSet<Symbol> _universe = new();

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var universeSymbols = LoadUniverse();

            foreach (var ticker in universeSymbols)
            {
                var symbol = AddCryptoFuture(ticker, Resolution.Daily, Market.Binance).Symbol;
                _universe.Add(symbol);

                _rsi[symbol] = RSI(symbol, RsiPeriod, MovingAverageType.Wilders, Resolution.Daily);
            }

            SetWarmUp(RsiPeriod + 1, Resolution.Daily);

            Log($"I03 initialized with {_universe.Count} symbols, RSI({RsiPeriod}), " +
                $"thresholds={RsiLongEntry}/{RsiLongExit}/{RsiShortEntry}/{RsiShortExit}, " +
                $"stop={StopLossPct:P0}");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            foreach (var symbol in _universe)
            {
                if (!_rsi[symbol].IsReady || !data.ContainsKey(symbol))
                    continue;

                var rsiValue = _rsi[symbol].Current.Value;
                var holding = Portfolio[symbol];
                var price = data[symbol].Close;

                // ── Check stop-loss on existing positions ──
                if (holding.Invested && _entryPrice.ContainsKey(symbol))
                {
                    var entry = _entryPrice[symbol];
                    decimal pnlPct;

                    if (holding.IsLong)
                        pnlPct = (price - entry) / entry;
                    else
                        pnlPct = (entry - price) / entry;

                    if (pnlPct <= -StopLossPct)
                    {
                        Liquidate(symbol, $"Stop-loss hit ({pnlPct:P2})");
                        _entryPrice.Remove(symbol);
                        continue;
                    }
                }

                // ── Entry / Exit logic ──
                if (!holding.Invested)
                {
                    // Long entry: RSI oversold
                    if (rsiValue < RsiLongEntry)
                    {
                        SetHoldings(symbol, PositionSizePct);
                        _entryPrice[symbol] = price;
                    }
                    // Short entry: RSI overbought
                    else if (rsiValue > RsiShortEntry)
                    {
                        SetHoldings(symbol, -PositionSizePct);
                        _entryPrice[symbol] = price;
                    }
                }
                else if (holding.IsLong && rsiValue > RsiLongExit)
                {
                    // Long exit: RSI reverted to neutral
                    Liquidate(symbol, $"RSI long exit ({rsiValue:F1})");
                    _entryPrice.Remove(symbol);
                }
                else if (holding.IsShort && rsiValue < RsiShortExit)
                {
                    // Short exit: RSI reverted to neutral
                    Liquidate(symbol, $"RSI short exit ({rsiValue:F1})");
                    _entryPrice.Remove(symbol);
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

            Log("WARNING: universe.json not found, using default 10-symbol universe");
            return new List<string>
            {
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
            };
        }
    }
}
