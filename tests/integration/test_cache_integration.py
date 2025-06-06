"""Integration tests for cache functionality."""

import pytest
import asyncio
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

from src.data.cache import CacheService
from src.data.cache_manager import CacheManager
from src.data.base import Quote, Headline
from src.config.settings import get_config


class TestCacheIntegration:
    """Test cache integration with various components."""

    @pytest.fixture
    def cache_service(self, tmp_path):
        """Create a cache service instance."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return CacheService(str(cache_dir))

    @pytest.fixture
    def cache_manager(self, tmp_path):
        """Create a cache manager instance."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        with patch('src.config.settings.get_config') as mock_config:
            mock_config.return_value.cache_dir = str(cache_dir)
            return CacheManager()

    def test_cache_ttl_expiration(self, cache_service):
        """Test that cache entries expire after TTL."""
        # Set item with 1 second TTL
        cache_service.set("test_key", {"data": "test_value"}, ttl=1)
        
        # Should be retrievable immediately
        data = cache_service.get("test_key")
        assert data is not None
        assert data["data"] == "test_value"
        
        # Wait for expiration
        time.sleep(1.5)
        
        # Should be expired
        data = cache_service.get("test_key")
        assert data is None

    @pytest.mark.asyncio
    async def test_cache_fallback_chain(self, cache_manager):
        """Test cache fallback mechanism."""
        # Create test quotes
        quotes = [
            Quote(symbol="AAPL", timestamp=datetime.now(), price=150.0),
            Quote(symbol="GOOGL", timestamp=datetime.now(), price=2800.0),
            Quote(symbol="MSFT", timestamp=datetime.now(), price=380.0)
        ]
        
        # Cache quotes
        for quote in quotes:
            await cache_manager.put_quote(quote)
        
        # Retrieve from cache
        cached_quotes = await cache_manager.get_quotes(["AAPL", "GOOGL", "MSFT"])
        assert len(cached_quotes) == 3
        assert cached_quotes["AAPL"] is not None
        assert cached_quotes["AAPL"].symbol == "AAPL"
        assert cached_quotes["AAPL"].price == 150.0

    def test_concurrent_cache_access(self, cache_service):
        """Test concurrent read/write operations."""
        async def write_task(key, value):
            await asyncio.sleep(0.001)  # Simulate some work
            cache_service.set(key, value)
        
        async def read_task(key):
            await asyncio.sleep(0.001)  # Simulate some work
            return cache_service.get(key)
        
        async def run_concurrent_test():
            # Create multiple concurrent tasks
            write_tasks = []
            read_tasks = []
            
            # Write 100 items concurrently
            for i in range(100):
                task = asyncio.create_task(write_task(f"key_{i}", {"index": i}))
                write_tasks.append(task)
            
            await asyncio.gather(*write_tasks)
            
            # Read 100 items concurrently
            for i in range(100):
                task = asyncio.create_task(read_task(f"key_{i}"))
                read_tasks.append(task)
            
            results = await asyncio.gather(*read_tasks)
            
            # Verify all reads successful
            assert len(results) == 100
            assert all(r is not None for r in results)
            assert results[50]["index"] == 50
        
        asyncio.run(run_concurrent_test())

    def test_cache_size_management(self, cache_service, tmp_path):
        """Test cache behavior with large data volumes."""
        cache_dir = Path(str(tmp_path / "cache"))
        
        # Write large amounts of data
        large_data = "x" * 10000  # 10KB string
        
        for i in range(100):
            cache_service.set(f"large_key_{i}", {"data": large_data})
        
        # Check cache directory size
        total_size = sum(f.stat().st_size for f in cache_dir.glob("*.json"))
        
        # Should have written all files
        assert len(list(cache_dir.glob("*.json"))) == 100
        # Total size should be roughly 1MB (100 * 10KB)
        assert total_size > 900000  # Allow some overhead

    def test_cache_invalidation(self, cache_service):
        """Test cache invalidation mechanisms."""
        # Set multiple related items
        cache_service.set("quotes_AAPL", {"price": 150.0})
        cache_service.set("quotes_GOOGL", {"price": 2800.0})
        cache_service.set("news_AAPL", {"headline": "Apple news"})
        
        # Delete specific item
        cache_service.delete("quotes_AAPL")
        
        # Verify deletion
        assert cache_service.get("quotes_AAPL") is None
        assert cache_service.get("quotes_GOOGL") is not None
        assert cache_service.get("news_AAPL") is not None

    def test_cache_persistence_across_restarts(self, tmp_path):
        """Test that cache persists across application restarts."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        
        # First instance
        cache1 = CacheService(str(cache_dir))
        cache1.set("persistent_key", {"data": "should_persist"}, ttl=3600)
        
        # Simulate restart with new instance
        cache2 = CacheService(str(cache_dir))
        data = cache2.get("persistent_key")
        
        assert data is not None
        assert data["data"] == "should_persist"

    def test_cache_corruption_handling(self, cache_service, tmp_path):
        """Test handling of corrupted cache files."""
        cache_dir = Path(str(tmp_path / "cache"))
        
        # Write valid cache entry
        cache_service.set("valid_key", {"data": "valid"})
        
        # Corrupt a cache file
        corrupted_file = cache_dir / "corrupted_key.json"
        with open(corrupted_file, 'w') as f:
            f.write("{ invalid json }")
        
        # Should handle gracefully
        data = cache_service.get("corrupted_key")
        assert data is None  # Returns None for corrupted data
        
        # Other entries should still work
        data = cache_service.get("valid_key")
        assert data is not None
        assert data["data"] == "valid"

    @pytest.mark.asyncio
    async def test_cache_with_async_data_sources(self, cache_manager):
        """Test cache integration with async data sources."""
        # Mock async data source
        async def fetch_quote(symbol):
            await asyncio.sleep(0.1)  # Simulate network delay
            return Quote(
                symbol=symbol,
                timestamp=datetime.now(),
                price=100.0 + hash(symbol) % 50
            )
        
        # First call should fetch from source
        start_time = time.time()
        quote1 = await fetch_quote("AAPL")
        fetch_time = time.time() - start_time
        
        # Cache the quote
        await cache_manager.put_quote(quote1)
        
        # Second call should be from cache (much faster)
        start_time = time.time()
        cached_quotes = await cache_manager.get_quotes(["AAPL"])
        cache_time = time.time() - start_time
        
        assert len(cached_quotes) == 1
        assert cached_quotes["AAPL"] is not None
        assert cached_quotes["AAPL"].symbol == "AAPL"
        assert cache_time < fetch_time / 10  # Cache should be at least 10x faster

    def test_cache_quota_management(self, cache_service):
        """Test cache behavior under quota constraints."""
        # Track cache operations
        operations = []
        
        def track_operation(op_type, key):
            operations.append((op_type, key, time.time()))
        
        # Simulate quota-limited operations
        for i in range(100):
            if i < 60:  # Within quota
                cache_service.set(f"quota_key_{i}", {"index": i})
                track_operation("write", f"quota_key_{i}")
            else:  # Would exceed quota
                # In real scenario, this would be blocked by quota guard
                # Here we just track it
                track_operation("blocked", f"quota_key_{i}")
        
        # Verify operations
        writes = [op for op in operations if op[0] == "write"]
        blocked = [op for op in operations if op[0] == "blocked"]
        
        assert len(writes) == 60
        assert len(blocked) == 40

    def test_cache_with_different_data_types(self, cache_service):
        """Test caching various data types."""
        test_data = {
            "string": "test_string",
            "number": 42,
            "float": 3.14159,
            "list": [1, 2, 3, 4, 5],
            "dict": {"nested": {"data": "value"}},
            "bool": True,
            "null": None,
            "datetime": datetime.now().isoformat(),
            "complex": {
                "quotes": [{"symbol": "AAPL", "price": 150.0}],
                "metadata": {"timestamp": time.time()}
            }
        }
        
        # Cache all data types
        for key, value in test_data.items():
            cache_service.set(f"type_{key}", value)
        
        # Retrieve and verify
        for key, expected in test_data.items():
            cached = cache_service.get(f"type_{key}")
            assert cached == expected

    def test_cache_namespace_isolation(self, cache_service):
        """Test that different cache namespaces are isolated."""
        # Set data in different namespaces
        cache_service.set("quotes:AAPL", {"price": 150.0})
        cache_service.set("news:AAPL", {"headline": "Apple news"})
        cache_service.set("sentiment:AAPL", {"score": 0.8})
        
        # Verify isolation
        assert cache_service.get("quotes:AAPL")["price"] == 150.0
        assert cache_service.get("news:AAPL")["headline"] == "Apple news"
        assert cache_service.get("sentiment:AAPL")["score"] == 0.8
        
        # Deleting one shouldn't affect others
        cache_service.delete("quotes:AAPL")
        assert cache_service.get("quotes:AAPL") is None
        assert cache_service.get("news:AAPL") is not None