"""Trade Planner - Calculate entry/exit points and position sizing.

This module creates detailed trade plans with entry points, stop losses,
profit targets, and position sizing based on risk parameters.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
import math

from src.utils.logger import setup_logger
from src.config.settings import get_config
from src.domain.scoring import ScoredCandidate

logger = setup_logger(__name__)


class EntryStrategy(Enum):
    """Entry strategy types."""
    VWAP = "vwap"          # Volume Weighted Average Price
    ORB = "orb"            # Opening Range Breakout
    PULLBACK = "pullback"  # Wait for pullback
    MARKET = "market"      # Market order at open


class ExitStrategy(Enum):
    """Exit strategy types."""
    FIXED_TARGET = "fixed_target"    # Fixed percentage target
    ATR_BASED = "atr_based"         # ATR multiple
    TRAILING = "trailing"            # Trailing stop
    TIME_BASED = "time_based"       # Exit by time


@dataclass
class TradePlan:
    """Detailed trade plan with entry/exit parameters."""
    symbol: str
    score: float
    direction: str  # "long" or "short"
    
    # Entry parameters
    entry_strategy: EntryStrategy
    entry_price: float
    
    # Exit parameters
    stop_loss: float
    stop_loss_percent: float
    target_price: float
    target_percent: float
    exit_strategy: ExitStrategy
    
    # Position sizing
    position_size_eur: float
    position_size_shares: int
    max_risk_eur: float
    
    # Risk metrics
    risk_reward_ratio: float
    
    # Optional fields with defaults
    entry_time: Optional[time] = None
    entry_conditions: List[str] = field(default_factory=list)
    win_probability: float = 0.60  # Default 60% from PRD
    kelly_fraction: float = 0.0
    
    # Additional parameters
    atr: Optional[float] = None
    trailing_stop_distance: Optional[float] = None
    time_exit: Optional[time] = None
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    notes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'symbol': self.symbol,
            'score': self.score,
            'direction': self.direction,
            'entry': {
                'strategy': self.entry_strategy.value,
                'price': self.entry_price,
                'time': self.entry_time.isoformat() if self.entry_time else None,
                'conditions': self.entry_conditions
            },
            'exit': {
                'stop_loss': self.stop_loss,
                'stop_loss_percent': self.stop_loss_percent,
                'target': self.target_price,
                'target_percent': self.target_percent,
                'strategy': self.exit_strategy.value
            },
            'position': {
                'size_eur': self.position_size_eur,
                'size_shares': self.position_size_shares,
                'max_risk_eur': self.max_risk_eur
            },
            'risk': {
                'risk_reward_ratio': self.risk_reward_ratio,
                'win_probability': self.win_probability,
                'kelly_fraction': self.kelly_fraction
            },
            'created_at': self.created_at.isoformat(),
            'notes': self.notes
        }


class TradePlanner:
    """Creates detailed trade plans for selected candidates."""
    
    def __init__(self):
        """Initialize Trade Planner."""
        # Default parameters from PRD
        self.default_stop_loss_percent = 3.0  # 3% stop loss
        self.default_target_percent = 9.0     # 9% profit target
        self.min_risk_reward = 2.0            # Minimum 2:1 R/R
        
        # Position sizing
        self.max_position_size_eur = 250.0    # €250 max per position
        self.max_risk_per_trade_eur = 33.0   # €33 max risk (daily limit)
        
        # ATR multipliers
        self.atr_stop_multiplier = 2.0        # 2x ATR for stop
        self.atr_target_multiplier = 4.0      # 4x ATR for target
        
        # Entry timing
        self.orb_period_minutes = 30          # First 30 minutes for ORB
        self.market_open = time(9, 30)        # US market open
        self.market_close = time(16, 0)       # US market close
        
    def create_plan(
        self,
        candidate: ScoredCandidate,
        entry_strategy: Optional[EntryStrategy] = None,
        custom_stop_percent: Optional[float] = None,
        custom_target_percent: Optional[float] = None
    ) -> TradePlan:
        """Create a trade plan for a scored candidate.
        
        Args:
            candidate: Scored candidate from factor model
            entry_strategy: Optional entry strategy override
            custom_stop_percent: Optional stop loss percentage
            custom_target_percent: Optional target percentage
            
        Returns:
            Complete trade plan
        """
        symbol = candidate.symbol
        current_price = candidate.gap_result.current_price
        gap_percent = candidate.gap_result.gap_percent
        atr = candidate.gap_result.atr
        
        # Determine trade direction
        direction = "long" if gap_percent > 0 else "short"
        
        # Select entry strategy
        if entry_strategy is None:
            entry_strategy = self._select_entry_strategy(candidate)
            
        # Calculate entry price
        entry_price, entry_conditions = self._calculate_entry(
            candidate, 
            entry_strategy
        )
        
        # Calculate stop loss
        stop_loss, stop_percent = self._calculate_stop_loss(
            entry_price,
            direction,
            atr,
            custom_stop_percent
        )
        
        # Calculate target
        target_price, target_percent = self._calculate_target(
            entry_price,
            direction,
            atr,
            stop_percent,
            custom_target_percent
        )
        
        # Calculate position size
        position_size_eur, position_size_shares, max_risk_eur = self._calculate_position_size(
            entry_price,
            stop_loss,
            direction
        )
        
        # Calculate risk metrics
        risk_reward = abs(target_price - entry_price) / abs(entry_price - stop_loss)
        win_prob = self._estimate_win_probability(candidate, risk_reward)
        kelly_fraction = self._calculate_kelly_fraction(win_prob, risk_reward)
        
        # Create plan
        plan = TradePlan(
            symbol=symbol,
            score=candidate.composite_score,
            direction=direction,
            entry_strategy=entry_strategy,
            entry_price=entry_price,
            entry_conditions=entry_conditions,
            stop_loss=stop_loss,
            stop_loss_percent=stop_percent,
            target_price=target_price,
            target_percent=target_percent,
            exit_strategy=ExitStrategy.FIXED_TARGET,
            position_size_eur=position_size_eur,
            position_size_shares=position_size_shares,
            max_risk_eur=max_risk_eur,
            risk_reward_ratio=risk_reward,
            win_probability=win_prob,
            kelly_fraction=kelly_fraction,
            atr=atr
        )
        
        # Add notes
        plan.notes.append(f"Gap: {gap_percent:+.1f}%")
        plan.notes.append(f"Volume: {candidate.gap_result.volume_ratio:.1f}x average")
        
        if risk_reward < self.min_risk_reward:
            plan.notes.append(f"Warning: R/R {risk_reward:.1f} below minimum {self.min_risk_reward}")
            
        logger.info(
            f"Created plan for {symbol}: Entry=${entry_price:.2f}, "
            f"Stop=${stop_loss:.2f} ({stop_percent:.1f}%), "
            f"Target=${target_price:.2f} ({target_percent:.1f}%), "
            f"Size={position_size_shares} shares (€{position_size_eur:.0f})"
        )
        
        return plan
        
    def _select_entry_strategy(self, candidate: ScoredCandidate) -> EntryStrategy:
        """Select appropriate entry strategy based on conditions.
        
        Args:
            candidate: Scored candidate
            
        Returns:
            Selected entry strategy
        """
        gap_percent = candidate.gap_result.gap_percent
        volume_ratio = candidate.gap_result.volume_ratio
        
        # Large gap with high volume: Wait for pullback
        if abs(gap_percent) > 6.0 and volume_ratio > 3.0:
            return EntryStrategy.PULLBACK
            
        # Moderate gap: Use VWAP
        elif 4.0 <= abs(gap_percent) <= 6.0:
            return EntryStrategy.VWAP
            
        # Strong momentum: ORB strategy
        elif volume_ratio > 2.5:
            return EntryStrategy.ORB
            
        # Default: Market order
        else:
            return EntryStrategy.MARKET
            
    def _calculate_entry(
        self,
        candidate: ScoredCandidate,
        strategy: EntryStrategy
    ) -> Tuple[float, List[str]]:
        """Calculate entry price based on strategy.
        
        Args:
            candidate: Scored candidate
            strategy: Entry strategy
            
        Returns:
            Tuple of (entry_price, conditions)
        """
        current_price = candidate.gap_result.current_price
        prev_close = candidate.gap_result.prev_close
        gap_percent = candidate.gap_result.gap_percent
        
        conditions = []
        
        if strategy == EntryStrategy.MARKET:
            # Enter at current price
            entry_price = current_price
            conditions.append("Market order at open")
            
        elif strategy == EntryStrategy.VWAP:
            # Enter near VWAP
            # Estimate VWAP as midpoint for now
            if gap_percent > 0:
                entry_price = current_price * 0.995  # 0.5% below current
                conditions.append("Limit order at/below VWAP")
            else:
                entry_price = current_price * 1.005  # 0.5% above current
                conditions.append("Limit order at/above VWAP")
                
        elif strategy == EntryStrategy.PULLBACK:
            # Wait for pullback to gap fill area
            if gap_percent > 0:
                # Long: Enter on pullback toward prev close
                entry_price = prev_close + (current_price - prev_close) * 0.5
                conditions.append("Wait for 50% gap fill")
            else:
                # Short: Enter on bounce toward prev close  
                entry_price = prev_close - (prev_close - current_price) * 0.5
                conditions.append("Wait for 50% gap fill bounce")
                
        elif strategy == EntryStrategy.ORB:
            # Opening range breakout
            if gap_percent > 0:
                entry_price = current_price * 1.01  # 1% above current
                conditions.append("Buy stop above 30-min high")
            else:
                entry_price = current_price * 0.99  # 1% below current
                conditions.append("Sell stop below 30-min low")
                
        else:
            entry_price = current_price
            
        return entry_price, conditions
        
    def _calculate_stop_loss(
        self,
        entry_price: float,
        direction: str,
        atr: Optional[float],
        custom_percent: Optional[float]
    ) -> Tuple[float, float]:
        """Calculate stop loss price and percentage.
        
        Args:
            entry_price: Entry price
            direction: Trade direction
            atr: Average True Range
            custom_percent: Optional custom stop percentage
            
        Returns:
            Tuple of (stop_price, stop_percent)
        """
        if custom_percent:
            stop_percent = custom_percent
        elif atr and atr > 0:
            # ATR-based stop
            atr_percent = (atr * self.atr_stop_multiplier / entry_price) * 100
            stop_percent = max(self.default_stop_loss_percent, atr_percent)
        else:
            # Default percentage
            stop_percent = self.default_stop_loss_percent
            
        # Calculate stop price
        if direction == "long":
            stop_price = entry_price * (1 - stop_percent / 100)
        else:
            stop_price = entry_price * (1 + stop_percent / 100)
            
        return stop_price, stop_percent
        
    def _calculate_target(
        self,
        entry_price: float,
        direction: str,
        atr: Optional[float],
        stop_percent: float,
        custom_percent: Optional[float]
    ) -> Tuple[float, float]:
        """Calculate target price and percentage.
        
        Args:
            entry_price: Entry price
            direction: Trade direction
            atr: Average True Range
            stop_percent: Stop loss percentage
            custom_percent: Optional custom target percentage
            
        Returns:
            Tuple of (target_price, target_percent)
        """
        if custom_percent:
            target_percent = custom_percent
        else:
            # Ensure minimum risk/reward ratio
            min_target = stop_percent * self.min_risk_reward
            
            if atr and atr > 0:
                # ATR-based target
                atr_percent = (atr * self.atr_target_multiplier / entry_price) * 100
                target_percent = max(min_target, atr_percent)
            else:
                # Default percentage
                target_percent = max(min_target, self.default_target_percent)
                
        # Calculate target price
        if direction == "long":
            target_price = entry_price * (1 + target_percent / 100)
        else:
            target_price = entry_price * (1 - target_percent / 100)
            
        return target_price, target_percent
        
    def _calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        direction: str
    ) -> Tuple[float, int, float]:
        """Calculate position size based on risk management rules.
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            direction: Trade direction
            
        Returns:
            Tuple of (position_size_eur, position_size_shares, max_risk_eur)
        """
        # Calculate risk per share
        risk_per_share = abs(entry_price - stop_loss)
        
        # Position size based on max risk
        max_shares_by_risk = self.max_risk_per_trade_eur / risk_per_share
        
        # Position size based on max position size
        max_shares_by_size = self.max_position_size_eur / entry_price
        
        # Take the smaller of the two
        position_shares = int(min(max_shares_by_risk, max_shares_by_size))
        
        # Ensure at least 1 share
        position_shares = max(1, position_shares)
        
        # Calculate actual position size and risk
        position_size_eur = position_shares * entry_price
        max_risk_eur = position_shares * risk_per_share
        
        # Ensure we don't exceed limits
        if position_size_eur > self.max_position_size_eur:
            position_shares = int(self.max_position_size_eur / entry_price)
            position_size_eur = position_shares * entry_price
            max_risk_eur = position_shares * risk_per_share
            
        return position_size_eur, position_shares, max_risk_eur
        
    def _estimate_win_probability(
        self,
        candidate: ScoredCandidate,
        risk_reward: float
    ) -> float:
        """Estimate win probability based on score and R/R.
        
        Args:
            candidate: Scored candidate
            risk_reward: Risk/reward ratio
            
        Returns:
            Estimated win probability (0-1)
        """
        # Base probability from PRD
        base_prob = 0.60
        
        # Adjust based on score (±10%)
        score_adjustment = (candidate.composite_score - 50) / 500
        
        # Adjust based on R/R (lower R/R = higher probability)
        rr_adjustment = -0.05 * (risk_reward - 2.0)
        
        # Combine adjustments
        win_prob = base_prob + score_adjustment + rr_adjustment
        
        # Clamp to reasonable range
        return max(0.40, min(0.75, win_prob))
        
    def _calculate_kelly_fraction(
        self,
        win_prob: float,
        risk_reward: float
    ) -> float:
        """Calculate Kelly criterion fraction for position sizing.
        
        Args:
            win_prob: Win probability
            risk_reward: Risk/reward ratio
            
        Returns:
            Kelly fraction (0-1)
        """
        # Kelly formula: f = (p * b - q) / b
        # where p = win prob, q = loss prob, b = win/loss ratio
        p = win_prob
        q = 1 - p
        b = risk_reward
        
        kelly = (p * b - q) / b
        
        # Use fractional Kelly (25%) for safety
        kelly_fraction = kelly * 0.25
        
        # Clamp to reasonable range
        return max(0.0, min(0.25, kelly_fraction))