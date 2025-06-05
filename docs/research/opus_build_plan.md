# Opus Build Plan - One-Day Trading Agent (ODTA)
*Created: 2025-06-05*

## Overview
This build plan outlines the implementation approach for the One-Day Trading Agent (ODTA), a free-tier Python assistant that recommends 5 US stocks daily with 7-10% intraday profit potential for a €500 bankroll via Revolut.

## Core Principles
1. **Modular Architecture** - Clean separation between data, domain, and presentation layers
2. **Free-Tier First** - Smart quota management and fallback strategies
3. **Risk-Controlled** - Enforced €33 daily loss cap and €250 per position limit
4. **User-Centric** - Interactive Streamlit dashboard with real-time updates

## Implementation Phases

### Phase 0: Foundation & Environment Setup
**Goal**: Create project skeleton with proper structure and tooling

**Tasks**:
- Initialize project structure with proper Python packaging
- Set up virtual environment (Python 3.11)
- Create requirements.txt with core dependencies
- Configure .env template and .gitignore
- Set up basic Streamlit app with placeholder content
- Initialize git repository

**Key Files**:
```
fin_agent_v0_050625/
├── requirements.txt
├── .env.template
├── .gitignore
├── README.md
├── setup.py
├── src/
│   └── __init__.py
└── dashboard.py
```

### Phase 1: Infrastructure Layer
**Goal**: Build robust configuration, logging, and quota management

**Components**:
1. **Config Management** (`src/config.py`)
   - Load environment variables and defaults
   - Singleton pattern for global access
   - Trading parameters (times, caps, thresholds)

2. **Logging System** (`src/logger.py`)
   - Structured logging with color coding
   - Log levels: DEBUG, INFO, WARNING, ERROR
   - Rotation and archival support

3. **Quota Guard** (`src/quota.py`)
   - Track API calls per provider
   - Rate limiting decorator
   - Automatic fallback triggers
   - Real-time quota status

**Key Decisions**:
- Use dataclasses for configuration
- Implement quota persistence in JSON for simplicity
- Async-first design for all components

### Phase 2: Data Acquisition Layer
**Goal**: Reliable market data ingestion with intelligent fallback

**Primary Components**:

1. **Market Data Adapters** (`src/data/market.py`)
   - FinnhubWebSocket: Real-time quotes (primary)
   - YFinance: Delayed data fallback
   - YFinance: Delayed data last resort
   - Unified interface for all providers

2. **News & Sentiment** (`src/data/news.py`)
   - GDELT integration for breaking news
   - NewsAPI for comprehensive coverage
   - VADER sentiment analysis
   - Headline deduplication

3. **Cache Manager** (`src/data/cache.py`)
   - JSON-based storage (human-readable)
   - TTL-based expiration
   - Atomic writes
   - Quick retrieval by symbol/date

**Fallback Strategy**:
```
Finnhub WS → IEX REST → YFinance (15min delay)
     ↓            ↓              ↓
  Real-time    1-2s delay    Tag as DELAYED
```

### Phase 3: Domain Logic Layer
**Goal**: Core trading intelligence and risk management

**Components**:

1. **Universe Manager** (`src/domain/universe.py`)
   - Load Revolut tradable symbols
   - Liquidity filtering (≥€5M ADV)
   - Price range validation (€2-€300)
   - PRIIPs compliance check

2. **Gap Scanner** (`src/domain/scanner.py`)
   - Pre-market gap calculation
   - Volume spike detection
   - Unusual options activity flag
   - Short interest tracker

3. **Factor Scoring** (`src/domain/scoring.py`)
   - Multi-factor model:
     - Volatility: 40% (ATR, gap %)
     - Catalyst: 30% (earnings, news)
     - Sentiment: 10% (VADER score)
     - Liquidity: 20% (spread, volume)
   - Dynamic weight adjustment via UI

4. **Trade Planner** (`src/domain/planner.py`)
   - Entry: VWAP ± tolerance or ORB
   - Stop: -3% or 2×ATR(5)
   - Target: +8-10% scaled by volatility
   - Position sizing with Kelly criterion

5. **Risk Manager** (`src/domain/risk.py`)
   - Daily loss cap enforcement (€33)
   - Position limit check (€250)
   - Correlation analysis
   - Drawdown tracking

### Phase 4: Orchestration Layer
**Goal**: Coordinate components and manage execution flow

**Core Components**:

1. **Event Bus** (`src/orchestration/bus.py`)
   - Async message passing
   - Event types: SCAN_START, QUOTE_UPDATE, PLAN_READY
   - Subscribe/publish pattern
   - Error propagation

2. **Scheduler** (`src/orchestration/scheduler.py`)
   - Primary scan at 14:00 CET
   - Continuous monitoring 14:00-16:15
   - Second-look scan at 18:15
   - Manual trigger support

3. **Main Coordinator** (`src/orchestration/main.py`)
   - Workflow orchestration
   - State management
   - Error recovery
   - Graceful shutdown

### Phase 5: Presentation Layer
**Goal**: Interactive Streamlit dashboard

**UI Components**:

1. **Dashboard Layout** (`dashboard.py`)
   - Sidebar: Controls & settings
   - Main: Top-5 recommendations table
   - Charts: Plotly candlesticks
   - Alerts: Toast notifications

2. **Real-time Updates**
   - WebSocket data streaming
   - Auto-refresh on new scans
   - Live quota badges
   - Price ticker updates

3. **Interactive Features**
   - Factor weight sliders
   - Manual scan triggers
   - Symbol deep-dive drawer
   - Historical performance view

4. **Visual Design**
   - Dark/light theme support
   - Responsive layout
   - Color-coded sentiment
   - Loading states

### Phase 6: Persistence & Analytics
**Goal**: Track performance and enable optimization

**Components**:

1. **Trade Journal** (`src/persistence/journal.py`)
   - CSV format for easy analysis
   - Auto-capture all recommendations
   - Manual P&L entry support
   - Export functionality

2. **Metrics Tracking** (`src/persistence/metrics.py`)
   - Hit rate calculation
   - Average gain/loss
   - Sharpe ratio
   - Maximum drawdown

3. **Quota Logger** (`src/persistence/quota_log.py`)
   - Daily usage stats
   - Cost projections
   - Fallback frequency
   - API health metrics

### Phase 7: Testing & Quality
**Goal**: Ensure reliability and maintainability

**Test Strategy**:

1. **Unit Tests** (`tests/unit/`)
   - Pure functions first
   - Mock external dependencies
   - 80% coverage target

2. **Integration Tests** (`tests/integration/`)
   - API adapter verification
   - Fallback scenarios
   - Cache operations

3. **System Tests** (`tests/system/`)
   - Full workflow validation
   - Performance benchmarks
   - Load testing

4. **CI/CD Pipeline**
   - GitHub Actions
   - Automated testing
   - Code quality checks
   - Security scanning

## Technical Stack

```yaml
Core:
  - Python: 3.11+
  - Async: asyncio, aiohttp
  - Web: Streamlit 1.32+

Data:
  - WebSocket: websockets
  - HTTP: httpx with retry
  - Cache: JSON files
  - Analysis: pandas, numpy

UI:
  - Charts: Plotly
  - Layout: Streamlit native
  - Styling: Custom CSS

Testing:
  - Framework: pytest
  - Mocking: pytest-mock
  - Coverage: pytest-cov
```

## Development Timeline

| Week | Phase | Deliverables |
|------|-------|--------------|
| 1 | Foundation + Infrastructure | Working skeleton, config, logging |
| 2 | Data Layer | All API adapters, caching, fallback |
| 3 | Domain Logic | Screening, scoring, planning |
| 4 | Orchestration + UI | Full workflow, basic dashboard |
| 5 | Polish + Testing | Complete UI, 80% test coverage |

## Risk Mitigation

1. **API Quota Exhaustion**
   - Implement aggressive caching
   - Use WebSocket where possible
   - Graceful degradation to delayed data

2. **Performance Issues**
   - Profile critical paths
   - Implement data sampling
   - Use async throughout

3. **Compliance Concerns**
   - PRIIPs validation on every pick
   - Clear risk disclosures
   - Audit trail of all recommendations

## Success Criteria

- [ ] Delivers Top-5 picks by 14:20 CET (95% of days)
- [ ] 60%+ win rate on 7% profit target
- [ ] Zero API quota overages
- [ ] Dashboard loads in <5 seconds
- [ ] Handles 500+ symbols efficiently
- [ ] Clean fallback to delayed data

## Next Steps

1. Set up development environment
2. Implement Phase 0 foundation
3. Begin Phase 1 infrastructure components
4. Create detailed test plan
5. Set up monitoring and alerting

---

*This plan prioritizes practical implementation over theoretical perfection, focusing on getting a working system quickly while maintaining quality and extensibility.*