"""Unit tests for the orchestration layer."""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from src.orchestration import (
    EventBus, Event, EventType, EventPriority,
    ScanRequest, TradeSignal, SystemStatus, ErrorEvent,
    Scheduler, Coordinator
)
from src.domain.planner import TradePlan


class TestEventBus:
    """Test EventBus functionality."""
    
    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test starting and stopping event bus."""
        bus = EventBus()
        
        # Start
        await bus.start()
        assert bus._running is True
        
        # Stop
        await bus.stop()
        assert bus._running is False
        
    @pytest.mark.asyncio
    async def test_publish_subscribe(self):
        """Test publishing and subscribing to events."""
        bus = EventBus()
        await bus.start()
        
        # Track received events
        received_events = []
        
        async def handler(event: ScanRequest):
            received_events.append(event)
        
        # Subscribe
        await bus.subscribe(ScanRequest, handler)
        
        # Publish event
        event = ScanRequest(scan_type="primary")
        await bus.publish(event)
        
        # Wait for processing
        await asyncio.sleep(0.1)
        
        # Check event received
        assert len(received_events) == 1
        assert received_events[0].scan_type == "primary"
        
        await bus.stop()
        
    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """Test that higher priority events are processed first."""
        bus = EventBus(max_queue_size=10)
        await bus.start()
        
        received_order = []
        
        async def handler(event: Event):
            received_order.append(event.priority.name)
        
        await bus.subscribe(Event, handler)
        
        # Publish events in mixed order
        low_event = Event(priority=EventPriority.LOW)
        critical_event = Event(priority=EventPriority.CRITICAL)
        normal_event = Event(priority=EventPriority.NORMAL)
        high_event = Event(priority=EventPriority.HIGH)
        
        await bus.publish(low_event)
        await bus.publish(critical_event)
        await bus.publish(normal_event)
        await bus.publish(high_event)
        
        # Wait for processing
        await asyncio.sleep(0.2)
        
        # Check order - should be CRITICAL, HIGH, NORMAL, LOW
        assert received_order == ["CRITICAL", "HIGH", "NORMAL", "LOW"]
        
        await bus.stop()
        
    @pytest.mark.asyncio
    async def test_error_isolation(self):
        """Test that handler errors don't crash the bus."""
        bus = EventBus()
        await bus.start()
        
        good_events = []
        
        async def bad_handler(event: Event):
            raise ValueError("Test error")
        
        async def good_handler(event: Event):
            good_events.append(event)
        
        # Subscribe both handlers
        await bus.subscribe(Event, bad_handler, name="bad_handler")
        await bus.subscribe(Event, good_handler, name="good_handler")
        
        # Publish event
        event = SystemStatus(component="test", status="running")
        await bus.publish(event)
        
        # Wait for processing
        await asyncio.sleep(0.1)
        
        # Good handler should still receive event
        assert len(good_events) == 1
        
        await bus.stop()


class TestScheduler:
    """Test Scheduler functionality."""
    
    @pytest.mark.asyncio
    async def test_manual_scan_trigger(self):
        """Test triggering a manual scan."""
        bus = EventBus()
        await bus.start()
        
        scheduler = Scheduler(bus)
        
        # Track scan requests
        scan_requests = []
        
        async def handler(event: ScanRequest):
            scan_requests.append(event)
        
        await bus.subscribe(ScanRequest, handler)
        
        # Trigger manual scan
        success = await scheduler.trigger_manual_scan("primary")
        assert success is True
        
        # Wait for event
        await asyncio.sleep(0.1)
        
        # Check scan request published
        assert len(scan_requests) == 1
        assert scan_requests[0].scan_type == "primary"
        
        await bus.stop()
        
    def test_scheduler_status(self):
        """Test getting scheduler status."""
        bus = EventBus()
        scheduler = Scheduler(bus)
        
        status = scheduler.get_status()
        
        assert "running" in status
        assert "websocket_connected" in status
        assert "scheduled_scans" in status
        assert status["running"] is False


class TestCoordinator:
    """Test Coordinator functionality."""
    
    @pytest.mark.asyncio
    async def test_scan_workflow(self):
        """Test complete scan workflow with mocked components."""
        bus = EventBus()
        await bus.start()
        
        # Create coordinator with mocked dependencies
        with patch('src.orchestration.coordinator.UniverseManager') as MockUniverse, \
             patch('src.orchestration.coordinator.GapScanner') as MockScanner, \
             patch('src.orchestration.coordinator.FactorModel') as MockModel, \
             patch('src.orchestration.coordinator.TradePlanner') as MockPlanner, \
             patch('src.orchestration.coordinator.RiskManager') as MockRisk:
            
            # Setup mocks
            mock_universe = MockUniverse.return_value
            mock_universe.get_tradable_symbols = AsyncMock(return_value=["AAPL", "MSFT"])
            
            mock_scanner = MockScanner.return_value
            mock_gap_result = Mock()
            mock_gap_result.symbol = "AAPL"
            mock_gap_result.gap_percent = 5.0
            mock_scanner.scan_universe = AsyncMock(return_value=[mock_gap_result])
            
            mock_model = MockModel.return_value
            mock_score = Mock(total_score=0.8, factor_scores={"volatility": 0.9})
            mock_model.score_candidate = Mock(return_value=mock_score)
            mock_model.select_top_candidates = Mock(return_value=[(mock_gap_result, mock_score)])
            
            mock_planner = MockPlanner.return_value
            mock_trade = TradePlan(
                symbol="AAPL",
                entry_price=150.0,
                target_price=155.0,
                stop_price=147.0,
                position_size=100,
                strategy="VWAP"
            )
            mock_planner.plan_trade = Mock(return_value=mock_trade)
            
            mock_risk = MockRisk.return_value
            mock_risk.check_trade = AsyncMock(return_value=(True, None))
            
            coordinator = Coordinator(bus)
            await coordinator.start()
            
            # Track signals
            trade_signals = []
            
            async def signal_handler(event: TradeSignal):
                trade_signals.append(event)
            
            await bus.subscribe(TradeSignal, signal_handler)
            
            # Run scan
            results = await coordinator.run_primary_scan()
            
            # Verify results
            assert len(results) == 1
            assert results[0].symbol == "AAPL"
            assert results[0].entry_price == 150.0
            
            # Check trade signal published
            await asyncio.sleep(0.1)
            assert len(trade_signals) == 1
            
            await coordinator.stop()
        
        await bus.stop()
        
    def test_coordinator_status(self):
        """Test getting coordinator status."""
        bus = EventBus()
        coordinator = Coordinator(bus)
        
        status = coordinator.get_status()
        
        assert "running" in status
        assert "scan_active" in status
        assert "components" in status
        assert status["running"] is False


@pytest.mark.asyncio
async def test_event_types():
    """Test that all event types work correctly."""
    events = [
        ScanRequest(scan_type="primary"),
        TradeSignal(
            trade_plan=TradePlan("TEST", 100, 105, 95, 100, "VWAP"),
            score=0.8
        ),
        SystemStatus(component="test", status="running"),
        ErrorEvent(
            error_type="test_error",
            error_message="Test",
            component="test"
        )
    ]
    
    for event in events:
        assert event.event_type is not None
        assert event.timestamp is not None
        assert event.priority is not None