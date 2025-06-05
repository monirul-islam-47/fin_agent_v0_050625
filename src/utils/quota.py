"""
Quota management system for API rate limiting
Tracks usage and enforces limits across all providers
"""

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Callable, Any
import functools
from enum import Enum

from ..config.settings import get_config
from .logger import get_logger

logger = get_logger(__name__)

class QuotaPeriod(Enum):
    """Quota reset periods"""
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"

@dataclass
class QuotaInfo:
    """Information about a single quota"""
    provider: str
    limit: int
    period: QuotaPeriod
    used: int = 0
    last_reset: float = field(default_factory=time.time)
    last_call: float = field(default_factory=time.time)
    
    @property
    def remaining(self) -> int:
        """Calculate remaining quota"""
        return max(0, self.limit - self.used)
    
    @property
    def usage_percentage(self) -> float:
        """Calculate usage percentage"""
        if self.limit == 0:
            return 0.0
        return (self.used / self.limit) * 100
    
    @property
    def should_reset(self) -> bool:
        """Check if quota should be reset based on period"""
        now = time.time()
        elapsed = now - self.last_reset
        
        if self.period == QuotaPeriod.MINUTE:
            return elapsed >= 60
        elif self.period == QuotaPeriod.HOUR:
            return elapsed >= 3600
        elif self.period == QuotaPeriod.DAY:
            return elapsed >= 86400
        elif self.period == QuotaPeriod.MONTH:
            # Approximate month as 30 days
            return elapsed >= 2592000
        
        return False
    
    def reset(self):
        """Reset quota counter"""
        self.used = 0
        self.last_reset = time.time()
        logger.info(f"Reset quota for {self.provider}: {self.limit} per {self.period.value}")
    
    def increment(self, count: int = 1):
        """Increment usage counter"""
        self.used += count
        self.last_call = time.time()

class QuotaExhausted(Exception):
    """Raised when API quota is exhausted"""
    def __init__(self, provider: str, quota_info: QuotaInfo):
        self.provider = provider
        self.quota_info = quota_info
        super().__init__(
            f"Quota exhausted for {provider}: "
            f"{quota_info.used}/{quota_info.limit} per {quota_info.period.value}"
        )

class QuotaGuard:
    """Manages API quotas across all providers"""
    
    def __init__(self, quota_file: Optional[Path] = None):
        self.config = get_config()
        self.quotas: Dict[str, QuotaInfo] = {}
        self.quota_file = quota_file or self.config.system.logs_dir / "quota_state.json"
        self._lock = asyncio.Lock()
        self._fallback_callbacks: Dict[str, Callable] = {}
        
        # Initialize quotas from config
        self._initialize_quotas()
        
        # Load saved state if exists
        self._load_state()
    
    def _initialize_quotas(self):
        """Initialize quota tracking from configuration"""
        self.quotas["finnhub"] = QuotaInfo(
            provider="finnhub",
            limit=self.config.api.finnhub_calls_per_minute,
            period=QuotaPeriod.MINUTE
        )
        
        # IEX Cloud shut down - removed
        
        self.quotas["alpha_vantage"] = QuotaInfo(
            provider="alpha_vantage",
            limit=self.config.api.alpha_vantage_daily_calls,
            period=QuotaPeriod.DAY
        )
        
        self.quotas["newsapi"] = QuotaInfo(
            provider="newsapi",
            limit=self.config.api.news_api_daily_calls,
            period=QuotaPeriod.DAY
        )
    
    def _load_state(self):
        """Load saved quota state from file"""
        if self.quota_file.exists():
            try:
                with open(self.quota_file, 'r') as f:
                    state = json.load(f)
                
                for provider, data in state.items():
                    if provider in self.quotas:
                        quota = self.quotas[provider]
                        quota.used = data.get('used', 0)
                        quota.last_reset = data.get('last_reset', time.time())
                        quota.last_call = data.get('last_call', time.time())
                        
                        # Check if reset needed
                        if quota.should_reset:
                            quota.reset()
                
                logger.info(f"Loaded quota state from {self.quota_file}")
            except Exception as e:
                logger.warning(f"Failed to load quota state: {e}")
    
    def _save_state(self):
        """Save current quota state to file"""
        try:
            state = {}
            for provider, quota in self.quotas.items():
                state[provider] = {
                    'used': quota.used,
                    'last_reset': quota.last_reset,
                    'last_call': quota.last_call
                }
            
            self.quota_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.quota_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save quota state: {e}")
    
    async def check_quota(self, provider: str, count: int = 1) -> bool:
        """
        Check if quota is available for provider
        
        Args:
            provider: API provider name
            count: Number of calls to make
            
        Returns:
            True if quota available, False otherwise
        """
        async with self._lock:
            if provider not in self.quotas:
                logger.warning(f"Unknown provider: {provider}")
                return True
            
            quota = self.quotas[provider]
            
            # Reset if needed
            if quota.should_reset:
                quota.reset()
            
            # Check availability
            if quota.remaining >= count:
                return True
            
            # Log warning when approaching limit
            if quota.usage_percentage > 80:
                logger.warning(
                    f"Quota warning for {provider}: "
                    f"{quota.usage_percentage:.1f}% used "
                    f"({quota.used}/{quota.limit})"
                )
            
            return False
    
    async def consume_quota(self, provider: str, count: int = 1):
        """
        Consume quota for a provider
        
        Args:
            provider: API provider name
            count: Number of calls made
            
        Raises:
            QuotaExhausted: If quota would be exceeded
        """
        async with self._lock:
            if provider not in self.quotas:
                logger.warning(f"Unknown provider: {provider}, not tracking quota")
                return
            
            quota = self.quotas[provider]
            
            # Reset if needed
            if quota.should_reset:
                quota.reset()
            
            # Check if we can consume
            if quota.remaining < count:
                # Trigger fallback callback if registered
                if provider in self._fallback_callbacks:
                    logger.info(f"Triggering fallback for {provider}")
                    await self._fallback_callbacks[provider]()
                
                raise QuotaExhausted(provider, quota)
            
            # Consume quota
            quota.increment(count)
            self._save_state()
            
            # Log if high usage
            if quota.usage_percentage > 90:
                logger.warning(
                    f"High quota usage for {provider}: "
                    f"{quota.usage_percentage:.1f}% "
                    f"({quota.remaining} remaining)"
                )
    
    def register_fallback(self, provider: str, callback: Callable):
        """Register a fallback callback for when quota is exhausted"""
        self._fallback_callbacks[provider] = callback
        logger.info(f"Registered fallback for {provider}")
    
    def get_status(self) -> Dict[str, Dict[str, Any]]:
        """Get current quota status for all providers"""
        status = {}
        for provider, quota in self.quotas.items():
            # Reset check
            if quota.should_reset:
                quota.reset()
            
            status[provider] = {
                'used': quota.used,
                'limit': quota.limit,
                'remaining': quota.remaining,
                'percentage': round(quota.usage_percentage, 1),
                'period': quota.period.value,
                'last_call': datetime.fromtimestamp(quota.last_call).isoformat()
            }
        return status
    
    async def reset_all(self):
        """Force reset all quotas (useful for testing)"""
        async with self._lock:
            for quota in self.quotas.values():
                quota.reset()
            self._save_state()
            logger.info("Reset all quotas")

# Global quota guard instance
_quota_guard: Optional[QuotaGuard] = None

def get_quota_guard() -> QuotaGuard:
    """Get or create the quota guard singleton"""
    global _quota_guard
    if _quota_guard is None:
        _quota_guard = QuotaGuard()
    return _quota_guard

# Decorator for rate limiting
def rate_limit(provider: str, count: int = 1):
    """
    Decorator to enforce rate limiting on functions
    
    Args:
        provider: API provider name
        count: Number of API calls this function makes
    """
    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            guard = get_quota_guard()
            await guard.consume_quota(provider, count)
            return await func(*args, **kwargs)
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            guard = get_quota_guard()
            # Run async consume_quota in sync context
            loop = asyncio.get_event_loop()
            loop.run_until_complete(guard.consume_quota(provider, count))
            return func(*args, **kwargs)
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

# Utility function to check quotas before expensive operations
async def check_multi_quota(providers: Dict[str, int]) -> bool:
    """
    Check if multiple quotas are available
    
    Args:
        providers: Dict of provider -> count needed
        
    Returns:
        True if all quotas available
    """
    guard = get_quota_guard()
    for provider, count in providers.items():
        if not await guard.check_quota(provider, count):
            return False
    return True