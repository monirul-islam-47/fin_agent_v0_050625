"""
Quota management system for API rate limiting
Tracks usage and enforces limits across all providers
"""

import asyncio
import json
import time
import csv
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Callable, Any, List
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
    
    def __init__(self, quota_file: Optional[Path] = None, usage_log_file: Optional[Path] = None):
        self.config = get_config()
        self.quotas: Dict[str, QuotaInfo] = {}
        self.quota_file = quota_file or self.config.system.logs_dir / "quota_state.json"
        self.usage_log_file = usage_log_file or self.config.system.logs_dir / "quota_usage.csv"
        self._lock = asyncio.Lock()
        self._fallback_callbacks: Dict[str, Callable] = {}
        
        # Initialize quotas from config
        self._initialize_quotas()
        
        # Load saved state if exists
        self._load_state()
        
        # Initialize usage log
        self._init_usage_log()
    
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
    
    async def consume_quota(self, provider: str, count: int = 1, endpoint: str = ""):
        """
        Consume quota for a provider
        
        Args:
            provider: API provider name
            count: Number of calls made
            endpoint: Optional endpoint identifier for logging
            
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
                # Log failed attempt
                self._log_usage(provider, count, endpoint, success=False, 
                              error_message="Quota exhausted")
                
                # Trigger fallback callback if registered
                if provider in self._fallback_callbacks:
                    logger.info(f"Triggering fallback for {provider}")
                    await self._fallback_callbacks[provider]()
                
                raise QuotaExhausted(provider, quota)
            
            # Consume quota
            quota.increment(count)
            self._save_state()
            
            # Log successful usage
            self._log_usage(provider, count, endpoint, success=True)
            
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
    
    def _init_usage_log(self):
        """Initialize usage log CSV file if it doesn't exist or is empty"""
        self.usage_log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Check if file exists and has content
        file_exists = self.usage_log_file.exists()
        has_content = file_exists and self.usage_log_file.stat().st_size > 0
        
        if not has_content:
            with open(self.usage_log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'provider', 'endpoint', 'count', 
                    'usage_before', 'usage_after', 'limit', 'percentage',
                    'period', 'success', 'error_message'
                ])
            logger.info(f"Created quota usage log at {self.usage_log_file}")
    
    def _log_usage(self, provider: str, count: int, endpoint: str = "", 
                   success: bool = True, error_message: str = ""):
        """Log API usage to CSV file"""
        try:
            quota = self.quotas.get(provider)
            if not quota:
                return
                
            with open(self.usage_log_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().isoformat(),
                    provider,
                    endpoint,
                    count,
                    quota.used - count,  # usage before
                    quota.used,  # usage after
                    quota.limit,
                    round(quota.usage_percentage, 2),
                    quota.period.value,
                    success,
                    error_message
                ])
        except Exception as e:
            logger.error(f"Failed to log quota usage: {e}")
    
    def get_usage_summary(self, days: int = 7) -> Dict[str, Any]:
        """Get usage summary for the last N days"""
        summary = {
            'by_provider': defaultdict(lambda: {'total_calls': 0, 'total_cost': 0}),
            'by_day': defaultdict(lambda: defaultdict(int)),
            'total_calls': 0,
            'estimated_cost': 0.0
        }
        
        try:
            if not self.usage_log_file.exists():
                return dict(summary)
                
            cutoff_date = datetime.now() - timedelta(days=days)
            
            with open(self.usage_log_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        timestamp = datetime.fromisoformat(row['timestamp'])
                    except (KeyError, ValueError):
                        continue
                    if timestamp < cutoff_date:
                        continue
                        
                    provider = row['provider']
                    count = int(row['count'])
                    day = timestamp.date().isoformat()
                    
                    # Update summaries
                    summary['by_provider'][provider]['total_calls'] += count
                    summary['by_day'][day][provider] += count
                    summary['total_calls'] += count
                    
            # Calculate estimated costs (placeholder - adjust based on actual pricing)
            cost_per_call = {
                'finnhub': 0.0,  # Free tier
                'alpha_vantage': 0.0,  # Free tier
                'newsapi': 0.0  # Free tier
            }
            
            for provider, data in summary['by_provider'].items():
                cost = data['total_calls'] * cost_per_call.get(provider, 0)
                data['total_cost'] = cost
                summary['estimated_cost'] += cost
                
        except Exception as e:
            logger.error(f"Failed to get usage summary: {e}")
            
        return dict(summary)
    
    def export_daily_summary(self, date: Optional[datetime] = None) -> Path:
        """Export daily usage summary to separate CSV"""
        if date is None:
            date = datetime.now()
            
        summary_file = self.config.system.logs_dir / f"quota_daily_{date.strftime('%Y%m%d')}.csv"
        
        try:
            # Get all usage for the date
            start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = date.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            daily_usage = defaultdict(lambda: {
                'calls': 0, 'success': 0, 'failed': 0, 
                'endpoints': defaultdict(int)
            })
            
            if not self.usage_log_file.exists():
                # No data to export
                return None
                
            with open(self.usage_log_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        timestamp = datetime.fromisoformat(row['timestamp'])
                    except (KeyError, ValueError):
                        continue
                    if start_of_day <= timestamp <= end_of_day:
                        provider = row['provider']
                        count = int(row['count'])
                        endpoint = row['endpoint']
                        success = row['success'] == 'True'
                        
                        daily_usage[provider]['calls'] += count
                        if success:
                            daily_usage[provider]['success'] += count
                        else:
                            daily_usage[provider]['failed'] += count
                        if endpoint:
                            daily_usage[provider]['endpoints'][endpoint] += count
                            
            # Write summary
            with open(summary_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Date', date.strftime('%Y-%m-%d')])
                writer.writerow([])
                writer.writerow(['Provider', 'Total Calls', 'Successful', 'Failed', 'Success Rate', 'Top Endpoints'])
                
                for provider, data in daily_usage.items():
                    success_rate = (data['success'] / data['calls'] * 100) if data['calls'] > 0 else 0
                    top_endpoints = sorted(data['endpoints'].items(), key=lambda x: x[1], reverse=True)[:3]
                    endpoints_str = ', '.join([f"{ep}({cnt})" for ep, cnt in top_endpoints])
                    
                    writer.writerow([
                        provider,
                        data['calls'],
                        data['success'],
                        data['failed'],
                        f"{success_rate:.1f}%",
                        endpoints_str
                    ])
                    
            logger.info(f"Exported daily summary to {summary_file}")
            return summary_file
            
        except Exception as e:
            logger.error(f"Failed to export daily summary: {e}")
            return None

# Global quota guard instance
_quota_guard: Optional[QuotaGuard] = None

def get_quota_guard() -> QuotaGuard:
    """Get or create the quota guard singleton"""
    global _quota_guard
    if _quota_guard is None:
        _quota_guard = QuotaGuard()
    return _quota_guard

# Decorator for rate limiting
def rate_limit(provider: str, count: int = 1, endpoint: str = ""):
    """
    Decorator to enforce rate limiting on functions
    
    Args:
        provider: API provider name
        count: Number of API calls this function makes
        endpoint: Optional endpoint identifier for logging
    """
    def decorator(func):
        # Use function name as endpoint if not provided
        endpoint_name = endpoint or func.__name__
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            guard = get_quota_guard()
            await guard.consume_quota(provider, count, endpoint_name)
            return await func(*args, **kwargs)
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            guard = get_quota_guard()
            # Run async consume_quota in sync context
            loop = asyncio.get_event_loop()
            loop.run_until_complete(guard.consume_quota(provider, count, endpoint_name))
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