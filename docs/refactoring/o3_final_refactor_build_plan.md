# **fin\_agent v0\_050625 – Comprehensive Build Plan**

*Robust‑Monolith refactor (SQLite durable queue, in‑process services)*

---

## Table of Contents

1. [Guiding Principles](#0-guiding-principles)
2. [Current Pain‑Points Recap](#1-current-pain-points-recap)
3. [Target Architecture Diagram](#2-target-architecture-robust-monolith)
4. [Phase‑by‑Phase Build Tasks](#3-step-by-step-roadmap)

   * P0 – Baseline
   * P1 – Domain Isolation
   * P2 – Config & Error Hygiene
   * P3 – SQLite Durable Queue
   * P4 – Service Concurrency
   * P5 – Time‑Zone‑Safe Scheduling
   * P6 – Observability Minimum
   * P7 – Ops & Secrets
   * P8 – Future Scaling (Redis/NATS adapter)
5. [Risk & Mitigation](#4-risk--mitigation)
6. [Success Metrics](#5-success-metrics)
7. [Appendix A – Script Templates](#6-appendix-a--script-templates)
8. [Appendix B – Queue Table DDL](#7-appendix-b--queue-table-ddl)

---

## 0 Guiding Principles

|  #  | Principle                  | Rationale                                                                          |
| --- | -------------------------- | ---------------------------------------------------------------------------------- |
|  1  | **Right‑Sized Complexity** | Only add components that directly solve an identified pain‑point.                  |
|  2  | **Pure Domain Layer**      | Domain aggregates & logic have **no** knowledge of I/O, env vars, or logging.      |
|  3  | **Evolution > Revolution** | System compiles & tests pass after every phase; no long‑lived feature branches.    |
|  4  | **Ops in a Box**           | Single Docker image (plus optional sidecars) can run all services locally & in CI. |
|  5  | **Observability First**    | Structured JSON logs & Prometheus metrics from Phase 6 onward.                     |

Return to **Table of Contents** ↑

---

## 1 Current Pain‑Points Recap

| Area                       | Symptom                             | Impact                                  |
| -------------------------- | ----------------------------------- | --------------------------------------- |
| In‑memory EventBus         | Loses messages on crash             | Missed trade signals & silent data loss |
| Global settings leakage    | Domain imports env & logger         | Difficult unit‑testing; hidden coupling |
| Hard‑coded CET schedule    | No DST awareness; not user‑override | Missed scans in other locales           |
| Minimal observability      | Pretty console logs only            | No trend analysis; slow prod debugging  |
| One‑process, no durability | Crash = restart from zero           | Manual recovery effort                  |

Return to **Table of Contents** ↑

---

## 2 Target Architecture (Robust Monolith)

```
┌────────────────────────────────────┐         ┌─────────────────────┐
│   CLI / Streamlit Dashboard       │◀───────▶│   ApplicationCtx    │
└────────────────────────────────────┘         │  (DB pool, config)  │
            ▲                                   └────────┬────────────┘
            │ SQLite (WAL)                              │
┌───────────┴──────────────┐    in‑proc asyncio Queues   │
│  SQLite Job Queue Table  │◀────────────────────────────┘
└───────────┬──────────────┘
            │                                  ┌───────────────────┐
   polling  │                                  │  ScannerService   │
  coroutine │                                  ├───────────────────┤
            ▼                                  │  ScoringService   │
┌──────────────────────────┐                  │  PlannerService    │
│ EventLoopProcessor Task  │                 │  RiskService       │
└──────────────────────────┘                  └───────────────────┘
```

*Single process, one DB file, multiple coroutines*.

Return to **Table of Contents** ↑

---

## 3 Step‑by‑Step Roadmap

> **Workflow Convention** – Each phase = short‑lived feature branch (`feat/P#_…`).
>  Merge criteria: ✅ tests + ✅ type‑checks + ✅ lint + ✅ CI docker build.

---

### **P0 – Baseline (Dev & CI Skeleton)**

|  #  | Task                                                    | File(s) / Location         | Why                                |
| --- | ------------------------------------------------------- | -------------------------- | ---------------------------------- |
| 0‑1 | Add **pytest**, **mypy** & **ruff** configs             | `pyproject.toml`           | Standard tooling, single source.   |
| 0‑2 | Create bare‑bones **Dockerfile**                        | `/Dockerfile`              | Enables identical local + CI env.  |
| 0‑3 | Create **GitHub Actions** workflow                      | `.github/workflows/ci.yml` | Auto run tests, lint, type‑checks. |
| 0‑4 | Pin Python 3.12 in `pyproject.toml` & Docker base image |                            | Futures‑ready environment.         |
| 0‑5 | Ensure **100 % unit‑tests** pass inside container       |                            | Locks baseline behaviour.          |

**Acceptance:** CI badge shows green on main branch.

---

### **P1 – Domain Isolation**

|  #  | Task                                                                | File(s) / Location                     | Change Details                                        |
| --- | ------------------------------------------------------------------- | -------------------------------------- | ----------------------------------------------------- |
| 1‑1 | Create new package `finagent/domain/`                               | new dir                                | Add `__init__.py`.                                    |
| 1‑2 | Move business types (`GapScanner`, `RiskProfile`, etc.) into domain | from `src/scanner.py`, `risk.py`, etc. | Split into `gap_scanner.py`, `risk.py` inside domain. |
| 1‑3 | Define **aggregates & value objects**                               | `domain/models.py`                     | `TradePlan`, `GapCandidate`, etc.                     |
| 1‑4 | Define **ports (ABCs)**                                             | `domain/ports.py`                      | `class MarketDataPort(Protocol): ...` etc.            |
| 1‑5 | Remove all logger/env imports from domain code                      | grep replace                           | Accept injected dependencies only.                    |
| 1‑6 | Adjust import paths in existing tests                               | all test files                         | Ensure tests import from `finagent.domain`.           |
| 1‑7 | Run **mypy --strict** & fix annotations                             | anytime                                | Enforces purity & explicitness.                       |

**Acceptance:** Unit tests pass with mocks; `python -m mypy finagent/domain --strict` has zero errors.

---

### **P2 – Config & Error Hygiene**

|  #  | Task                                                      | File(s) / Location                  | Change Details                                          |
| --- | --------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------- |
| 2‑1 | Add dependency **pydantic‑settings**                      | `pyproject.toml`                    |                                                         |
| 2‑2 | Create `finagent/config/settings.py`                      | new                                 | `class Settings(BaseSettings): ...` with env parsing.   |
| 2‑3 | Refactor services to accept `Settings` instance           | `finagent/services/*`               | Constructor injection; remove `os.getenv`.              |
| 2‑4 | Add **tenacity** to deps                                  | pyproject                           |                                                         |
| 2‑5 | Wrap all external HTTP/WS calls in `@retry` decorator     | data adapters                       | Use `wait_random_exponential`, `stop_after_attempt(5)`. |
| 2‑6 | Add integration test that stubs API with 503 then success | `tests/integration/test_retries.py` | Validates retry logic.                                  |

**Acceptance:** Grep finds zero `os.getenv(` outside config; retry test passes.

---

### **P3 – SQLite Durable Queue**

|  #  | Task                                                                             | File(s) / Location                     | Change Details                              |
| --- | -------------------------------------------------------------------------------- | -------------------------------------- | ------------------------------------------- |
| 3‑1 | Enable **WAL mode** at DB init                                                   | `finagent/db/__init__.py`              | `PRAGMA journal_mode=WAL;`                  |
| 3‑2 | Add DDL migration for `job_queue`                                                | `migrations/001_queue.sql`             | See Appendix B.                             |
| 3‑3 | Implement `QueuePublisher`                                                       | `finagent/infra/queue/sqlite_queue.py` | `publish(event: DomainEvent)` ⇒ INSERT row. |
| 3‑4 | Implement `EventLoopProcessor`                                                   | `finagent/runtime/loop.py`             | Poll `SELECT ... WHERE status='pending'`.   |
| 3‑5 | On fetch, update row to `processing` (transaction)                               | same                                   | Guarantees lock.                            |
| 3‑6 | After success, `DELETE` or set `done`.                                           | same                                   |                                             |
| 3‑7 | Add boot‑up requeue logic for unfinished jobs                                    | same                                   | Crash → resume.                             |
| 3‑8 | Wire existing Producer sites (Scanner etc.) to use `QueuePublisher`              | services                               | Replace direct in‑proc event publish.       |
| 3‑9 | Integration test: enqueue 50 jobs, force `SIGKILL`, restart, verify 50 processed | new test                               | Validates durability.                       |

**Acceptance:** Crash‑recovery test passes; no Redis container running.

---

### **P4 – Service Concurrency**

|  #  | Task                                                                        | File(s) / Location                  | Change Details                                    |
| --- | --------------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------- |
| 4‑1 | Create `finagent/runtime/service_runner.py`                                 | new                                 | Helper to `asyncio.create_task` for each service. |
| 4‑2 | Refactor each service (`ScannerService`, etc.) to expose async `run()` loop | `finagent/services/scanner.py` etc. | Consume from in‑proc `asyncio.Queue`.             |
| 4‑3 | Add `ApplicationCtx` class                                                  | `finagent/runtime/context.py`       | Holds DB pool, settings, queues.                  |
| 4‑4 | Update CLI entrypoint (`finagent/cli.py`)                                   |                                     | Build ctx, spin runner.                           |
| 4‑5 | Soak test: feed fake market data for 6 hrs in sim                           | `tests/stateful/test_soak.py`       | Detect deadlocks / memory leaks.                  |

**Acceptance:** Soak test passes; memory <200 MB, no deadlocks.

---

### **P5 – Time‑Zone‑Safe Scheduling**

|  #  | Task                                                | File(s) / Location                 | Change Details                                   |
| --- | --------------------------------------------------- | ---------------------------------- | ------------------------------------------------ |
| 5‑1 | Add **pendulum** dep                                | pyproject                          |                                                  |
| 5‑2 | Move schedule expressions to `settings.yaml`        | `config/`                          | Cron‑like: `14:00 Europe/Berlin`, `18:15` etc.   |
| 5‑3 | Implement `SchedulerService` using pendulum         | `finagent/services/scheduler.py`   | Emits `ScanRequested` events via QueuePublisher. |
| 5‑4 | DST Unit test: simulate last Sunday March & October | `tests/unit/test_scheduler_dst.py` | ensure fires correctly.                          |

**Acceptance:** DST test passes in CI.

---

### **P6 – Observability Minimum**

|  #  | Task                                                                                       | File(s) / Location                    | Change Details                              |
| --- | ------------------------------------------------------------------------------------------ | ------------------------------------- | ------------------------------------------- |
| 6‑1 | Add **python‑json‑logger** dep                                                             | pyproject                             |                                             |
| 6‑2 | Configure root logger to JSON                                                              | `finagent/logging.py`                 | Fields: ts, level, msg, service, event\_id. |
| 6‑3 | Add **prometheus‑client** dep                                                              |                                       |                                             |
| 6‑4 | Expose `/metrics` ASGI route (Starlette fast‑API mini app)                                 | `finagent/metrics/app.py`             | Run in same process.                        |
| 6‑5 | Instrument counters: `queue_latency_seconds`, `api_retries_total`, `scan_duration_seconds` | scattered                             | Use decorators.                             |
| 6‑6 | Add Grafana dashboard JSON (optional)                                                      | `ops/grafana/finagent_dashboard.json` |                                             |

**Acceptance:** `curl localhost:8000/metrics` shows metrics; logs are valid JSON.

---

### **P7 – Ops & Secrets**

|  #  | Task                                                                      | File(s) / Location | Change Details                         |
| --- | ------------------------------------------------------------------------- | ------------------ | -------------------------------------- |
| 7‑1 | Finalize **Dockerfile** (slim, poetry/venv)                               | root               | Multi‑stage build optional.            |
| 7‑2 | Add `docker-compose.yml` for app + (optional) grafana/prometheus sidecars | root               | Quick local run.                       |
| 7‑3 | Create `.env.example` documenting required secrets                        | root               | Keys: FINNHUB\_KEY, NEWSAPI\_KEY, etc. |
| 7‑4 | Update GitHub Actions to inject secrets via repo settings                 | CI yml             | Uses `${{ secrets.FINNHUB_KEY }}`.     |
| 7‑5 | README update – local dev & prod run commands                             | README.md          | Precise instructions.                  |

**Acceptance:** `docker compose up` works on clean machine; README steps succeed.

---

### **P8 – Future Scaling (Optional)**

This phase **starts only if** sustained queue publish > 5 msg/s or multi‑process workers required.

|  #  | Task                                             | File(s) / Location                     | Change Details                 |
| --- | ------------------------------------------------ | -------------------------------------- | ------------------------------ |
| 8‑1 | Define `QueueInterface` ABC                      | `finagent/infra/queue/interface.py`    | `publish`, `subscribe`, `ack`. |
| 8‑2 | Implement `RedisQueueAdapter` using Streams      | `infra/queue/redis_queue.py`           | Requires `redis-py`.           |
| 8‑3 | Add config flag `settings.queue_backend="redis"` | config                                 |                                |
| 8‑4 | Contract tests across adapters                   | `tests/contract/test_queue_adapter.py` | Ensure identical behaviour.    |
| 8‑5 | Benchmark script `scripts/bench_queue.py`        |                                        | Output latency stats.          |

**Acceptance:** Contract tests pass; latency <500 µs publish on localhost.

Return to **Table of Contents** ↑

---

## 4 Risk & Mitigation

| Risk                               | Likelihood | Impact | Mitigation                                                       |
| ---------------------------------- | ---------- | ------ | ---------------------------------------------------------------- |
| SQLite DB lock under high write    | Low        | Medium | WAL mode; single writer; queue row payload < 4 KB                |
| Long‑running coroutine blocks loop | Medium     | High   | Guard heavy CPU with `asyncio.to_thread`; expose watchdog metric |
| Vendor rate limit                  | Medium     | Medium | Tenacity jitter; adaptive back‑off; API key rotation             |
| Time‑zone misconfig                | Low        | Medium | Config validation; pendulum test matrix                          |

Return to **Table of Contents** ↑

---

## 5 Success Metrics

* 100 % unit‑test coverage; ≥90 % branch coverage.
* Crash‑recovery test (Phase 3) shows zero lost messages.
* 24‑hour soak test completes with **no** unhandled exceptions.
* End‑to‑end daily scan latency <1 s on M1 laptop.

Return to **Table of Contents** ↑

---

## 6 Appendix A – Script Templates

```bash
# launch_dev.sh (Phase 7)
export FINAGENT_ENV=dev
uvicorn finagent.app:make_app --reload --port 8000
```

```dockerfile
# Dockerfile (Phase 7)
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN pip install --upgrade pip && pip install poetry && poetry config virtualenvs.create false && poetry install --no-dev --only main
COPY . .
CMD ["python", "-m", "finagent.cli", "orchestrate"]
```

Return to **Table of Contents** ↑

---

## 7 Appendix B – Queue Table DDL

```sql
-- migrations/001_queue.sql
CREATE TABLE IF NOT EXISTS job_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT CHECK(status IN ('pending','processing','done')) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_job_status ON job_queue(status);
```

Return to **Table of Contents** ↑
