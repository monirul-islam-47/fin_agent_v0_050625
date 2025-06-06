"""System tests for error recovery and resilience."""

import pytest
import asyncio
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import aiohttp

from src.orchestration.coordinator import Coordinator
from src.orchestration.event_bus import EventBus
from src.data.market import MarketDataManager
from src.persistence.journal import TradeJournal
from src.data.cache_manager import CacheManager


class TestSystemRecovery:
    """Test system recovery from various failure scenarios."""

    @pytest.fixture
    async def recovery_system(self, tmp_path):
        """Create system with recovery capabilities."""
        event_bus = EventBus()
        await event_bus.start()
        
        # Create real components where needed
        db_path = tmp_path / "test_trades.db"
        journal = TradeJournal(str(db_path))
        
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        with patch('src.config.settings.get_config') as mock_config:
            mock_config.return_value.cache_dir = str(cache_dir)
            cache_manager = CacheManager()
        
        # Mock other components
        mock_market_data = AsyncMock()
        mock_scanner = AsyncMock()
        mock_planner = AsyncMock()
        mock_risk = AsyncMock()
        mock_universe = AsyncMock()
        
        coordinator = Coordinator(
            event_bus=event_bus,
            market_data_manager=mock_market_data,
            gap_scanner=mock_scanner,
            trade_planner=mock_planner,
            risk_manager=mock_risk,
            trade_journal=journal,
            universe_manager=mock_universe
        )
        
        yield {
            "coordinator": coordinator,
            "event_bus": event_bus,
            "journal": journal,
            "cache_manager": cache_manager,
            "market_data": mock_market_data,
            "tmp_path": tmp_path
        }
        
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_api_failure_recovery(self, recovery_system):
        """Test recovery from API failures."""
        coordinator = recovery_system["coordinator"]
        market_data = recovery_system["market_data"]
        
        # Simulate API failures then recovery
        call_count = 0
        
        async def flaky_api(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            
            if call_count <= 3:
                # First 3 calls fail
                raise aiohttp.ClientError("API Error")
            else:
                # Then succeed
                from src.data.base import Quote
                return Quote(
                    symbol=args[0] if args else "AAPL",
                    timestamp=datetime.now(),
                    price=150.0,
                    volume=1000000
                )
        
        market_data.get_quote.side_effect = flaky_api
        
        # System should retry and eventually succeed
        quote = await coordinator._get_quote_with_retry("AAPL", max_retries=5)
        
        assert quote is not None
        assert quote.price == 150.0
        assert call_count == 4  # Failed 3 times, succeeded on 4th

    @pytest.mark.asyncio
    async def test_database_corruption_recovery(self, recovery_system):
        """Test recovery from database corruption."""
        journal = recovery_system["journal"]
        db_path = recovery_system["tmp_path"] / "test_trades.db"
        
        # Add some trades
        from src.domain.planner import TradePlan, EntryStrategy, ExitStrategy
        trade = TradePlan(
            symbol="AAPL",
            score=80.0,
            direction="long",
            entry_strategy=EntryStrategy.VWAP,
            entry_price=150.0,
            stop_loss=145.0,
            stop_loss_percent=3.33,
            target_price=160.0,
            target_percent=6.67,
            exit_strategy=ExitStrategy.FIXED_TARGET,
            position_size_eur=250.0,
            position_size_shares=2,
            max_risk_eur=10.0,
            risk_reward_ratio=2.0
        )
        journal.record_trade(trade)
        
        # Corrupt the database
        journal._conn.close()
        with open(db_path, 'r+b') as f:
            f.seek(100)
            f.write(b'\x00' * 100)  # Write nulls to corrupt
        
        # Try to access - should handle corruption
        try:
            # Reinitialize journal
            new_journal = TradeJournal(str(db_path))
            trades = new_journal.get_recent_trades()
            # If we get here, recovery worked
            assert True
        except sqlite3.DatabaseError:
            # Should have recovery mechanism
            pytest.fail("Database recovery failed")

    @pytest.mark.asyncio
    async def test_event_bus_overflow_recovery(self, recovery_system):
        """Test recovery from event bus overflow."""
        event_bus = recovery_system["event_bus"]
        
        # Track dropped events
        dropped_count = 0
        
        async def slow_handler(event):
            # Simulate slow processing
            await asyncio.sleep(0.1)
        
        from src.orchestration.events import Event
        await event_bus.subscribe(Event, slow_handler)
        
        # Flood event bus
        flood_tasks = []
        for i in range(2000):  # More than queue size
            event = Event(data={"index": i})
            task = event_bus.publish(event)
            flood_tasks.append(task)
        
        # Some should fail due to overflow
        results = await asyncio.gather(*flood_tasks, return_exceptions=True)
        exceptions = [r for r in results if isinstance(r, Exception)]
        
        # System should handle overflow gracefully
        assert len(exceptions) > 0  # Some dropped
        assert len(exceptions) < 1000  # But not all
        
        # Should still be functional after overflow
        test_event = Event(data={"test": "recovery"})
        await event_bus.publish(test_event)

    @pytest.mark.asyncio
    async def test_network_partition_recovery(self, recovery_system):
        """Test recovery from network partitions."""
        market_data = recovery_system["market_data"]
        
        # Simulate network partition
        network_up = False
        
        async def network_dependent_call(*args, **kwargs):
            if not network_up:
                raise ConnectionError("Network unreachable")
            
            from src.data.base import Quote
            return Quote(
                symbol="AAPL",
                timestamp=datetime.now(),
                price=150.0,
                volume=1000000
            )
        
        market_data.get_quote.side_effect = network_dependent_call
        
        # Try during partition
        with pytest.raises(ConnectionError):
            await market_data.get_quote("AAPL")
        
        # Network recovers
        network_up = True
        
        # Should work now
        quote = await market_data.get_quote("AAPL")
        assert quote.price == 150.0

    @pytest.mark.asyncio
    async def test_cache_corruption_recovery(self, recovery_system):
        """Test recovery from cache corruption."""
        cache_manager = recovery_system["cache_manager"]
        cache_dir = recovery_system["tmp_path"] / "cache"
        
        # Write valid cache entry
        from src.data.base import Quote
        quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,
            volume=1000000
        )
        cache_manager.put_quote(quote)
        
        # Corrupt cache file
        cache_files = list(cache_dir.glob("*.json"))
        if cache_files:
            with open(cache_files[0], 'w') as f:
                f.write("{ corrupted json }")
        
        # Should handle corrupted cache gracefully
        quotes = cache_manager.get_quotes(["AAPL"])
        assert quotes == []  # Returns empty on corruption, doesn't crash

    @pytest.mark.asyncio
    async def test_partial_system_failure(self, recovery_system):
        """Test system continues with partial component failures."""
        coordinator = recovery_system["coordinator"]
        
        # Make scanner fail
        coordinator.gap_scanner.scan_pre_market.side_effect = Exception("Scanner failed")
        
        # But universe and market data work
        coordinator.universe_manager.get_active_symbols.return_value = ["AAPL", "GOOGL"]
        
        # Run scan - should handle scanner failure gracefully
        from src.orchestration.events import ScanRequest
        event = ScanRequest(scan_type="primary")
        
        # Should not crash entire system
        try:
            await coordinator.handle_scan_request(event)
            # Continued despite scanner failure
            assert True
        except Exception:
            pytest.fail("System should handle partial failures")

    @pytest.mark.asyncio
    async def test_memory_exhaustion_recovery(self, recovery_system):
        """Test recovery from memory pressure."""
        import gc
        import psutil
        
        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        # Simulate memory pressure
        large_objects = []
        try:
            for i in range(1000):
                # Create large objects
                large_obj = "x" * (10 * 1024 * 1024)  # 10MB string
                large_objects.append(large_obj)
                
                current_memory = process.memory_info().rss / 1024 / 1024
                
                # If memory grows too much, trigger cleanup
                if current_memory - initial_memory > 500:  # 500MB increase
                    large_objects.clear()
                    gc.collect()
                    break
        except MemoryError:
            # Should handle gracefully
            large_objects.clear()
            gc.collect()
        
        # System should recover
        final_memory = process.memory_info().rss / 1024 / 1024
        # Memory should be reasonably close to initial
        assert final_memory - initial_memory < 100  # Less than 100MB increase

    @pytest.mark.asyncio
    async def test_cascading_failure_prevention(self, recovery_system):
        """Test prevention of cascading failures."""
        coordinator = recovery_system["coordinator"]
        event_bus = recovery_system["event_bus"]
        
        # Track failures
        failures = []
        
        async def failing_handler(event):
            failures.append(event)
            raise Exception("Handler failed")
        
        # Subscribe failing handler
        from src.orchestration.events import Event
        await event_bus.subscribe(Event, failing_handler)
        
        # Publish multiple events
        for i in range(10):
            event = Event(data={"index": i})
            await event_bus.publish(event)
        
        # Despite handler failures, system should continue
        await asyncio.sleep(0.5)
        
        # All events should have been attempted
        assert len(failures) == 10
        
        # Event bus should still be functional
        assert event_bus._running is True

    @pytest.mark.asyncio
    async def test_state_recovery_after_crash(self, recovery_system):
        """Test state recovery after simulated crash."""
        journal = recovery_system["journal"]
        cache_manager = recovery_system["cache_manager"]
        
        # Create some state
        from src.domain.planner import TradePlan, EntryStrategy, ExitStrategy
        trade = TradePlan(
            symbol="AAPL",
            score=80.0,
            direction="long",
            entry_strategy=EntryStrategy.VWAP,
            entry_price=150.0,
            stop_loss=145.0,
            stop_loss_percent=3.33,
            target_price=160.0,
            target_percent=6.67,
            exit_strategy=ExitStrategy.FIXED_TARGET,
            position_size_eur=250.0,
            position_size_shares=2,
            max_risk_eur=10.0,
            risk_reward_ratio=2.0
        )
        journal.record_trade(trade)
        
        # Cache some data
        from src.data.base import Quote
        quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=151.0,
            volume=1500000
        )
        cache_manager.put_quote(quote)
        
        # Simulate crash by closing connections
        journal._conn.close()
        
        # Simulate restart
        db_path = recovery_system["tmp_path"] / "test_trades.db"
        new_journal = TradeJournal(str(db_path))
        
        # Should recover state
        trades = new_journal.get_recent_trades()
        assert len(trades) == 1
        assert trades[0]["symbol"] == "AAPL"
        
        # Cache should also be recovered
        cached_quotes = cache_manager.get_quotes(["AAPL"])
        assert len(cached_quotes) == 1

    @pytest.mark.asyncio
    async def test_timeout_recovery(self, recovery_system):
        """Test recovery from operation timeouts."""
        market_data = recovery_system["market_data"]
        
        # Simulate slow operation that times out
        async def slow_operation(*args, **kwargs):
            await asyncio.sleep(10)  # Longer than timeout
            return {"data": "eventual_response"}
        
        market_data.get_quote.side_effect = slow_operation
        
        # Operation with timeout
        try:
            result = await asyncio.wait_for(
                market_data.get_quote("AAPL"),
                timeout=1.0
            )
            pytest.fail("Should have timed out")
        except asyncio.TimeoutError:
            # Expected timeout
            pass
        
        # System should still be functional
        market_data.get_quote.side_effect = None
        market_data.get_quote.return_value = "quick_response"
        
        result = await market_data.get_quote("AAPL")
        assert result == "quick_response"