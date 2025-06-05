"""Domain layer - Core business logic for ODTA.

This package contains the core trading logic including:
- Universe management
- Gap scanning
- Factor scoring
- Trade planning
- Risk management
"""

from src.domain.universe import UniverseManager
from src.domain.scanner import GapScanner, GapResult, GapType
from src.domain.scoring import (
    FactorModel, 
    ScoredCandidate, 
    FactorWeights,
    FactorType
)
from src.domain.planner import (
    TradePlanner,
    TradePlan,
    EntryStrategy,
    ExitStrategy
)
from src.domain.risk import (
    RiskManager,
    RiskDecision,
    RiskStatus,
    RiskMetrics
)

__all__ = [
    # Universe
    'UniverseManager',
    
    # Scanner
    'GapScanner',
    'GapResult', 
    'GapType',
    
    # Scoring
    'FactorModel',
    'ScoredCandidate',
    'FactorWeights',
    'FactorType',
    
    # Planner
    'TradePlanner',
    'TradePlan',
    'EntryStrategy',
    'ExitStrategy',
    
    # Risk
    'RiskManager',
    'RiskDecision',
    'RiskStatus',
    'RiskMetrics'
]