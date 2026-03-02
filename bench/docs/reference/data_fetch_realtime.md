# Realtime Data Fetch (Streaming or Polling)

This reference is for the realtime fetch task. The goal is to pull live updates from a realtime endpoint (streaming preferred, polling acceptable fallback) and store results in CSV.

## Task Goal

Create:

- `realtime_stream_ticks.csv`

The CSV should contain timestamped updates captured during runtime (not static historical dumps).

## Suggested Free/Free-Tier Realtime Sources

| API | Realtime Mode | Official Docs | Auth |
|---|---|---|---|
| Finnhub | WebSocket trades/quotes | https://finnhub.io/docs/api | API key |
| Twelve Data | WebSocket + REST | https://twelvedata.com/docs | API key |
| Alpha Vantage | REST quote polling | https://www.alphavantage.co/documentation/ | API key |
| CoinGecko | REST price polling | https://docs.coingecko.com/ | Free demo tier |

## Endpoint Patterns

### Finnhub WebSocket (streaming)

WebSocket URL:

`wss://ws.finnhub.io?token=<FINNHUB_API_KEY>`

Subscribe payload:

```json
{"type":"subscribe","symbol":"AAPL"}
```

### REST Polling Fallback

If WebSocket is unavailable in your runtime, poll a quote endpoint repeatedly and append rows.

Examples:

- Alpha Vantage quote: `function=GLOBAL_QUOTE&symbol=AAPL`
- CoinGecko simple price for crypto pairs (if you choose crypto): `/api/v3/simple/price`

## Required CSV Schema

`realtime_stream_ticks.csv` should include at least:

- `timestamp` (UTC ISO-8601 recommended)
- `symbol`
- `price`
- `source` (optional but recommended)

Recommended target: at least 20 updates.

## Quant Realtime Concepts (Must Be Taught During This Task)

Cover these concepts while guiding implementation:

1. Quote vs trade semantics:
- Last trade price, bid, and ask represent different microstructure states.
- Mid-price and spread are often better for signal sanity checks than trade prints alone.

2. Latency and out-of-order data:
- Realtime feeds can deliver stale or out-of-order ticks.
- You should sort by timestamp, deduplicate, and document dropped records.

3. Session boundaries and timezone:
- Normalize all timestamps to UTC (or explicit exchange timezone).
- Decide whether to include pre-market/after-hours; do not mix blindly.

## Example Streaming Skeleton

```python
import csv
import json
from datetime import datetime, timezone
from websocket import create_connection

token = "YOUR_FINNHUB_KEY"
symbol = "AAPL"

ws = create_connection(f"wss://ws.finnhub.io?token={token}")
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

with open("realtime_stream_ticks.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "source"])
    writer.writeheader()
    writer.writerows(rows)
```

## Example Polling Skeleton

```python
import csv
import time
from datetime import datetime, timezone
import requests

rows = []
for _ in range(20):
    # Replace URL + parsing for your provider
    price = 0.0
    rows.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "AAPL",
        "price": price,
        "source": "rest_poll",
    })
    time.sleep(2)

with open("realtime_stream_ticks.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "source"])
    writer.writeheader()
    writer.writerows(rows)
```

## Validation Checklist

1. CSV exists in workspace root.
2. Header is present.
3. `timestamp` and `price` columns are populated.
4. File has multiple time-separated updates (not one snapshot).
5. Integrity checks are run: timestamp ordering, duplicate handling, and explicit timezone/session handling.
