"""Universe Manager - Load and validate tradable symbols.

This module manages the trading universe, loading symbols from CSV,
validating tradability, and filtering based on liquidity criteria.
"""

import asyncio
import csv
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timedelta

from src.utils.logger import setup_logger
from src.config.settings import get_config
from src.data.market import MarketDataManager
from src.data.cache_manager import CacheManager

logger = setup_logger(__name__)


class UniverseManager:
    """Manages the trading universe for ODTA."""
    
    def __init__(
        self,
        market_data: MarketDataManager,
        cache_manager: CacheManager,
        universe_file: Optional[Path] = None
    ):
        """Initialize Universe Manager.
        
        Args:
            market_data: Market data manager for price/volume validation
            cache_manager: Cache manager for storing universe data
            universe_file: Path to universe CSV file
        """
        self.market_data = market_data
        self.cache = cache_manager
        self.config = get_config()
        
        # Default universe file
        if universe_file is None:
            universe_file = Path(self.config.system.data_dir) / "universe" / "revolut_universe.csv"
        self.universe_file = universe_file
        
        # Universe constraints from PRD
        self.min_price = 2.0  # €2 minimum
        self.max_price = 300.0  # €300 maximum
        self.min_adv = 5_000_000  # €5M average daily volume
        self.min_market_cap = 100_000_000  # €100M market cap
        
        # Cache settings
        self.cache_key = "universe:active"
        self.cache_ttl = 86400  # 24 hours
        
        # Internal state
        self._universe: Set[str] = set()
        self._validated: Dict[str, Dict[str, Any]] = {}
        
    async def load_universe(self) -> List[str]:
        """Load universe from CSV file.
        
        Returns:
            List of symbol strings
            
        Raises:
            FileNotFoundError: If universe file doesn't exist
            ValueError: If CSV format is invalid
        """
        # Check cache first
        cached = await self.cache.store.get('universe', {'key': self.cache_key})
        if cached:
            logger.info(f"Loaded {len(cached)} symbols from cache")
            self._universe = set(cached)
            return cached
            
        # Check if file exists
        if not self.universe_file.exists():
            # Try template file
            template_file = self.universe_file.with_name("revolut_universe_template.csv")
            if template_file.exists():
                logger.warning(f"Universe file not found. Using template: {template_file}")
                self.universe_file = template_file
            else:
                raise FileNotFoundError(f"Universe file not found: {self.universe_file}")
        
        symbols = []
        try:
            with open(self.universe_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                # Validate CSV format
                if 'symbol' not in reader.fieldnames:
                    raise ValueError("CSV must have 'symbol' column")
                
                for row in reader:
                    symbol = row['symbol'].strip().upper()
                    if symbol and not symbol.startswith('#'):  # Skip comments
                        symbols.append(symbol)
                        
            logger.info(f"Loaded {len(symbols)} symbols from {self.universe_file}")
            self._universe = set(symbols)
            
            # Cache for 24 hours
            await self.cache.store.set('universe', {'key': self.cache_key}, symbols, self.cache_ttl)
            
            return symbols
            
        except Exception as e:
            logger.error(f"Error loading universe: {e}")
            raise
            
    async def validate_symbol(self, symbol: str) -> bool:
        """Validate if a symbol meets trading criteria.
        
        Args:
            symbol: Stock symbol to validate
            
        Returns:
            True if symbol meets all criteria
        """
        try:
            # Get current quote
            quote = await self.market_data.get_quote(symbol)
            if not quote:
                logger.debug(f"{symbol}: No quote data available")
                return False
                
            # Handle both dict and Quote object
            if hasattr(quote, 'price'):
                price = quote.price
                volume = quote.volume or 0
            else:
                price = quote.get('price', 0)
                volume = quote.get('volume', 0)
            
            # Price range check
            if price < self.min_price or price > self.max_price:
                logger.debug(f"{symbol}: Price ${price:.2f} outside range [${self.min_price}-${self.max_price}]")
                return False
                
            # Volume check (approximate ADV)
            adv = price * volume
            if adv < self.min_adv:
                logger.debug(f"{symbol}: ADV ${adv:,.0f} below minimum ${self.min_adv:,.0f}")
                return False
                
            # Market cap check (if available)
            market_cap = getattr(quote, 'market_cap', 0) if hasattr(quote, 'market_cap') else quote.get('market_cap', 0)
            if market_cap > 0 and market_cap < self.min_market_cap:
                logger.debug(f"{symbol}: Market cap ${market_cap:,.0f} below minimum")
                return False
                
            # PRIIPs compliance - basic check
            # In production, this would check regulatory database
            if not self._check_priips_compliance(symbol):
                logger.debug(f"{symbol}: Failed PRIIPs compliance check")
                return False
                
            # Store validation data
            self._validated[symbol] = {
                'price': price,
                'volume': volume,
                'adv': adv,
                'market_cap': market_cap,
                'validated_at': datetime.now()
            }
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating {symbol}: {e}")
            return False
            
    async def get_active_symbols(self) -> List[str]:
        """Get list of active, validated symbols.
        
        Returns:
            List of symbols that pass all validation criteria
        """
        # Load universe if not already loaded
        if not self._universe:
            await self.load_universe()
            
        # Check cache for validated symbols
        cache_key = f"universe:validated:{datetime.now().strftime('%Y%m%d')}"
        cached = await self.cache.store.get('universe', {'key': cache_key})
        if cached:
            logger.info(f"Using cached validated symbols: {len(cached)} active")
            return cached
            
        # Validate all symbols
        logger.info(f"Validating {len(self._universe)} symbols...")
        
        # Batch validate for efficiency
        batch_size = 50
        symbols = list(self._universe)
        active_symbols = []
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            
            # Validate batch concurrently
            tasks = [self.validate_symbol(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks)
            
            # Collect valid symbols
            for symbol, is_valid in zip(batch, results):
                if is_valid:
                    active_symbols.append(symbol)
                    
            # Progress update
            if (i + batch_size) % 100 == 0:
                logger.info(f"Validated {min(i + batch_size, len(symbols))}/{len(symbols)} symbols")
                
        logger.info(f"Found {len(active_symbols)} active symbols out of {len(symbols)}")
        
        # Cache for the day
        await self.cache.store.set('universe', {'key': cache_key}, active_symbols, self.cache_ttl)
        
        return active_symbols
        
    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get cached validation info for a symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with validation data or None
        """
        return self._validated.get(symbol)
        
    def _check_priips_compliance(self, symbol: str) -> bool:
        """Check if symbol is PRIIPs compliant for EU retail trading.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            True if compliant (simplified check)
        """
        # In production, this would check:
        # 1. KIID availability
        # 2. EU passport status
        # 3. Retail investor suitability
        
        # For now, assume major US stocks are compliant
        # Exclude certain sectors/types known to have issues
        excluded_prefixes = ['SPAC', 'BTC', 'GBTC', 'BITI']
        excluded_suffixes = ['.U', '.W']  # Units and warrants
        
        symbol_upper = symbol.upper()
        
        # Check exclusions
        for prefix in excluded_prefixes:
            if symbol_upper.startswith(prefix):
                return False
                
        for suffix in excluded_suffixes:
            if symbol_upper.endswith(suffix):
                return False
                
        return True
        
    async def refresh_universe(self) -> List[str]:
        """Force refresh of universe from file.
        
        Returns:
            Updated list of symbols
        """
        # Clear cache
        await self.cache.store.delete('universe', {'key': self.cache_key})
        cache_key = f"universe:validated:{datetime.now().strftime('%Y%m%d')}"
        await self.cache.store.delete('universe', {'key': cache_key})
        
        # Clear internal state
        self._universe.clear()
        self._validated.clear()
        
        # Reload
        return await self.load_universe()