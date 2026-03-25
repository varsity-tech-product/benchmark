/*
 * I05 — Cross-Asset Pairs / Statistical Arbitrage
 *
 * Strategy:
 *   - Universe: Symbols extracted from I05_candidate_pairs.json (tier2 subset)
 *   - Pairs: 10 pre-computed correlated pairs loaded from I05_candidate_pairs.json
 *   - Signal: Z-score of the rolling spread (log-return difference)
 *       - Z > +2.0 → short spread (short asset A, long asset B)
 *       - Z < -2.0 → long spread (long asset A, short asset B)
 *       - |Z| < 0.5 → close spread position
 *   - Sizing: Equal notional per leg (5% per leg), max 10 active pairs
 *   - Rebalance: Daily
 *
 * LEAN API: QCAlgorithm, AddCryptoFuture, Market.Binance
 */

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
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
        private const decimal ZScoreEntry = 2.0m;
        private const decimal ZScoreExit = 0.5m;
        private const int MaxActivePairs = 10;
        private const decimal PerLegWeight = 0.05m;  // 5% per leg
        private const decimal InitialCash = 100_000m;

        // ── State ──
        private readonly Dictionary<Symbol, RollingWindow<decimal>> _priceHistory = new();
        private readonly Dictionary<string, Symbol> _tickerToSymbol = new();

        private class PairDef
        {
            public Symbol SymbolA;
            public Symbol SymbolB;
            public int Rank;
        }

        private class PairState
        {
            public PairDef Pair;
            public int Direction;  // +1 = long spread, -1 = short spread, 0 = flat
        }

        private readonly List<PairDef> _candidatePairs = new();
        private readonly List<PairState> _activePairs = new();
        private DateTime _lastRebalance = DateTime.MinValue;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            // Load candidate pairs and subscribe to their symbols
            LoadCandidatePairs();

            SetWarmUp(LookbackPeriod + 1, Resolution.Daily);

            Log($"I05 initialized with {_tickerToSymbol.Count} symbols from {_candidatePairs.Count} candidate pairs, " +
                $"lookback={LookbackPeriod}, z_entry={ZScoreEntry}, z_exit={ZScoreExit}");
        }

        public override void OnData(Slice data)
        {
            // Update price history
            foreach (var kvp in _tickerToSymbol)
            {
                var symbol = kvp.Value;
                if (data.ContainsKey(symbol))
                {
                    _priceHistory[symbol].Add(data[symbol].Close);
                }
            }

            if (IsWarmingUp) return;

            // Daily rebalance only
            if (Time.Date == _lastRebalance) return;
            _lastRebalance = Time.Date;

            // ── Manage existing pairs: close if z-score reverts ──
            var pairsToRemove = new List<PairState>();
            foreach (var ps in _activePairs)
            {
                var zScore = GetCurrentZScore(ps.Pair.SymbolA, ps.Pair.SymbolB);
                if (!zScore.HasValue || Math.Abs(zScore.Value) < ZScoreExit)
                {
                    if (Portfolio[ps.Pair.SymbolA].Invested)
                        Liquidate(ps.Pair.SymbolA, $"Pair exit z={zScore?.ToString("F2") ?? "N/A"}");
                    if (Portfolio[ps.Pair.SymbolB].Invested)
                        Liquidate(ps.Pair.SymbolB, $"Pair exit z={zScore?.ToString("F2") ?? "N/A"}");
                    pairsToRemove.Add(ps);
                }
            }
            foreach (var p in pairsToRemove)
                _activePairs.Remove(p);

            // ── Enter new pairs from candidates ──
            foreach (var pair in _candidatePairs)
            {
                if (_activePairs.Count >= MaxActivePairs) break;

                // Skip if this pair is already active
                if (_activePairs.Any(ps =>
                    (ps.Pair.SymbolA == pair.SymbolA && ps.Pair.SymbolB == pair.SymbolB)))
                    continue;

                // Skip if either symbol is already in an active pair
                if (_activePairs.Any(ps =>
                    ps.Pair.SymbolA == pair.SymbolA || ps.Pair.SymbolA == pair.SymbolB ||
                    ps.Pair.SymbolB == pair.SymbolA || ps.Pair.SymbolB == pair.SymbolB))
                    continue;

                var zScore = GetCurrentZScore(pair.SymbolA, pair.SymbolB);
                if (!zScore.HasValue) continue;

                int direction;
                if (zScore.Value > ZScoreEntry)
                {
                    // Spread is high → short spread (short A, long B)
                    SetHoldings(pair.SymbolA, -PerLegWeight);
                    SetHoldings(pair.SymbolB, PerLegWeight);
                    direction = -1;
                }
                else if (zScore.Value < -ZScoreEntry)
                {
                    // Spread is low → long spread (long A, short B)
                    SetHoldings(pair.SymbolA, PerLegWeight);
                    SetHoldings(pair.SymbolB, -PerLegWeight);
                    direction = 1;
                }
                else
                {
                    continue;
                }

                _activePairs.Add(new PairState
                {
                    Pair = pair,
                    Direction = direction,
                });

                Log($"PAIR OPENED: {pair.SymbolA}/{pair.SymbolB} (rank={pair.Rank}) | dir={direction} | z={zScore.Value:F2}");
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

        private decimal? GetCurrentZScore(Symbol symA, Symbol symB)
        {
            if (!_priceHistory.ContainsKey(symA) || !_priceHistory.ContainsKey(symB))
                return null;
            if (!_priceHistory[symA].IsReady || !_priceHistory[symB].IsReady)
                return null;
            var rA = ComputeLogReturns(_priceHistory[symA]);
            var rB = ComputeLogReturns(_priceHistory[symB]);
            if (rA == null || rB == null) return null;
            var spread = ComputeSpread(rA, rB);
            return ComputeZScore(spread);
        }

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

        private void LoadCandidatePairs()
        {
            // Try multiple paths where I05_candidate_pairs.json may be staged
            var paths = new[]
            {
                Path.Combine(Globals.DataFolder, "I05_candidate_pairs.json"),
                "/CustomAlgo/I05_candidate_pairs.json",
                "/data/I05_candidate_pairs.json",
                "/lean/Data/I05_candidate_pairs.json"
            };

            string json = null;
            foreach (var path in paths)
            {
                if (File.Exists(path))
                {
                    json = File.ReadAllText(path);
                    Log($"Loaded candidate pairs from {path}");
                    break;
                }
            }

            if (json == null)
            {
                Log("WARNING: I05_candidate_pairs.json not found, using default pairs");
                // Fallback: hardcode the top-3 pairs from tier2
                SubscribeAndAddPair("BTCUSDT", "ETHUSDT", 1);
                SubscribeAndAddPair("ADAUSDT", "DOTUSDT", 2);
                SubscribeAndAddPair("ATOMUSDT", "DOTUSDT", 3);
                return;
            }

            var doc = JObject.Parse(json);
            var pairsArray = doc["candidate_pairs"] as JArray;
            if (pairsArray == null)
            {
                Log("ERROR: candidate_pairs array not found in JSON");
                return;
            }

            foreach (var item in pairsArray)
            {
                var pairTickers = item["pair"].ToObject<List<string>>();
                var rank = item["rank"].Value<int>();
                if (pairTickers.Count >= 2)
                {
                    SubscribeAndAddPair(pairTickers[0], pairTickers[1], rank);
                }
            }
        }

        private void SubscribeAndAddPair(string tickerA, string tickerB, int rank)
        {
            var symA = EnsureSubscribed(tickerA);
            var symB = EnsureSubscribed(tickerB);

            _candidatePairs.Add(new PairDef
            {
                SymbolA = symA,
                SymbolB = symB,
                Rank = rank,
            });
        }

        private Symbol EnsureSubscribed(string ticker)
        {
            if (_tickerToSymbol.ContainsKey(ticker))
                return _tickerToSymbol[ticker];

            var symbol = AddCryptoFuture(ticker, Resolution.Daily, Market.Binance).Symbol;
            _tickerToSymbol[ticker] = symbol;
            _priceHistory[symbol] = new RollingWindow<decimal>(LookbackPeriod);
            return symbol;
        }
    }
}
