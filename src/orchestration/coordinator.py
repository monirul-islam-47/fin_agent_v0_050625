"""Main coordinator for orchestrating the complete scan workflow."""
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import time

from src.utils.logger import get_logger
from src.config.settings import get_config
from src.data.cache_manager import CacheManager
from src.data.market import MarketDataManager
from src.data.news_manager import NewsManager
from src.domain.universe import UniverseManager
from src.domain.scanner import GapScanner
from src.domain.scoring import FactorModel, FactorWeights
from src.domain.planner import TradePlanner, TradePlan
from src.domain.risk import RiskManager
from src.orchestration.event_bus import EventBus
from src.orchestration.events import (
    ScanRequest, TradeSignal, SystemStatus, ErrorEvent,
    RiskAlert, DataUpdate, EventPriority
)
from src.persistence.journal import TradeJournal
from src.persistence.metrics import PerformanceMetrics


logger = get_logger(__name__)


@dataclass
class ScanResult:
    """Result of a market scan."""
    scan_type: str
    timestamp: datetime
    total_symbols: int
    gaps_found: int
    candidates_scored: int
    trades_planned: int
    trades_approved: int
    execution_time: float
    top_trades: List[TradePlan]
    errors: List[str]


class Coordinator:
    """Orchestrates the complete market scan workflow."""
    
    def __init__(
        self,
        event_bus: EventBus,
        cache: Optional[CacheManager] = None,
        market_data: Optional[MarketDataManager] = None,
        news_manager: Optional[NewsManager] = None
    ):
        """Initialize coordinator.
        
        Args:
            event_bus: Event bus for communication
            cache: Optional cache manager (will create if not provided)
            market_data: Optional market data manager
            news_manager: Optional news manager
        """
        self.event_bus = event_bus
        self.config = get_config()
        
        # Initialize components
        self.cache = cache or CacheManager()
        self.market_data = market_data or MarketDataManager()
        self.news_manager = news_manager or NewsManager()
        
        # Domain components
        self.universe_manager = UniverseManager(self.market_data, self.cache)
        self.gap_scanner = GapScanner(self.cache)
        self.factor_model = FactorModel()
        self.trade_planner = TradePlanner()
        self.risk_manager = RiskManager()
        
        # Persistence components
        self.trade_journal = TradeJournal()
        self.performance_metrics = PerformanceMetrics(self.trade_journal)
        
        # State
        self._running = False
        self._current_scan: Optional[asyncio.Task] = None
        
    async def start(self):
        """Start the coordinator."""
        if self._running:
            logger.warning("Coordinator already running")
            return
            
        self._running = True
        
        # Subscribe to events
        await self.event_bus.subscribe(
            ScanRequest,
            self._handle_scan_request,
            name="coordinator_scan_handler"
        )
        
        # Initialize components
        await self._initialize_components()
        
        # Subscribe persistence components to events
        await self.trade_journal.subscribe_to_events(self.event_bus)
        
        # Emit status
        await self.event_bus.publish(SystemStatus(
            component="coordinator",
            status="started",
            message="Coordinator ready to process scans"
        ))
        
        logger.info("Coordinator started")
        
    async def stop(self):
        """Stop the coordinator."""
        self._running = False
        
        # Cancel current scan if running
        if self._current_scan and not self._current_scan.done():
            self._current_scan.cancel()
            try:
                await self._current_scan
            except asyncio.CancelledError:
                pass
                
        # Emit status
        await self.event_bus.publish(SystemStatus(
            component="coordinator",
            status="stopped",
            message="Coordinator stopped"
        ))
        
        logger.info("Coordinator stopped")
        
    async def run_primary_scan(self) -> List[TradePlan]:
        """Run the primary market scan (14:00 CET).
        
        Returns:
            List of approved trade plans
        """
        logger.info("Starting primary market scan")
        
        result = await self._execute_scan("primary")
        
        # Return top trades
        return result.top_trades
        
    async def run_second_look_scan(self) -> List[TradePlan]:
        """Run the second-look scan (18:15 CET).
        
        Returns:
            List of approved trade plans
        """
        logger.info("Starting second-look market scan")
        
        result = await self._execute_scan("second_look")
        
        # Return top trades
        return result.top_trades
        
    async def _handle_scan_request(self, event: ScanRequest):
        """Handle scan request event.
        
        Args:
            event: Scan request event
        """
        logger.info(f"Received scan request: {event.scan_type}")
        
        # Cancel current scan if running
        if self._current_scan and not self._current_scan.done():
            logger.warning("Cancelling current scan for new request")
            self._current_scan.cancel()
            
        # Start new scan
        self._current_scan = asyncio.create_task(
            self._execute_scan(event.scan_type, event.universe)
        )
        
    async def _execute_scan(
        self,
        scan_type: str,
        specific_universe: Optional[List[str]] = None
    ) -> ScanResult:
        """Execute a complete market scan.
        
        Args:
            scan_type: Type of scan to execute
            specific_universe: Optional specific symbols to scan
            
        Returns:
            Scan result with statistics and top trades
        """
        start_time = time.time()
        errors = []
        
        # Initialize result
        result = ScanResult(
            scan_type=scan_type,
            timestamp=datetime.now(),
            total_symbols=0,
            gaps_found=0,
            candidates_scored=0,
            trades_planned=0,
            trades_approved=0,
            execution_time=0,
            top_trades=[],
            errors=errors
        )
        
        try:
            # Emit scan started event
            await self.event_bus.publish(SystemStatus(
                component="coordinator",
                status="scan_started",
                message=f"Started {scan_type} scan",
                metrics={"scan_type": scan_type}
            ))
            
            # Step 1: Load universe
            logger.info("Loading trading universe...")
            if specific_universe:
                universe = specific_universe
            else:
                universe = await self.universe_manager.get_active_symbols()
            result.total_symbols = len(universe)
            logger.info(f"Loaded {len(universe)} symbols")
            
            # Step 2: Scan for gaps
            logger.info("Scanning for gaps...")
            gap_results = await self.gap_scanner.scan_gaps(universe)
            gaps = [r for r in gap_results if r is not None]
            result.gaps_found = len(gaps)
            logger.info(f"Found {len(gaps)} gaps")
            
            # Step 3: Score candidates
            logger.info("Scoring candidates...")
            scored_candidates = []
            
            for gap_result in gaps:
                try:
                    # Get news sentiment
                    news_sentiment = await self._get_news_sentiment(gap_result.symbol)
                    
                    # Score the candidate
                    score = self.factor_model.score_candidate(
                        gap_result,
                        news_sentiment
                    )
                    
                    scored_candidates.append((gap_result, score))
                    
                    # Emit data update
                    await self.event_bus.publish(DataUpdate(
                        symbol=gap_result.symbol,
                        data_type="score",
                        update_data={
                            "score": score.total_score,
                            "factors": score.factor_scores
                        }
                    ))
                    
                except Exception as e:
                    logger.error(f"Error scoring {gap_result.symbol}: {e}")
                    errors.append(f"Scoring error for {gap_result.symbol}: {str(e)}")
                    
            result.candidates_scored = len(scored_candidates)
            
            # Step 4: Select top candidates
            top_candidates = self.factor_model.select_top_candidates(
                scored_candidates,
                max_positions=5
            )
            logger.info(f"Selected {len(top_candidates)} top candidates")
            
            # Step 5: Generate trade plans
            logger.info("Generating trade plans...")
            trade_plans = []
            
            for gap_result, score in top_candidates:
                try:
                    # Generate trade plan
                    trade_plan = self.trade_planner.plan_trade(
                        gap_result,
                        score,
                        account_balance=10000.0  # TODO: Get from settings
                    )
                    
                    if trade_plan:
                        trade_plans.append(trade_plan)
                        
                except Exception as e:
                    logger.error(f"Error planning trade for {gap_result.symbol}: {e}")
                    errors.append(f"Trade planning error for {gap_result.symbol}: {str(e)}")
                    
            result.trades_planned = len(trade_plans)
            
            # Step 6: Risk validation
            logger.info("Validating trades with risk manager...")
            approved_trades = []
            
            for trade_plan in trade_plans:
                try:
                    # Check if trade is allowed
                    is_allowed, rejection_reason = await self.risk_manager.check_trade(
                        trade_plan
                    )
                    
                    if is_allowed:
                        approved_trades.append(trade_plan)
                        
                        # Emit trade signal
                        await self.event_bus.publish(TradeSignal(
                            trade_plan=trade_plan,
                            score=next(
                                s.total_score for g, s in top_candidates
                                if g.symbol == trade_plan.symbol
                            ),
                            factors=next(
                                s.factor_scores for g, s in top_candidates
                                if g.symbol == trade_plan.symbol
                            )
                        ))
                    else:
                        # Emit risk alert
                        await self.event_bus.publish(RiskAlert(
                            alert_type="trade_rejected",
                            severity="warning",
                            message=f"Trade rejected: {rejection_reason}",
                            affected_symbols=[trade_plan.symbol]
                        ))
                        
                except Exception as e:
                    logger.error(f"Error validating trade for {trade_plan.symbol}: {e}")
                    errors.append(f"Risk validation error for {trade_plan.symbol}: {str(e)}")
                    
            result.trades_approved = len(approved_trades)
            result.top_trades = approved_trades
            
            # Calculate execution time
            result.execution_time = time.time() - start_time
            
            # Emit scan completed event
            await self.event_bus.publish(SystemStatus(
                component="coordinator",
                status="scan_completed",
                message=f"Completed {scan_type} scan",
                metrics={
                    "scan_type": scan_type,
                    "total_symbols": result.total_symbols,
                    "gaps_found": result.gaps_found,
                    "candidates_scored": result.candidates_scored,
                    "trades_planned": result.trades_planned,
                    "trades_approved": result.trades_approved,
                    "execution_time": result.execution_time,
                    "errors": len(errors)
                }
            ))
            
            logger.info(
                f"Scan completed in {result.execution_time:.2f}s: "
                f"{result.trades_approved}/{result.trades_planned} trades approved"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Scan failed: {e}")
            errors.append(f"Scan failed: {str(e)}")
            
            # Emit error event
            await self.event_bus.publish(ErrorEvent(
                error_type="scan_error",
                error_message=str(e),
                component="coordinator",
                recoverable=True
            ))
            
            result.errors = errors
            result.execution_time = time.time() - start_time
            return result
            
    async def _get_news_sentiment(self, symbol: str) -> Dict[str, Any]:
        """Get news sentiment for a symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            News sentiment data
        """
        try:
            news_data = await self.news_manager.get_sentiment(symbol)
            return news_data
        except Exception as e:
            logger.error(f"Error getting news sentiment for {symbol}: {e}")
            return {
                "sentiment_score": 0.0,
                "article_count": 0,
                "has_catalyst": False
            }
            
    async def _initialize_components(self):
        """Initialize all components."""
        try:
            # Load universe
            symbols = await self.universe_manager.get_active_symbols()
            logger.info(f"Universe loaded with {len(symbols)} symbols")
            
            # Risk manager loads state on init
            
        except Exception as e:
            logger.error(f"Component initialization error: {e}")
            # Continue anyway - components can initialize lazily
            
    def get_status(self) -> Dict[str, Any]:
        """Get coordinator status.
        
        Returns:
            Status dictionary
        """
        return {
            "running": self._running,
            "scan_active": self._current_scan is not None and not self._current_scan.done(),
            "components": {
                "universe_manager": "ready",
                "gap_scanner": "ready",
                "factor_model": "ready",
                "trade_planner": "ready",
                "risk_manager": "ready"
            }
        }