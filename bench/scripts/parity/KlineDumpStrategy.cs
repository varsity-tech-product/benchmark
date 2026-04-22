using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Orders;
using QuantConnect.Securities;
using NodaTime;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// 12-column bar reader (same as ParityValidation).
    /// </summary>
    public class Dump12ColBar : BaseData
    {
        public static string CsvPath = string.Empty;
        public static string CustomDataRoot = string.Empty;
        public static string ResolutionFolder = "hour";
        public static TimeSpan BarSpan = TimeSpan.FromHours(1);

        public decimal Open { get; set; }
        public decimal High { get; set; }
        public decimal Low { get; set; }
        public decimal Close { get; set; }
        public decimal Volume { get; set; }
        public decimal TakerBuyVolume { get; set; }
        public decimal TakerSellVolume { get; set; }
        public decimal TakerBuyQuoteVolume { get; set; }
        public decimal TakerSellQuoteVolume { get; set; }
        public decimal TakerBuyTrades { get; set; }
        public decimal TakerSellTrades { get; set; }

        public override DateTime EndTime { get; set; }

        public override SubscriptionDataSource GetSource(
            SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            if (!string.IsNullOrWhiteSpace(CsvPath))
                return new SubscriptionDataSource(CsvPath,
                    SubscriptionTransportMedium.LocalFile, FileFormat.Csv);

            var symbolLower = config.Symbol.Value.ToLowerInvariant();
            var sliceKey = config.Resolution == Resolution.Daily
                ? date.ToString("yyyyMM", CultureInfo.InvariantCulture)
                : date.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            var zipPath = Path.Combine(CustomDataRoot, ResolutionFolder, symbolLower,
                $"{sliceKey}_trade.zip");
            var entryName = $"{sliceKey}_{symbolLower}_{ResolutionFolder}.csv";
            return new SubscriptionDataSource($"{zipPath}#{entryName}",
                SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }

        public override BaseData Reader(
            SubscriptionDataConfig config, string line, DateTime date, bool isLiveMode)
        {
            if (string.IsNullOrEmpty(line) || line.StartsWith("\"") || line.StartsWith("open"))
                return null;
            var csv = line.Split(',');
            if (csv.Length < 12) return null;
            try
            {
                var tsMs = long.Parse(csv[0], CultureInfo.InvariantCulture);
                var time = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddMilliseconds(tsMs);
                var bar = new Dump12ColBar
                {
                    Symbol = config.Symbol, Time = time, EndTime = time + BarSpan,
                    Open = decimal.Parse(csv[1], CultureInfo.InvariantCulture),
                    High = decimal.Parse(csv[2], CultureInfo.InvariantCulture),
                    Low = decimal.Parse(csv[3], CultureInfo.InvariantCulture),
                    Close = decimal.Parse(csv[4], CultureInfo.InvariantCulture),
                    Volume = decimal.Parse(csv[5], CultureInfo.InvariantCulture),
                    TakerBuyVolume = decimal.Parse(csv[6], CultureInfo.InvariantCulture),
                    TakerSellVolume = decimal.Parse(csv[7], CultureInfo.InvariantCulture),
                    TakerBuyQuoteVolume = decimal.Parse(csv[8], CultureInfo.InvariantCulture),
                    TakerSellQuoteVolume = decimal.Parse(csv[9], CultureInfo.InvariantCulture),
                    TakerBuyTrades = decimal.Parse(csv[10], CultureInfo.InvariantCulture),
                    TakerSellTrades = decimal.Parse(csv[11], CultureInfo.InvariantCulture),
                };
                bar.Value = bar.Close;
                return bar;
            }
            catch { return null; }
        }

        public override bool RequiresMapping() => false;
        public override bool IsSparseData() => false;
        public override DateTimeZone DataTimeZone() => TimeZones.Utc;
        public override Resolution DefaultResolution() => Resolution.Hour;
        public override List<Resolution> SupportedResolutions() =>
            new List<Resolution> { Resolution.Minute, Resolution.Hour, Resolution.Daily };
    }

    /// <summary>
    /// Strategy 1: Dump all kline bars — prints every bar for cross-validation.
    /// </summary>
    public class KlineDumpStrategy : QCAlgorithm
    {
        private Symbol _symbol;
        private int _barCount;

        public override void Initialize()
        {
            SetAccountCurrency("USDT");
            SetCash(100000);
            SetStartDate(2024, 1, 1);
            SetEndDate(2024, 1, 7);

            Dump12ColBar.CsvPath = GetParameter("csv-path", string.Empty).Trim();
            Dump12ColBar.CustomDataRoot = GetParameter("custom-data-root", "/data/custom/binance").Trim();
            Dump12ColBar.ResolutionFolder = "hour";
            Dump12ColBar.BarSpan = TimeSpan.FromHours(1);

            _symbol = AddData<Dump12ColBar>("BTCUSDT", Resolution.Hour).Symbol;

            typeof(Security)
                .GetProperty("SymbolProperties", BindingFlags.Public | BindingFlags.Instance)
                ?.SetValue(Securities[_symbol],
                    new SymbolProperties("BTCUSDT", "USDT", 1m, 0.01m, 0.00001m, string.Empty));

            Settings.TradingDaysPerYear = 365;
            _barCount = 0;
        }

        public override void OnData(Slice data)
        {
            if (!data.ContainsKey(_symbol)) return;
            var bar = data.Get<Dump12ColBar>()[_symbol];
            _barCount++;
            // Log every bar: timestamp and all 12 fields
            var ts = (long)(bar.Time - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds;
            Log($"BAR|{ts}|{bar.Open}|{bar.High}|{bar.Low}|{bar.Close}|{bar.Volume}|{bar.TakerBuyVolume}|{bar.TakerSellVolume}|{bar.TakerBuyQuoteVolume}|{bar.TakerSellQuoteVolume}|{bar.TakerBuyTrades}|{bar.TakerSellTrades}");
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"TOTAL_BARS|{_barCount}");
        }
    }
}
