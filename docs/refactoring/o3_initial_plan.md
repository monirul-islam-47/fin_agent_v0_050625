# Refactoring Plan – **fin\_agent v0\_050625** (One‑Day Trading Agent)

> **Goal:** Evolve the current proof‑of‑concept into an operationally robust, testable and extensible platform capable of running unattended with real capital, while preserving the existing strategy logic and Streamlit UX.

---

## 0. Guiding Principles

| Principle                         | Why it Matters                                                                 |
| --------------------------------- | ------------------------------------------------------------------------------ |
| **Clean Architecture & DDD**      | Keep domain core pure (no I/O), enable adapter swap‑outs, simplify testing.    |
| **SOLID**                         | Single‑responsibility entities, dependency‑inverted ports, open for extension. |
| **Fail‑fast, recover‑gracefully** | Durable messaging, back‑pressure, retries.                                     |
| **Incremental delivery**          | Ship value every sprint; avoid big‑bang rewrites.                              |

---

## 1. Current Pain‑Points Recap

1. **In‑memory event bus** → message loss on crash, no back‑pressure.
2. **Domain objects touch globals & I/O** → leaky boundaries, brittle tests.
3. **Config duplication** → risk caps & weights scattered.
4. **Scheduler hard‑codes CET** → DST bugs, poor portability.
5. **SQLite locking** under concurrent writers.
6. **Observable gaps** (plain logs only) → hard to debug live.
7. **No container/CI story**, plaintext secrets.

---

## 2. Target Architecture (High‑Level)

```
┌──────────────────────────┐  events   ┌───────────────────────┐
│  Gateway / Adapters      │──────────▶│      Event Bus        │  (Redis Streams/NATS)
│  - Finnhub WS            │           └───────────────────────┘
│  - Yahoo / AV REST       │                  ▲      ▲
└──────────────────────────┘                  │      │domain events
                                              │      │
                                      ┌───────┴──────┴───────┐
                                      │   Domain Services    │ (pure Python)
                                      │  - GapScanner        │
                                      │  - RiskEngine        │
                                      └────────┬─────────────┘
                                               │queries
┌──────────────────────────┐           ┌────────▼─────────────┐
│ Persistence (SQLite→DB)  │◀──────────│  Application Layer  │
└──────────────────────────┘           └────────┬─────────────┘
                                               │REST/pubsub
┌──────────────────────────┐           ┌────────▼─────────────┐
│ Streamlit Dashboard      │◀──────────│  API / Coordinator   │
└──────────────────────────┘           └──────────────────────┘
```

---

## 3. Detailed Work‑streams & Tasks

### 3.1 Domain & Strategy Layer (P1)

| Task                   | Detail                                                                             | Acceptance                                        |
| ---------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------- |
| **Extract Aggregates** | Create `TradeCandidate`, `TradePlan`, `RiskProfile` pure dataclasses.              | Domain unit tests pass without touching adapters. |
| **Define Ports**       | `MarketDataPort`, `NewsPort`, `OrderPort` ABCs in `ports.py`.                      | Domain depends only on ports.                     |
| **Inject Config**      | Replace settings globals with `TradingConfig` (Pydantic) injected via constructor. | One source of truth; pytest fixture overrides.    |
| **Parameterise Risk**  | Position sizing formula based on ATR & `%_of_equity`.                              | Tests prove sizing changes when bankroll changes. |

### 3.2 Event Bus & Messaging (P1‑P2)

| Task                              | Detail                                                              | Acceptance                                  |
| --------------------------------- | ------------------------------------------------------------------- | ------------------------------------------- |
| **Adopt Redis Streams (initial)** | Use `aioredis` stream: durable, at‑least‑once, capped length.       | Coordinator restarts without lost signals.  |
| **Define Event Schema**           | `SignalGenerated`, `PlanExecuted`, `ErrorRaised` – JSON w/ version. | Schema unit tests, backwards‑compat check.  |
| **Back‑pressure Logic**           | Consumer groups + pending entries; auto‑nack after retry‑limit.     | Stress test (10× rate) completes w/o crash. |

### 3.3 Configuration System (P1)

* Create `config/` module: `AppSettings`, `APISettings`, `ScheduleSettings` (Pydantic, env‑driven).
* Support profiles: `dev`, `paper`, `prod`.

### 3.4 Error Handling & Resilience (P1)

* Wrap all HTTP/WS calls with **tenacity** (exponential/jitter).
* Custom exception hierarchy; criticals published to `ErrorRaised` event.

### 3.5 Scheduler & Time‑zone (P2)

* Replace hard‑coded times with cron‑like `scanner_cron: "0 13 * * 1-5"` in settings.
* Evaluate with **pendulum** respecting `tz`. Unit tests for DST edge‑cases.

### 3.6 Persistence (P2)

Phase 1: Enable SQLite **WAL**; move writes to a single async queue.

Phase 2: Option to migrate to **DuckDB** (analytical) or **TimescaleDB** (time‑series).

### 3.7 Observability (P2)

* **Structured logs** to stdout (python‑json‑logger).
* **Health‑check** FastAPI endpoint (`/healthz`).
* **OpenTelemetry** tracing + Prometheus metrics (`processing_time_seconds`).
* Dashboard: add status banner fed by `ErrorRaised` events.

### 3.8 Deployment & DevOps (P2)

| Artefact             | Content                                           |
| -------------------- | ------------------------------------------------- |
| `Dockerfile`         | Multi‑stage build, poetry install, non‑root user. |
| `docker-compose.yml` | app, redis, dashboard, prometheus, grafana.       |
| GitHub Actions       | lint → test → build‑image → push registry.        |
| Secrets              | GH Encrypted Secrets + Docker secrets in compose. |

### 3.9 Testing & QA (cross‑cutting)

* **mypy --strict** gate in CI.
* Integration test spins local Redis, hits FastAPI, validates end‑to‑end.
* Concurrency test simulates 1000 WS messages/s.

### 3.10 Security (P2)

* Move API keys to env + Docker secrets; prohibit `.env` commit.
* Add Bandit static scan in CI.

---

## 4. Incremental Roadmap & Timeline (ideal sprint = 1 wk)

| Sprint            | Focus                     | Key Deliverables                                                 |
| ----------------- | ------------------------- | ---------------------------------------------------------------- |
| **0 (Hardening)** | Quick wins                | Tenacity wrappers, WAL, JSON logs, health‑check.                 |
| **1**             | Domain extraction         | Pure aggregates & ports; config injection.                       |
| **2**             | Durable bus               | Redis Streams integration; schema & back‑pressure.               |
| **3**             | Scheduler + Observability | Cron config, DST tests, tracing & metrics.                       |
| **4**             | DevOps                    | Docker/compose, GH Actions, secrets.                             |
| **5**             | Persistence upgrade       | Optional DuckDB/Timescale migration; data migration script.      |
| **6+**            | Evolution                 | Model‑based scoring, broker API integration, advanced analytics. |

Total **6–8 weeks** with 2 devs.

---

## 5. Risk & Mitigation

| Risk                         | Likelihood | Impact              | Mitigation                                                      |
| ---------------------------- | ---------- | ------------------- | --------------------------------------------------------------- |
| Redis downtime               | Low        | Message loss        | Enable AOF persistence; local fallback queue.                   |
| Domain refactor stalls scans | Med        | Trading halted      | Feature flags – run old & new side‑by‑side until parity proven. |
| Schema drift                 | Med        | Incompatible events | Version events; contract tests in CI.                           |

---

## 6. Acceptance Criteria (v1.0‑GA)

* End‑to‑end test passes with 10× live rate and forced crash‑restart.
* CI pipeline green on lint, mypy, 100 % tests.
* Docker `docker-compose up` produces live dashboard in <30 s.
* Strategy config editable via `config.yaml` without code change.
* Secrets never written to disk in container.

---

### Conclusion

This refactor keeps the strong points of the existing project—async workflow, clear layers, tidy tests—while solving durability, configurability and operability.  Executed incrementally, the plan delivers immediate reliability wins and positions the codebase for sophisticated strategy and execution features down the road.
