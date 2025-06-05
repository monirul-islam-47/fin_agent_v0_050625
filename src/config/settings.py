"""
Configuration management for ODTA
Loads environment variables and provides centralized settings
"""

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Dict, Optional
from dotenv import load_dotenv
import logging

# Load environment variables
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

@dataclass
class APIConfig:
    """API configuration and keys"""
    finnhub_key: str
    alpha_vantage_key: str
    news_api_key: str
    
    # Quota limits
    finnhub_calls_per_minute: int = 60
    alpha_vantage_daily_calls: int = 25
    news_api_daily_calls: int = 1000

@dataclass
class TradingConfig:
    """Trading parameters and risk limits"""
    daily_loss_cap_eur: float = 33.0
    max_position_eur: float = 250.0
    min_gap_pct: float = 4.0
    target_profit_pct: float = 9.0
    stop_loss_pct: float = 3.0
    
    # Factor weights (will be overridden by UI)
    volatility_weight: float = 0.4
    catalyst_weight: float = 0.3
    sentiment_weight: float = 0.1
    liquidity_weight: float = 0.2

@dataclass 
class TimingConfig:
    """Market timing configuration (CET)"""
    scan_time: str = "14:00"
    second_look_time: str = "18:15"
    market_open_time: str = "15:30"
    market_close_time: str = "22:00"
    
    def get_scan_time(self) -> time:
        """Convert scan time string to time object"""
        h, m = map(int, self.scan_time.split(':'))
        return time(h, m)
    
    def get_second_look_time(self) -> time:
        """Convert second look time string to time object"""
        h, m = map(int, self.second_look_time.split(':'))
        return time(h, m)

@dataclass
class SystemConfig:
    """System-level configuration"""
    log_level: str = "INFO"
    cache_ttl_minutes: int = 15
    enable_paper_trading: bool = True
    timezone: str = "Europe/Berlin"
    
    # Paths
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent)
    data_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    universe_dir: Path = field(init=False)
    
    def __post_init__(self):
        self.data_dir = self.project_root / "data"
        self.cache_dir = self.data_dir / "cache"
        self.logs_dir = self.project_root / "logs"
        self.universe_dir = self.data_dir / "universe"
        
        # Ensure directories exist
        for dir_path in [self.data_dir, self.cache_dir, self.logs_dir, self.universe_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

@dataclass
class UIConfig:
    """UI/Dashboard configuration"""
    streamlit_theme: str = "dark"
    auto_refresh_seconds: int = 30
    show_debug_info: bool = False
    max_chart_points: int = 200  # Limit for performance

@dataclass
class Config:
    """Main configuration container"""
    api: APIConfig
    trading: TradingConfig
    timing: TimingConfig
    system: SystemConfig
    ui: UIConfig
    
    # Runtime overrides
    _overrides: Dict[str, any] = field(default_factory=dict)
    
    def override(self, key: str, value: any):
        """Override a configuration value at runtime"""
        self._overrides[key] = value
    
    def get(self, key: str, default=None):
        """Get configuration value with override support"""
        if key in self._overrides:
            return self._overrides[key]
        
        # Navigate nested attributes
        parts = key.split('.')
        obj = self
        for part in parts:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                return default
        return obj

# Singleton instance
_config_instance: Optional[Config] = None

def get_config() -> Config:
    """Get or create the configuration singleton"""
    global _config_instance
    
    if _config_instance is None:
        # Load from environment
        api_config = APIConfig(
            finnhub_key=os.getenv("FINNHUB_API_KEY", ""),
            alpha_vantage_key=os.getenv("ALPHA_VANTAGE_API_KEY", ""),
            news_api_key=os.getenv("NEWS_API_KEY", ""),
            finnhub_calls_per_minute=int(os.getenv("FINNHUB_CALLS_PER_MINUTE", "60")),
            alpha_vantage_daily_calls=int(os.getenv("ALPHA_VANTAGE_DAILY_CALLS", "25")),
            news_api_daily_calls=int(os.getenv("NEWS_API_DAILY_CALLS", "1000"))
        )
        
        trading_config = TradingConfig(
            daily_loss_cap_eur=float(os.getenv("DAILY_LOSS_CAP_EUR", "33")),
            max_position_eur=float(os.getenv("MAX_POSITION_EUR", "250")),
            min_gap_pct=float(os.getenv("MIN_GAP_PCT", "4")),
            target_profit_pct=float(os.getenv("TARGET_PROFIT_PCT", "9")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "3"))
        )
        
        timing_config = TimingConfig(
            scan_time=os.getenv("SCAN_TIME", "14:00"),
            second_look_time=os.getenv("SECOND_LOOK_TIME", "18:15"),
            market_open_time=os.getenv("MARKET_OPEN_TIME", "15:30"),
            market_close_time=os.getenv("MARKET_CLOSE_TIME", "22:00")
        )
        
        system_config = SystemConfig(
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            cache_ttl_minutes=int(os.getenv("CACHE_TTL_MINUTES", "15")),
            enable_paper_trading=os.getenv("ENABLE_PAPER_TRADING", "true").lower() == "true",
            timezone=os.getenv("TIMEZONE", "Europe/Berlin")
        )
        
        ui_config = UIConfig(
            streamlit_theme=os.getenv("STREAMLIT_THEME", "dark"),
            auto_refresh_seconds=int(os.getenv("AUTO_REFRESH_SECONDS", "30")),
            show_debug_info=os.getenv("SHOW_DEBUG_INFO", "false").lower() == "true"
        )
        
        _config_instance = Config(
            api=api_config,
            trading=trading_config,
            timing=timing_config,
            system=system_config,
            ui=ui_config
        )
        
        # Validate critical settings
        if not api_config.finnhub_key:
            logging.warning("FINNHUB_API_KEY not set - real-time quotes will not work")
    
    return _config_instance

def reset_config():
    """Reset the configuration singleton (mainly for testing)"""
    global _config_instance
    _config_instance = None