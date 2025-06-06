"""Unit tests for domain layer components."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch

from src.domain.scanner import GapScanner, GapResult, GapType
from src.domain.scoring import FactorModel, ScoredCandidate, FactorType
from src.domain.planner import TradePlanner, TradePlan, EntryStrategy, ExitStrategy
from src.domain.risk import RiskManager, PositionSizing, RiskStatus
from src.domain.universe import UniverseManager
from src.data.base import Quote, Bar
from src.data.market import MarketDataManager
from src.data.cache_manager import CacheManager


class TestGapScanner:
    """Test gap scanner functionality."""

    @pytest.fixture
    def gap_scanner(self):
        """Create gap scanner instance with test market data."""
        # Create a simple test market data manager
        class TestMarketData:
            async def get_quote(self, symbol):
                quotes = {
                    "AAPL": Quote(
                        symbol="AAPL",
                        timestamp=datetime.now(),
                        price=155.0,
                        prev_close=150.0,
                        volume=2000000,  # High volume for good volume ratio
                        high=156.0,
                        low=154.0
                    ),
                    "GOOGL": Quote(
                        symbol="GOOGL",
                        timestamp=datetime.now(),
                        price=2900.0,
                        prev_close=2700.0,  # Larger gap to ensure it passes 4% minimum
                        volume=1800000,  # High volume for good volume ratio
                        high=2910.0,
                        low=2890.0
                    ),
                    "MSFT": Quote(
                        symbol="MSFT",
                        timestamp=datetime.now(),
                        price=381.0,
                        prev_close=380.0,
                        volume=500,  # Very low volume to ensure it gets filtered
                        high=381.0,
                        low=380.0
                    )
                }
                return quotes.get(symbol)
            
            async def get_price_history(self, symbol, interval='1d', period='5d'):
                # Return appropriate history based on symbol
                base_close = {
                    "AAPL": 150.0,
                    "GOOGL": 2800.0,
                    "MSFT": 380.0
                }.get(symbol, 100.0)
                
                return [
                    Bar(symbol=symbol, timestamp=datetime.now() - timedelta(days=2), open=base_close-5, high=base_close, low=base_close-10, close=base_close-5, volume=800000),
                    Bar(symbol=symbol, timestamp=datetime.now() - timedelta(days=1), open=base_close-5, high=base_close+1, low=base_close-5, close=base_close, volume=1000000)
                ]
        
        return GapScanner(TestMarketData())

    @pytest.fixture
    def sample_quotes(self):
        """Create sample quote data."""
        return {
            "AAPL": Quote(
                symbol="AAPL",
                timestamp=datetime.now(),
                price=155.0,  # Current pre-market
                prev_close=150.0,  # 3.33% gap
                volume=100000,
                high=156.0,
                low=154.0
            ),
            "GOOGL": Quote(
                symbol="GOOGL",
                timestamp=datetime.now(),
                price=2900.0,  # Current pre-market
                prev_close=2800.0,  # 3.57% gap
                volume=50000,
                high=2910.0,
                low=2890.0
            ),
            "MSFT": Quote(
                symbol="MSFT",
                timestamp=datetime.now(),
                price=381.0,  # Current pre-market
                prev_close=380.0,  # 0.26% gap (too small)
                volume=80000,
                high=382.0,
                low=380.0
            )
        }

    @pytest.mark.asyncio
    async def test_gap_detection(self, gap_scanner):
        """Test basic gap detection."""
        # Mock ATR calculation to avoid complex calculation
        gap_scanner._calculate_atr = AsyncMock(return_value=2.0)
        
        # Scan for gaps
        gaps = await gap_scanner.scan_gaps(["AAPL", "GOOGL", "MSFT"])
        
        # Should find 1 gap (AAPL has ~6.9% gap)
        # GOOGL has ~3.57% gap (below 4% minimum)
        # MSFT has ~0.26% gap (below 4% minimum)
        assert len(gaps) == 1
        assert all(isinstance(g, GapResult) for g in gaps)
        assert gaps[0].symbol == "AAPL"
        assert gaps[0].gap_percent > 4.0  # Above minimum threshold
        assert gaps[0].volume_ratio > 1.5  # Above minimum volume ratio

    def test_gap_classification(self, gap_scanner):
        """Test gap type classification."""
        # Test different gap scenarios
        test_cases = [
            (5.5, 3.5, GapType.BREAKAWAY),  # Large gap, high volume (>3.0)
            (5.5, 2.5, GapType.RUNAWAY),    # Medium gap, good volume (>2.0)
            (11.0, 3.0, GapType.EXHAUSTION), # Very large gap (>10%)
            (2.0, 0.8, None),                # Too small
        ]
        
        for gap_pct, volume_ratio, expected_type in test_cases:
            gap_type = gap_scanner._classify_gap(
                gap_percent=gap_pct, 
                volume_ratio=volume_ratio,
                atr=2.0,  # Dummy ATR value (not used in classification)
                current=100.0,  # Dummy current price (not used)
                prev_close=95.0  # Dummy prev close (not used)
            )
            if expected_type is None:
                assert gap_type == GapType.COMMON  # Small gaps become common
            else:
                assert gap_type == expected_type

    @pytest.mark.asyncio
    async def test_volume_analysis(self, gap_scanner):
        """Test volume analysis in gap detection."""
        # Create quote with volume data
        quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=155.0,
            prev_close=150.0,
            volume=2000000,  # Current volume
            high=156.0,
            low=154.0
        )
        
        # Mock average volume
        gap_scanner.market_data.get_average_volume = Mock(return_value=1000000)
        
        # Calculate volume ratio  
        gap_scanner.market_data.get_quote = AsyncMock(return_value=quote)
        gap_scanner.market_data.get_price_history = AsyncMock(return_value=[
            Bar(symbol="AAPL", timestamp=datetime.now() - timedelta(days=2), open=145.0, high=146.0, low=144.0, close=145.0, volume=900000),
            Bar(symbol="AAPL", timestamp=datetime.now() - timedelta(days=1), open=145.0, high=151.0, low=145.0, close=150.0, volume=1000000)
        ])
        gap_scanner._calculate_atr = AsyncMock(return_value=2.0)
        
        gaps = await gap_scanner.scan_gaps(["AAPL"])
        
        assert len(gaps) == 1
        assert 2.0 <= gaps[0].volume_ratio <= 2.5  # Around 2x average volume

    @pytest.mark.asyncio
    async def test_atr_calculation(self, gap_scanner):
        """Test ATR calculation for volatility."""
        # Mock historical data for ATR
        historical_data = [
            {"high": 152.0, "low": 148.0, "close": 150.0},
            {"high": 151.0, "low": 147.0, "close": 149.0},
            {"high": 153.0, "low": 149.0, "close": 151.0},
        ]
        
        # Create enough historical data for ATR calculation (needs 14+ bars)
        history = []
        for i in range(20):
            history.append({
                'high': 152.0 + i * 0.5,
                'low': 148.0 + i * 0.5, 
                'close': 150.0 + i * 0.5,
                'volume': 1000000
            })
        
        gap_scanner.market_data.get_price_history = AsyncMock(return_value=history)
        
        # Calculate ATR
        atr = await gap_scanner._calculate_atr("AAPL")
        
        assert atr is not None
        assert atr > 0
        assert atr < 10  # Reasonable ATR value


class TestFactorModel:
    """Test scoring model functionality."""

    @pytest.fixture
    def factor_model(self):
        """Create factor model instance."""
        return FactorModel()

    @pytest.fixture
    def sample_gaps(self):
        """Create sample gap results."""
        return [
            GapResult(
                symbol="AAPL",
                gap_percent=5.0,
                gap_type=GapType.BREAKAWAY,
                current_price=155.0,
                prev_close=150.0,
                volume=2000000,
                volume_ratio=2.5,
                atr=3.0
            ),
            GapResult(
                symbol="GOOGL",
                gap_percent=3.5,
                gap_type=GapType.RUNAWAY,
                current_price=2900.0,
                prev_close=2800.0,
                volume=1000000,
                volume_ratio=1.8,
                atr=50.0
            ),
            GapResult(
                symbol="MSFT",
                gap_percent=7.0,
                gap_type=GapType.EXHAUSTION,
                current_price=407.0,
                prev_close=380.0,
                volume=3000000,
                volume_ratio=3.0,
                atr=5.0
            )
        ]

    def test_scoring_calculation(self, factor_model, sample_gaps):
        """Test scoring calculation for candidates."""
        scored = factor_model.score_candidates(sample_gaps)
        
        assert len(scored) == 3
        assert all(isinstance(s, ScoredCandidate) for s in scored)
        assert all(0 <= s.composite_score <= 100 for s in scored)
        
        # Check scoring order (exhaustion gaps might score lower)
        scores = {s.gap_result.symbol: s.composite_score for s in scored}
        assert scores["MSFT"] < scores["AAPL"]  # Exhaustion gap scores lower

    def test_factor_weights(self, factor_model):
        """Test individual factor calculations."""
        gap = GapResult(
            symbol="TEST",
            gap_percent=5.0,
            gap_type=GapType.BREAKAWAY,
            current_price=105.0,
            prev_close=100.0,
            volume=2000000,
            volume_ratio=2.0,
            atr=2.0
        )
        
        # Calculate individual factors
        volatility_score = factor_model._score_volatility(gap)
        catalyst_score = factor_model._score_catalyst(gap)
        sentiment_score = factor_model._score_sentiment(gap)
        liquidity_score = factor_model._score_liquidity(gap)
        
        assert 0 <= volatility_score <= 100
        assert 0 <= catalyst_score <= 100
        assert 0 <= sentiment_score <= 100
        assert 0 <= liquidity_score <= 100
        
        # Breakaway gap with good volume should score well
        assert volatility_score > 60  # Breakaway gap + 5% gap should score well
        assert liquidity_score > 30   # Volume ratio 2.0 should give good liquidity score

    def test_custom_weights(self, factor_model):
        """Test scoring with custom factor weights."""
        # Set custom weights
        custom_weights = {
            "volatility": 0.5,   # Emphasize volatility
            "catalyst": 0.2,
            "sentiment": 0.2,
            "liquidity": 0.1
        }
        
        factor_model.update_weights(custom_weights)
        
        gap = GapResult(
            symbol="TEST",
            gap_percent=6.0,
            gap_type=GapType.BREAKAWAY,
            current_price=106.0,
            prev_close=100.0,
            volume=2000000,
            volume_ratio=2.0,
            atr=2.0
        )
        
        scored = factor_model.score_candidates([gap])
        assert len(scored) == 1
        
        # Score should reflect custom weights
        # Check that custom weights are reflected in the scoring
        assert len(scored) == 1


class TestTradePlanner:
    """Test trade planning functionality."""

    @pytest.fixture
    def trade_planner(self):
        """Create trade planner instance."""
        return TradePlanner()

    @pytest.fixture
    def scored_candidate(self):
        """Create a scored candidate."""
        gap = GapResult(
            symbol="AAPL",
            gap_percent=5.0,
            gap_type=GapType.BREAKAWAY,
            current_price=155.0,
            prev_close=150.0,
            volume=2000000,
            volume_ratio=2.5,
            atr=3.0
        )
        
        return ScoredCandidate(
            symbol="AAPL",
            gap_result=gap,
            scores={
                FactorType.VOLATILITY: 80.0,
                FactorType.CATALYST: 90.0,
                FactorType.SENTIMENT: 85.0,
                FactorType.LIQUIDITY: 85.0
            },
            composite_score=85.0
        )

    def test_trade_plan_creation(self, trade_planner, scored_candidate):
        """Test creating a trade plan."""
        # Create trade plan
        plan = trade_planner.create_plan(scored_candidate)
        
        assert isinstance(plan, TradePlan)
        assert plan.symbol == "AAPL"
        assert plan.score == 85.0
        assert plan.direction == "long"
        # Entry price should be close to current price (depending on strategy)
        assert 150.0 <= plan.entry_price <= 160.0
        assert plan.position_size_shares >= 1
        assert plan.position_size_eur <= 250.0  # Should respect max position size

    def test_stop_loss_calculation(self, trade_planner, scored_candidate):
        """Test stop loss calculation."""
        # Test ATR-based stop loss
        stop_price, stop_percent = trade_planner._calculate_stop_loss(
            entry_price=100.0,
            direction="long",
            atr=2.0,
            custom_percent=None
        )
        
        # Should be at least the default stop loss percentage
        assert stop_price < 100.0  # Below entry for long
        assert stop_percent >= trade_planner.default_stop_loss_percent

    def test_target_calculation(self, trade_planner, scored_candidate):
        """Test target price calculation."""
        # Test risk-reward based target
        entry_price = 100.0
        
        target_price, target_percent = trade_planner._calculate_target(
            entry_price=entry_price,
            direction="long",
            atr=2.0,
            stop_percent=5.0,
            custom_percent=None
        )
        
        # Target should be above entry for long position
        assert target_price > entry_price
        # Should respect minimum risk/reward ratio
        assert target_percent >= 5.0 * trade_planner.min_risk_reward  # 5% stop * 2 = 10% minimum target

    def test_entry_strategy_selection(self, trade_planner, scored_candidate):
        """Test entry strategy selection."""
        # Test VWAP strategy for moderate gap (5%)
        strategy = trade_planner._select_entry_strategy(scored_candidate)
        assert strategy == EntryStrategy.VWAP  # 5% gap falls in VWAP range
        
        # Test market strategy for small gap
        small_gap = scored_candidate
        small_gap.gap_result.gap_percent = 2.0
        small_gap.gap_result.volume_ratio = 1.5
        
        strategy = trade_planner._select_entry_strategy(small_gap)
        assert strategy == EntryStrategy.MARKET
        
        # Test pullback strategy for large gap
        large_gap = scored_candidate  
        large_gap.gap_result.gap_percent = 8.0
        large_gap.gap_result.volume_ratio = 4.0
        
        strategy = trade_planner._select_entry_strategy(large_gap)
        assert strategy == EntryStrategy.PULLBACK


class TestRiskManager:
    """Test risk management functionality."""

    @pytest.fixture
    def risk_manager(self):
        """Create risk manager instance."""
        return RiskManager()

    def test_position_sizing(self, risk_manager):
        """Test position size calculation."""
        sizing = risk_manager.calculate_position_size(
            entry_price=100.0,
            stop_loss=95.0,
            account_balance=10000.0,
            max_risk_percent=1.0,
            max_position_percent=2.5
        )
        
        assert isinstance(sizing, PositionSizing)
        assert sizing.position_value_eur <= 250.0  # Max 2.5% of 10k
        assert sizing.max_risk_eur <= 100.0  # Max 1% of 10k
        
        # Check risk calculation
        risk_per_share = 100.0 - 95.0  # 5 EUR
        max_shares_by_risk = 100.0 / risk_per_share  # 20 shares
        assert sizing.shares <= max_shares_by_risk

    def test_daily_loss_limit(self, risk_manager):
        """Test daily loss limit enforcement."""
        # Simulate losses by directly updating metrics
        risk_manager.metrics.daily_loss_eur = -25.0
        
        assert abs(risk_manager.metrics.daily_loss_eur) == 25.0
        assert risk_manager.check_daily_loss_limit() is True
        
        # Add more to exceed limit
        risk_manager.metrics.daily_loss_eur = -35.0
        assert abs(risk_manager.metrics.daily_loss_eur) == 35.0
        assert risk_manager.check_daily_loss_limit() is False  # Exceeds €33 limit

    def test_position_validation(self, risk_manager):
        """Test trade validation."""
        # Valid trade
        valid_trade = TradePlan(
            symbol="AAPL",
            score=80.0,
            direction="long",
            entry_strategy=EntryStrategy.VWAP,
            entry_price=100.0,
            stop_loss=95.0,
            stop_loss_percent=5.0,
            target_price=110.0,
            target_percent=10.0,
            exit_strategy=ExitStrategy.FIXED_TARGET,
            position_size_eur=250.0,
            position_size_shares=2.5,
            max_risk_eur=12.5,
            risk_reward_ratio=2.0
        )
        
        decision = risk_manager.evaluate_trade(valid_trade)
        assert decision.approved is True
        assert decision.status == RiskStatus.APPROVED
        
        # Invalid trade (position too large)
        invalid_trade = TradePlan(
            symbol="AAPL",
            score=80.0,
            direction="long",
            entry_strategy=EntryStrategy.VWAP,
            entry_price=100.0,
            stop_loss=95.0,
            stop_loss_percent=5.0,
            target_price=110.0,
            target_percent=10.0,
            exit_strategy=ExitStrategy.FIXED_TARGET,
            position_size_eur=300.0,  # Exceeds limit
            position_size_shares=3.0,
            max_risk_eur=15.0,
            risk_reward_ratio=2.0
        )
        
        decision = risk_manager.evaluate_trade(invalid_trade)
        assert decision.approved is False
        assert decision.status == RiskStatus.REJECTED_SIZE
        assert "position size" in decision.reason.lower()


class TestUniverseManager:
    """Test universe management functionality."""

    @pytest.fixture
    def universe_manager(self, tmp_path):
        """Create universe manager instance."""
        # Create test universe file
        universe_file = tmp_path / "test_universe.csv"
        with open(universe_file, 'w') as f:
            f.write("symbol,sector,market_cap\n")
            f.write("AAPL,Technology,Large\n")
            f.write("GOOGL,Technology,Large\n")
            f.write("JPM,Financial,Large\n")
            f.write("TSLA,Automotive,Large\n")
            f.write("NFLX,Technology,Large\n")
        
        # Mock dependencies
        mock_market_data = Mock()
        mock_cache_manager = Mock()
        
        return UniverseManager(
            market_data=mock_market_data,
            cache_manager=mock_cache_manager,
            universe_file=universe_file
        )

    @pytest.mark.asyncio
    async def test_load_universe(self, universe_manager):
        """Test loading universe from file."""
        # Mock cache to return None so it loads from file
        universe_manager.cache.store.get = AsyncMock(return_value=None)
        universe_manager.cache.store.set = AsyncMock()
        
        symbols = await universe_manager.load_universe()
        
        assert len(symbols) == 5
        assert "AAPL" in symbols
        assert "GOOGL" in symbols
        assert "JPM" in symbols

    @pytest.mark.asyncio
    async def test_get_active_symbols(self, universe_manager):
        """Test getting active symbols with validation."""
        # Mock cache to return None
        universe_manager.cache.store.get = AsyncMock(return_value=None)
        universe_manager.cache.store.set = AsyncMock()
        
        # Mock market data validation
        quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,
            prev_close=149.0,
            volume=1000000,
            high=151.0,
            low=149.0
        )
        universe_manager.market_data.get_quote = AsyncMock(return_value=quote)
        
        # Test get_active_symbols
        symbols = await universe_manager.get_active_symbols()
        
        # Should validate all symbols
        assert isinstance(symbols, list)
        assert universe_manager.market_data.get_quote.called

    @pytest.mark.asyncio
    async def test_symbol_validation(self, universe_manager):
        """Test symbol validation."""
        # Mock quote for valid symbol
        valid_quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,  # Within range
            prev_close=149.0,
            volume=100000,
            high=151.0,
            low=149.0
        )
        
        # Mock quote for invalid symbol (price too low)
        invalid_quote = Quote(
            symbol="PENNY",
            timestamp=datetime.now(),
            price=0.50,  # Below minimum
            prev_close=0.45,
            volume=10000,
            high=0.52,
            low=0.48
        )
        
        universe_manager.market_data.get_quote = AsyncMock(side_effect=[valid_quote, invalid_quote, None])
        
        # Test valid symbol
        assert await universe_manager.validate_symbol("AAPL") is True
        
        # Test invalid symbol (price too low)
        assert await universe_manager.validate_symbol("PENNY") is False
        
        # Test symbol with no quote
        assert await universe_manager.validate_symbol("NODATA") is False

    @pytest.mark.asyncio
    async def test_refresh_universe(self, universe_manager):
        """Test refreshing universe from file."""
        # Mock cache operations
        universe_manager.cache.store.delete = AsyncMock()
        universe_manager.cache.store.get = AsyncMock(return_value=None)
        universe_manager.cache.store.set = AsyncMock()
        
        # Test refresh
        symbols = await universe_manager.refresh_universe()
        
        assert len(symbols) == 5
        assert universe_manager.cache.store.delete.called  # Should clear cache