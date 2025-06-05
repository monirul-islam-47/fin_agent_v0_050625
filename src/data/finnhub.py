"""
Finnhub WebSocket adapter for real-time market data
Primary data source with 60 calls/minute limit
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any
import websockets
from websockets.exceptions import WebSocketException

from ..config import get_config
from ..utils import get_logger, get_quota_guard
from .base import MarketDataAdapter, DataProvider, Quote

logger = get_logger(__name__)

class FinnhubWebSocket(MarketDataAdapter):
    """
    Finnhub WebSocket client for real-time quotes
    Uses WebSocket for efficient real-time data streaming
    """
    
    def __init__(self):
        super().__init__(DataProvider.FINNHUB)
        self.config = get_config()
        self.quota_guard = get_quota_guard()
        self.ws_url = f"wss://ws.finnhub.io?token={self.config.api.finnhub_key}"
        self.websocket = None
        self.subscribed_symbols = set()
        self.quote_callbacks: List[Callable[[Quote], None]] = []
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        
    async def connect(self):
        """Establish WebSocket connection to Finnhub"""
        try:
            logger.info("Connecting to Finnhub WebSocket...")
            self.websocket = await websockets.connect(self.ws_url)
            self.is_connected = True
            self._reconnect_delay = 5  # Reset delay on successful connection
            logger.info("Successfully connected to Finnhub WebSocket")
            
            # Re-subscribe to symbols if reconnecting
            if self.subscribed_symbols:
                await self._resubscribe_all()
                
        except Exception as e:
            logger.error(f"Failed to connect to Finnhub WebSocket: {e}")
            self.is_connected = False
            raise
            
    async def disconnect(self):
        """Close WebSocket connection"""
        if self.websocket:
            logger.info("Disconnecting from Finnhub WebSocket...")
            await self.websocket.close()
            self.websocket = None
            self.is_connected = False
            
    async def health_check(self) -> bool:
        """Check if WebSocket connection is healthy"""
        if not self.websocket:
            return False
            
        try:
            # Send ping to check connection
            pong_waiter = await self.websocket.ping()
            await asyncio.wait_for(pong_waiter, timeout=5)
            return True
        except (asyncio.TimeoutError, WebSocketException):
            logger.warning("Finnhub WebSocket health check failed")
            return False
            
    async def subscribe(self, symbols: List[str]):
        """Subscribe to real-time quotes for symbols"""
        if not self.websocket or not self.is_connected:
            await self.connect()
            
        for symbol in symbols:
            if symbol not in self.subscribed_symbols:
                # Track API usage
                if not self.quota_guard.check_and_update('finnhub', 1):
                    logger.warning(f"Finnhub quota exceeded, cannot subscribe to {symbol}")
                    continue
                    
                msg = {"type": "subscribe", "symbol": symbol}
                await self.websocket.send(json.dumps(msg))
                self.subscribed_symbols.add(symbol)
                logger.debug(f"Subscribed to {symbol}")
                
    async def unsubscribe(self, symbols: List[str]):
        """Unsubscribe from real-time quotes"""
        if not self.websocket or not self.is_connected:
            return
            
        for symbol in symbols:
            if symbol in self.subscribed_symbols:
                msg = {"type": "unsubscribe", "symbol": symbol}
                await self.websocket.send(json.dumps(msg))
                self.subscribed_symbols.remove(symbol)
                logger.debug(f"Unsubscribed from {symbol}")
                
    async def _resubscribe_all(self):
        """Re-subscribe to all symbols after reconnection"""
        logger.info(f"Re-subscribing to {len(self.subscribed_symbols)} symbols")
        symbols = list(self.subscribed_symbols)
        self.subscribed_symbols.clear()
        await self.subscribe(symbols)
        
    def add_quote_callback(self, callback: Callable[[Quote], None]):
        """Add callback for quote updates"""
        self.quote_callbacks.append(callback)
        
    async def listen(self):
        """
        Listen for incoming WebSocket messages
        Handles automatic reconnection on failure
        """
        while True:
            try:
                if not self.websocket or not self.is_connected:
                    await self.connect()
                    
                async for message in self.websocket:
                    data = json.loads(message)
                    
                    if data.get('type') == 'trade':
                        # Process trade data into quotes
                        for trade in data.get('data', []):
                            quote = self._parse_trade(trade)
                            if quote:
                                # Notify all callbacks
                                for callback in self.quote_callbacks:
                                    try:
                                        callback(quote)
                                    except Exception as e:
                                        logger.error(f"Error in quote callback: {e}")
                                        
            except WebSocketException as e:
                logger.warning(f"WebSocket error: {e}, reconnecting in {self._reconnect_delay}s...")
                self.is_connected = False
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
                
            except Exception as e:
                logger.error(f"Unexpected error in WebSocket listener: {e}")
                await asyncio.sleep(self._reconnect_delay)
                
    def _parse_trade(self, trade: Dict[str, Any]) -> Optional[Quote]:
        """Parse Finnhub trade data into Quote object"""
        try:
            return Quote(
                symbol=trade['s'],
                timestamp=datetime.fromtimestamp(trade['t'] / 1000),  # Convert ms to seconds
                price=trade['p'],
                volume=trade.get('v'),
                provider=self.provider.value,
                is_delayed=False
            )
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse trade data: {e}")
            return None
            
    # MarketDataAdapter interface implementations
    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """
        Get latest quote for symbol
        Note: WebSocket is push-based, this requires maintaining latest quotes
        """
        logger.warning("get_quote not implemented for WebSocket adapter, use callbacks instead")
        return None
        
    async def get_quotes(self, symbols: List[str]) -> Dict[str, Optional[Quote]]:
        """
        Get latest quotes for multiple symbols
        Note: WebSocket is push-based, this requires maintaining latest quotes
        """
        logger.warning("get_quotes not implemented for WebSocket adapter, use callbacks instead")
        return {symbol: None for symbol in symbols}
        
    async def get_bars(
        self, 
        symbol: str, 
        start: datetime, 
        end: datetime,
        interval: str = "1min"
    ) -> List:
        """
        WebSocket doesn't provide historical data
        Use REST adapter for historical bars
        """
        logger.warning("Historical bars not available via WebSocket")
        return []