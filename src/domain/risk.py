"""Risk Manager - Enforce position limits and daily loss caps.

This module manages risk by enforcing strict position size limits,
daily loss caps, and PRIIPs compliance for EU retail trading.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import json
from pathlib import Path

from src.utils.logger import setup_logger
from src.config.settings import get_config
from src.domain.planner import TradePlan

logger = setup_logger(__name__)


class RiskStatus(Enum):
    """Risk check status."""
    APPROVED = "approved"
    REJECTED_SIZE = "rejected_size"
    REJECTED_LOSS = "rejected_loss"
    REJECTED_CORRELATION = "rejected_correlation"
    REJECTED_PRIIPS = "rejected_priips"
    WARNING = "warning"


@dataclass
class RiskMetrics:
    """Current risk metrics and limits."""
    # Current exposure
    open_positions: int = 0
    total_exposure_eur: float = 0.0
    total_risk_eur: float = 0.0
    realized_pnl_eur: float = 0.0
    
    # Daily metrics
    daily_loss_eur: float = 0.0
    daily_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    
    # Limits from PRD
    max_daily_loss_eur: float = 33.0
    max_position_size_eur: float = 250.0
    max_total_exposure_eur: float = 500.0  # Total bankroll
    max_correlation: float = 0.70  # Max correlation between positions
    
    # Risk utilization
    daily_loss_utilized_pct: float = 0.0
    exposure_utilized_pct: float = 0.0
    
    def update_utilization(self) -> None:
        """Update utilization percentages."""
        self.daily_loss_utilized_pct = (
            abs(self.daily_loss_eur) / self.max_daily_loss_eur * 100
            if self.max_daily_loss_eur > 0 else 0
        )
        self.exposure_utilized_pct = (
            self.total_exposure_eur / self.max_total_exposure_eur * 100
            if self.max_total_exposure_eur > 0 else 0
        )


@dataclass
class RiskDecision:
    """Risk management decision for a trade."""
    status: RiskStatus
    trade_plan: TradePlan
    approved: bool
    reason: str
    
    # Risk metrics at decision time
    metrics: RiskMetrics
    
    # Adjustments made
    adjusted_size: Optional[int] = None
    adjusted_risk: Optional[float] = None
    
    # Warnings
    warnings: List[str] = field(default_factory=list)
    
    # Decision metadata
    timestamp: datetime = field(default_factory=datetime.now)


class RiskManager:
    """Manages trading risk and enforces limits."""
    
    def __init__(self, state_file: Optional[Path] = None):
        """Initialize Risk Manager.
        
        Args:
            state_file: Optional path to persist risk state
        """
        self.config = get_config()
        
        # State persistence
        if state_file is None:
            state_file = Path(self.config.system.data_dir) / "risk_state.json"
        self.state_file = state_file
        
        # Current metrics
        self.metrics = RiskMetrics()
        
        # Position tracking
        self.open_positions: Dict[str, TradePlan] = {}
        self.closed_positions: List[Dict[str, Any]] = []
        
        # Correlation matrix cache
        self.correlation_cache: Dict[Tuple[str, str], float] = {}
        
        # Load saved state
        self._load_state()
        
    def check_position_size(self, plan: TradePlan) -> bool:
        """Check if position size is within limits.
        
        Args:
            plan: Trade plan to check
            
        Returns:
            True if position size is acceptable
        """
        # Check individual position size
        if plan.position_size_eur > self.metrics.max_position_size_eur:
            logger.warning(
                f"{plan.symbol}: Position size €{plan.position_size_eur:.0f} "
                f"exceeds limit €{self.metrics.max_position_size_eur:.0f}"
            )
            return False
            
        # Check total exposure
        new_exposure = self.metrics.total_exposure_eur + plan.position_size_eur
        if new_exposure > self.metrics.max_total_exposure_eur:
            logger.warning(
                f"{plan.symbol}: Would exceed total exposure limit "
                f"(€{new_exposure:.0f} > €{self.metrics.max_total_exposure_eur:.0f})"
            )
            return False
            
        return True
        
    def calculate_position_size(
        self,
        price: float,
        stop_loss: float,
        max_risk_eur: Optional[float] = None
    ) -> int:
        """Calculate position size based on risk parameters.
        
        Args:
            price: Entry price
            stop_loss: Stop loss price
            max_risk_eur: Maximum risk in EUR (uses daily limit if None)
            
        Returns:
            Number of shares
        """
        if max_risk_eur is None:
            # Use remaining daily loss capacity
            remaining_capacity = self.metrics.max_daily_loss_eur - abs(self.metrics.daily_loss_eur)
            max_risk_eur = max(0, remaining_capacity)
            
        if max_risk_eur <= 0:
            return 0
            
        # Risk per share
        risk_per_share = abs(price - stop_loss)
        if risk_per_share <= 0:
            return 0
            
        # Calculate shares
        shares = int(max_risk_eur / risk_per_share)
        
        # Apply position size limit
        max_shares_by_size = int(self.metrics.max_position_size_eur / price)
        shares = min(shares, max_shares_by_size)
        
        return max(0, shares)
        
    def check_daily_loss_limit(self) -> bool:
        """Check if daily loss limit has been reached.
        
        Returns:
            True if still within limits
        """
        return abs(self.metrics.daily_loss_eur) < self.metrics.max_daily_loss_eur
        
    def evaluate_trade(self, plan: TradePlan) -> RiskDecision:
        """Evaluate a trade plan against all risk criteria.
        
        Args:
            plan: Trade plan to evaluate
            
        Returns:
            Risk decision with approval status
        """
        # Update metrics
        self.metrics.update_utilization()
        
        # Check daily loss limit
        if not self.check_daily_loss_limit():
            return RiskDecision(
                status=RiskStatus.REJECTED_LOSS,
                trade_plan=plan,
                approved=False,
                reason=f"Daily loss limit reached (€{self.metrics.daily_loss_eur:.0f})",
                metrics=self.metrics
            )
            
        # Check if we have capacity for this risk
        remaining_capacity = self.metrics.max_daily_loss_eur - abs(self.metrics.daily_loss_eur)
        if plan.max_risk_eur > remaining_capacity:
            # Try to adjust position size
            adjusted_shares = self.calculate_position_size(
                plan.entry_price,
                plan.stop_loss,
                remaining_capacity
            )
            
            if adjusted_shares > 0:
                decision = RiskDecision(
                    status=RiskStatus.WARNING,
                    trade_plan=plan,
                    approved=True,
                    reason=f"Position size adjusted to fit remaining risk capacity",
                    metrics=self.metrics,
                    adjusted_size=adjusted_shares,
                    adjusted_risk=remaining_capacity
                )
                decision.warnings.append(
                    f"Reduced from {plan.position_size_shares} to {adjusted_shares} shares"
                )
                return decision
            else:
                return RiskDecision(
                    status=RiskStatus.REJECTED_LOSS,
                    trade_plan=plan,
                    approved=False,
                    reason=f"Insufficient risk capacity (€{remaining_capacity:.0f} < €{plan.max_risk_eur:.0f})",
                    metrics=self.metrics
                )
                
        # Check position size limits
        if not self.check_position_size(plan):
            return RiskDecision(
                status=RiskStatus.REJECTED_SIZE,
                trade_plan=plan,
                approved=False,
                reason="Position size exceeds limits",
                metrics=self.metrics
            )
            
        # Check correlation with existing positions
        if self.open_positions:
            max_correlation = self._check_correlation(plan.symbol)
            if max_correlation > self.metrics.max_correlation:
                return RiskDecision(
                    status=RiskStatus.REJECTED_CORRELATION,
                    trade_plan=plan,
                    approved=False,
                    reason=f"High correlation ({max_correlation:.2f}) with existing positions",
                    metrics=self.metrics
                )
                
        # Check PRIIPs compliance
        if not self._check_priips_compliance(plan.symbol):
            return RiskDecision(
                status=RiskStatus.REJECTED_PRIIPS,
                trade_plan=plan,
                approved=False,
                reason="Failed PRIIPs compliance check",
                metrics=self.metrics
            )
            
        # All checks passed
        decision = RiskDecision(
            status=RiskStatus.APPROVED,
            trade_plan=plan,
            approved=True,
            reason="All risk checks passed",
            metrics=self.metrics
        )
        
        # Add warnings if close to limits
        if self.metrics.daily_loss_utilized_pct > 70:
            decision.warnings.append(
                f"Daily loss utilization at {self.metrics.daily_loss_utilized_pct:.0f}%"
            )
            
        if self.metrics.exposure_utilized_pct > 80:
            decision.warnings.append(
                f"Exposure utilization at {self.metrics.exposure_utilized_pct:.0f}%"
            )
            
        return decision
        
    def record_trade_open(self, plan: TradePlan) -> None:
        """Record a trade being opened.
        
        Args:
            plan: Trade plan that was executed
        """
        # Add to open positions
        self.open_positions[plan.symbol] = plan
        
        # Update metrics
        self.metrics.open_positions = len(self.open_positions)
        self.metrics.total_exposure_eur += plan.position_size_eur
        self.metrics.total_risk_eur += plan.max_risk_eur
        self.metrics.daily_trades += 1
        
        # Save state
        self._save_state()
        
        logger.info(
            f"Opened position: {plan.symbol} "
            f"(Exposure: €{self.metrics.total_exposure_eur:.0f}, "
            f"Risk: €{self.metrics.total_risk_eur:.0f})"
        )
        
    def record_trade_close(
        self,
        symbol: str,
        exit_price: float,
        exit_time: datetime
    ) -> float:
        """Record a trade being closed.
        
        Args:
            symbol: Symbol that was closed
            exit_price: Exit price
            exit_time: Exit timestamp
            
        Returns:
            Realized P&L in EUR
        """
        if symbol not in self.open_positions:
            logger.warning(f"No open position found for {symbol}")
            return 0.0
            
        plan = self.open_positions[symbol]
        
        # Calculate P&L
        if plan.direction == "long":
            pnl_per_share = exit_price - plan.entry_price
        else:
            pnl_per_share = plan.entry_price - exit_price
            
        pnl_eur = pnl_per_share * plan.position_size_shares
        
        # Update metrics
        self.metrics.realized_pnl_eur += pnl_eur
        self.metrics.daily_loss_eur = min(0, self.metrics.daily_loss_eur + pnl_eur)
        
        if pnl_eur > 0:
            self.metrics.winning_trades += 1
        else:
            self.metrics.losing_trades += 1
            
        # Remove from open positions
        del self.open_positions[symbol]
        self.metrics.open_positions = len(self.open_positions)
        self.metrics.total_exposure_eur -= plan.position_size_eur
        self.metrics.total_risk_eur -= plan.max_risk_eur
        
        # Record closed position
        self.closed_positions.append({
            'symbol': symbol,
            'entry_price': plan.entry_price,
            'exit_price': exit_price,
            'entry_time': plan.created_at.isoformat(),
            'exit_time': exit_time.isoformat(),
            'pnl_eur': pnl_eur,
            'pnl_percent': (pnl_eur / plan.position_size_eur) * 100
        })
        
        # Save state
        self._save_state()
        
        logger.info(
            f"Closed position: {symbol} at ${exit_price:.2f} "
            f"(P&L: €{pnl_eur:+.2f}, Daily: €{self.metrics.daily_loss_eur:.2f})"
        )
        
        return pnl_eur
        
    def reset_daily_metrics(self) -> None:
        """Reset daily metrics at start of trading day."""
        logger.info(
            f"Resetting daily metrics - "
            f"Yesterday: {self.metrics.daily_trades} trades, "
            f"P&L: €{self.metrics.realized_pnl_eur:+.2f}"
        )
        
        self.metrics.daily_loss_eur = 0.0
        self.metrics.daily_trades = 0
        self.metrics.winning_trades = 0
        self.metrics.losing_trades = 0
        
        # Clear old closed positions (keep last 30 days)
        cutoff = datetime.now().timestamp() - (30 * 86400)
        self.closed_positions = [
            p for p in self.closed_positions
            if datetime.fromisoformat(p['exit_time']).timestamp() > cutoff
        ]
        
        self._save_state()
        
    def get_risk_summary(self) -> Dict[str, Any]:
        """Get current risk summary.
        
        Returns:
            Dictionary with risk metrics and positions
        """
        self.metrics.update_utilization()
        
        return {
            'metrics': {
                'open_positions': self.metrics.open_positions,
                'total_exposure_eur': self.metrics.total_exposure_eur,
                'total_risk_eur': self.metrics.total_risk_eur,
                'daily_loss_eur': self.metrics.daily_loss_eur,
                'daily_loss_utilized_pct': self.metrics.daily_loss_utilized_pct,
                'exposure_utilized_pct': self.metrics.exposure_utilized_pct,
                'daily_trades': self.metrics.daily_trades,
                'win_rate': (
                    self.metrics.winning_trades / max(1, self.metrics.winning_trades + self.metrics.losing_trades) * 100
                )
            },
            'limits': {
                'max_daily_loss_eur': self.metrics.max_daily_loss_eur,
                'max_position_size_eur': self.metrics.max_position_size_eur,
                'max_total_exposure_eur': self.metrics.max_total_exposure_eur
            },
            'positions': [
                {
                    'symbol': symbol,
                    'size_eur': plan.position_size_eur,
                    'risk_eur': plan.max_risk_eur,
                    'entry': plan.entry_price,
                    'stop': plan.stop_loss,
                    'target': plan.target_price
                }
                for symbol, plan in self.open_positions.items()
            ]
        }
        
    def _check_correlation(self, symbol: str) -> float:
        """Check correlation with existing positions.
        
        Args:
            symbol: Symbol to check
            
        Returns:
            Maximum correlation found
        """
        # In production, would calculate actual price correlations
        # For now, use simplified sector-based correlation
        
        # Technology stocks tend to correlate
        tech_symbols = {'AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'AMD'}
        
        max_correlation = 0.0
        
        for open_symbol in self.open_positions:
            if symbol == open_symbol:
                correlation = 1.0
            elif symbol in tech_symbols and open_symbol in tech_symbols:
                correlation = 0.75
            else:
                # Default low correlation
                correlation = 0.25
                
            max_correlation = max(max_correlation, correlation)
            
        return max_correlation
        
    def _check_priips_compliance(self, symbol: str) -> bool:
        """Check PRIIPs compliance for EU trading.
        
        Args:
            symbol: Symbol to check
            
        Returns:
            True if compliant
        """
        # Simplified check - in production would verify KIID availability
        # Exclude known non-compliant instruments
        
        excluded = {'VIX', 'UVXY', 'SVXY'}  # Volatility products
        excluded_suffixes = ['.W', '.U']     # Warrants and units
        
        if symbol.upper() in excluded:
            return False
            
        for suffix in excluded_suffixes:
            if symbol.upper().endswith(suffix):
                return False
                
        return True
        
    def _save_state(self) -> None:
        """Save current state to file."""
        try:
            state = {
                'metrics': {
                    'open_positions': self.metrics.open_positions,
                    'total_exposure_eur': self.metrics.total_exposure_eur,
                    'total_risk_eur': self.metrics.total_risk_eur,
                    'realized_pnl_eur': self.metrics.realized_pnl_eur,
                    'daily_loss_eur': self.metrics.daily_loss_eur,
                    'daily_trades': self.metrics.daily_trades,
                    'winning_trades': self.metrics.winning_trades,
                    'losing_trades': self.metrics.losing_trades
                },
                'open_positions': {
                    symbol: plan.to_dict()
                    for symbol, plan in self.open_positions.items()
                },
                'closed_positions': self.closed_positions[-100:],  # Keep last 100
                'last_updated': datetime.now().isoformat()
            }
            
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
                
        except Exception as e:
            logger.error(f"Error saving risk state: {e}")
            
    def _load_state(self) -> None:
        """Load saved state from file."""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    
                # Load metrics
                metrics = state.get('metrics', {})
                for key, value in metrics.items():
                    if hasattr(self.metrics, key):
                        setattr(self.metrics, key, value)
                        
                # Load closed positions
                self.closed_positions = state.get('closed_positions', [])
                
                # Check if we need to reset daily metrics
                last_updated = state.get('last_updated')
                if last_updated:
                    last_date = datetime.fromisoformat(last_updated).date()
                    if last_date < date.today():
                        self.reset_daily_metrics()
                        
                logger.info(f"Loaded risk state from {self.state_file}")
                
        except Exception as e:
            logger.error(f"Error loading risk state: {e}")