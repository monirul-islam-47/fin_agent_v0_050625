"""Event definitions for the orchestration layer."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.planner import TradePlan


class EventPriority(Enum):
    """Event priority levels."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class EventType(Enum):
    """Available event types."""
    SCAN_REQUEST = "scan_request"
    DATA_UPDATE = "data_update"
    TRADE_SIGNAL = "trade_signal"
    RISK_ALERT = "risk_alert"
    SYSTEM_STATUS = "system_status"
    QUOTA_WARNING = "quota_warning"
    ERROR = "error"


@dataclass
class Event:
    """Base event class."""
    timestamp: datetime = field(default_factory=datetime.now)
    priority: EventPriority = field(default=EventPriority.NORMAL)
    source: str = field(default="system")
    data: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def event_type(self) -> EventType:
        """Get event type - must be overridden by subclasses."""
        return EventType.SYSTEM_STATUS


@dataclass
class ScanRequest(Event):
    """Request to run a market scan."""
    scan_type: str = ""  # "primary" or "second_look"
    universe: Optional[List[str]] = field(default=None)  # Optional specific symbols
    priority: EventPriority = field(default=EventPriority.HIGH)
    
    @property
    def event_type(self) -> EventType:
        return EventType.SCAN_REQUEST


@dataclass
class DataUpdate(Event):
    """Market data update event."""
    symbol: str = ""
    data_type: str = ""  # "quote", "news", "sentiment"
    update_data: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def event_type(self) -> EventType:
        return EventType.DATA_UPDATE


@dataclass
class TradeSignal(Event):
    """Trading signal event."""
    trade_plan: Any = None  # TradePlan instance
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    priority: EventPriority = field(default=EventPriority.HIGH)
    
    @property
    def event_type(self) -> EventType:
        return EventType.TRADE_SIGNAL


@dataclass
class RiskAlert(Event):
    """Risk management alert."""
    alert_type: str = ""  # "position_limit", "loss_limit", "correlation", "priips"
    severity: str = "warning"  # "warning", "critical"
    message: str = ""
    affected_symbols: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        # Set priority based on severity
        if self.severity == "critical":
            self.priority = EventPriority.CRITICAL
        else:
            self.priority = EventPriority.HIGH
    
    @property
    def event_type(self) -> EventType:
        return EventType.RISK_ALERT


@dataclass
class SystemStatus(Event):
    """System status update."""
    component: str = ""
    status: str = ""  # "started", "stopped", "error", "ready"
    message: Optional[str] = field(default=None)
    metrics: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def event_type(self) -> EventType:
        return EventType.SYSTEM_STATUS


@dataclass
class QuotaWarning(Event):
    """API quota warning event."""
    provider: str = ""
    usage_percent: float = 0.0
    remaining_calls: int = 0
    reset_time: Optional[datetime] = field(default=None)
    
    def __post_init__(self):
        # Set priority based on usage
        if self.usage_percent > 80:
            self.priority = EventPriority.HIGH
        else:
            self.priority = EventPriority.NORMAL
    
    @property
    def event_type(self) -> EventType:
        return EventType.QUOTA_WARNING


@dataclass
class ErrorEvent(Event):
    """Error event for system errors."""
    error_type: str = ""
    error_message: str = ""
    component: str = ""
    traceback: Optional[str] = field(default=None)
    recoverable: bool = field(default=True)
    
    def __post_init__(self):
        # Set priority based on recoverability
        if not self.recoverable:
            self.priority = EventPriority.CRITICAL
        else:
            self.priority = EventPriority.HIGH
    
    @property
    def event_type(self) -> EventType:
        return EventType.ERROR


@dataclass
class PersistenceEvent(Event):
    """Event for persistence operations."""
    priority: EventPriority = EventPriority.NORMAL
    operation: str = ""  # "trade_recorded", "metrics_updated", "export_completed"
    entity_type: str = ""  # "trade", "metrics", "journal"
    entity_id: Optional[int] = None
    details: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error_message: Optional[str] = None
    
    @property
    def event_type(self) -> EventType:
        return EventType.SYSTEM_STATUS