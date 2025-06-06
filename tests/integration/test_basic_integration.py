"""Basic integration tests that work with the current implementation."""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from src.orchestration.coordinator import Coordinator
from src.orchestration.event_bus import EventBus
from src.orchestration.events import TradeSignal, QuotaWarning, SystemStatus
from src.domain.planner import TradePlan
from src.domain.scanner import GapResult


class TestBasicIntegration:
    """Test basic integration scenarios."""

    @pytest.mark.asyncio
    async def test_event_bus_integration(self):
        """Test event bus can handle multiple event types."""
        event_bus = EventBus()
        await event_bus.start()
        
        events_received = []
        
        async def handler(event):
            events_received.append(event)
        
        # Subscribe to multiple event types
        await event_bus.subscribe(SystemStatus, handler)
        await event_bus.subscribe(QuotaWarning, handler)
        await event_bus.subscribe(TradeSignal, handler)
        
        # Publish different events
        status_event = SystemStatus(
            component="test",
            status="ready",
            message="Test message"
        )
        await event_bus.publish(status_event)
        
        quota_event = QuotaWarning(
            provider="test_api",
            usage_percent=83.3,
            remaining_calls=10
        )
        await event_bus.publish(quota_event)
        
        # Wait for processing
        await asyncio.sleep(0.1)
        
        await event_bus.stop()
        
        assert len(events_received) == 2
        # QuotaWarning has HIGH priority, so it's processed first
        assert isinstance(events_received[0], QuotaWarning)
        assert isinstance(events_received[1], SystemStatus)

    @pytest.mark.asyncio
    async def test_coordinator_initialization(self):
        """Test coordinator can be initialized and started."""
        event_bus = EventBus()
        await event_bus.start()
        
        coordinator = Coordinator(event_bus)
        
        # Check components are initialized
        assert coordinator.event_bus is event_bus
        assert coordinator.cache is not None
        assert coordinator.market_data is not None
        
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_trade_signal_flow(self):
        """Test trade signal flows through the system."""
        event_bus = EventBus()
        await event_bus.start()
        
        signals_received = []
        
        async def signal_handler(event):
            if hasattr(event, 'trade_plan'):
                signals_received.append(event.trade_plan)
        
        await event_bus.subscribe(TradeSignal, signal_handler)
        
        # Create test trade plan
        from src.domain.planner import EntryStrategy, ExitStrategy
        
        test_plan = TradePlan(
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
        
        # Publish signal
        signal_event = TradeSignal(trade_plan=test_plan)
        await event_bus.publish(signal_event)
        
        # Wait for processing
        await asyncio.sleep(0.1)
        
        await event_bus.stop()
        
        assert len(signals_received) == 1
        assert signals_received[0].symbol == "AAPL"
        assert signals_received[0].score == 80.0

    @pytest.mark.asyncio
    async def test_quota_warning_handling(self):
        """Test quota warnings are properly handled."""
        event_bus = EventBus()
        await event_bus.start()
        
        warnings = []
        
        async def warning_handler(event):
            warnings.append(event)
        
        await event_bus.subscribe(QuotaWarning, warning_handler)
        
        # Simulate quota warning
        warning_event = QuotaWarning(
            provider="finnhub",
            usage_percent=98.3,
            remaining_calls=1
        )
        await event_bus.publish(warning_event)
        
        await asyncio.sleep(0.1)
        
        await event_bus.stop()
        
        assert len(warnings) == 1
        assert warnings[0].provider == "finnhub"
        assert warnings[0].usage_percent == 98.3

    @pytest.mark.asyncio 
    async def test_concurrent_event_processing(self):
        """Test system handles concurrent events properly."""
        event_bus = EventBus()
        await event_bus.start()
        
        processed_events = []
        
        async def slow_handler(event):
            # Simulate slow processing
            await asyncio.sleep(0.05)
            processed_events.append(event.data["id"])
        
        await event_bus.subscribe(SystemStatus, slow_handler)
        
        # Publish many events concurrently
        tasks = []
        for i in range(20):
            event = SystemStatus(component="test", status="running", data={"id": i})
            task = event_bus.publish(event)
            tasks.append(task)
        
        await asyncio.gather(*tasks)
        
        # Wait for all processing
        await asyncio.sleep(1.5)
        
        await event_bus.stop()
        
        # All events should be processed
        assert len(processed_events) == 20
        assert set(processed_events) == set(range(20))

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        """Test errors in one handler don't affect others."""
        event_bus = EventBus()
        await event_bus.start()
        
        good_events = []
        
        async def failing_handler(event):
            raise Exception("Handler failed")
        
        async def good_handler(event):
            good_events.append(event)
        
        await event_bus.subscribe(SystemStatus, failing_handler)
        await event_bus.subscribe(SystemStatus, good_handler)
        
        # Publish event
        test_event = SystemStatus(component="test", status="test")
        await event_bus.publish(test_event)
        
        await asyncio.sleep(0.1)
        
        await event_bus.stop()
        
        # Good handler should still receive event
        assert len(good_events) == 1