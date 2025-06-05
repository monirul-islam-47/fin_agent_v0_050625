"""Scheduler for automated market scans."""
import asyncio
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum
import pytz

from src.utils.logger import get_logger
from src.orchestration.event_bus import EventBus
from src.orchestration.events import (
    ScanRequest, SystemStatus, ErrorEvent, EventPriority
)
from src.data.finnhub import FinnhubWebSocket
from src.config.settings import get_config


logger = get_logger(__name__)


class ScanType(Enum):
    """Types of market scans."""
    PRIMARY = "primary"
    SECOND_LOOK = "second_look"
    MANUAL = "manual"


@dataclass
class ScheduledScan:
    """Scheduled scan configuration."""
    scan_type: ScanType
    scheduled_time: time
    enabled: bool = True
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None


class Scheduler:
    """Manages scheduled market scans and WebSocket connections."""
    
    def __init__(self, event_bus: EventBus):
        """Initialize scheduler.
        
        Args:
            event_bus: Event bus for communication
        """
        self.event_bus = event_bus
        self.config = get_config()
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}
        
        # WebSocket connection
        self._websocket: Optional[FinnhubWebSocket] = None
        self._websocket_task: Optional[asyncio.Task] = None
        
        # Scheduled scans (in CET timezone)
        self.cet = pytz.timezone('CET')
        self.scheduled_scans = {
            ScanType.PRIMARY: ScheduledScan(
                scan_type=ScanType.PRIMARY,
                scheduled_time=time(14, 0),  # 14:00 CET
                enabled=True
            ),
            ScanType.SECOND_LOOK: ScheduledScan(
                scan_type=ScanType.SECOND_LOOK,
                scheduled_time=time(18, 15),  # 18:15 CET
                enabled=True
            )
        }
        
        # State for persistence
        self._state_file = "data/scheduler_state.json"
        
    async def start(self):
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return
            
        self._running = True
        
        # Load saved state
        await self._load_state()
        
        # Calculate next run times
        self._update_next_runs()
        
        # Start WebSocket connection
        await self._start_websocket()
        
        # Start scheduler task
        self._tasks["scheduler"] = asyncio.create_task(self._scheduler_loop())
        
        # Emit status event
        await self.event_bus.publish(SystemStatus(
            component="scheduler",
            status="started",
            message="Scheduler started successfully",
            metrics={
                "scheduled_scans": len(self.scheduled_scans),
                "websocket_connected": self._websocket is not None
            }
        ))
        
        logger.info("Scheduler started")
        
    async def stop(self):
        """Stop the scheduler gracefully."""
        self._running = False
        
        # Save state
        await self._save_state()
        
        # Stop WebSocket
        await self._stop_websocket()
        
        # Cancel all tasks
        for task_name, task in self._tasks.items():
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug(f"Task {task_name} cancelled")
                    
        self._tasks.clear()
        
        # Emit status event
        await self.event_bus.publish(SystemStatus(
            component="scheduler",
            status="stopped",
            message="Scheduler stopped"
        ))
        
        logger.info("Scheduler stopped")
        
    async def trigger_manual_scan(self, scan_type: str = "manual") -> bool:
        """Trigger a manual scan.
        
        Args:
            scan_type: Type of scan to trigger
            
        Returns:
            True if scan was triggered successfully
        """
        try:
            # Create scan request
            scan_request = ScanRequest(
                scan_type=scan_type,
                source="scheduler_manual"
            )
            scan_request.priority = EventPriority.HIGH
            
            # Publish event
            await self.event_bus.publish(scan_request)
            
            logger.info(f"Manual scan triggered: {scan_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to trigger manual scan: {e}")
            await self.event_bus.publish(ErrorEvent(
                error_type="scan_trigger_error",
                error_message=str(e),
                component="scheduler",
                recoverable=True
            ))
            return False
            
    async def schedule_scan(self, scheduled_time: str, scan_type: str):
        """Schedule a new scan time.
        
        Args:
            scheduled_time: Time in HH:MM format (CET)
            scan_type: Type of scan
        """
        try:
            # Parse time
            hour, minute = map(int, scheduled_time.split(":"))
            new_time = time(hour, minute)
            
            # Update or create scheduled scan
            scan_enum = ScanType(scan_type)
            if scan_enum in self.scheduled_scans:
                self.scheduled_scans[scan_enum].scheduled_time = new_time
                self.scheduled_scans[scan_enum].enabled = True
            else:
                self.scheduled_scans[scan_enum] = ScheduledScan(
                    scan_type=scan_enum,
                    scheduled_time=new_time,
                    enabled=True
                )
                
            # Update next run
            self._update_next_runs()
            
            # Save state
            await self._save_state()
            
            logger.info(f"Scheduled {scan_type} scan at {scheduled_time} CET")
            
        except Exception as e:
            logger.error(f"Failed to schedule scan: {e}")
            raise
            
    async def _scheduler_loop(self):
        """Main scheduler loop."""
        logger.info("Scheduler loop started")
        
        while self._running:
            try:
                # Check for scheduled scans
                now = datetime.now(self.cet)
                
                for scan_type, scan_config in self.scheduled_scans.items():
                    if not scan_config.enabled:
                        continue
                        
                    if scan_config.next_run and now >= scan_config.next_run:
                        # Time to run scan
                        await self._execute_scheduled_scan(scan_config)
                        
                        # Update last run and calculate next run
                        scan_config.last_run = now
                        self._update_next_runs()
                        
                        # Save state
                        await self._save_state()
                        
                # Sleep for a short interval
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await self.event_bus.publish(ErrorEvent(
                    error_type="scheduler_loop_error",
                    error_message=str(e),
                    component="scheduler",
                    recoverable=True
                ))
                
                # Sleep before retry
                await asyncio.sleep(60)
                
    async def _execute_scheduled_scan(self, scan_config: ScheduledScan):
        """Execute a scheduled scan.
        
        Args:
            scan_config: Scan configuration
        """
        logger.info(f"Executing scheduled {scan_config.scan_type.value} scan")
        
        try:
            # Create scan request
            scan_request = ScanRequest(
                scan_type=scan_config.scan_type.value,
                source="scheduler_auto"
            )
            scan_request.priority = EventPriority.HIGH
            
            # Publish event
            await self.event_bus.publish(scan_request)
            
            # Emit status
            await self.event_bus.publish(SystemStatus(
                component="scheduler",
                status="scan_triggered",
                message=f"Triggered {scan_config.scan_type.value} scan",
                metrics={
                    "scan_type": scan_config.scan_type.value,
                    "scheduled_time": scan_config.scheduled_time.isoformat(),
                    "execution_time": datetime.now().isoformat()
                }
            ))
            
        except Exception as e:
            logger.error(f"Failed to execute scheduled scan: {e}")
            await self.event_bus.publish(ErrorEvent(
                error_type="scheduled_scan_error",
                error_message=str(e),
                component="scheduler",
                recoverable=True
            ))
            
    def _update_next_runs(self):
        """Update next run times for all scheduled scans."""
        now = datetime.now(self.cet)
        
        for scan_config in self.scheduled_scans.values():
            if not scan_config.enabled:
                scan_config.next_run = None
                continue
                
            # Calculate next run time
            next_run = now.replace(
                hour=scan_config.scheduled_time.hour,
                minute=scan_config.scheduled_time.minute,
                second=0,
                microsecond=0
            )
            
            # If time has passed today, schedule for tomorrow
            if next_run <= now:
                next_run += timedelta(days=1)
                
            scan_config.next_run = next_run
            
        # Log next runs
        for scan_type, scan_config in self.scheduled_scans.items():
            if scan_config.next_run:
                logger.info(
                    f"Next {scan_type.value} scan scheduled for: "
                    f"{scan_config.next_run.strftime('%Y-%m-%d %H:%M %Z')}"
                )
                
    async def _start_websocket(self):
        """Start WebSocket connection for real-time data."""
        try:
            if self.config.api.finnhub_key:
                self._websocket = FinnhubWebSocket(
                    api_key=self.config.api.finnhub_key
                )
                
                # Start connection task
                self._websocket_task = asyncio.create_task(
                    self._manage_websocket()
                )
                
                logger.info("WebSocket connection started")
            else:
                logger.warning("No Finnhub API key, WebSocket disabled")
                
        except Exception as e:
            logger.error(f"Failed to start WebSocket: {e}")
            
    async def _stop_websocket(self):
        """Stop WebSocket connection."""
        if self._websocket_task:
            self._websocket_task.cancel()
            try:
                await self._websocket_task
            except asyncio.CancelledError:
                pass
                
        if self._websocket:
            await self._websocket.disconnect()
            self._websocket = None
            
        logger.info("WebSocket connection stopped")
        
    async def _manage_websocket(self):
        """Manage WebSocket connection with reconnection logic."""
        reconnect_delay = 5
        max_reconnect_delay = 300  # 5 minutes
        
        while self._running and self._websocket:
            try:
                # Connect
                await self._websocket.connect()
                
                # Reset reconnect delay on successful connection
                reconnect_delay = 5
                
                # Keep connection alive
                while self._running and self._websocket.connected:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                
                # Exponential backoff
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                
    async def _load_state(self):
        """Load scheduler state from file."""
        # TODO: Implement state loading from JSON file
        pass
        
    async def _save_state(self):
        """Save scheduler state to file."""
        # TODO: Implement state saving to JSON file
        pass
        
    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status.
        
        Returns:
            Status dictionary
        """
        status = {
            "running": self._running,
            "websocket_connected": self._websocket and self._websocket.connected,
            "scheduled_scans": {}
        }
        
        for scan_type, scan_config in self.scheduled_scans.items():
            status["scheduled_scans"][scan_type.value] = {
                "enabled": scan_config.enabled,
                "scheduled_time": scan_config.scheduled_time.isoformat(),
                "last_run": scan_config.last_run.isoformat() if scan_config.last_run else None,
                "next_run": scan_config.next_run.isoformat() if scan_config.next_run else None
            }
            
        return status