"""
Logging configuration for ODTA
Provides structured logging with color support and rotation
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
import colorama
from colorama import Fore, Style

# Initialize colorama for Windows support
colorama.init()

# Custom log colors
LOG_COLORS = {
    'DEBUG': Fore.CYAN,
    'INFO': Fore.GREEN,
    'WARNING': Fore.YELLOW,
    'ERROR': Fore.RED,
    'CRITICAL': Fore.MAGENTA
}

class ColoredFormatter(logging.Formatter):
    """Custom formatter with color support"""
    
    def __init__(self, fmt=None, datefmt=None, use_colors=True):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors and sys.stderr.isatty()
    
    def format(self, record):
        if self.use_colors:
            levelname = record.levelname
            if levelname in LOG_COLORS:
                record.levelname = f"{LOG_COLORS[levelname]}{levelname}{Style.RESET_ALL}"
                record.name = f"{Fore.BLUE}{record.name}{Style.RESET_ALL}"
        
        # Add custom fields
        record.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        return super().format(record)

class StructuredLogger:
    """Wrapper for structured logging with context"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.context = {}
    
    def add_context(self, **kwargs):
        """Add persistent context to all log messages"""
        self.context.update(kwargs)
    
    def clear_context(self):
        """Clear all context"""
        self.context = {}
    
    def _log(self, level, msg, *args, **kwargs):
        """Internal log method with context injection"""
        extra = kwargs.get('extra', {})
        extra.update(self.context)
        kwargs['extra'] = extra
        getattr(self.logger, level)(msg, *args, **kwargs)
    
    def debug(self, msg, *args, **kwargs):
        self._log('debug', msg, *args, **kwargs)
    
    def info(self, msg, *args, **kwargs):
        self._log('info', msg, *args, **kwargs)
    
    def warning(self, msg, *args, **kwargs):
        self._log('warning', msg, *args, **kwargs)
    
    def error(self, msg, *args, **kwargs):
        self._log('error', msg, *args, **kwargs)
    
    def critical(self, msg, *args, **kwargs):
        self._log('critical', msg, *args, **kwargs)

def setup_logger(
    name: str,
    level: Optional[str] = None,
    log_file: Optional[Path] = None,
    use_colors: bool = True
) -> StructuredLogger:
    """
    Set up a logger with console and optional file output
    
    Args:
        name: Logger name (usually __name__)
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for logging
        use_colors: Whether to use colored output
    
    Returns:
        StructuredLogger instance
    """
    from ..config.settings import get_config
    config = get_config()
    
    # Use provided level or fall back to config
    if level is None:
        level = config.system.log_level
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    logger.handlers = []
    
    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stderr)
    console_format = "%(timestamp)s [%(levelname)s] %(name)s: %(message)s"
    console_formatter = ColoredFormatter(console_format, use_colors=use_colors)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        file_formatter = logging.Formatter(file_format)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return StructuredLogger(logger)

def get_logger(name: str) -> StructuredLogger:
    """
    Get or create a logger instance
    
    Args:
        name: Logger name (usually __name__)
    
    Returns:
        StructuredLogger instance
    """
    return setup_logger(name)

# Performance logging decorator
def log_performance(logger: Optional[StructuredLogger] = None):
    """Decorator to log function performance"""
    import functools
    import time
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal logger
            if logger is None:
                logger = get_logger(func.__module__)
            
            start_time = time.time()
            logger.debug(f"Starting {func.__name__}")
            
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                logger.info(
                    f"Completed {func.__name__}",
                    extra={'duration_ms': int(elapsed * 1000)}
                )
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(
                    f"Failed {func.__name__}: {str(e)}",
                    extra={'duration_ms': int(elapsed * 1000)},
                    exc_info=True
                )
                raise
        
        return wrapper
    return decorator

# Async performance logging decorator
def log_async_performance(logger: Optional[StructuredLogger] = None):
    """Decorator to log async function performance"""
    import functools
    import time
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal logger
            if logger is None:
                logger = get_logger(func.__module__)
            
            start_time = time.time()
            logger.debug(f"Starting async {func.__name__}")
            
            try:
                result = await func(*args, **kwargs)
                elapsed = time.time() - start_time
                logger.info(
                    f"Completed async {func.__name__}",
                    extra={'duration_ms': int(elapsed * 1000)}
                )
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(
                    f"Failed async {func.__name__}: {str(e)}",
                    extra={'duration_ms': int(elapsed * 1000)},
                    exc_info=True
                )
                raise
        
        return wrapper
    return decorator