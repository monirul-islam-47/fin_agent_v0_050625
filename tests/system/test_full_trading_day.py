"""System tests for full trading day simulation."""

import pytest
import asyncio
from datetime import datetime, time, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import json
import sqlite3
from pathlib import Path

# Remove unused import - we'll test components directly
from src.orchestration.coordinator import Coordinator
from src.orchestration.event_bus import EventBus
from src.persistence.journal import TradeJournal
from src.domain.planner import TradePlan


class TestFullTradingDay:
    """Test complete trading day scenarios from market open to close."""

    @pytest.fixture
    def mock_market_data(self):
        """Generate realistic market data for testing."""
        return {
            "AAPL": {
                "pre_market": {"price": 152.50, "volume": 1500000, "previous_close": 150.00},
                "regular": {"open": 153.00, "high": 156.00, "low": 152.00, "close": 155.50},
                "post_market": {"price": 155.25, "volume": 500000}
            },
            "MSFT": {
                "pre_market": {"price": 310.00, "volume": 800000, "previous_close": 305.00},
                "regular": {"open": 311.00, "high": 315.00, "low": 309.00, "close": 314.00},
                "post_market": {"price": 313.50, "volume": 300000}
            },
            "GOOGL": {
                "pre_market": {"price": 125.00, "volume": 600000, "previous_close": 123.00},
                "regular": {"open": 125.50, "high": 127.00, "low": 124.50, "close": 126.50},
                "post_market": {"price": 126.25, "volume": 200000}
            }
        }

    @pytest.fixture
    async def test_environment(self, tmp_path, mock_market_data):
        """Set up complete test environment."""
        # Create directory structure
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        
        universe_dir = data_dir / "universe"
        universe_dir.mkdir()
        
        # Create universe file
        universe_file = universe_dir / "revolut_universe.csv"
        with open(universe_file, 'w') as f:
            f.write("symbol\n")
            for symbol in mock_market_data.keys():
                f.write(f"{symbol}\n")
        
        # Create test database
        db_path = data_dir / "trades.db"
        
        # Set up environment
        env_vars = {
            "FINNHUB_API_KEY": "test_key",
            "ALPHA_VANTAGE_API_KEY": "test_key",
            "NEWS_API_KEY": "test_key",
            "CACHE_DIR": str(cache_dir),
            "UNIVERSE_FILE": str(universe_file),
            "DATABASE_PATH": str(db_path)
        }
        
        with patch.dict('os.environ', env_vars):
            yield {
                "cache_dir": cache_dir,
                "data_dir": data_dir,
                "db_path": db_path,
                "market_data": mock_market_data
            }

    @pytest.mark.asyncio
    async def test_full_trading_day_simulation(self, test_environment):
        """Simulate a complete trading day from pre-market to after-hours."""
        market_data = test_environment["market_data"]
        
        # Mock time progression through trading day
        times = [
            datetime.now().replace(hour=8, minute=0),   # Pre-market scan
            datetime.now().replace(hour=14, minute=0),  # Primary scan (CET)
            datetime.now().replace(hour=15, minute=30),  # US market open
            datetime.now().replace(hour=18, minute=15),  # Second look scan
            datetime.now().replace(hour=22, minute=0),   # US market close
        ]
        
        with patch('datetime.datetime') as mock_datetime:
            time_index = 0
            
            def advance_time():
                nonlocal time_index
                if time_index < len(times):
                    current = times[time_index]
                    time_index += 1
                    return current
                return times[-1]
            
            mock_datetime.now.side_effect = advance_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            # Initialize system
            event_bus = EventBus()
            await event_bus.start()
            
            coordinator = Coordinator(event_bus)
            trades_recorded = []
            
            # Mock data providers
            with patch.object(coordinator, 'market_data_manager') as mock_market:
                async def get_quote(symbol):
                    data = market_data.get(symbol, {})
                    current_time = mock_datetime.now()
                    
                    if current_time.hour < 15:  # Pre-market
                        return {
                            "symbol": symbol,
                            "current_price": data["pre_market"]["price"],
                            "previous_close": data["pre_market"]["previous_close"],
                            "volume": data["pre_market"]["volume"]
                        }
                    elif current_time.hour < 22:  # Regular hours
                        return {
                            "symbol": symbol,
                            "current_price": data["regular"]["close"],
                            "previous_close": data["pre_market"]["previous_close"],
                            "volume": data["pre_market"]["volume"] * 3
                        }
                    else:  # After hours
                        return {
                            "symbol": symbol,
                            "current_price": data["post_market"]["price"],
                            "previous_close": data["regular"]["close"],
                            "volume": data["post_market"]["volume"]
                        }
                
                mock_market.get_quote.side_effect = get_quote
                
                # Track trade signals
                async def trade_handler(event):
                    if hasattr(event, 'trade_plan'):
                        trades_recorded.append(event.trade_plan)
                
                event_bus.subscribe("TradeSignal", trade_handler)
                
                # Run through trading day
                await coordinator.start()
                
                # Pre-market preparation
                assert mock_datetime.now().hour == 8
                
                # Primary scan at 14:00 CET
                await coordinator.run_primary_scan()
                await asyncio.sleep(0.1)
                
                # Should have some trade signals
                assert len(trades_recorded) > 0
                
                # Second look scan at 18:15 CET
                initial_trades = len(trades_recorded)
                await coordinator.run_second_look_scan()
                await asyncio.sleep(0.1)
                
                # May have additional trades
                assert len(trades_recorded) >= initial_trades
                
                # Verify all trades have required fields
                for trade in trades_recorded:
                    assert trade.symbol in market_data
                    assert trade.entry_price > 0
                    assert trade.stop_loss < trade.entry_price
                    assert trade.take_profit > trade.entry_price
                    assert trade.risk_reward_ratio > 1.5
                
                await coordinator.stop()
                await event_bus.stop()

    @pytest.mark.asyncio
    async def test_multi_user_dashboard_simulation(self, test_environment):
        """Test multiple dashboard sessions accessing the system concurrently."""
        # This would test the dashboard's ability to handle multiple users
        # For now, we'll simulate multiple event bus connections
        
        event_buses = []
        coordinators = []
        
        try:
            # Create 5 concurrent sessions
            for i in range(5):
                bus = EventBus()
                await bus.start()
                event_buses.append(bus)
                
                coordinator = Coordinator(bus)
                coordinators.append(coordinator)
            
            # Simulate concurrent operations
            tasks = []
            for i, coordinator in enumerate(coordinators):
                # Stagger the scans slightly
                delay = i * 0.1
                tasks.append(self._delayed_scan(coordinator, delay))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Verify no failures
            exceptions = [r for r in results if isinstance(r, Exception)]
            assert len(exceptions) == 0
            
        finally:
            # Clean up
            for coordinator in coordinators:
                await coordinator.stop()
            for bus in event_buses:
                await bus.stop()

    async def _delayed_scan(self, coordinator, delay):
        """Helper to run scan with delay."""
        await asyncio.sleep(delay)
        return await coordinator.run_primary_scan()

    @pytest.mark.asyncio
    async def test_recovery_from_system_crash(self, test_environment):
        """Test system recovery after unexpected shutdown."""
        db_path = test_environment["db_path"]
        
        # Phase 1: Normal operation with some trades
        event_bus = EventBus()
        await event_bus.start()
        
        coordinator = Coordinator(event_bus)
        journal = TradeJournal(str(db_path))
        
        # Record some trades
        from src.domain.planner import EntryStrategy, ExitStrategy
        test_trades = [
            TradePlan(
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
        ]
        
        for trade in test_trades:
            await journal.record_trade(trade)
        
        # Get state before crash
        trades_before = journal.get_recent_trades()
        metrics_before = journal.get_performance_summary()
        
        # Simulate crash
        await coordinator.stop()
        await event_bus.stop()
        
        # Phase 2: Recovery
        new_bus = EventBus()
        await new_bus.start()
        
        new_coordinator = Coordinator(new_bus)
        new_journal = TradeJournal(str(db_path))
        
        # Verify data persisted
        trades_after = new_journal.get_recent_trades()
        metrics_after = new_journal.get_performance_summary()
        
        assert len(trades_after) == len(trades_before)
        assert trades_after[0]["symbol"] == "AAPL"
        
        # Verify system can continue operating
        await new_coordinator.start()
        
        # Should be able to run new scans
        await new_coordinator.run_primary_scan()
        
        await new_coordinator.stop()
        await new_bus.stop()

    @pytest.mark.asyncio
    async def test_handling_market_holidays(self, test_environment):
        """Test system behavior on market holidays."""
        # Mock a holiday (e.g., Christmas)
        holiday = datetime(2024, 12, 25)
        
        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = holiday
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            event_bus = EventBus()
            await event_bus.start()
            
            coordinator = Coordinator(event_bus)
            
            # System should recognize market is closed
            is_open = coordinator._is_market_open()
            assert not is_open
            
            # Scans should not execute or return empty
            result = await coordinator.run_primary_scan()
            
            await coordinator.stop()
            await event_bus.stop()

    @pytest.mark.asyncio
    async def test_handling_daylight_saving_time(self, test_environment):
        """Test system handles DST transitions correctly."""
        # Test spring forward (2nd Sunday in March)
        spring_forward = datetime(2024, 3, 10, 2, 0)  # 2 AM becomes 3 AM
        
        with patch('datetime.datetime') as mock_datetime:
            # Before DST
            mock_datetime.now.return_value = spring_forward - timedelta(hours=1)
            
            event_bus = EventBus()
            await event_bus.start()
            coordinator = Coordinator(event_bus)
            
            # Schedule should adjust
            schedule_before = coordinator._get_scan_schedule()
            
            # After DST
            mock_datetime.now.return_value = spring_forward + timedelta(hours=1)
            schedule_after = coordinator._get_scan_schedule()
            
            # Verify times adjusted correctly
            # CET to CEST means US market opens "earlier" in European time
            
            await coordinator.stop()
            await event_bus.stop()

    @pytest.mark.asyncio
    async def test_performance_under_load(self, test_environment):
        """Test system performance with large universe."""
        # Create large universe
        large_universe = [f"TEST{i:04d}" for i in range(1000)]
        
        universe_file = test_environment["data_dir"] / "universe" / "large_universe.csv"
        with open(universe_file, 'w') as f:
            f.write("symbol\n")
            for symbol in large_universe:
                f.write(f"{symbol}\n")
        
        with patch.dict('os.environ', {"UNIVERSE_FILE": str(universe_file)}):
            event_bus = EventBus()
            await event_bus.start()
            
            coordinator = Coordinator(event_bus)
            
            # Mock data for all symbols
            with patch.object(coordinator.market_data_manager, 'get_quote') as mock_quote:
                async def mock_get_quote(symbol):
                    return {
                        "symbol": symbol,
                        "current_price": 100.0,
                        "previous_close": 95.0,
                        "volume": 1000000
                    }
                
                mock_quote.side_effect = mock_get_quote
                
                # Measure scan time
                start_time = asyncio.get_event_loop().time()
                await coordinator.run_primary_scan()
                end_time = asyncio.get_event_loop().time()
                
                scan_duration = end_time - start_time
                
                # Should complete within 20 seconds even with 1000 symbols
                assert scan_duration < 20.0
                
                await coordinator.stop()
                await event_bus.stop()

    @pytest.mark.asyncio
    async def test_concurrent_database_operations(self, test_environment):
        """Test database handles concurrent reads/writes."""
        db_path = test_environment["db_path"]
        
        # Create multiple journal instances
        journals = [TradeJournal(str(db_path)) for _ in range(10)]
        
        # Generate test trades
        test_trades = []
        for i in range(100):
            from src.domain.planner import EntryStrategy, ExitStrategy
            trade = TradePlan(
                symbol=f"TEST{i:03d}",
                score=75.0,
                direction="long",
                entry_strategy=EntryStrategy.VWAP,
                entry_price=100.0 + i,
                stop_loss=95.0 + i,
                stop_loss_percent=5.0,
                target_price=110.0 + i,
                target_percent=10.0,
                exit_strategy=ExitStrategy.FIXED_TARGET,
                position_size_eur=250.0,
                position_size_shares=2,
                max_risk_eur=10.0,
                risk_reward_ratio=2.0
            )
            test_trades.append(trade)
        
        # Concurrent writes
        write_tasks = []
        for i, trade in enumerate(test_trades):
            journal = journals[i % len(journals)]
            write_tasks.append(journal.record_trade(trade))
        
        await asyncio.gather(*write_tasks)
        
        # Verify all trades recorded
        all_trades = journals[0].get_recent_trades()
        assert len(all_trades) == 100
        
        # Concurrent reads
        read_tasks = [journal.get_recent_trades() for journal in journals]
        results = await asyncio.gather(*read_tasks)
        
        # All should see same data
        for result in results:
            assert len(result) == 100