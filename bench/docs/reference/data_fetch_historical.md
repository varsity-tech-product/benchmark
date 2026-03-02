# Historical Data Fetch (Free APIs, Programmatic)

This reference is for the historical data fetch task. The goal is to write code that pulls data from public APIs and saves CSV outputs.

## Task Goal

Build a Python data pull that creates exactly:

- `historical_market_prices.csv`
- `historical_macro_data.csv`

Do not use local frozen benchmark CSV files for this task.

## Runtime Notes

- This task runs in a network-enabled sandbox.
- MCP `search_web` can be used to find official provider documentation quickly.
- `search_docs` remains available for this local reference file.

## Suggested Free Data Sources

Use any one market-price source and any one macro source.

| Data Type | API | Official Docs | Auth |
|---|---|---|---|
| Market prices | Alpha Vantage | https://www.alphavantage.co/documentation/ | API key |
| Market prices | Twelve Data | https://twelvedata.com/docs | API key |
| Market prices | Nasdaq Data Link | https://docs.data.nasdaq.com/ | API key (dataset-dependent) |
| Macro | FRED | https://fred.stlouisfed.org/docs/api/fred/ | API key |
| Macro | World Bank Indicators API | https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation | No key |
| Macro | BLS Public API | https://www.bls.gov/bls/api_features.htm | No key for basic usage |
| Macro | BEA | https://www.bea.gov/resources/for-developers | API key |

## Common Endpoint Patterns

### Alpha Vantage daily prices

`GET https://www.alphavantage.co/query`

Typical params:

- `function=TIME_SERIES_DAILY_ADJUSTED`
- `symbol=SPY` (or AAPL, MSFT, etc.)
- `outputsize=full`
- `apikey=<ALPHAVANTAGE_API_KEY>`

### FRED observations

`GET https://api.stlouisfed.org/fred/series/observations`

Typical params:

- `series_id=CPIAUCSL` (CPI) or `UNRATE` (unemployment)
- `api_key=<FRED_API_KEY>`
- `file_type=json`
- `observation_start=2015-01-01`
- `observation_end=2025-12-31`

### World Bank indicator

`GET https://api.worldbank.org/v2/country/{country_code}/indicator/{indicator_code}`

Typical params:

- `format=json`
- `per_page=20000`

Examples:

- US GDP: `country_code=USA`, `indicator_code=NY.GDP.MKTP.CD`
- US CPI proxy: `indicator_code=FP.CPI.TOTL`

## Implementation Notes

- Use environment variables for keys (example: `ALPHAVANTAGE_API_KEY`, `FRED_API_KEY`).
- Handle API errors and empty payloads explicitly.
- Normalize to lower-case CSV columns.
- Include at least a date column in both outputs.

## Quant Validity Concepts (Must Be Taught During This Task)

When tutoring this task, cover these concepts explicitly:

1. Adjusted vs unadjusted prices:
- Backtests should generally use adjusted prices for split/dividend continuity.
- Raw close can create fake jumps around corporate actions.

2. Macro release lag and revisions:
- Macro series are published with lag and often revised later.
- Using final revised values as if they were known at the decision date introduces leakage.

3. Point-in-time discipline:
- Join macro values using what was known as-of that date.
- Avoid future information bleed when building research features.

Recommended minimum columns:

- `historical_market_prices.csv`: `date`, `open`, `high`, `low`, `close`, `volume`
- `historical_macro_data.csv`: `date`, `value`, plus optional metadata columns

## Minimal Validation Checklist

Before finishing:

1. Both CSV files exist in workspace root.
2. Each CSV has a header row and non-empty data rows.
3. Market file has price-like numeric columns.
4. Macro file has a numeric indicator/value column.
5. Run quality checks: missing values, duplicate timestamps, date sorting, and basic dtype validation.

## Rate Limits and Terms

Free tiers are rate-limited. Add retry/backoff and avoid aggressive loops.

Check provider terms before commercial or redistributed use.
