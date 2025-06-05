"""Test script for domain components."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.append(str(Path(__file__).parent))

from src.utils.logger import setup_logger
from src.config.settings import get_config
from src.data.cache_manager import CacheManager
from src.data.market import MarketDataManager
from src.data.news_manager import NewsManager
from src.domain import (
    UniverseManager,
    GapScanner,
    GapResult,
    FactorModel,
    FactorWeights,
    TradePlanner,
    RiskManager
)

logger = setup_logger(__name__)


async def test_universe_manager():
    """Test Universe Manager functionality."""
    logger.info("\n=== Testing Universe Manager ===")
    
    # Create components
    cache = CacheManager()
    market_data = MarketDataManager()
    
    # Create universe manager
    universe = UniverseManager(market_data, cache)
    
    try:
        # Test loading universe
        symbols = await universe.load_universe()
        logger.info(f"Loaded {len(symbols)} symbols from universe file")
        
        if symbols:
            # Test validating a symbol
            test_symbol = symbols[0] if symbols else "AAPL"
            is_valid = await universe.validate_symbol(test_symbol)
            logger.info(f"Symbol {test_symbol} validation: {is_valid}")
            
        return True
        
    except FileNotFoundError:
        logger.warning("Universe file not found - creating sample")
        # Create sample universe file
        universe_dir = Path(get_config().system.data_dir) / "universe"
        universe_dir.mkdir(parents=True, exist_ok=True)
        
        sample_file = universe_dir / "revolut_universe.csv"
        with open(sample_file, 'w') as f:
            f.write("symbol,name,sector\n")
            f.write("AAPL,Apple Inc,Technology\n")
            f.write("MSFT,Microsoft Corp,Technology\n")
            f.write("GOOGL,Alphabet Inc,Technology\n")
            f.write("AMZN,Amazon.com Inc,Consumer\n")
            f.write("TSLA,Tesla Inc,Automotive\n")
        
        logger.info(f"Created sample universe file: {sample_file}")
        return True
        
    except Exception as e:
        logger.error(f"Universe Manager test failed: {e}")
        return False


async def test_gap_scanner():
    """Test Gap Scanner functionality."""
    logger.info("\n=== Testing Gap Scanner ===")
    
    # Create components
    cache = CacheManager()
    market_data = MarketDataManager()
    scanner = GapScanner(market_data)
    
    try:
        # Test gap calculation
        gap = scanner.calculate_gap(current=105.0, prev_close=100.0)
        logger.info(f"Gap calculation: Current=105, Prev=100, Gap={gap:.1f}%")
        
        # Create mock gap result for testing
        mock_result = GapResult(
            symbol="TEST",
            gap_percent=5.5,
            gap_type=scanner._classify_gap(5.5, 2.5, None, 105, 100),
            current_price=105.0,
            prev_close=100.0,
            volume=1_000_000,
            volume_ratio=2.5
        )
        
        logger.info(f"Mock gap result: {mock_result.symbol} gap={mock_result.gap_percent}% type={mock_result.gap_type.value}")
        
        return True
        
    except Exception as e:
        logger.error(f"Gap Scanner test failed: {e}")
        return False


async def test_factor_model():
    """Test Factor Scoring Model."""
    logger.info("\n=== Testing Factor Model ===")
    
    try:
        # Create factor model
        model = FactorModel()
        
        # Test weight management
        logger.info(f"Default weights: {model.weights.to_dict()}")
        
        # Update weights
        new_weights = {
            'volatility': 0.50,
            'catalyst': 0.25,
            'sentiment': 0.10,
            'liquidity': 0.15
        }
        model.update_weights(new_weights)
        logger.info(f"Updated weights: {model.weights.to_dict()}")
        
        # Create mock candidates
        cache = CacheManager()
        market_data = MarketDataManager()
        scanner = GapScanner(market_data)
        
        mock_candidates = [
            GapResult(
                symbol="HIGH_VOL",
                gap_percent=7.5,
                gap_type=scanner._classify_gap(7.5, 3.0, 2.5, 110, 100),
                current_price=110.0,
                prev_close=100.0,
                volume=2_000_000,
                volume_ratio=3.0,
                atr=2.5,
                news_count=3
            ),
            GapResult(
                symbol="MED_VOL",
                gap_percent=5.0,
                gap_type=scanner._classify_gap(5.0, 2.0, 1.5, 105, 100),
                current_price=105.0,
                prev_close=100.0,
                volume=1_500_000,
                volume_ratio=2.0,
                atr=1.5,
                news_count=1
            )
        ]
        
        # Score candidates
        scored = model.score_candidates(mock_candidates)
        
        for candidate in scored:
            logger.info(
                f"Scored {candidate.symbol}: Score={candidate.composite_score:.1f}, "
                f"Rank={candidate.rank}"
            )
            breakdown = candidate.get_score_breakdown()
            for factor, pct in breakdown.items():
                logger.info(f"  - {factor}: {pct:.1f}%")
                
        return True
        
    except Exception as e:
        logger.error(f"Factor Model test failed: {e}")
        return False


async def test_trade_planner():
    """Test Trade Planner."""
    logger.info("\n=== Testing Trade Planner ===")
    
    try:
        # Create planner
        planner = TradePlanner()
        
        # Create mock scored candidate
        cache = CacheManager()
        market_data = MarketDataManager()
        scanner = GapScanner(market_data)
        
        gap_result = GapResult(
            symbol="PLAN_TEST",
            gap_percent=6.0,
            gap_type=scanner._classify_gap(6.0, 2.5, 2.0, 106, 100),
            current_price=106.0,
            prev_close=100.0,
            volume=1_800_000,
            volume_ratio=2.5,
            atr=2.0
        )
        
        from src.domain.scoring import ScoredCandidate
        candidate = ScoredCandidate(
            symbol="PLAN_TEST",
            gap_result=gap_result,
            composite_score=75.0
        )
        
        # Create trade plan
        plan = planner.create_plan(candidate)
        
        logger.info(f"\nTrade Plan for {plan.symbol}:")
        logger.info(f"  Direction: {plan.direction}")
        logger.info(f"  Entry: ${plan.entry_price:.2f} ({plan.entry_strategy.value})")
        logger.info(f"  Stop: ${plan.stop_loss:.2f} ({plan.stop_loss_percent:.1f}%)")
        logger.info(f"  Target: ${plan.target_price:.2f} ({plan.target_percent:.1f}%)")
        logger.info(f"  Position: {plan.position_size_shares} shares (€{plan.position_size_eur:.0f})")
        logger.info(f"  Risk: €{plan.max_risk_eur:.2f}")
        logger.info(f"  R/R Ratio: {plan.risk_reward_ratio:.2f}")
        logger.info(f"  Win Prob: {plan.win_probability:.1%}")
        
        return True
        
    except Exception as e:
        logger.error(f"Trade Planner test failed: {e}")
        return False


async def test_risk_manager():
    """Test Risk Manager."""
    logger.info("\n=== Testing Risk Manager ===")
    
    try:
        # Create risk manager
        risk_mgr = RiskManager()
        
        # Check initial state
        summary = risk_mgr.get_risk_summary()
        logger.info(f"\nInitial Risk State:")
        logger.info(f"  Open Positions: {summary['metrics']['open_positions']}")
        logger.info(f"  Daily Loss: €{summary['metrics']['daily_loss_eur']:.2f}")
        logger.info(f"  Total Exposure: €{summary['metrics']['total_exposure_eur']:.2f}")
        
        # Create mock trade plan
        planner = TradePlanner()
        cache = CacheManager()
        market_data = MarketDataManager()
        scanner = GapScanner(market_data)
        
        gap_result = GapResult(
            symbol="RISK_TEST",
            gap_percent=5.0,
            gap_type=scanner._classify_gap(5.0, 2.0, 1.5, 105, 100),
            current_price=105.0,
            prev_close=100.0,
            volume=1_500_000,
            volume_ratio=2.0,
            atr=1.5
        )
        
        from src.domain.scoring import ScoredCandidate
        candidate = ScoredCandidate(
            symbol="RISK_TEST",
            gap_result=gap_result,
            composite_score=70.0
        )
        
        plan = planner.create_plan(candidate)
        
        # Evaluate trade
        decision = risk_mgr.evaluate_trade(plan)
        
        logger.info(f"\nRisk Decision for {plan.symbol}:")
        logger.info(f"  Status: {decision.status.value}")
        logger.info(f"  Approved: {decision.approved}")
        logger.info(f"  Reason: {decision.reason}")
        
        if decision.warnings:
            logger.info("  Warnings:")
            for warning in decision.warnings:
                logger.info(f"    - {warning}")
                
        # Test position size calculation
        test_size = risk_mgr.calculate_position_size(
            price=50.0,
            stop_loss=48.5,
            max_risk_eur=25.0
        )
        logger.info(f"\nPosition size calculation: {test_size} shares (€50 price, €48.5 stop, €25 risk)")
        
        return True
        
    except Exception as e:
        logger.error(f"Risk Manager test failed: {e}")
        return False


async def run_all_tests():
    """Run all domain tests."""
    logger.info("Starting Domain Component Tests")
    logger.info("=" * 50)
    
    results = {
        "Universe Manager": await test_universe_manager(),
        "Gap Scanner": await test_gap_scanner(),
        "Factor Model": await test_factor_model(),
        "Trade Planner": await test_trade_planner(),
        "Risk Manager": await test_risk_manager()
    }
    
    # Summary
    logger.info("\n" + "=" * 50)
    logger.info("Test Summary:")
    passed = sum(1 for r in results.values() if r)
    total = len(results)
    
    for component, result in results.items():
        status = "✅ PASSED" if result else "❌ FAILED"
        logger.info(f"  {component}: {status}")
        
    logger.info(f"\nTotal: {passed}/{total} tests passed")
    
    return passed == total


if __name__ == "__main__":
    asyncio.run(run_all_tests())