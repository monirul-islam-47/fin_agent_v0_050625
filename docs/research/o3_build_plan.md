# Phase-by-Phase Build Plan  
*One-Day Trading Agent (ODTA) · Free-Tier Edition*  

---

## 📑 Phase 0 — Environment & Scaffolding

| Goal | How (Files / Details) | Cautions | Why It Matters |
|------|-----------------------|----------|----------------|
| Spin up an **empty but runnable repo** so later phases slot in cleanly. | *Files*<br>• `requirements.txt` — only minimal set (`streamlit`, `pytest`, `python-dotenv`).<br>• `one_day_agent/` folder with `__init__.py`.<br>• `dashboard.py` → displays “Hello, ODTA”.<br>• `README.md` with install / run steps.<br>• `.env.template` for API keys. | - Commit **no real keys**; `.gitignore` `.env`.<br>- Use Python 3.11 virtual-env to avoid OS libs mismatch. | Gives every later phase a stable import path; CI can already run `pytest` and `streamlit run dashboard.py` smoke test. |

---

## ⚙️ Phase 1 — Infrastructure Layer

| Goal | How (Files / Details) | Cautions | Contribution |
|------|-----------------------|----------|--------------|
| Provide **config + logging + quota guard** so all modules share the same conventions. | *Files*<br>1. `src/settings.py`<br>&nbsp;  • `Config` dataclass loading `.env` + defaults.<br>&nbsp;  • `get_config()` singleton.<br>2. `src/logger.py`<br>&nbsp;  • `setup_logger(name)` returns colorised logger.<br>3. `src/quota.py`<br>&nbsp;  • `QuotaGuard` tracks calls per provider.<br>&nbsp;  • `@rate_limit(provider)` decorator raises `QuotaExhausted`. | Make log level configurable (DEBUG/INFO).<br>Persist quota counters in memory only (reset each run) — later we can move to SQLite. | Every network call later passes through `@rate_limit`, enforcing **NFR-07 Cost** and surfacing quota data to dashboard. |

---

## 🌐 Phase 2 — Data Layer

| Goal | How (Files / Details) | Cautions | Contribution |
|------|-----------------------|----------|--------------|
| Fetch **price, historical bars, and headlines** with smart caching + fallback. | *Files*<br>1. `src/ingest.py`<br>&nbsp;  • `FinnhubWS(symbols: list)` — async iterator yielding ticks.<br>&nbsp;  • `IexHTTP.get_quote(sym)` (REST).<br>&nbsp;  • `YFClient.get_bars(sym, interval='1m')`.<br>2. `src/cache.py`<br>&nbsp;  • `CacheStore.save(symbol,date,json)` / `load(...)` — JSON under `data/cache/YYYY-MM-DD/`.<br>3. `src/news.py`<br>&nbsp;  • `GdeltClient.fetch(symbol)` – returns list[Headline].<br>&nbsp;  • `NewsApiClient.fetch(symbol)` – backup.<br>&nbsp;  • `SentimentAnalyzer.score(text)` (VADER). | ⚠️ Alpha Vantage 25-call cap: pre-seed cache via nightly cron (out-of-scope).<br>⚠️ GDELT can flood results — limit to `max_results=20`. | Supplies raw **facts** to Domain layer; caching meets **NFR-01 Performance** by avoiding redundant HTTP. |

---

## 🧠 Phase 3 — Domain Logic

| Goal | How (Files / Details) | Cautions | Contribution |
|------|-----------------------|----------|--------------|
| Turn data into **ranked ideas & trade plans** while enforcing risk. | *Files*<br>1. `src/screener.py`<br>&nbsp;  • `GapScanner.compute(prev_close, pre_mkt)` — returns % gap.<br>&nbsp;  • `LiquidityFilter.filter(df_adv)`. <br>2. `src/scoring.py`<br>&nbsp;  • `FactorModel(weights)` → returns composite score.<br>&nbsp;  • Weights injected from `settings` **or** Streamlit sliders. <br>3. `src/planner.py`<br>&nbsp;  • `PlanBuilder.build(symbol, bars)` → entry / stop / target dataclass.<br>4. `src/risk.py`<br>&nbsp;  • `ComplianceGuard.validate(plan)` — PRIIPs check, €33 loss cap. | Keep FactorModel pure (no network) so we can unit-test easily.<br>Avoid look-ahead bias when computing ATR (use prior 14 days). | Satisfies **FR-02, FR-03, FR-04** — the heart of ODTA. |

---

## 🔄 Phase 4 — Orchestration

| Goal | How (Files / Details) | Cautions | Contribution |
|------|-----------------------|----------|--------------|
| Glue data + domain layers, expose **async event bus** for UI. | *Files*<br>1. `src/bus.py` — `EventBus(queue: asyncio.Queue)` with pub/sub helpers.<br>2. `src/main.py`<br>&nbsp;  • `async run_scan()` orchestrates ingest → domain → bus.<br>&nbsp;  • `async run_second_look()` same but at 18 : 15 CET.<br>3. CLI entry in `setup.cfg` or `pyproject` (`odta-scan`). | Handle `KeyboardInterrupt` cleanly so WebSocket closes.<br>Ensure `await bus.publish()` doesn’t block UI loop. | Decouples heavy lifting from UI, enabling both **cron automation** and manual button triggers. |

---

## 🎨 Phase 5 — Presentation (Streamlit Dashboard)

| Goal | How (Files / Details) | Cautions | Contribution |
|------|-----------------------|----------|--------------|
| Show **Top-5 table, charts, sliders, alerts** in clean UI. | *Files*<br>1. `dashboard.py`<br>&nbsp;  • Sidebar: factor sliders (bind to `settings.live_weights`).<br>&nbsp;  • Main: `st.dataframe` with plans, color-coded sentiment.<br>&nbsp;  • Plotly mini-candles fed from WebSocket ticks (14 : 00-15 : 15 one-min, tick-by-tick 15 : 15-15 : 45).<br>&nbsp;  • Toast banners for quota fallbacks.<br>&nbsp;  • “Run Scan” & “Second Look” buttons call `asyncio.run(run_scan())`. | Keep websocket listener in background `st.session_state`, else Streamlit reruns will kill it.<br>Plotly extremely verbose — limit to 200 points to keep ≤ 5 s load (NFR-05). | Delivers **FR-05, FR-08** usability; makes the agent tangible for users. |

---

## 🗒️ Phase 6 — Journaling & Metrics

| Goal | How (Files / Details) | Cautions | Contribution |
|------|-----------------------|----------|--------------|
| Persist **trade plans & quota logs** for review + future ML. | *Files*<br>1. `src/journal.py` – helper to append to `logs/trades.csv`.<br>2. Extend `QuotaGuard` to dump counters to `logs/quotas.csv` at shutdown.<br>3. Dashboard “History” tab reads `trades.csv` and plots cumulative P/L (placeholder until user fills actual results). | CSV append must be atomic → use `with open(..., 'a', newline='') as f`. | Enables **FR-07**, sets stage for RL auto-sizing and post-mortems. |

---

## ✅ Phase 7 — Testing & CI

| Goal | How | Cautions | Contribution |
|------|-----|----------|--------------|
| Reach **≥ 80 % unit-test coverage**; CI green on every push. | • PyTest modules in `tests/` for each layer.<br>• Mock HTTP via `responses` lib.<br>• GitHub Actions: matrix on py 3.11, run `pytest --cov` + `flake8`. | Mock WebSocket with `websockets.server` in-process to avoid live data during test. | Guarantees **NFR-04 Maintainability** and early catch of quota/breaking changes. |

---

## 🎁 Phase 8 — Documentation & Packaging

| Goal | How | Cautions | Contribution |
|------|-----|----------|--------------|
| Ship **README + screenshots + Makefile** for easy onboarding. | • `README.md` quick-start.<br>• GIF of dashboard.<br>• `Makefile` targets (`make install`, `make ui`, `make scan`). | Keep GIF < 2 MB so repo stays lightweight. | Smooth handoff to other devs; satisfies stakeholder transparency. |

---

### How the Phases Interlock

1. **Infrastructure** underpins every call (logging, config, quota).  
2. **Data layer** streams facts → cached → Domain layer.  
3. **Domain** converts facts → trade intelligence.  
4. **Orchestrator** forms the “brain stem,” coordinating loops.  
5. **Presentation** turns intelligence into actionable UI.  
6. **Journaling** feeds historical performance data back for tuning.  
7. **Testing + Docs** wrap the whole agent into a maintainable, cost-controlled product that meets every **FR / NFR** in the PRD.

---

### Suggested Timeline (working-days)

| Days | Phase |
|------|-------|
| 1    | 0 & 1 |
| 2-4  | 2 |
| 5-7  | 3 |
| 8-9  | 4 |
| 10-12| 5 |
| 13   | 6 |
| 14-15| 7 & 8 |

Total ≈ **3 calendar weeks** part-time or **2 weeks** full-time.

---
