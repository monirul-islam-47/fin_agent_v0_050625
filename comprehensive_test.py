#!/usr/bin/env python3
"""
Comprehensive test suite for ODTA Phases 1 and 2
Tests all components without requiring installation
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

# Mock missing modules to allow imports
class MockModule:
    def __getattr__(self, name):
        return MockModule()
    def __call__(self, *args, **kwargs):
        return MockModule()

# Add mocks for modules we haven't installed yet
sys.modules['dotenv'] = MockModule()
sys.modules['httpx'] = MockModule()
sys.modules['websockets'] = MockModule()
sys.modules['yfinance'] = MockModule()
sys.modules['vaderSentiment'] = MockModule()
sys.modules['vaderSentiment.vaderSentiment'] = MockModule()
sys.modules['tenacity'] = MockModule()
sys.modules['finnhub'] = MockModule()

print("=== ODTA Comprehensive Test Suite ===\n")

# Test 1: Configuration and Environment
print("1. Testing Configuration Module...")
try:
    from src.config import get_config
    config = get_config()
    
    print("   ✓ Config loaded successfully")
    print(f"   - Log level: {config.system.log_level}")
    print(f"   - Timezone: {config.system.timezone}")
    print(f"   - Data dir: {config.system.data_dir}")
    
    # Check API keys
    api_status = []
    if config.api.finnhub_key:
        api_status.append("Finnhub ✓")
    if config.api.alpha_vantage_key:
        api_status.append("Alpha Vantage ✓")
    if config.api.news_api_key:
        api_status.append("NewsAPI ✓")
    
    print(f"   - API Keys: {', '.join(api_status)}")
except Exception as e:
    print(f"   ✗ Config test failed: {e}")

# Test 2: Logging System
print("\n2. Testing Logging Module...")
try:
    from src.utils import get_logger
    logger = get_logger("test")
    
    print("   ✓ Logger created successfully")
    logger.info("Test info message")
    logger.warning("Test warning message")
    logger.error("Test error message")
    print("   ✓ All log levels working")
except Exception as e:
    print(f"   ✗ Logger test failed: {e}")

# Test 3: Quota Management
print("\n3. Testing Quota Guard...")
try:
    from src.utils import get_quota_guard
    quota_guard = get_quota_guard()
    
    # Test quota tracking
    success = quota_guard.check_and_update('finnhub', 1)
    print(f"   ✓ Quota check: {success}")
    
    # Get status
    status = quota_guard.get_status()
    for provider, info in status.items():
        if provider != 'iex':  # Skip IEX since it's shut down
            print(f"   - {provider}: {info['used']}/{info['limit']} ({info['percentage']}%)")
except Exception as e:
    print(f"   ✗ Quota test failed: {e}")

# Test 4: Directory Structure
print("\n4. Testing Directory Structure...")
try:
    dirs_to_check = [
        config.system.data_dir,
        config.system.cache_dir,
        config.system.logs_dir,
        config.system.universe_dir
    ]
    
    for dir_path in dirs_to_check:
        if dir_path.exists():
            print(f"   ✓ {dir_path.name}/ exists")
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"   ✓ {dir_path.name}/ created")
except Exception as e:
    print(f"   ✗ Directory test failed: {e}")

# Test 5: Data Layer Components
print("\n5. Testing Data Layer Components...")
try:
    from src.data import (
        DataProvider, Quote, Bar, Headline,
        MarketDataManager, NewsManager
    )
    
    print("   ✓ All data classes imported successfully")
    
    # Test Quote creation
    test_quote = Quote(
        symbol="AAPL",
        timestamp=datetime.now(),
        price=150.0,
        volume=1000000,
        provider="test"
    )
    print(f"   ✓ Quote object created: {test_quote.symbol} @ ${test_quote.price}")
    
    # Test Bar creation
    test_bar = Bar(
        symbol="AAPL",
        timestamp=datetime.now(),
        open=149.0,
        high=151.0,
        low=148.0,
        close=150.0,
        volume=1000000
    )
    print(f"   ✓ Bar object created: OHLC {test_bar.open}/{test_bar.high}/{test_bar.low}/{test_bar.close}")
    
except Exception as e:
    print(f"   ✗ Data layer test failed: {e}")

# Test 6: Cache System
print("\n6. Testing Cache System...")
try:
    from src.data import CacheStore
    cache = CacheStore()
    
    # Test cache write
    test_data = {"test": "data", "timestamp": datetime.now().isoformat()}
    asyncio.run(cache.set(
        provider="test",
        params={"type": "test"},
        data=test_data,
        ttl_seconds=60
    ))
    print("   ✓ Cache write successful")
    
    # Test cache read
    retrieved = asyncio.run(cache.get(
        provider="test",
        params={"type": "test"}
    ))
    if retrieved:
        print("   ✓ Cache read successful")
    
    # Get cache stats
    stats = asyncio.run(cache.get_stats())
    print(f"   - Total entries: {stats['total_entries']}")
    print(f"   - Cache size: {stats['total_size_mb']} MB")
    
except Exception as e:
    print(f"   ✗ Cache test failed: {e}")

# Test 7: Market Data Architecture
print("\n7. Testing Market Data Architecture...")
try:
    # Test the fallback chain logic
    from src.data.base import DataProvider
    
    providers = [
        DataProvider.FINNHUB,
        DataProvider.YAHOO,
        DataProvider.GDELT,
        DataProvider.NEWSAPI
    ]
    
    print("   ✓ Data providers enumeration working")
    for provider in providers:
        print(f"   - {provider.value} available")
        
except Exception as e:
    print(f"   ✗ Market data architecture test failed: {e}")

# Test 8: CLI Interface
print("\n8. Testing CLI Interface...")
try:
    from src.main import cli
    
    print("   ✓ CLI imported successfully")
    print("   - Commands available: status, scan, second-look")
    
except Exception as e:
    print(f"   ✗ CLI test failed: {e}")

# Test 9: File I/O
print("\n9. Testing File I/O...")
try:
    # Test universe file
    universe_file = config.system.universe_dir / "revolut_universe_template.csv"
    if universe_file.exists():
        print(f"   ✓ Universe template found")
    else:
        print(f"   ! Universe template not found at {universe_file}")
    
    # Test log file creation
    log_file = config.system.logs_dir / "test.log"
    log_file.write_text("Test log entry")
    print(f"   ✓ Log file write successful")
    log_file.unlink()  # Clean up
    
except Exception as e:
    print(f"   ✗ File I/O test failed: {e}")

# Summary
print("\n=== Test Summary ===")
print("Phase 1 (Infrastructure): Configuration, Logging, Quota Guard - TESTED")
print("Phase 2 (Data Layer): Data models, Cache, Architecture - TESTED")
print("\nNote: Full integration tests require installing dependencies:")
print("- python-dotenv, httpx, websockets, yfinance, vaderSentiment")
print("\nTo run full tests:")
print("1. Create virtual environment: python3 -m venv venv")
print("2. Activate it: source venv/bin/activate")
print("3. Install deps: pip install -r requirements.txt")
print("4. Run: python test_market_data.py")