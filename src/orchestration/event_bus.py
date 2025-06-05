"""Async event bus for component communication."""
import asyncio
from collections import defaultdict
from typing import Callable, Type, List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import traceback

from src.utils.logger import get_logger
from src.orchestration.events import Event, EventType, EventPriority, ErrorEvent


logger = get_logger(__name__)


@dataclass
class Subscription:
    """Event subscription details."""
    event_type: Type[Event]
    handler: Callable[[Event], Any]
    filter_fn: Optional[Callable[[Event], bool]] = None
    name: Optional[str] = None


class EventBus:
    """Asynchronous event bus for component communication."""
    
    def __init__(self, max_queue_size: int = 1000):
        """Initialize event bus.
        
        Args:
            max_queue_size: Maximum number of events in queue
        """
        self.max_queue_size = max_queue_size
        self._subscribers: Dict[Type[Event], List[Subscription]] = defaultdict(list)
        self._event_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._event_counter = 0
        self._metrics = {
            "events_published": 0,
            "events_processed": 0,
            "events_failed": 0,
            "events_dropped": 0
        }
        
    async def start(self):
        """Start the event bus."""
        if self._running:
            logger.warning("Event bus already running")
            return
            
        self._running = True
        self._worker_task = asyncio.create_task(self._process_events())
        logger.info("Event bus started")
        
    async def stop(self):
        """Stop the event bus gracefully."""
        self._running = False
        
        # Process remaining events
        while not self._event_queue.empty():
            await asyncio.sleep(0.1)
            
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
                
        logger.info(f"Event bus stopped. Metrics: {self._metrics}")
        
    async def publish(self, event: Event, priority: Optional[EventPriority] = None):
        """Publish an event to the bus.
        
        Args:
            event: Event to publish
            priority: Override event priority
        """
        if not self._running:
            logger.error("Cannot publish event - bus not running")
            return
            
        if priority:
            event.priority = priority
            
        # Use negative priority for proper ordering (higher priority = lower number)
        priority_value = -event.priority.value
        self._event_counter += 1
        
        try:
            # Non-blocking put
            self._event_queue.put_nowait((priority_value, self._event_counter, event))
            self._metrics["events_published"] += 1
            logger.debug(f"Published event: {event.event_type.value} with priority {event.priority.name}")
        except asyncio.QueueFull:
            self._metrics["events_dropped"] += 1
            logger.error(f"Event queue full, dropping event: {event.event_type.value}")
            
    async def subscribe(
        self,
        event_type: Type[Event],
        handler: Callable[[Event], Any],
        filter_fn: Optional[Callable[[Event], bool]] = None,
        name: Optional[str] = None
    ):
        """Subscribe to an event type.
        
        Args:
            event_type: Type of event to subscribe to
            handler: Async function to handle the event
            filter_fn: Optional filter function
            name: Optional name for the subscription
        """
        subscription = Subscription(
            event_type=event_type,
            handler=handler,
            filter_fn=filter_fn,
            name=name or handler.__name__
        )
        
        self._subscribers[event_type].append(subscription)
        logger.info(f"Subscribed {subscription.name} to {event_type.__name__}")
        
    async def unsubscribe(self, event_type: Type[Event], handler: Callable[[Event], Any]):
        """Unsubscribe from an event type.
        
        Args:
            event_type: Type of event to unsubscribe from
            handler: Handler function to remove
        """
        self._subscribers[event_type] = [
            sub for sub in self._subscribers[event_type]
            if sub.handler != handler
        ]
        
    async def _process_events(self):
        """Process events from the queue."""
        logger.info("Event processor started")
        
        while self._running:
            try:
                # Wait for event with timeout
                priority, counter, event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )
                
                # Process event
                await self._dispatch_event(event)
                self._metrics["events_processed"] += 1
                
            except asyncio.TimeoutError:
                # No events, continue
                continue
            except Exception as e:
                logger.error(f"Error processing event: {e}")
                self._metrics["events_failed"] += 1
                
    async def _dispatch_event(self, event: Event):
        """Dispatch event to all subscribers.
        
        Args:
            event: Event to dispatch
        """
        # Get subscribers for exact event type
        subscribers = self._subscribers.get(type(event), [])
        
        # Also get subscribers for base Event class
        if type(event) != Event:
            subscribers.extend(self._subscribers.get(Event, []))
            
        if not subscribers:
            logger.debug(f"No subscribers for event: {event.event_type.value}")
            return
            
        # Create tasks for all handlers
        tasks = []
        for subscription in subscribers:
            # Apply filter if present
            if subscription.filter_fn and not subscription.filter_fn(event):
                continue
                
            task = asyncio.create_task(
                self._safe_handler_call(subscription, event)
            )
            tasks.append(task)
            
        # Wait for all handlers to complete
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
    async def _safe_handler_call(self, subscription: Subscription, event: Event):
        """Safely call event handler with error isolation.
        
        Args:
            subscription: Subscription details
            event: Event to handle
        """
        try:
            # Check if handler is async
            if asyncio.iscoroutinefunction(subscription.handler):
                await subscription.handler(event)
            else:
                # Run sync handler in thread pool
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    subscription.handler,
                    event
                )
                
        except Exception as e:
            logger.error(f"Handler {subscription.name} failed for event {event.event_type.value}: {e}")
            
            # Publish error event (avoid infinite loop)
            if not isinstance(event, ErrorEvent):
                error_event = ErrorEvent(
                    error_type="handler_error",
                    error_message=str(e),
                    component=subscription.name,
                    traceback=traceback.format_exc(),
                    recoverable=True
                )
                await self.publish(error_event)
                
    def get_metrics(self) -> Dict[str, Any]:
        """Get event bus metrics.
        
        Returns:
            Dictionary of metrics
        """
        return {
            **self._metrics,
            "queue_size": self._event_queue.qsize(),
            "subscriber_count": sum(len(subs) for subs in self._subscribers.values())
        }
        
    async def wait_for_event(
        self,
        event_type: Type[Event],
        timeout: Optional[float] = None,
        filter_fn: Optional[Callable[[Event], bool]] = None
    ) -> Optional[Event]:
        """Wait for a specific event type.
        
        Args:
            event_type: Type of event to wait for
            timeout: Maximum time to wait
            filter_fn: Optional filter function
            
        Returns:
            Event if received, None if timeout
        """
        received_event = None
        event_received = asyncio.Event()
        
        async def handler(event: Event):
            nonlocal received_event
            if not filter_fn or filter_fn(event):
                received_event = event
                event_received.set()
                
        # Subscribe temporarily
        await self.subscribe(event_type, handler, name="wait_for_event")
        
        try:
            await asyncio.wait_for(event_received.wait(), timeout=timeout)
            return received_event
        except asyncio.TimeoutError:
            return None
        finally:
            await self.unsubscribe(event_type, handler)