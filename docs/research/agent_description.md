# Problem Description – One‑Day Trading Agent (Free‑Tier Edition)

## 1  Purpose & Vision

Design a lightweight **Python‑based trading assistant** that, every trading day, surfaces **five US‑listed stocks** likely to yield a **7 – 10 % intraday profit** for a €500 portfolio operated through **Revolut (Germany)**. The agent runs locally and guides the user between **14:00 – 16:15 CET** and again at **18:15 CET**, while respecting EU compliance and free‑tier data limits.

## 2  User & Operating Context

| Aspect                     | Detail                                                                                |
| -------------------------- | ------------------------------------------------------------------------------------- |
| **User**                   | Retail trader in Germany; Revolut account; uses ChatGPT (OpenAI o3).                  |
| **Capital**                | €500 per day, redeployed daily.                                                       |
| **Risk Appetite**          | Target gain 7–10 %; max daily loss **€33**; max position per symbol **€250**.         |
| **Time Windows**           | Pre‑market scan 14:00–15:15; active trading 15:15–16:15; "second‑look" scan at 18:15. |
| **Regulatory Constraints** | PRIIPs: U.S. leveraged ETFs blocked; KID‑less instruments excluded.                   |
| **Cost Constraints**       | Must stay within free tiers for all third‑party services except OpenAI usage.         |

## 3  High‑Level Goals & Success Metrics

1. **Accuracy** – ≥60 % of recommended trades hit ≥7 % profit within session.
2. **Risk Control** – Losses contained to ≤€33 on losing days.
3. **Cost Efficiency** – API call quotas never exceeded; zero recurring fees.
4. **Usability** – Clear Streamlit dashboard; ≤5 s refresh latency on key actions.

## 4  Functional Requirements

### 4.1 Data Ingestion

* **Real‑time quotes:** Finnhub WebSocket (primary) → yfinance (fallback).
* **Pre‑market ticks:** Finnhub between 14:00–15:30 CET.
* **Historical bars:** Alpha Vantage intraday, cached locally.
* **News & Sentiment:** GDELT + NewsAPI headlines; VADER sentiment scoring.
* **Earnings & Macro Calendar:** Finnhub free endpoints.

### 4.2 Universe Filtering

* Revolut‑tradable **US common stocks** only (CSV whitelist).
* Liquidity ≥ €5 M ADV, price €2–€300, no leveraged/inverse ETFs.

### 4.3 Scoring Engine

Weighted model combining:

1. **Volatility Potential** – 14‑day ATR % & pre‑market gap.
2. **Catalyst Strength** – Earnings today / unusual option or short float.
3. **Sentiment Score** – VADER polarity average of top headlines.
4. **Liquidity Score** – ADV & bid‑ask spread proxy.

Top 5 symbols with projected ≥8 % range proceed to planning.

### 4.4 Trade Plan Generation

* **Entry Zone:** VWAP ± x % or ORH breakout.
* **Stop‑Loss:** −3 % or 2× ATR(5), whichever tighter.
* **Take‑Profit:** +8 – 10 %.
* **Sizing:** Even split or ≤€250 per symbol within €500 bankroll.

### 4.5 Risk Management & Compliance

* Enforce daily loss cap and per‑trade stops.
* PRIIPs/KID availability check before display.
* Flag delayed data if falling back to yfinance.

### 4.6 User Interface (Streamlit Dashboard)

1. **Quota Status Badges**
2. **Top‑5 Recommendation Table**
3. **Headline & Sentiment Drawer**
4. **Mini‑Candlestick Charts** (1‑min or tick view as configured)
5. **Alerts Log** (fallback & error notifications)

### 4.7 Logging & Journaling

* Auto‑append `logs/trades.csv` with plan meta; user manually fills actual fills/P\&L.
* Store API quota usage stats per day.

### 4.8 Fallback Behaviour

When Finnhub quotas exceeded:

1. Switch to yfinance (15‑min delay) and tag data as `DELAYED`.
2. Notify user in dashboard Alerts Log.

## 5  Non‑Functional Requirements

| Category            | Requirement                                                                   |
| ------------------- | ----------------------------------------------------------------------------- |
| **Deployment**      | Runs on user’s laptop; `streamlit run dashboard.py`.                          |
| **Scheduling**      | Cron job (optional) triggers main script at 14:00 CET; user may run manually. |
| **Security**        | API keys stored in `~/.agent/.env`; `.gitignore` enforced.                    |
| **Maintainability** | Modular codebase (`ingest.py`, `news.py`, `screener.py`, etc.).               |
| **Performance**     | Initial scan completes ≤20 s; 1‑min chart refresh ≤2 s.                       |

## 6  Assumptions & Dependencies

* Free tiers remain available and terms don’t tighten.
* Revolut’s tradable universe list updated weekly by user.
* User connects to reliable \~25 Mbit internet during trading hours.

## 7  Out of Scope (MVP)

* Automated order execution via Revolut API.
* EU ticker coverage (future `eu_universe.csv`).
* Sophisticated ML portfolio optimization; RL ranking.
* Mobile‑first UI.


---

*Prepared 2025‑06‑05 (Europe/Berlin)*
