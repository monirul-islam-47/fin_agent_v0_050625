#!/usr/bin/env python3
"""
Quick test script for market data functionality
Run this to verify the data adapters are working
"""

import asyncio
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add src to path
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.data import MarketDataManager, Quote
from src.utils import get_logger

logger = get_logger(__name__)

async def test_market_data():
    """Test market data manager with fallback"""
    
    # Initialize manager
    manager = MarketDataManager()
    await manager.initialize()
    
    print("\n=== Testing Market Data Manager ===\n")
    
    # Test 1: Single quote
    print("1. Testing single quote fetch...")
    quote = await manager.get_quote("AAPL")
    if quote:
        print(f"   ✓ Got quote for AAPL: ${quote.price} from {quote.provider}")
        print(f"   - Timestamp: {quote.timestamp}")
        print(f"   - Delayed: {quote.is_delayed}")
    else:
        print("   ✗ Failed to get quote for AAPL")
    
    # Test 2: Batch quotes
    print("\n2. Testing batch quote fetch...")
    symbols = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]
    quotes = await manager.get_quotes(symbols)
    
    for symbol, quote in quotes.items():
        if quote:
            print(f"   ✓ {symbol}: ${quote.price} from {quote.provider}")
        else:
            print(f"   ✗ {symbol}: No quote available")
    
    # Test 3: Historical bars
    print("\n3. Testing historical bars...")
    end = datetime.now()
    start = end - timedelta(days=1)
    
    bars = await manager.get_bars("AAPL", start, end, "5min")
    if bars:
        print(f"   ✓ Got {len(bars)} bars for AAPL")
        print(f"   - First bar: {bars[0].timestamp} O:{bars[0].open} H:{bars[0].high} L:{bars[0].low} C:{bars[0].close}")
        print(f"   - Last bar: {bars[-1].timestamp} O:{bars[-1].open} H:{bars[-1].high} L:{bars[-1].low} C:{bars[-1].close}")
    else:
        print("   ✗ Failed to get bars for AAPL")
    
    # Test 4: Check current data priority
    print(f"\n4. Current data priority: {manager.get_current_priority()}")
    
    # Test 5: Cache stats
    print("\n5. Testing cache...")
    cache_stats = await manager.cache.get_cache_stats()
    print(f"   - Total entries: {cache_stats['total_entries']}")
    print(f"   - Active entries: {cache_stats['active_entries']}")
    print(f"   - Memory entries: {cache_stats['memory_entries']}")
    
    # Clean up
    await manager.shutdown()
    print("\n=== Test Complete ===")

async def test_websocket():
    """Test WebSocket streaming (requires Finnhub API key)"""
    print("\n=== Testing WebSocket Streaming ===\n")
    
    manager = MarketDataManager()
    await manager.initialize()
    
    # Subscribe to symbols
    symbols = ["AAPL", "MSFT", "TSLA"]
    await manager.subscribe_quotes(symbols)
    
    # Start streaming
    await manager.start_quote_stream()
    
    print(f"Subscribed to {symbols}")
    print("Listening for quotes for 30 seconds...")
    
    # Wait and check for quotes
    for i in range(30):
        await asyncio.sleep(1)
        latest = manager.get_latest_quotes()
        if latest:
            print(f"\nLatest quotes ({len(latest)} symbols):")
            for symbol, quote in list(latest.items())[:5]:  # Show max 5
                print(f"  {symbol}: ${quote.price} @ {quote.timestamp}")
    
    await manager.shutdown()
    print("\n=== WebSocket Test Complete ===")

if __name__ == "__main__":
    # Check if API keys are set
    required_keys = ["FINNHUB_API_KEY", "IEX_CLOUD_API_KEY"]
    missing_keys = [key for key in required_keys if not os.getenv(key)]
    
    if missing_keys:
        print(f"Error: Missing API keys: {missing_keys}")
        print("Please set them in your .env file")
        sys.exit(1)
    
    # Run tests
    print("Starting market data tests...")
    asyncio.run(test_market_data())
    
    # Optional: Test WebSocket (uncomment to test)
    # asyncio.run(test_websocket())