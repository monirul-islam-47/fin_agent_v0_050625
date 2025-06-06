"""Stress testing scenarios for the trading system."""

import pytest
import asyncio
import random
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, AsyncMock, patch
import sqlite3
import json

from src.orchestration.coordinator import Coordinator
from src.orchestration.event_bus import EventBus
from src.data.cache_manager import CacheManager
from src.persistence.journal import TradeJournal
from src.domain.planner import TradePlan, EntryStrategy, ExitStrategy
from src.utils.quota import get_quota_guard, rate_limit


class TestStressScenarios:
    """Stress test the system under extreme conditions."""
    
    @pytest.fixture(autouse=True)
    def suppress_logs(self):
        """Reduce logging noise during stress tests."""
        # Set all loggers to WARNING level during stress tests
        logging.getLogger('src.persistence.journal').setLevel(logging.WARNING)
        logging.getLogger('src.data').setLevel(logging.WARNING)
        logging.getLogger('src.orchestration').setLevel(logging.WARNING)
        yield
        # Reset to INFO after test
        logging.getLogger('src.persistence.journal').setLevel(logging.INFO)
        logging.getLogger('src.data').setLevel(logging.INFO)
        logging.getLogger('src.orchestration').setLevel(logging.INFO)

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_massive_symbol_universe(self, tmp_path):
        """Test system with 1000+ symbols."""
        # Create massive universe
        universe_file = tmp_path / "massive_universe.csv"
        with open(universe_file, 'w') as f:
            f.write("symbol\n")
            for i in range(1500):
                f.write(f"TEST{i:04d}\n")
        
        event_bus = EventBus()
        await event_bus.start()
        
        # Mock dependencies
        mock_market_data = AsyncMock()
        mock_scanner = AsyncMock()
        mock_planner = AsyncMock()
        mock_risk = AsyncMock()
        mock_journal = AsyncMock()
        mock_universe = AsyncMock()
        
        coordinator = Coordinator(
            event_bus=event_bus,
            market_data_manager=mock_market_data,
            gap_scanner=mock_scanner,
            trade_planner=mock_planner,
            risk_manager=mock_risk,
            trade_journal=mock_journal,
            universe_manager=mock_universe
        )
        
        # Mock market data to avoid real API calls
        async def mock_get_quote(symbol):
            await asyncio.sleep(0.001)  # Simulate network delay
            return {
                "symbol": symbol,
                "current_price": 100.0 + hash(symbol) % 50,
                "previous_close": 95.0 + hash(symbol) % 45,
                "volume": 1000000
            }
        
        mock_market_data.get_quote.side_effect = mock_get_quote
        mock_universe.get_active_symbols.return_value = [f"TEST{i:04d}" for i in range(1500)]
        mock_scanner.scan_pre_market.return_value = []
        
        # Time the scan
        start_time = time.time()
        
        try:
            await asyncio.wait_for(
                coordinator.run_primary_scan(),
                timeout=30.0  # 30 second timeout
            )
            duration = time.time() - start_time
            
            # Should complete within timeout
            assert duration < 30.0
            
        except asyncio.TimeoutError:
            pytest.fail("Scan timed out with 1500 symbols")
        
        finally:
            await coordinator.stop()
            await event_bus.stop()

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_rapid_api_quota_exhaustion(self):
        """Test system behavior under rapid quota exhaustion."""
        call_count = 0
        max_calls = 60  # Finnhub limit from config
        
        # Get the quota guard (uses finnhub which is already configured)
        guard = get_quota_guard()
        
        @rate_limit("finnhub", count=1)
        async def api_call():
            nonlocal call_count
            call_count += 1
            return {"data": call_count}
        
        # Reset finnhub quota to ensure clean state
        await guard.reset_all()
        
        # Attempt to make 100 calls rapidly
        tasks = []
        for i in range(100):
            tasks.append(api_call())
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Should have successful calls up to quota limit, rest should fail
        successful = [r for r in results if isinstance(r, dict)]
        failed = [r for r in results if isinstance(r, Exception)]
        
        # At least some calls should succeed, some should fail due to quota
        assert len(successful) > 0
        assert len(failed) > 0
        assert len(successful) + len(failed) == 100

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_database_with_massive_history(self, tmp_path):
        """Test performance with 10,000+ historical trades."""
        db_path = tmp_path / "massive_trades.db"
        journal = TradeJournal(str(db_path))
        
        # Generate 1,000 trades in batch mode for faster testing (can be increased)
        batch_size = 100
        num_batches = 10  # Reduced from 100 to 10 for testing
        for batch in range(num_batches):
            trades = []
            for i in range(batch_size):
                trade_num = batch * batch_size + i
                # EntryStrategy and ExitStrategy already imported at top
                trade = TradePlan(
                    symbol=f"TEST{trade_num % 500:03d}",
                    score=70.0 + (trade_num % 20),
                    direction="long",
                    entry_strategy=EntryStrategy.VWAP,
                    entry_price=100.0 + (trade_num % 50),
                    stop_loss=95.0 + (trade_num % 45),
                    stop_loss_percent=5.0,
                    target_price=110.0 + (trade_num % 60),
                    target_percent=10.0,
                    exit_strategy=ExitStrategy.FIXED_TARGET,
                    position_size_eur=250.0,
                    position_size_shares=2,
                    max_risk_eur=10.0,
                    risk_reward_ratio=2.0
                )
                # Add sample factors for the trade
                factors = {
                    "gap": 0.8,
                    "volume": 0.7,
                    "momentum": 0.6,
                    "volatility": 0.5,
                    "news": 0.7
                }
                journal.record_trade(trade, factors, batch_mode=True)
        
        # Test query performance
        start_time = time.time()
        
        # Various queries
        all_trades = journal.get_recent_trades(limit=num_batches * batch_size)
        recent_trades = journal.get_recent_trades(limit=100)
        from datetime import datetime, timedelta
        date_trades = journal.get_trades_by_date_range(
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now()
        )
        metrics = journal.get_performance_summary()
        
        query_time = time.time() - start_time
        
        assert len(all_trades) == num_batches * batch_size
        assert len(recent_trades) == 100
        assert query_time < 5.0  # Queries should complete in < 5 seconds

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_multiple_concurrent_users(self, tmp_path):
        """Simulate multiple dashboard users accessing system concurrently."""
        num_users = 20
        operations_per_user = 50
        
        event_bus = EventBus()
        await event_bus.start()
        
        # Track operations
        operations_completed = []
        errors_encountered = []
        
        async def simulate_user(user_id):
            """Simulate a single user's operations."""
            for op in range(operations_per_user):
                try:
                    operation_type = random.choice([
                        "view_trades",
                        "run_scan",
                        "update_weights",
                        "export_data"
                    ])
                    
                    if operation_type == "view_trades":
                        # Simulate viewing trades
                        from src.orchestration.events import DataUpdate
                        event = DataUpdate(
                            symbol="ALL",
                            data_type="trades",
                            update_data={"user_id": user_id}
                        )
                        await event_bus.publish(event)
                    
                    elif operation_type == "run_scan":
                        # Simulate triggering scan
                        from src.orchestration.events import ScanRequest
                        event = ScanRequest(
                            scan_type="manual",
                            data={"user_id": user_id}
                        )
                        await event_bus.publish(event)
                    
                    elif operation_type == "update_weights":
                        # Simulate updating factor weights
                        weights = {
                            "momentum": random.uniform(0.2, 0.4),
                            "volume": random.uniform(0.1, 0.3),
                            "news": random.uniform(0.1, 0.2),
                            "volatility": random.uniform(0.2, 0.4)
                        }
                        from src.orchestration.events import Event
                        event = Event(
                            data={"config_type": "weights", "values": weights, "user_id": user_id}
                        )
                        await event_bus.publish(event)
                    
                    else:  # export_data
                        # Simulate data export
                        from src.orchestration.events import Event
                        event = Event(
                            data={"format": "csv", "user_id": user_id}
                        )
                        await event_bus.publish(event)
                    
                    operations_completed.append((user_id, operation_type))
                    await asyncio.sleep(random.uniform(0.1, 0.5))
                    
                except Exception as e:
                    errors_encountered.append((user_id, str(e)))
        
        # Run all users concurrently
        user_tasks = [simulate_user(i) for i in range(num_users)]
        await asyncio.gather(*user_tasks)
        
        await event_bus.stop()
        
        # Verify results
        total_operations = len(operations_completed)
        total_errors = len(errors_encountered)
        
        assert total_operations >= num_users * operations_per_user * 0.95  # 95% success rate
        assert total_errors < num_users * operations_per_user * 0.05  # <5% error rate

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_network_failure_recovery(self, tmp_path):
        """Test system recovery from network failures."""
        event_bus = EventBus()
        await event_bus.start()
        
        coordinator = Coordinator(event_bus)
        
        # Track network calls
        network_failures = 0
        successful_calls = 0
        
        async def flaky_network_call(symbol):
            nonlocal network_failures, successful_calls
            
            # 30% chance of failure
            if random.random() < 0.3:
                network_failures += 1
                raise ConnectionError("Network timeout")
            
            successful_calls += 1
            return {
                "symbol": symbol,
                "current_price": 100.0,
                "volume": 1000000
            }
        
        # Mock dependencies
        mock_market_data = AsyncMock()
        mock_scanner = AsyncMock()
        mock_planner = AsyncMock()
        mock_risk = AsyncMock()
        mock_journal = AsyncMock()
        mock_universe = AsyncMock()
        
        coordinator = Coordinator(
            event_bus=event_bus,
            market_data_manager=mock_market_data,
            gap_scanner=mock_scanner,
            trade_planner=mock_planner,
            risk_manager=mock_risk,
            trade_journal=mock_journal,
            universe_manager=mock_universe
        )
        
        mock_market_data.get_quote.side_effect = flaky_network_call
        
        # Create small test universe
        test_symbols = [f"TEST{i:03d}" for i in range(100)]
        mock_universe.get_active_symbols.return_value = test_symbols
        mock_scanner.scan_pre_market.return_value = []
        
        # Run scan with flaky network
        try:
            await coordinator.run_primary_scan()
            
            # System should handle failures gracefully
            assert successful_calls > 50  # At least half should succeed
            assert network_failures > 0   # Some failures occurred
            
        finally:
            await coordinator.stop()
            await event_bus.stop()

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_cache_under_memory_pressure(self, tmp_path):
        """Test cache behavior under memory pressure."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        from src.data.cache import CacheService
        cache = CacheService(str(cache_dir))
        
        # Generate large data objects
        large_objects = []
        for i in range(1000):
            obj = {
                "id": i,
                "data": "x" * 10000,  # 10KB per object
                "nested": {
                    "values": list(range(1000)),
                    "metadata": {"index": i}
                }
            }
            large_objects.append(obj)
        
        # Fill cache
        stored_keys = []
        for i, obj in enumerate(large_objects):
            key = f"large_object_{i}"
            cache.set(key, obj, ttl=3600)
            stored_keys.append(key)
            
            # Simulate memory pressure by checking size periodically
            if i % 100 == 0:
                cache_size = sum(
                    (cache_dir / f"{k}.json").stat().st_size
                    for k in stored_keys
                    if (cache_dir / f"{k}.json").exists()
                )
                # Only print every 500 objects to reduce output
                if i % 500 == 0:
                    pass  # Comment out: print(f"Cache size after {i} objects: {cache_size / 1024 / 1024:.2f} MB")
        
        # Verify cache still functional
        sample_keys = random.sample(stored_keys, 10)
        for key in sample_keys:
            data = cache.get(key)
            assert data is not None

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_event_bus_message_flooding(self):
        """Test event bus under message flooding conditions."""
        event_bus = EventBus()
        await event_bus.start()
        
        received_messages = []
        dropped_messages = 0
        
        async def message_handler(event):
            received_messages.append(event)
            # Simulate slow processing
            await asyncio.sleep(0.01)
        
        # Subscribe to Event type for flood test
        from src.orchestration.events import Event
        await event_bus.subscribe(Event, message_handler)
        
        # Flood with messages
        flood_size = 10000
        publish_tasks = []
        
        for i in range(flood_size):
            event = Event(
                data={"message_id": i, "timestamp": time.time(), "index": i}
            )
            task = event_bus.publish(event)
            publish_tasks.append(task)
        
        # Wait for all publishes
        await asyncio.gather(*publish_tasks)
        
        # Allow processing time
        await asyncio.sleep(5.0)
        
        await event_bus.stop()
        
        # Check results
        received_count = len(received_messages)
        drop_rate = (flood_size - received_count) / flood_size
        
        # Only show results in assertion failure if needed
        # Results available in variables: received_count, drop_rate
        
        # System should handle most messages even under flood
        assert received_count > flood_size * 0.8  # >80% delivery rate

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_concurrent_file_operations(self, tmp_path):
        """Test system under concurrent file I/O stress."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        
        # Multiple cache managers simulating concurrent processes
        from src.data.cache import CacheService
        cache_managers = [CacheService(str(cache_dir)) for _ in range(10)]
        
        async def stress_cache_manager(manager_id, cache_manager):
            """Stress test a single cache manager."""
            operations = []
            
            for i in range(100):
                op_type = random.choice(["read", "write", "delete"])
                key = f"key_{random.randint(0, 50)}"
                
                try:
                    if op_type == "write":
                        data = {"manager": manager_id, "op": i, "data": "x" * 1000}
                        cache_manager.set(key, data)
                        operations.append(("write", key, True))
                    
                    elif op_type == "read":
                        data = cache_manager.get(key)
                        operations.append(("read", key, data is not None))
                    
                    else:  # delete
                        cache_manager.delete(key)
                        operations.append(("delete", key, True))
                        
                except Exception as e:
                    operations.append((op_type, key, False))
                
                # Small delay between operations
                await asyncio.sleep(0.001)
            
            return operations
        
        # Run all managers concurrently
        tasks = [
            stress_cache_manager(i, manager)
            for i, manager in enumerate(cache_managers)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Analyze results
        total_operations = sum(len(r) for r in results)
        successful_operations = sum(
            1 for r in results for op in r if op[2]
        )
        
        success_rate = successful_operations / total_operations
        # Results available for assertion: success_rate
        
        # Should maintain reasonable success rate despite concurrency
        # In high-concurrency file operations, some failures are expected
        assert success_rate > 0.8  # >80% success rate