"""Factor Scoring Model - Multi-factor scoring for trade candidates.

This module implements the scoring model that ranks candidates based on
volatility, catalyst strength, sentiment, and liquidity factors.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
import statistics

from src.utils.logger import setup_logger
from src.config.settings import get_config
from src.domain.scanner import GapResult, GapType
from src.data.news_manager import NewsManager

logger = setup_logger(__name__)


class FactorType(Enum):
    """Types of scoring factors."""
    VOLATILITY = "volatility"
    CATALYST = "catalyst"
    SENTIMENT = "sentiment"
    LIQUIDITY = "liquidity"


@dataclass
class FactorWeights:
    """Configurable factor weights for scoring model."""
    volatility: float = 0.40  # 40% default
    catalyst: float = 0.30    # 30% default
    sentiment: float = 0.10   # 10% default
    liquidity: float = 0.20   # 20% default
    
    def __post_init__(self):
        """Validate weights sum to 1.0."""
        total = self.volatility + self.catalyst + self.sentiment + self.liquidity
        if abs(total - 1.0) > 0.001:
            # Normalize weights
            self.volatility /= total
            self.catalyst /= total
            self.sentiment /= total
            self.liquidity /= total
            logger.warning(f"Normalized weights to sum to 1.0")
            
    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary."""
        return {
            FactorType.VOLATILITY.value: self.volatility,
            FactorType.CATALYST.value: self.catalyst,
            FactorType.SENTIMENT.value: self.sentiment,
            FactorType.LIQUIDITY.value: self.liquidity
        }


@dataclass
class ScoredCandidate:
    """A candidate with factor scores and composite score."""
    symbol: str
    gap_result: GapResult
    scores: Dict[FactorType, float] = field(default_factory=dict)
    composite_score: float = 0.0
    rank: int = 0
    
    # Additional metadata
    news_items: List[Dict[str, Any]] = field(default_factory=list)
    sentiment_data: Optional[Dict[str, Any]] = None
    liquidity_data: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def get_score_breakdown(self) -> Dict[str, float]:
        """Get percentage contribution of each factor."""
        if self.composite_score == 0:
            return {factor.value: 0.0 for factor in FactorType}
            
        breakdown = {}
        for factor, score in self.scores.items():
            breakdown[factor.value] = (score / self.composite_score) * 100
            
        return breakdown


class FactorModel:
    """Multi-factor scoring model for ranking trade candidates."""
    
    def __init__(self, news_manager: Optional[NewsManager] = None):
        """Initialize Factor Model.
        
        Args:
            news_manager: News manager for catalyst/sentiment scoring
        """
        self.news_manager = news_manager
        self.weights = FactorWeights()
        
        # Score normalization parameters
        self.score_min = 0.0
        self.score_max = 100.0
        
        # Factor-specific thresholds
        self.volatility_thresholds = {
            'low': 2.0,      # ATR multiplier
            'medium': 3.0,
            'high': 4.0
        }
        
        self.catalyst_thresholds = {
            'earnings': 10.0,  # Score boost for earnings
            'news': 5.0,       # Per news item
            'upgrade': 8.0     # Analyst upgrade
        }
        
    def score_candidates(
        self,
        candidates: List[GapResult],
        weights: Optional[FactorWeights] = None
    ) -> List[ScoredCandidate]:
        """Score and rank candidates using multi-factor model.
        
        Args:
            candidates: List of gap scan results
            weights: Optional custom weights (uses defaults if None)
            
        Returns:
            List of scored candidates sorted by composite score
        """
        if weights:
            self.weights = weights
            
        logger.info(f"Scoring {len(candidates)} candidates with weights: {self.weights.to_dict()}")
        
        scored_candidates = []
        
        for candidate in candidates:
            # Calculate individual factor scores
            volatility_score = self._score_volatility(candidate)
            catalyst_score = self._score_catalyst(candidate)
            sentiment_score = self._score_sentiment(candidate)
            liquidity_score = self._score_liquidity(candidate)
            
            # Store factor scores
            scores = {
                FactorType.VOLATILITY: volatility_score,
                FactorType.CATALYST: catalyst_score,
                FactorType.SENTIMENT: sentiment_score,
                FactorType.LIQUIDITY: liquidity_score
            }
            
            # Calculate weighted composite score
            composite = (
                volatility_score * self.weights.volatility +
                catalyst_score * self.weights.catalyst +
                sentiment_score * self.weights.sentiment +
                liquidity_score * self.weights.liquidity
            )
            
            # Create scored candidate
            scored = ScoredCandidate(
                symbol=candidate.symbol,
                gap_result=candidate,
                scores=scores,
                composite_score=composite
            )
            
            scored_candidates.append(scored)
            
        # Sort by composite score (descending)
        scored_candidates.sort(key=lambda x: x.composite_score, reverse=True)
        
        # Assign ranks
        for i, candidate in enumerate(scored_candidates):
            candidate.rank = i + 1
            
        # Log top candidates
        if scored_candidates:
            top_5 = scored_candidates[:5]
            logger.info("Top 5 candidates:")
            for candidate in top_5:
                logger.info(
                    f"  {candidate.rank}. {candidate.symbol}: "
                    f"Score={candidate.composite_score:.1f} "
                    f"(V={candidate.scores[FactorType.VOLATILITY]:.1f}, "
                    f"C={candidate.scores[FactorType.CATALYST]:.1f}, "
                    f"S={candidate.scores[FactorType.SENTIMENT]:.1f}, "
                    f"L={candidate.scores[FactorType.LIQUIDITY]:.1f})"
                )
                
        return scored_candidates
        
    def update_weights(self, weights: Dict[str, float]) -> None:
        """Update factor weights.
        
        Args:
            weights: Dictionary with factor names and weights
        """
        self.weights = FactorWeights(
            volatility=weights.get('volatility', self.weights.volatility),
            catalyst=weights.get('catalyst', self.weights.catalyst),
            sentiment=weights.get('sentiment', self.weights.sentiment),
            liquidity=weights.get('liquidity', self.weights.liquidity)
        )
        logger.info(f"Updated weights: {self.weights.to_dict()}")
        
    def _score_volatility(self, candidate: GapResult) -> float:
        """Score based on volatility indicators.
        
        High scores for:
        - Large gaps (4-10%)
        - High ATR
        - Breakaway gap type
        """
        score = 0.0
        
        # Gap size component (40 points max)
        gap_abs = abs(candidate.gap_percent)
        if 4.0 <= gap_abs <= 10.0:
            # Linear scale from 4% to 10%
            score += 20 + (gap_abs - 4.0) * 3.33
        elif gap_abs > 10.0:
            # Diminishing returns above 10%
            score += 40 - (gap_abs - 10.0)
            
        # ATR component (30 points max)
        if candidate.atr:
            price_atr_ratio = candidate.atr / candidate.current_price
            # Higher ATR = higher volatility = higher score
            score += min(30, price_atr_ratio * 1000)
            
        # Gap type component (30 points max)
        gap_type_scores = {
            GapType.BREAKAWAY: 30,
            GapType.RUNAWAY: 20,
            GapType.EXHAUSTION: 10,
            GapType.COMMON: 5
        }
        score += gap_type_scores.get(candidate.gap_type, 0)
        
        return min(self.score_max, score)
        
    def _score_catalyst(self, candidate: GapResult) -> float:
        """Score based on catalyst strength.
        
        High scores for:
        - Recent news
        - Earnings events
        - High news count
        """
        score = 0.0
        
        # News count component (50 points max)
        if candidate.news_count > 0:
            score += min(50, candidate.news_count * 10)
            
        # Volume spike as proxy for catalyst (30 points max)
        if candidate.volume_ratio > 2.0:
            score += min(30, (candidate.volume_ratio - 1.0) * 15)
            
        # Short interest component (20 points max)
        if candidate.short_interest and candidate.short_interest > 20:
            # High short interest + gap up = squeeze potential
            if candidate.gap_percent > 0:
                score += 20
                
        return min(self.score_max, score)
        
    def _score_sentiment(self, candidate: GapResult) -> float:
        """Score based on sentiment analysis.
        
        High scores for:
        - Positive sentiment
        - Bullish options flow
        """
        score = 50.0  # Neutral baseline
        
        # Options activity component
        if candidate.options_activity == "calls":
            score += 30
        elif candidate.options_activity == "puts":
            score -= 20
            
        # Would integrate with news sentiment here
        # For now, use gap direction as proxy
        if candidate.gap_percent > 5.0:
            score += 20
        elif candidate.gap_percent < -5.0:
            score -= 20
            
        return max(0, min(self.score_max, score))
        
    def _score_liquidity(self, candidate: GapResult) -> float:
        """Score based on liquidity metrics.
        
        High scores for:
        - High volume
        - Tight spreads (not available in free tier)
        - Good market depth
        """
        score = 0.0
        
        # Volume ratio component (60 points max)
        if candidate.volume_ratio > 1.0:
            # Higher volume = better liquidity
            score += min(60, candidate.volume_ratio * 20)
            
        # Price level component (40 points max)
        # Mid-priced stocks typically have better liquidity
        if 10 <= candidate.current_price <= 100:
            score += 40
        elif 5 <= candidate.current_price <= 200:
            score += 20
        else:
            score += 10
            
        return min(self.score_max, score)
        
    def get_selection(
        self,
        scored_candidates: List[ScoredCandidate],
        count: int = 5,
        min_score: float = 50.0
    ) -> List[ScoredCandidate]:
        """Get top N candidates that meet minimum score.
        
        Args:
            scored_candidates: List of scored candidates
            count: Number to select
            min_score: Minimum composite score required
            
        Returns:
            Top candidates meeting criteria
        """
        # Filter by minimum score
        qualified = [c for c in scored_candidates if c.composite_score >= min_score]
        
        # Return top N
        selected = qualified[:count]
        
        if len(selected) < count:
            logger.warning(
                f"Only {len(selected)} candidates meet minimum score of {min_score}"
            )
            
        return selected
        
    def explain_score(self, candidate: ScoredCandidate) -> str:
        """Generate human-readable explanation of score.
        
        Args:
            candidate: Scored candidate
            
        Returns:
            Explanation string
        """
        breakdown = candidate.get_score_breakdown()
        
        explanation = f"Score Analysis for {candidate.symbol}:\n"
        explanation += f"Composite Score: {candidate.composite_score:.1f}/100\n\n"
        
        explanation += "Factor Contributions:\n"
        for factor, percentage in breakdown.items():
            score = candidate.scores.get(FactorType(factor), 0)
            explanation += f"- {factor.capitalize()}: {score:.1f} ({percentage:.1f}%)\n"
            
        # Add specific insights
        gap = candidate.gap_result
        explanation += f"\nKey Metrics:\n"
        explanation += f"- Gap: {gap.gap_percent:+.1f}%\n"
        explanation += f"- Volume Ratio: {gap.volume_ratio:.1f}x average\n"
        
        if gap.atr:
            explanation += f"- ATR: ${gap.atr:.2f}\n"
            
        if gap.news_count > 0:
            explanation += f"- News Items: {gap.news_count}\n"
            
        return explanation