# Realtime Financial Data Ingestion

Realtime data ingestion is the process of programmatically receiving live market updates — prices, quotes, and trades — as they occur. Unlike historical data fetching (which retrieves past records in bulk), realtime ingestion requires maintaining an open connection or polling at regular intervals. It is used for live monitoring, execution systems, and intraday strategy research.

There are two primary approaches: **streaming** (WebSocket or SSE) delivers updates as push events with minimal latency, while **polling** (repeated REST calls) is simpler to implement but introduces a fixed delay between updates.

## 1. Common Realtime Data Sources

| Provider | Realtime Mode | Official Docs | Auth |
|---|---|---|---|
| Finnhub | WebSocket trades/quotes | https://finnhub.io/docs/api | API key |
| Twelve Data | WebSocket + REST | https://twelvedata.com/docs | API key |
| Alpha Vantage | REST quote polling | https://www.alphavantage.co/documentation/ | API key |
| CoinGecko | REST price polling | https://docs.coingecko.com/ | Free demo tier |

## 2. Connection Patterns

### 2.1 WebSocket Streaming

WebSocket connections maintain a persistent bidirectional channel. The client sends a subscription message, and the server pushes updates as they occur.

Finnhub WebSocket URL:

`wss://ws.finnhub.io?token=<YOUR_API_KEY>`

Subscribe payload:

```json
{"type": "subscribe", "symbol": "AAPL"}
```

The server responds with JSON messages containing trade data (`type: "trade"`) with fields such as symbol (`s`), price (`p`), volume (`v`), and timestamp (`t`).

### 2.2 REST Polling

When WebSocket is unavailable or unnecessary, poll a quote endpoint at fixed intervals and append each response as a new row.

Examples:

- Alpha Vantage quote: `GET /query?function=GLOBAL_QUOTE&symbol=AAPL&apikey=<KEY>`
- CoinGecko simple price: `GET /api/v3/simple/price?ids=bitcoin&vs_currencies=usd`

Polling interval should respect the provider's rate limit (typically 1-5 requests per second for free tiers).

## 3. Python Implementation

### 3.1 WebSocket Example

```python
import csv
import json
import os
from datetime import datetime, timezone
from websocket import create_connection

API_KEY = os.environ.get("FINNHUB_API_KEY", "demo")
symbol = "AAPL"

ws = create_connection(f"wss://ws.finnhub.io?token={API_KEY}")
ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))

rows = []
while len(rows) < 20:
    msg = json.loads(ws.recv())
    for item in msg.get("data", []):
        rows.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": item.get("s", symbol),
            "price": item.get("p"),
            "source": "finnhub_ws",
        })
ws.close()

with open("tick_data.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "source"])
    writer.writeheader()
    writer.writerows(rows)
```

### 3.2 Polling Example

```python
import csv
import os
import time
from datetime import datetime, timezone
import requests

API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "demo")

rows = []
for _ in range(20):
    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": "AAPL", "apikey": API_KEY},
        timeout=10,
    )
    quote = resp.json().get("Global Quote", {})
    price = float(quote.get("05. price", 0))

    rows.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "AAPL",
        "price": price,
        "source": "alphavantage_rest",
    })
    time.sleep(15)  # Respect free-tier rate limit

with open("tick_data.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "source"])
    writer.writeheader()
    writer.writerows(rows)
```

## 4. Common Data Schema

Realtime tick datasets typically include the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | datetime | UTC ISO-8601 timestamp of the update |
| `symbol` | str | Ticker or instrument identifier |
| `price` | float | Last trade price or mid-quote price |
| `volume` | float | Trade size (optional, not always available) |
| `bid` | float | Best bid price (optional) |
| `ask` | float | Best ask price (optional) |
| `source` | str | Provider or feed identifier (optional) |

## 5. Quant Caveats

### 5.1 Quote vs Trade Semantics

Realtime feeds deliver different types of market data, each with distinct meaning:

- **Last trade price** reflects the most recent executed transaction. It can be stale if the asset is illiquid.
- **Bid and ask** represent the best available prices for selling and buying respectively. The **spread** (ask - bid) is a measure of liquidity.
- **Mid-price** ((bid + ask) / 2) is often more stable than last trade and better suited for signal generation and fair-value estimation.

Using only last trade price can be misleading — a single odd-lot trade at an outlier price does not represent the current market consensus.

### 5.2 Latency and Out-of-Order Data

Realtime feeds are subject to network and processing delays:

- **Stale ticks**: A quote arriving at T+500ms may reflect market state at T. If your strategy is latency-sensitive, track exchange timestamps separately from receipt timestamps.
- **Out-of-order delivery**: Network routing can cause messages to arrive in a different order than they were generated. Always sort by exchange timestamp before analysis.
- **Deduplication**: The same event may be delivered multiple times (especially during reconnects). Deduplicate by a combination of timestamp + symbol + price + volume.

### 5.3 Session Boundaries and Timezone

Financial markets have defined trading sessions, and mixing data across session boundaries creates artifacts:

- **Normalize timestamps to UTC** (or an explicit exchange timezone like `US/Eastern`) to avoid ambiguity.
- **Pre-market and after-hours** trading has lower liquidity, wider spreads, and different price dynamics. Decide whether to include or exclude these sessions, and apply the rule consistently.
- **Weekend and holiday gaps**: If polling continuously, weekend responses may return stale Friday close prices. Filter or flag these explicitly.

## 6. Data Quality Best Practices

After collecting realtime data, verify the following:

1. **Temporal coverage**: The dataset contains updates spread across the intended time window, not a single burst.
2. **Timestamp validity**: All timestamps parse correctly and are in a consistent timezone.
3. **Ordering**: Rows are sorted chronologically by timestamp.
4. **Deduplication**: No duplicate records (same timestamp + symbol + price).
5. **Numeric integrity**: Price values are positive and within a plausible range for the instrument.
6. **Source consistency**: If multiple feeds are used, the `source` column distinguishes them.

## 7. Streaming vs Polling Comparison

| Aspect | WebSocket Streaming | REST Polling |
|--------|-------------------|-------------|
| Latency | Low (push-based) | Higher (fixed interval) |
| Complexity | Moderate (connection management, reconnects) | Simple (stateless HTTP) |
| Rate limits | Subscription-based (usually generous) | Per-request (can be restrictive) |
| Data completeness | Receives all updates | May miss updates between polls |
| Best for | Live trading, tick-level analysis | Periodic monitoring, low-frequency signals |
