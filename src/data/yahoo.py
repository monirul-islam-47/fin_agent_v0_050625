"""
Yahoo Finance adapter for market data
Last resort fallback with 15-minute delayed data
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

from ..config import get_config
from ..utils import get_logger
from .base import MarketDataAdapter, DataProvider, Quote, Bar

logger = get_logger(__name__)

class YahooFinanceAdapter(MarketDataAdapter):
    """
    Yahoo Finance adapter using yfinance library
    Provides delayed data (15 minutes) as last resort fallback
    No API limits but data is delayed
    """
    
    def __init__(self):
        super().__init__(DataProvider.YAHOO)
        self.config = get_config()
        # YFinance is synchronous, so we use thread pool for async compatibility
        self.executor = ThreadPoolExecutor(max_workers=5)
        self._delay_minutes = 15
        
    async def connect(self):
        """No connection needed for yfinance"""
        self.is_connected = True
        logger.info("Yahoo Finance adapter ready (15-min delayed data)")
        
    async def disconnect(self):
        """Cleanup thread pool"""
        self.executor.shutdown(wait=False)
        self.is_connected = False
        
    async def health_check(self) -> bool:
        """Check if yfinance is working"""
        try:
            # Try to fetch a known symbol
            loop = asyncio.get_event_loop()
            ticker = await loop.run_in_executor(self.executor, yf.Ticker, "AAPL")
            info = await loop.run_in_executor(self.executor, lambda: ticker.info)
            return 'symbol' in info
        except Exception as e:
            logger.error(f"Yahoo Finance health check failed: {e}")
            return False
            
    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """Get current quote (15-min delayed)"""
        try:
            loop = asyncio.get_event_loop()
            
            # Create ticker object
            ticker = await loop.run_in_executor(self.executor, yf.Ticker, symbol)
            
            # Get current info
            info = await loop.run_in_executor(self.executor, lambda: ticker.info)
            
            if not info or 'regularMarketPrice' not in info:
                logger.warning(f"No quote data available for {symbol}")
                return None
                
            # YFinance provides delayed data during market hours
            return Quote(
                symbol=symbol,
                timestamp=datetime.now() - timedelta(minutes=self._delay_minutes),
                price=info.get('regularMarketPrice', info.get('currentPrice', 0)),
                bid=info.get('bid'),
                ask=info.get('ask'),
                volume=info.get('regularMarketVolume'),
                provider=self.provider.value,
                is_delayed=True  # Always delayed
            )
            
        except Exception as e:
            logger.error(f"Failed to get Yahoo quote for {symbol}: {e}")
            return None
            
    async def get_quotes(self, symbols: List[str]) -> Dict[str, Optional[Quote]]:
        """Get quotes for multiple symbols"""
        if not symbols:
            return {}
            
        try:
            loop = asyncio.get_event_loop()
            
            # YFinance supports batch download
            symbols_str = " ".join(symbols)
            tickers = await loop.run_in_executor(self.executor, yf.Tickers, symbols_str)
            
            results = {}
            
            # Get info for each ticker
            for symbol in symbols:
                try:
                    ticker = tickers.tickers.get(symbol.upper())
                    if not ticker:
                        results[symbol] = None
                        continue
                        
                    info = await loop.run_in_executor(self.executor, lambda t=ticker: t.info)
                    
                    if info and 'regularMarketPrice' in info:
                        results[symbol] = Quote(
                            symbol=symbol,
                            timestamp=datetime.now() - timedelta(minutes=self._delay_minutes),
                            price=info.get('regularMarketPrice', info.get('currentPrice', 0)),
                            bid=info.get('bid'),
                            ask=info.get('ask'),
                            volume=info.get('regularMarketVolume'),
                            provider=self.provider.value,
                            is_delayed=True
                        )
                    else:
                        results[symbol] = None
                        
                except Exception as e:
                    logger.error(f"Failed to get quote for {symbol}: {e}")
                    results[symbol] = None
                    
            return results
            
        except Exception as e:
            logger.error(f"Failed to get batch quotes: {e}")
            return {symbol: None for symbol in symbols}
            
    async def get_bars(
        self, 
        symbol: str, 
        start: datetime, 
        end: datetime,
        interval: str = "1m"
    ) -> List[Bar]:
        """Get historical bars"""
        try:
            loop = asyncio.get_event_loop()
            
            # Create ticker
            ticker = await loop.run_in_executor(self.executor, yf.Ticker, symbol)
            
            # Map our interval to yfinance interval
            interval_map = {
                "1min": "1m",
                "5min": "5m", 
                "15min": "15m",
                "30min": "30m",
                "60min": "60m",
                "1h": "60m",
                "1d": "1d"
            }
            yf_interval = interval_map.get(interval, "1m")
            
            # Download historical data
            # YFinance expects timezone-naive datetimes
            start_naive = start.replace(tzinfo=None)
            end_naive = end.replace(tzinfo=None)
            
            df = await loop.run_in_executor(
                self.executor,
                ticker.history,
                start=start_naive,
                end=end_naive,
                interval=yf_interval
            )
            
            if df.empty:
                logger.warning(f"No historical data available for {symbol}")
                return []
                
            # Convert DataFrame to Bar objects
            bars = []
            for idx, row in df.iterrows():
                # idx is a Timestamp object
                timestamp = idx.to_pydatetime()
                
                bars.append(Bar(
                    symbol=symbol,
                    timestamp=timestamp,
                    open=row['Open'],
                    high=row['High'],
                    low=row['Low'],
                    close=row['Close'],
                    volume=int(row['Volume']),
                    provider=self.provider.value
                ))
                
            return bars
            
        except Exception as e:
            logger.error(f"Failed to get bars for {symbol}: {e}")
            return []
            
    def __del__(self):
        """Cleanup on deletion"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)