/*
 * I05 — Cross-Asset Pairs / Statistical Arbitrage
 *
 * Strategy:
 *   - Universe: ~100 crypto-futures from universe.json
 *   - Pairs selection: C(N,2) pairwise correlation over 20-day lookback
 *       - Select pairs with correlation > 0.80
 *   - Signal: Z-score of the spread (price ratio log-return diff)
 *       - Z > +2.0 → short spread (short asset A, long asset B)
 *       - Z < -2.0 → long spread (long asset A, short asset B)
 *       - |Z| < 0.5 → close spread position
 *   - Sizing: Equal notional per leg, max 10 active pairs
 *   - Rebalance: Daily
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
    public class I05CrossAsset : QCAlgorithm
    {
        // ── Configuration ──
        private const int LookbackPeriod = 20;
        private const decimal CorrelationThreshold = 0.80m;
        private const decimal ZScoreEntry = 2.0m;
        private const decimal ZScoreExit = 0.5m;
        private const int MaxActivePairs = 10;
        private const decimal PerLegWeight = 0.05m;  // 5% per leg
        private const int MaxSymbols = 50;  // Cap for O(n²) pair computation
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private readonly List<Symbol> _symbols = new();
        private readonly Dictionary<Symbol, RollingWindow<decimal>> _priceHistory = new();

        private class PairState
        {
            public Symbol SymbolA;
            public Symbol SymbolB;
            public int Direction;  // +1 = long spread, -1 = short spread, 0 = flat
        }

        private readonly List<PairState> _activePairs = new();
        private DateTime _lastRebalance = DateTime.MinValue;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var tickers = LoadUniverse().Take(MaxSymbols).ToList();

            foreach (var ticker in tickers)
            {
                try
                {
                    var symbol = AddCryptoFuture(ticker, Resolution.Daily, Market.Binance).Symbol;
                    _symbols.Add(symbol);
                    _priceHistory[symbol] = new RollingWindow<decimal>(LookbackPeriod);
                }
                catch (Exception ex)
                {
                    Log($"Skipping {ticker}: {ex.Message}");
                }
            }

            SetWarmUp(LookbackPeriod + 1, Resolution.Daily);

            Log($"I05 initialized with {_symbols.Count} symbols, " +
                $"lookback={LookbackPeriod}, corr_thresh={CorrelationThreshold}, " +
                $"z_entry={ZScoreEntry}, z_exit={ZScoreExit}");
        }

        public override void OnData(Slice data)
        {
            // Update price history
            foreach (var symbol in _symbols)
            {
                if (data.ContainsKey(symbol))
                {
                    _priceHistory[symbol].Add(data[symbol].Close);
                }
            }

            if (IsWarmingUp) return;

            // Daily rebalance only
            if (Time.Date == _lastRebalance) return;
            _lastRebalance = Time.Date;

            // Check all symbols have enough history
            var readySymbols = _symbols
                .Where(s => _priceHistory[s].IsReady)
                .ToList();

            if (readySymbols.Count < 2) return;

            // ── Scan for correlated pairs ──
            var eligiblePairs = new List<(Symbol a, Symbol b, decimal corr, decimal zScore)>();

            for (int i = 0; i < readySymbols.Count; i++)
            {
                for (int j = i + 1; j < readySymbols.Count; j++)
                {
                    var symA = readySymbols[i];
                    var symB = readySymbols[j];

                    var returnsA = ComputeLogReturns(_priceHistory[symA]);
                    var returnsB = ComputeLogReturns(_priceHistory[symB]);

                    if (returnsA == null || returnsB == null) continue;

                    var corr = ComputeCorrelation(returnsA, returnsB);
                    if (Math.Abs(corr) < CorrelationThreshold) continue;

                    // Compute spread z-score
                    var spread = ComputeSpread(returnsA, returnsB);
                    var zScore = ComputeZScore(spread);

                    eligiblePairs.Add((symA, symB, corr, zScore));
                }
            }

            // ── Manage existing pairs ──
            var pairsToRemove = new List<PairState>();
            foreach (var pair in _activePairs)
            {
                var zScore = GetCurrentZScore(pair.SymbolA, pair.SymbolB);
                if (!zScore.HasValue || Math.Abs(zScore.Value) < ZScoreExit)
                {
                    // Close the spread
                    if (Portfolio[pair.SymbolA].Invested)
                        Liquidate(pair.SymbolA, $"Pair exit z={zScore?.ToString("F2") ?? "N/A"}");
                    if (Portfolio[pair.SymbolB].Invested)
                        Liquidate(pair.SymbolB, $"Pair exit z={zScore?.ToString("F2") ?? "N/A"}");
                    pairsToRemove.Add(pair);
                }
            }
            foreach (var p in pairsToRemove)
                _activePairs.Remove(p);

            // ── Enter new pairs ──
            var sortedPairs = eligiblePairs
                .Where(p => Math.Abs(p.zScore) >= ZScoreEntry)
                .OrderByDescending(p => Math.Abs(p.zScore))
                .ToList();

            foreach (var (symA, symB, corr, zScore) in sortedPairs)
            {
                if (_activePairs.Count >= MaxActivePairs) break;

                // Skip if either symbol is already in an active pair
                if (_activePairs.Any(p =>
                    p.SymbolA == symA || p.SymbolA == symB ||
                    p.SymbolB == symA || p.SymbolB == symB))
                    continue;

                int direction;
                if (zScore > ZScoreEntry)
                {
                    // Spread is high → short spread (short A, long B)
                    SetHoldings(symA, -PerLegWeight);
                    SetHoldings(symB, PerLegWeight);
                    direction = -1;
                }
                else if (zScore < -ZScoreEntry)
                {
                    // Spread is low → long spread (long A, short B)
                    SetHoldings(symA, PerLegWeight);
                    SetHoldings(symB, -PerLegWeight);
                    direction = 1;
                }
                else
                {
                    continue;
                }

                _activePairs.Add(new PairState
                {
                    SymbolA = symA,
                    SymbolB = symB,
                    Direction = direction,
                });

                Log($"PAIR OPENED: {symA}/{symB} | dir={direction} | z={zScore:F2} | corr={corr:F3}");
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

        // ── Helpers ──

        private decimal[] ComputeLogReturns(RollingWindow<decimal> prices)
        {
            if (prices.Count < 2) return null;
            var returns = new decimal[prices.Count - 1];
            for (int i = 0; i < returns.Length; i++)
            {
                var p1 = prices[i];
                var p0 = prices[i + 1];
                if (p0 <= 0) return null;
                returns[i] = (decimal)Math.Log((double)(p1 / p0));
            }
            return returns;
        }

        private decimal ComputeCorrelation(decimal[] a, decimal[] b)
        {
            int n = Math.Min(a.Length, b.Length);
            if (n < 3) return 0m;

            var meanA = a.Take(n).Average();
            var meanB = b.Take(n).Average();

            decimal cov = 0, varA = 0, varB = 0;
            for (int i = 0; i < n; i++)
            {
                var dA = a[i] - meanA;
                var dB = b[i] - meanB;
                cov += dA * dB;
                varA += dA * dA;
                varB += dB * dB;
            }

            if (varA == 0 || varB == 0) return 0m;
            return cov / (decimal)Math.Sqrt((double)(varA * varB));
        }

        private decimal[] ComputeSpread(decimal[] returnsA, decimal[] returnsB)
        {
            int n = Math.Min(returnsA.Length, returnsB.Length);
            var spread = new decimal[n];
            for (int i = 0; i < n; i++)
                spread[i] = returnsA[i] - returnsB[i];
            return spread;
        }

        private decimal ComputeZScore(decimal[] spread)
        {
            if (spread.Length < 2) return 0m;
            var mean = spread.Average();
            var variance = spread.Select(x => (x - mean) * (x - mean)).Average();
            var std = (decimal)Math.Sqrt((double)variance);
            if (std < 1e-10m) return 0m;
            return (spread[0] - mean) / std;
        }

        private decimal? GetCurrentZScore(Symbol symA, Symbol symB)
        {
            if (!_priceHistory[symA].IsReady || !_priceHistory[symB].IsReady)
                return null;
            var rA = ComputeLogReturns(_priceHistory[symA]);
            var rB = ComputeLogReturns(_priceHistory[symB]);
            if (rA == null || rB == null) return null;
            var spread = ComputeSpread(rA, rB);
            return ComputeZScore(spread);
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
