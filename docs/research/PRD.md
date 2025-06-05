```markdown
# Product Requirements Document (PRD)

### Product Name
**One-Day Trading Agent** (code-name: **ODTA**)

---

## 1. Purpose
Deliver a **free-tier, local Python assistant** that recommends **five U.S. stocks each trading day** with a realistic chance to earn **7-10 % intraday profit** for a €500 bankroll via Revolut (Germany).  
The agent scans markets, evaluates catalysts, and presents actionable trade plans through an interactive Streamlit dashboard.

---

## 2. Background & Problem Statement  
Retail traders with small accounts face three challenges:  

1. **Information overload** — dozens of data feeds, news sites, social chatter.  
2. **Regulatory hurdles** — PRIIPs restrict high-beta ETFs for EU clients.  
3. **Tooling cost** — pro platforms and real-time data are expensive.  

**ODTA** stitches together *free* data APIs, automates a repeatable screening logic, and surfaces insights in an easy UI without recurring SaaS fees.

---

## 3. Objectives & Success Metrics

| Objective                                   | KPI                                                        | Target |
|---------------------------------------------|------------------------------------------------------------|--------|
| Timely daily report                         | Trading days with Top-5 list published by 14 : 20 CET      | ≥ 95 % |
| Trading edge                                | Suggested trades hitting **+7 %** before **-3 %** stop     | ≥ 60 % (30-day rolling) |
| Risk control                                | Max single-day loss                                        | ≤ €33 |
| Cost control                                | API-quota overruns                                         | 0 / month |
| Usability                                   | Dashboard initial load time                                | ≤ 5 s |

---

## 4. Stakeholders

| Role                 | Name / Owner               |
|----------------------|----------------------------|
| **Primary User**     | Retail trader (Berlin-based) |
| **Product Owner**    | MI                          |
| **Developer**        | MI (or delegated)           |
| **Compliance Check** | Self-assessment (German PRIIPs & tax) |

---

## 5. User Personas & Use Cases

### Persona A – “Focused Day-Trader”  
*Logs in at 14 : 00 CET, skims Top-5, trades until 16 : 00.*

### Persona B – “After-Work Opportunist”  
*Runs second-look scan at 18 : 15 CET, takes a late move.*

| # | User Story                                                                         | Priority |
|---|------------------------------------------------------------------------------------|----------|
| 1 | As a trader, I want a ranked list of five volatile stocks by 14 : 20 so I can focus.| Must-have |
| 2 | As a trader, I need clear entry, stop, and target prices to manage risk quickly.    | Must-have |
| 3 | As a trader, I want to see catalyst headlines & sentiment to justify each pick.     | Must-have |
| 4 | As a trader, I need quota/error alerts so I trust data freshness.                   | Should-have |
| 5 | As a trader, I want sliders to tweak factor weights without code.                   | Should-have |
| 6 | As a trader, I’d like a journal of past plans to review performance.                | Nice-to-have |

---

## 6. Scope

### In-Scope (MVP)
* U.S. common stocks tradable on Revolut (no leveraged ETFs).  
* Data ingestion via free tiers of Finnhub, IEX Cloud, Alpha Vantage, GDELT, NewsAPI.  
* Pre-market gap & volatility screening, VADER sentiment scoring.  
* Streamlit dashboard with Top-5 table, Plotly mini-charts, headline drawer.  
* JSON/CSV caching and quota monitoring.

### Out of Scope (MVP)
* Automated order placement.  
* EU-ticker support (phase 2).  
* Mobile-first UX.  
* Portfolio-level optimisation or RL auto-sizing.

---

## 7. Functional Requirements

| ID | Functional Requirement                                                                                | Priority |
|----|-------------------------------------------------------------------------------------------------------|----------|
| **FR-01** | The agent **initiates data refresh at 14 : 00 CET** for live quotes, gaps, headlines, events. | Must-have |
| **FR-02** | It **filters and scores** the universe, producing a **Top-5 list** by **14 : 20 CET**.        | Must-have |
| **FR-03** | For each pick, it **generates a trade plan** (entry zone, stop, target, size).                | Must-have |
| **FR-04** | It **enforces risk guardrails**: daily loss ≤ €33, max €250 per symbol, PRIIPs/KID check.     | Must-have |
| **FR-05** | The **dashboard auto-refreshes** after each scan and on user-triggered refresh/second-look.    | Must-have |
| **FR-06** | When quotas near limits, the agent **falls back** to delayed data, **flags DEPRECATED/DELAYED** status, and logs the event. | Should-have |
| **FR-07** | The system **records every scan and plan** to `logs/trades.csv` and **API quotas** to `logs/quotas.csv`. | Should-have |
| **FR-08** | Users can **adjust factor weights** (Vol, Catalyst, Liquidity, Sentiment) via Streamlit sliders; changes apply to next scan. | Should-have |
| **FR-09** | The second-look scan can be **triggered manually** at any time (default 18 : 15 CET).          | Nice-to-have |

---

## 8. Non-Functional Requirements

| ID | Non-Functional Requirement                                                | Target / Note |
|----|---------------------------------------------------------------------------|---------------|
| **NFR-01** | **Performance** – Full scan completes **≤ 20 s** on consumer laptop using free APIs. | ≤ 20 s |
| **NFR-02** | **Reliability** – Graceful degrade to fallback data; **no unhandled crashes**.       | 0 fatal errors |
| **NFR-03** | **Security** – API keys stored locally in `.env`, **never committed to Git**.        | Pass review |
| **NFR-04** | **Maintainability** – Modular code, **≥ 80 % unit-test coverage** on core logic.     | ≥ 80 % |
| **NFR-05** | **Usability** – Dashboard initial load time **≤ 5 s**; interactive charts responsive. | ≤ 5 s |
| **NFR-06** | **Compatibility** – Runs on Python 3.11; OS-agnostic (Windows/Mac/Linux).            | ✔ |
| **NFR-07** | **Cost** – Stays within **free API quotas**; triggers fallback before overage.       | 0 overages/month |

---

## 9. Assumptions & Dependencies
* Free tiers of Finnhub, IEX, Alpha Vantage, GDELT remain available.  
* User updates the Revolut universe CSV weekly.  
* Stable internet connection (< 200 ms latency) during trading hours.

---

## 10. Release & Acceptance Criteria

| Milestone | Acceptance Criteria |
|-----------|---------------------|
| **MVP GA** | FR-01 … FR-06 and NFR-01 … NFR-05 met; Top-5 table and trade plans demoed; risk caps enforced. |
| **Post-MVP** | FR-07 … FR-09 implemented; 30-day performance review shows ≥ 60 % hit-rate with ≤ €33 worst-day loss. |

---

## 11. Future Enhancements
* EU-ticker expansion.  
* Auto-email PDF reports.  
* RL-based factor tuning.  
* Semi-automatic Revolut order pre-fill.

---

*Prepared 2025-06-05 (Europe/Berlin)*
```
