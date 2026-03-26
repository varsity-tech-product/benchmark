---
name: finance-assistant
description: Personal A-share finance assistant — daily report, weekly report, and interactive chat
trigger:
  automation:
    - cron: "30 18 * * *"
      timezone: "Asia/Shanghai"
      action: "daily"
    - cron: "0 20 * * 0"
      timezone: "Asia/Shanghai"
      action: "weekly"
  message: ".*"
---

# Personal Finance Assistant

A-share personal finance assistant with three modes:

## Scheduled triggers

1. **Daily evening report** — every day at 18:30 CST
   - Trading days: full report (market data + news + macro)
   - Non-trading days: news + macro only (stock data silenced)

2. **Weekly summary report** — Sunday 20:00 CST
   - Full week review: per-stock performance, key news, macro recap
   - Next week outlook and risk assessment

## Interactive chat

Users can message anytime for:
- **Stock query**: send a 6-digit code (e.g. "002639") → instant analysis
- **News briefing**: send "新闻" → latest financial news summary
- **Macro briefing**: send "宏观" → overnight markets + upcoming events
- **Portfolio management**: "持仓" / "添加 002639 雪人股份" / "删除 002639"
- **Manual triggers**: "晚报" → force daily report / "周报" → force weekly report
- **General chat**: any other message gets a helpful response

## Architecture

```
orchestrator.py main [daily|weekly|chat <msg>]
  ├── daily  → collect_data() → Phase 2 (3 LLM) → Phase 3 (report LLM) → Telegram
  ├── weekly → collect_weekly_data() → weekly_report LLM → Telegram
  └── chat   → parse_intent() → targeted data fetch → LLM response → Telegram
```

## Portfolio management via chat

- "添加 600036 招商银行" → add stock to portfolio.json
- "删除 601318" → remove stock from portfolio.json
- "持仓" → show current holdings
