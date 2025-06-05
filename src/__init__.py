"""
One-Day Trading Agent (ODTA)
A free-tier trading assistant for intraday opportunities
"""

__version__ = "0.1.0"
__author__ = "ODTA Team"

from . import config, data, domain, orchestration, persistence, utils

__all__ = ["config", "data", "domain", "orchestration", "persistence", "utils"]