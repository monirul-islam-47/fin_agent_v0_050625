"""
Cache management for market data and API responses
Provides JSON-based caching with TTL support
"""

import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Union
import hashlib
import asyncio
from dataclasses import dataclass, asdict

from ..config import get_config
from ..utils import get_logger

logger = get_logger(__name__)

@dataclass
class CacheEntry:
    """Represents a cached data entry"""
    key: str
    data: Any
    timestamp: float
    ttl_seconds: int
    provider: str
    
    @property
    def is_expired(self) -> bool:
        """Check if cache entry has expired"""
        age = time.time() - self.timestamp
        return age > self.ttl_seconds
    
    @property
    def age_seconds(self) -> float:
        """Get age of cache entry in seconds"""
        return time.time() - self.timestamp
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'key': self.key,
            'data': self.data,
            'timestamp': self.timestamp,
            'ttl_seconds': self.ttl_seconds,
            'provider': self.provider
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CacheEntry':
        """Create from dictionary"""
        return cls(**data)

class CacheStore:
    """
    JSON-based cache store with TTL support
    Organizes cache by date and provider for easy inspection
    """
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.config = get_config()
        self.cache_dir = cache_dir or self.config.system.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._memory_cache: Dict[str, CacheEntry] = {}
        
        # Default TTL from config
        self.default_ttl = self.config.system.cache_ttl_minutes * 60
        
        logger.info(f"Initialized cache store at {self.cache_dir}")
    
    def _get_cache_path(self, provider: str, date: Optional[datetime] = None) -> Path:
        """Get cache file path for provider and date"""
        if date is None:
            date = datetime.now()
        
        date_str = date.strftime("%Y-%m-%d")
        cache_file = self.cache_dir / date_str / f"{provider}.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        
        return cache_file
    
    def _generate_key(self, provider: str, params: Dict[str, Any]) -> str:
        """Generate a unique cache key from provider and parameters"""
        # Sort params for consistent hashing
        param_str = json.dumps(params, sort_keys=True)
        raw_key = f"{provider}:{param_str}"
        
        # Use first 16 chars of hash for readability
        hash_key = hashlib.md5(raw_key.encode()).hexdigest()[:16]
        
        # Include provider name for clarity
        return f"{provider}_{hash_key}"
    
    async def get(
        self, 
        provider: str, 
        params: Dict[str, Any],
        date: Optional[datetime] = None
    ) -> Optional[Any]:
        """
        Retrieve data from cache
        
        Args:
            provider: API provider name
            params: Parameters used for the API call
            date: Date to retrieve from (default: today)
            
        Returns:
            Cached data if found and not expired, None otherwise
        """
        key = self._generate_key(provider, params)
        
        # Check memory cache first
        if key in self._memory_cache:
            entry = self._memory_cache[key]
            if not entry.is_expired:
                logger.debug(f"Memory cache hit for {key} (age: {entry.age_seconds:.1f}s)")
                return entry.data
            else:
                # Remove expired entry
                del self._memory_cache[key]
        
        # Check disk cache
        cache_file = self._get_cache_path(provider, date)
        
        if cache_file.exists():
            try:
                async with self._lock:
                    with open(cache_file, 'r') as f:
                        cache_data = json.load(f)
                
                # Look for our key
                if key in cache_data:
                    entry = CacheEntry.from_dict(cache_data[key])
                    
                    if not entry.is_expired:
                        # Load into memory cache
                        self._memory_cache[key] = entry
                        logger.debug(f"Disk cache hit for {key} (age: {entry.age_seconds:.1f}s)")
                        return entry.data
                    else:
                        logger.debug(f"Cache expired for {key}")
            
            except Exception as e:
                logger.error(f"Failed to read cache file {cache_file}: {e}")
        
        logger.debug(f"Cache miss for {key}")
        return None
    
    async def set(
        self,
        provider: str,
        params: Dict[str, Any],
        data: Any,
        ttl_seconds: Optional[int] = None,
        date: Optional[datetime] = None
    ):
        """
        Store data in cache
        
        Args:
            provider: API provider name
            params: Parameters used for the API call
            data: Data to cache
            ttl_seconds: Time to live in seconds (default: from config)
            date: Date to store under (default: today)
        """
        key = self._generate_key(provider, params)
        ttl = ttl_seconds or self.default_ttl
        
        entry = CacheEntry(
            key=key,
            data=data,
            timestamp=time.time(),
            ttl_seconds=ttl,
            provider=provider
        )
        
        # Store in memory cache
        self._memory_cache[key] = entry
        
        # Store on disk
        cache_file = self._get_cache_path(provider, date)
        
        async with self._lock:
            try:
                # Read existing cache
                cache_data = {}
                if cache_file.exists():
                    with open(cache_file, 'r') as f:
                        cache_data = json.load(f)
                
                # Add our entry
                cache_data[key] = entry.to_dict()
                
                # Write back
                with open(cache_file, 'w') as f:
                    json.dump(cache_data, f, indent=2)
                
                logger.debug(f"Cached {key} with TTL {ttl}s")
            
            except Exception as e:
                logger.error(f"Failed to write cache file {cache_file}: {e}")
    
    async def clear(self, provider: Optional[str] = None, date: Optional[datetime] = None):
        """
        Clear cache entries
        
        Args:
            provider: Clear only this provider's cache (None = all)
            date: Clear only this date's cache (None = all)
        """
        # Clear memory cache
        if provider:
            keys_to_remove = [k for k in self._memory_cache if k.startswith(provider)]
            for key in keys_to_remove:
                del self._memory_cache[key]
        else:
            self._memory_cache.clear()
        
        # Clear disk cache
        if date:
            if provider:
                cache_file = self._get_cache_path(provider, date)
                if cache_file.exists():
                    cache_file.unlink()
                    logger.info(f"Cleared cache for {provider} on {date.strftime('%Y-%m-%d')}")
            else:
                # Clear all providers for date
                date_dir = self.cache_dir / date.strftime("%Y-%m-%d")
                if date_dir.exists():
                    for cache_file in date_dir.glob("*.json"):
                        cache_file.unlink()
                    logger.info(f"Cleared all cache for {date.strftime('%Y-%m-%d')}")
        else:
            # Clear everything
            for cache_file in self.cache_dir.rglob("*.json"):
                cache_file.unlink()
            logger.info("Cleared all cache")
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_entries = 0
        total_size = 0
        expired_entries = 0
        providers = set()
        
        # Scan all cache files
        for cache_file in self.cache_dir.rglob("*.json"):
            try:
                with open(cache_file, 'r') as f:
                    cache_data = json.load(f)
                
                for key, entry_data in cache_data.items():
                    total_entries += 1
                    entry = CacheEntry.from_dict(entry_data)
                    providers.add(entry.provider)
                    
                    if entry.is_expired:
                        expired_entries += 1
                
                total_size += cache_file.stat().st_size
            
            except Exception as e:
                logger.warning(f"Failed to read cache file {cache_file}: {e}")
        
        return {
            'total_entries': total_entries,
            'expired_entries': expired_entries,
            'active_entries': total_entries - expired_entries,
            'memory_entries': len(self._memory_cache),
            'total_size_mb': round(total_size / 1024 / 1024, 2),
            'providers': list(providers),
            'cache_dir': str(self.cache_dir)
        }
    
    async def cleanup_expired(self) -> int:
        """Remove all expired cache entries"""
        removed_count = 0
        
        for cache_file in self.cache_dir.rglob("*.json"):
            try:
                async with self._lock:
                    with open(cache_file, 'r') as f:
                        cache_data = json.load(f)
                    
                    # Filter out expired entries
                    active_data = {}
                    for key, entry_data in cache_data.items():
                        entry = CacheEntry.from_dict(entry_data)
                        if not entry.is_expired:
                            active_data[key] = entry_data
                        else:
                            removed_count += 1
                    
                    # Write back if changed
                    if len(active_data) < len(cache_data):
                        if active_data:
                            with open(cache_file, 'w') as f:
                                json.dump(active_data, f, indent=2)
                        else:
                            # Remove empty file
                            cache_file.unlink()
            
            except Exception as e:
                logger.error(f"Failed to cleanup cache file {cache_file}: {e}")
        
        # Clean memory cache
        memory_keys = list(self._memory_cache.keys())
        for key in memory_keys:
            if self._memory_cache[key].is_expired:
                del self._memory_cache[key]
                removed_count += 1
        
        logger.info(f"Cleaned up {removed_count} expired cache entries")
        return removed_count

# Global cache instance
_cache_store: Optional[CacheStore] = None

def get_cache_store() -> CacheStore:
    """Get or create the cache store singleton"""
    global _cache_store
    if _cache_store is None:
        _cache_store = CacheStore()
    return _cache_store