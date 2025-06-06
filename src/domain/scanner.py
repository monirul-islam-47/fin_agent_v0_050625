"""Gap Scanner - Identify pre-market gaps and volatility patterns.

This module scans for stocks with significant pre-market gaps,
volume spikes, and other momentum indicators.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

from src.utils.logger import setup_logger
from src.config.settings import get_config
from src.data.market import MarketDataManager

logger = setup_logger(__name__)


class GapType(Enum):
    """Types of gaps detected."""
    BREAKAWAY = "breakaway"  # Gap with high volume
    RUNAWAY = "runaway"      # Continuation gap
    EXHAUSTION = "exhaustion" # Potential reversal
    COMMON = "common"        # Low significance


@dataclass
class GapResult:
    """Result from gap analysis."""
    symbol: str
    gap_percent: float
    gap_type: GapType
    current_price: float
    prev_close: float
    volume: int
    volume_ratio: float  # Current vol / avg vol
    atr: Optional[float] = None
    news_count: int = 0
    short_interest: Optional[float] = None
    options_activity: Optional[str] = None  # "calls", "puts", "mixed"
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class GapScanner:
    """Scans for pre-market gaps and momentum patterns."""
    
    def __init__(self, market_data: MarketDataManager):
        """Initialize Gap Scanner.
        
        Args:
            market_data: Market data manager for quotes and history
        """
        self.market_data = market_data
        self.config = get_config()
        
        # Gap thresholds from PRD
        self.min_gap_percent = 4.0  # Minimum 4% gap
        self.max_gap_percent = 20.0  # Filter out extreme gaps (likely news/halt)
        self.min_volume_ratio = 1.5  # 150% of average volume
        
        # ATR multipliers for volatility
        self.atr_period = 14
        self.high_volatility_atr = 2.0  # 2x ATR = high volatility
        
        # Pre-market hours (US Eastern)
        self.premarket_start = time(4, 0)  # 4:00 AM ET
        self.premarket_end = time(9, 30)   # 9:30 AM ET
        
    async def scan_gaps(self, symbols: List[str]) -> List[GapResult]:
        """Scan symbols for significant gaps.
        
        Args:
            symbols: List of symbols to scan
            
        Returns:
            List of gap results sorted by significance
        """
        logger.info(f"Scanning {len(symbols)} symbols for gaps...")
        
        # Process in batches for efficiency
        batch_size = 50
        all_results = []
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            
            # Analyze batch concurrently
            tasks = [self._analyze_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks)
            
            # Filter out None results
            valid_results = [r for r in results if r is not None]
            all_results.extend(valid_results)
            
            # Progress update
            if (i + batch_size) % 100 == 0:
                logger.info(f"Scanned {min(i + batch_size, len(symbols))}/{len(symbols)} symbols")
                
        # Sort by gap percentage (descending)
        all_results.sort(key=lambda x: abs(x.gap_percent), reverse=True)
        
        logger.info(f"Found {len(all_results)} symbols with significant gaps")
        return all_results
        
    def calculate_gap(self, current: float, prev_close: float) -> float:
        """Calculate gap percentage.
        
        Args:
            current: Current price
            prev_close: Previous close price
            
        Returns:
            Gap percentage (positive for gap up, negative for gap down)
        """
        if prev_close == 0:
            return 0.0
            
        return ((current - prev_close) / prev_close) * 100
        
    async def _analyze_symbol(self, symbol: str) -> Optional[GapResult]:
        """Analyze a single symbol for gaps.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            GapResult if significant gap found, None otherwise
        """
        try:
            # Get current quote
            quote = await self.market_data.get_quote(symbol)
            if not quote:
                return None
                
            # Handle both dict and Quote object
            if hasattr(quote, 'price'):
                current_price = quote.price
                volume = quote.volume or 0
            else:
                current_price = quote.get('price', 0)
                volume = quote.get('volume', 0)
            
            # Get previous close
            history = await self.market_data.get_price_history(
                symbol, 
                interval='1d',
                period='5d'
            )
            
            if not history or len(history) < 2:
                return None
                
            # Previous close is from last trading day
            # Handle both dict and Bar object
            prev_bar = history[-2]
            if hasattr(prev_bar, 'close'):
                prev_close = prev_bar.close
            else:
                prev_close = prev_bar.get('close', 0)
            if prev_close == 0:
                return None
                
            # Calculate gap
            gap_percent = self.calculate_gap(current_price, prev_close)
            
            # Filter by gap size
            if abs(gap_percent) < self.min_gap_percent:
                return None
                
            if abs(gap_percent) > self.max_gap_percent:
                logger.debug(f"{symbol}: Gap {gap_percent:.1f}% exceeds maximum")
                return None
                
            # Calculate volume ratio
            avg_volume = self._calculate_avg_volume(history)
            volume_ratio = volume / avg_volume if avg_volume > 0 else 0
            
            # Filter by volume
            if volume_ratio < self.min_volume_ratio:
                logger.debug(f"{symbol}: Volume ratio {volume_ratio:.1f}x below minimum")
                return None
                
            # Calculate ATR for volatility
            atr = await self._calculate_atr(symbol)
            
            # Determine gap type
            gap_type = self._classify_gap(
                gap_percent, 
                volume_ratio,
                atr,
                current_price,
                prev_close
            )
            
            # Get additional indicators
            news_count = await self._get_news_count(symbol)
            short_interest = await self._get_short_interest(symbol)
            options_activity = await self._check_options_activity(symbol)
            
            return GapResult(
                symbol=symbol,
                gap_percent=gap_percent,
                gap_type=gap_type,
                current_price=current_price,
                prev_close=prev_close,
                volume=volume,
                volume_ratio=volume_ratio,
                atr=atr,
                news_count=news_count,
                short_interest=short_interest,
                options_activity=options_activity
            )
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
            
    def _calculate_avg_volume(self, history: List[Any]) -> float:
        """Calculate average volume from history.
        
        Args:
            history: Price history data (can be Bar objects or dicts)
            
        Returns:
            Average volume
        """
        if not history:
            return 0.0
            
        volumes = []
        for bar in history[:-1]:  # Exclude today
            if hasattr(bar, 'volume'):
                volumes.append(bar.volume)
            else:
                volumes.append(bar.get('volume', 0))
        return sum(volumes) / len(volumes) if volumes else 0.0
        
    async def _calculate_atr(self, symbol: str) -> Optional[float]:
        """Calculate Average True Range.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            ATR value or None
        """
        try:
            # Get more history for ATR calculation
            history = await self.market_data.get_price_history(
                symbol,
                interval='1d',
                period='1mo'
            )
            
            if not history or len(history) < self.atr_period:
                return None
                
            # Calculate True Range for each day
            true_ranges = []
            for i in range(1, len(history)):
                high = history[i].get('high', 0)
                low = history[i].get('low', 0)
                prev_close = history[i-1].get('close', 0)
                
                # TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
                
            # ATR is average of last N true ranges
            if len(true_ranges) >= self.atr_period:
                atr = sum(true_ranges[-self.atr_period:]) / self.atr_period
                return atr
                
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            
        return None
        
    def _classify_gap(
        self,
        gap_percent: float,
        volume_ratio: float,
        atr: Optional[float],
        current: float,
        prev_close: float
    ) -> GapType:
        """Classify the type of gap.
        
        Args:
            gap_percent: Gap percentage
            volume_ratio: Volume relative to average
            atr: Average True Range
            current: Current price
            prev_close: Previous close
            
        Returns:
            Gap classification
        """
        # Breakaway gap: High volume, often >5% gap
        if volume_ratio > 3.0 and abs(gap_percent) > 5.0:
            return GapType.BREAKAWAY
            
        # Exhaustion gap: Extreme gap that might reverse
        if abs(gap_percent) > 10.0:
            return GapType.EXHAUSTION
            
        # Runaway gap: Moderate gap in trend direction
        if volume_ratio > 2.0 and 4.0 <= abs(gap_percent) <= 8.0:
            return GapType.RUNAWAY
            
        # Common gap: Default
        return GapType.COMMON
        
    async def _get_news_count(self, symbol: str) -> int:
        """Get count of recent news for symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Number of news items in last 24 hours
        """
        # This would integrate with news manager
        # For now, return placeholder
        return 0
        
    async def _get_short_interest(self, symbol: str) -> Optional[float]:
        """Get short interest for symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Short interest percentage or None
        """
        # This would query short interest data
        # Not available in free tier
        return None
        
    async def _check_options_activity(self, symbol: str) -> Optional[str]:
        """Check unusual options activity.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            "calls", "puts", "mixed", or None
        """
        # This would analyze options flow
        # Not available in free tier
        return None
        
    def filter_by_volatility(
        self,
        results: List[GapResult],
        min_atr_ratio: float = 1.5
    ) -> List[GapResult]:
        """Filter results by volatility criteria.
        
        Args:
            results: Gap scan results
            min_atr_ratio: Minimum ATR ratio for high volatility
            
        Returns:
            Filtered results
        """
        filtered = []
        
        for result in results:
            if result.atr is None:
                # Include if no ATR data
                filtered.append(result)
                continue
                
            # Check if gap exceeds ATR threshold
            gap_size = abs(result.current_price - result.prev_close)
            if gap_size >= (result.atr * min_atr_ratio):
                filtered.append(result)
                
        return filtered