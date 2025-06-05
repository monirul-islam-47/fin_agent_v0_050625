# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the One-Day Trading Agent (ODTA) - a free-tier Python trading assistant that identifies 5 US stocks daily with 7-10% intraday profit potential. The system is designed to work within API free-tier limits and enforces strict risk management (€33 daily loss cap, €250 position limits).

## Development Commands

### Running the Application
```bash
# Interactive dashboard (primary interface)
streamlit run dashboard.py

# CLI commands
python -m src.main status      # Check system status and API quotas
python -m src.main scan        # Run primary market scan (14:00 CET)
python -m src.main second-look # Run second-look scan (18:15 CET)
```

### Testing
```bash
# Run all tests with coverage
pytest tests/ -v --cov=src

# Run specific test categories
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest tests/system/ -v
```

### Code Quality
```bash
# Format code
black src/ tests/

# Lint code
flake8 src/ tests/

# Type checking
mypy src/
```

## Architecture & Key Design Decisions

### Layered Architecture
The codebase follows a clean layered architecture with strict separation of concerns:

1. **Infrastructure Layer** (`src/config/`, `src/utils/`)
   - Configuration management with environment variables
   - Structured logging with color support
   - Quota management with automatic fallback triggers

2. **Data Layer** (`src/data/`)
   - Abstract base classes for all data adapters
   - Fallback chain: Finnhub WebSocket → YFinance (delayed)
   - JSON-based caching with TTL support for human readability

3. **Domain Layer** (`src/domain/`)
   - Pure business logic isolated from external dependencies
   - Multi-factor scoring model with configurable weights
   - Risk management enforcing position limits and loss caps

4. **Orchestration Layer** (`src/orchestration/`)
   - Event-driven architecture using asyncio
   - Scheduled scans at 14:00 and 18:15 CET
   - Graceful error recovery and state management

5. **Presentation Layer** (`dashboard.py`)
   - Streamlit-based interactive dashboard
   - Real-time WebSocket data streaming
   - Manual controls for factor weights and scan triggers

### Critical Implementation Notes

1. **Async-First Design**: All data adapters and orchestration use asyncio for better performance with 500+ symbols

2. **Quota Management**: The `QuotaGuard` decorator tracks API usage and automatically triggers fallback when approaching limits. Check quota status before implementing new API calls.

3. **Free-Tier Strategy**: 
   - Primary: Finnhub WebSocket (60 calls/min)
   - Fallback: YFinance (unlimited but 15-min delayed)
   - Always tag delayed data appropriately

4. **Risk Compliance**: All trades must pass PRIIPs/KID validation. The risk manager enforces hard caps that cannot be overridden.

5. **Caching Strategy**: Use JSON files for all caching (not SQLite) to maintain human readability for debugging.

## Development Workflow

### When Adding New Features
1. Follow the existing patterns in the respective layer
2. Ensure all new data sources have fallback options
3. Add appropriate quota tracking for any API calls
4. Update the progress tracker in `docs/research/opus_progress.md`

### Build Plan Reference
The detailed implementation plan is in `docs/research/opus_build_plan.md`. Key phases:
- Phase 0-1: Foundation & Infrastructure ✅ COMPLETED
- Phase 2: Data Acquisition Layer 🔄 IN PROGRESS
- Phase 3-7: Domain Logic through Testing (pending)

### Current Status
Check `docs/research/opus_progress.md` for the latest development status and completed features.

## Important Documentation

### Research Documents (Always consult these for requirements and design decisions)
- `docs/research/PRD.md` - Product Requirements Document with detailed specifications
- `docs/research/agent_description.md` - Problem statement and agent capabilities
- `docs/research/agent_arrchitecture.md` - Technical architecture diagrams and data flows
- `docs/research/opus_build_plan.md` - Comprehensive implementation plan (FOLLOW THIS)
- `docs/research/opus_progress.md` - Current development status and completed features
- `docs/research/o3_build_plan.md` - Alternative build plan for reference

**Note**: Always refer to these documents when implementing features or making architectural decisions. The opus_build_plan.md is the primary guide for development.

## Environment Setup

Required environment variables (create `.env` from `.env.template`):
- `FINNHUB_API_KEY`: Primary real-time data source
- `ALPHA_VANTAGE_API_KEY`: Historical data only
- `NEWS_API_KEY`: News headlines
- `GDELT_PROJECT_ID`: Optional, for GDELT integration

## Trading Universe

The system requires a CSV file at `data/universe/revolut_universe.csv` with Revolut-tradable symbols. Copy from the template and populate with valid US stock symbols.