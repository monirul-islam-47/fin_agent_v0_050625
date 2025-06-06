# Refactoring Plan – **fin\_agent v0\_050625** (One‑Day Trading Agent)

*Revised after over‑engineering critique – “Robust Monolith” variant*

---

## 0 Guiding Principles

1. **Solve the real problem, no extra complexity.** Durability, testability and observability are required; distributed messaging is optional until scale justifies it.
2. **Clean Architecture & DDD.** Domain core is pure Python; all I/O crosses explicit ports.
3. **Evolution over revolution.** Each increment leaves the system runnable and passing tests.
4. **Batteries‑included ops.** Everything ships in one Docker container; CI builds/flags on coverage & type‑checks.

---

## 1 Current Pain‑Points Recap

| Area                       | Symptom                              | Impact                                           |
| -------------------------- | ------------------------------------ | ------------------------------------------------ |
| In‑memory EventBus         | Loses messages on crash              | Potential missed trade signals, silent data gaps |
| Global settings leakage    | Domain knows about env vars & logger | Hard to unit‑test, brittle config                |
| Hard‑coded CET schedule    | DST issues; non‑portable             | Missed scans in other locales                    |
| Minimal observability      | Pretty console logs only             | No trend analysis, slow prod debugging           |
| One‑process, no durability | Crash = restart from zero            | Manual recovery effort                           |

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
            ▼                                  │ ScoringService    │
┌──────────────────────────┐                  │ PlannerService    │
│ EventLoopProcessor Task  │                 │ RiskService       │
└──────────────────────────┘                  └───────────────────┘
```

*One process, one DB file, multiple coroutines.*

---

## 3 Step‑by‑Step Roadmap

| Phase                           | Scope                                                                                                                                                                                                                 | Deliverables                                                              | Acceptance                                                         |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| **P0 – Baseline**               | CI pipeline & Docker skeleton                                                                                                                                                                                         | `Dockerfile`, GitHub Action testing matrix, 100 % tests pass in container | All tests green in CI                                              |
| **P1 – Domain Isolation**       | Extract *domain* pkg hosting `GapCandidate`, `TradePlan`, `RiskProfile`. Define `MarketDataPort`, `NewsPort`, `OrderExecutionPort`. Remove all logger/env imports from domain.                                        | Pure‑python domain package, mypy `--strict` passes                        | Unit tests run with mocks only                                     |
| **P2 – Config & Error Hygiene** | Introduce `Settings` via **pydantic‑settings**; inject into services. Wrap all external HTTP calls with **tenacity** exponential back‑off.                                                                            | No `os.getenv` in business code, retry decorator applied                  | Induced API 503 returns succeed after retries in e2e test          |
| **P3 – SQLite Durable Queue**   | Add `job_queue` table (`id`, `type`, `payload`, `status`, timestamps). Implement `QueuePublisher` helper & `EventLoopProcessor` polling task (back‑off 250 ms). Services switch to consuming in‑proc `asyncio.Queue`. | Crash test: kill process mid‑workflow, restart; pending jobs resume       | No Redis/NATS running; only SQLite                                 |
| **P4 – Service Concurrency**    | Wrap each service (`ScannerService`, `ScoringService`, `PlannerService`, `RiskService`) in its own `asyncio.Task`.                                                                                                    | Services share ApplicationCtx; zero deadlocks in soak test                | `pytest -q tests/stateful` passes for 6‑hour simulated market feed |
| **P5 – Scheduling**             | Replace custom CET math with **pendulum** cron expressions, stored in `settings.yaml`. Support user‑override via CLI flag/env.                                                                                        | Schedule unit tests across DST boundary pass                              |                                                                    |
| **P6 – Observability Minimum**  | Structured JSON logs (python‑json‑logger). Add **prometheus‑client** counters: job latency, API retries, scan durations. Expose `/metrics` HTTP endpoint.                                                             | `curl /metrics` shows >0 metrics; Grafana dashboard JSON committed        |                                                                    |
| **P7 – Ops & Secrets**          | 1‑container `docker-compose.yml` (app + optional grafana/prometheus sidecars). Secrets via env vars only.                                                                                                             | `docker compose up` launches full stack; no missing secrets error         |                                                                    |
| **P8 – Optional Future**        | Swap Queue adapter for Redis Streams when: sustained >5 jobs/s *or* need multi‑process scaling. Provide `QueueInterface` + redis implementation.                                                                      | Benchmark shows <500 µs publish latency; adapter switch toggled in config |                                                                    |

---

## 4 Risk & Mitigation

| Risk                                     | Likelihood | Impact | Mitigation                                                    |
| ---------------------------------------- | ---------- | ------ | ------------------------------------------------------------- |
| SQLite DB lock under high write          | Low        | Medium | Enable WAL; keep writes small; single writer pattern          |
| Long‑running coroutine blocks event loop | Medium     | High   | Guard heavy CPU in `asyncio.to_thread()`; add watchdog metric |
| API vendor rate limits                   | Medium     | Medium | Tenacity retries + exponential back‑off + randomized jitter   |
| Time‑zone misconfig                      | Low        | Medium | Pendulum TZ tests; config validation at startup               |

---

## 5 Success Metrics

* 100 % unit test coverage, 90 % branch coverage.
* Full e2e run recovers after forced `SIGKILL` with zero lost trade signals.
* Average end‑to‑end scan latency <1 s on MacBook Air M1.
* No unhandled exceptions in 24‑hour soak test.

---

## 6 Appendix – Script Templates

```bash
# launch_dev.sh
export FINAGENT_ENV=dev
uvicorn finagent.app:make_app --reload
```

```dockerfile
# Dockerfile (single stage)
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt --no-cache-dir
CMD ["python", "-m", "finagent.cli", "orchestrate"]
```

---

### Upgrade Path to Redis/NATS (Reference Only)

1. Implement `RedisQueueAdapter` satisfying `QueueInterface`.
2. Toggle with `settings.queue_backend = "redis"`.
3. Verify contract tests shared across adapters.

*Ship today with SQLite; evolve when scale demands.*
