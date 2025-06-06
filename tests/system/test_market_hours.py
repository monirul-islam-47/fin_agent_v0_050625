"""System tests for market hours handling."""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
import pytz

from src.orchestration.coordinator import Coordinator
from src.orchestration.event_bus import EventBus
from src.orchestration.scheduler import Scheduler
from src.orchestration.events import ScanRequest, EventPriority


class TestMarketHoursHandling:
    """Test system behavior during different market states."""

    @pytest.fixture
    async def system_components(self):
        """Create system components."""
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
        
        scheduler = Scheduler(event_bus)
        
        yield {
            "event_bus": event_bus,
            "coordinator": coordinator,
            "scheduler": scheduler,
            "market_data": mock_market_data,
            "scanner": mock_scanner,
            "universe": mock_universe
        }
        
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_pre_market_scan_timing(self, system_components):
        """Test that pre-market scan runs at correct time."""
        event_bus = system_components["event_bus"]
        coordinator = system_components["coordinator"]
        scheduler = system_components["scheduler"]
        
        # Mock universe
        system_components["universe"].get_active_symbols.return_value = ["AAPL", "GOOGL"]
        system_components["scanner"].scan_pre_market.return_value = []
        
        # Set time to just before 14:00 CET (8 AM ET)
        cet = pytz.timezone('Europe/Paris')
        mock_time = datetime.now(cet).replace(hour=13, minute=59, second=50)
        
        with patch('src.orchestration.scheduler.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            
            # Start scheduler
            await scheduler.start()
            
            # Wait for scan to trigger
            await asyncio.sleep(15)  # Wait past 14:00
            
            # Verify scan was triggered
            assert system_components["scanner"].scan_pre_market.called

    @pytest.mark.asyncio
    async def test_market_closed_behavior(self, system_components):
        """Test system behavior when market is closed."""
        coordinator = system_components["coordinator"]
        
        # Mock market as closed
        system_components["market_data"].is_market_open.return_value = False
        
        # Try to run scan
        event = ScanRequest(scan_type="manual")
        
        # Should handle gracefully
        result = await coordinator.handle_scan_request(event)
        
        # Should check market status
        system_components["market_data"].is_market_open.assert_called()

    @pytest.mark.asyncio
    async def test_weekend_handling(self, system_components):
        """Test system behavior on weekends."""
        scheduler = system_components["scheduler"]
        
        # Set time to Saturday
        with patch('src.orchestration.scheduler.datetime') as mock_datetime:
            saturday = datetime(2025, 1, 4, 14, 0)  # Saturday
            mock_datetime.now.return_value = saturday
            mock_datetime.now().weekday.return_value = 5  # Saturday
            
            # Check if scan should run
            should_run = scheduler._should_run_on_weekend()
            assert should_run is False

    @pytest.mark.asyncio
    async def test_holiday_handling(self, system_components):
        """Test system behavior on market holidays."""
        coordinator = system_components["coordinator"]
        
        # Define US market holidays
        holidays = [
            datetime(2025, 1, 1),   # New Year's Day
            datetime(2025, 7, 4),   # Independence Day
            datetime(2025, 12, 25), # Christmas
        ]
        
        for holiday in holidays:
            with patch('datetime.datetime') as mock_datetime:
                mock_datetime.now.return_value = holiday
                
                # Market should be closed
                is_trading_day = coordinator._is_trading_day(holiday)
                assert is_trading_day is False

    @pytest.mark.asyncio
    async def test_second_look_scan_timing(self, system_components):
        """Test second-look scan at 18:15 CET."""
        scheduler = system_components["scheduler"]
        event_bus = system_components["event_bus"]
        
        # Track scan requests
        scan_requests = []
        
        async def track_scan(event):
            if isinstance(event, ScanRequest):
                scan_requests.append(event)
        
        await event_bus.subscribe(ScanRequest, track_scan)
        
        # Set time to 18:14 CET
        cet = pytz.timezone('Europe/Paris')
        mock_time = datetime.now(cet).replace(hour=18, minute=14, second=50)
        
        with patch('src.orchestration.scheduler.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            
            # Start scheduler
            await scheduler.start()
            
            # Wait for second-look scan
            await asyncio.sleep(15)
            
            # Should have triggered second-look scan
            second_look_scans = [s for s in scan_requests if s.scan_type == "second_look"]
            assert len(second_look_scans) > 0

    @pytest.mark.asyncio
    async def test_market_state_transitions(self, system_components):
        """Test handling of market state transitions."""
        coordinator = system_components["coordinator"]
        market_data = system_components["market_data"]
        
        # Define market states
        states = [
            ("pre", True),    # Pre-market
            ("open", True),   # Market open
            ("after", True),  # After-hours
            ("closed", False) # Closed
        ]
        
        for state, can_trade in states:
            market_data.get_market_state.return_value = state
            market_data.is_market_open.return_value = (state == "open")
            
            # Check if trading allowed
            result = coordinator._can_execute_trades()
            
            if state == "open":
                assert result is True
            else:
                assert result is False

    @pytest.mark.asyncio
    async def test_timezone_handling(self, system_components):
        """Test correct timezone conversions."""
        scheduler = system_components["scheduler"]
        
        # Test conversions between CET and ET
        test_times = [
            # CET time, ET time
            (datetime(2025, 1, 6, 14, 0), datetime(2025, 1, 6, 8, 0)),   # 14:00 CET = 8:00 ET
            (datetime(2025, 1, 6, 18, 15), datetime(2025, 1, 6, 12, 15)), # 18:15 CET = 12:15 ET
        ]
        
        for cet_time, et_time in test_times:
            # Convert CET to ET
            cet_tz = pytz.timezone('Europe/Paris')
            et_tz = pytz.timezone('US/Eastern')
            
            cet_aware = cet_tz.localize(cet_time)
            et_converted = cet_aware.astimezone(et_tz)
            
            assert et_converted.hour == et_time.hour
            assert et_converted.minute == et_time.minute

    @pytest.mark.asyncio
    async def test_partial_market_days(self, system_components):
        """Test handling of early close days."""
        coordinator = system_components["coordinator"]
        
        # Early close days (e.g., day before holidays)
        early_close_days = [
            datetime(2025, 7, 3),    # Day before Independence Day
            datetime(2025, 12, 24),  # Christmas Eve
        ]
        
        for day in early_close_days:
            with patch('datetime.datetime') as mock_datetime:
                # Set time to 1 PM ET (normally open, but closed on early days)
                mock_datetime.now.return_value = day.replace(hour=13, minute=0)
                
                # Should recognize early close
                is_open = coordinator._is_market_open_at_time(mock_datetime.now())
                
                # Market closes at 1 PM ET on early close days
                if mock_datetime.now().hour >= 13:
                    assert is_open is False

    @pytest.mark.asyncio
    async def test_scan_scheduling_accuracy(self, system_components):
        """Test that scans run within acceptable time window."""
        scheduler = system_components["scheduler"]
        event_bus = system_components["event_bus"]
        
        # Track scan timing
        scan_times = []
        
        async def track_scan_time(event):
            if isinstance(event, ScanRequest):
                scan_times.append(datetime.now())
        
        await event_bus.subscribe(ScanRequest, track_scan_time)
        
        # Set time just before scan
        target_time = datetime.now().replace(hour=14, minute=0, second=0)
        
        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = target_time - timedelta(seconds=5)
            
            await scheduler.start()
            await asyncio.sleep(10)
            
            # Check scan ran close to target time
            if scan_times:
                actual_time = scan_times[0]
                time_diff = abs((actual_time - target_time).total_seconds())
                assert time_diff < 2  # Within 2 seconds

    @pytest.mark.asyncio
    async def test_daylight_saving_transitions(self, system_components):
        """Test handling of daylight saving time changes."""
        scheduler = system_components["scheduler"]
        
        # Test spring forward (lose an hour)
        spring_forward = datetime(2025, 3, 30, 2, 0)  # 2 AM becomes 3 AM
        
        # Test fall back (gain an hour)
        fall_back = datetime(2025, 10, 26, 3, 0)  # 3 AM becomes 2 AM
        
        # Verify scans still run at correct local times
        for transition_date in [spring_forward, fall_back]:
            with patch('datetime.datetime') as mock_datetime:
                mock_datetime.now.return_value = transition_date
                
                # Calculate next scan time
                next_scan = scheduler._calculate_next_scan_time()
                
                # Should maintain correct local time despite DST
                assert next_scan.hour == 14  # Still 14:00 CET