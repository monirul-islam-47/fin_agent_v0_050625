"""Unit tests for persistence layer (journal and metrics)."""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path

from src.persistence.journal import TradeJournal
from src.persistence.metrics import PerformanceMetrics
from src.domain.planner import TradePlan, EntryStrategy, ExitStrategy
from src.orchestration.events import TradeSignal


class TestTradeJournal:
    """Test trade journal functionality."""
    
    @pytest.fixture
    def temp_db(self):
        """Create temporary database file."""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        yield path
        os.unlink(path)
        
    @pytest.fixture
    def journal(self, temp_db):
        """Create journal with temporary database."""
        return TradeJournal(db_path=temp_db)
        
    @pytest.fixture
    def sample_trade_plan(self):
        """Create sample trade plan."""
        return TradePlan(
            symbol="AAPL",
            score=75.5,
            direction="long",
            entry_strategy=EntryStrategy.VWAP,
            entry_price=150.00,
            stop_loss=145.50,
            stop_loss_percent=3.0,
            target_price=163.50,
            target_percent=9.0,
            exit_strategy=ExitStrategy.FIXED_TARGET,
            position_size_eur=250.0,
            position_size_shares=2,
            max_risk_eur=11.0,
            risk_reward_ratio=3.0,
            win_probability=0.65,
            notes=["Gap: +5.2%", "Volume: 2.5x average"]
        )
        
    def test_init_database(self, journal):
        """Test database initialization."""
        # Check that tables exist
        conn = sqlite3.connect(journal.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
        )
        assert cursor.fetchone() is not None
        conn.close()
        
    def test_record_trade(self, journal, sample_trade_plan):
        """Test recording a trade."""
        factors = {"momentum": 0.8, "sentiment": 0.6, "liquidity": 0.7}
        trade_id = journal.record_trade(sample_trade_plan, factors)
        
        assert trade_id is not None
        assert trade_id > 0
        
        # Verify trade was recorded
        trades = journal.get_recent_trades(limit=1)
        assert len(trades) == 1
        assert trades[0]['symbol'] == 'AAPL'
        assert trades[0]['score'] == 75.5
        
    def test_update_execution(self, journal, sample_trade_plan):
        """Test updating trade execution."""
        # Record trade
        trade_id = journal.record_trade(sample_trade_plan, {})
        
        # Update execution
        entry_time = datetime.now()
        journal.update_execution(trade_id, 149.50, entry_time, "executed")
        
        # Verify update
        trades = journal.get_recent_trades(limit=1)
        assert trades[0]['actual_entry_price'] == 149.50
        assert trades[0]['status'] == 'executed'
        
    def test_close_trade(self, journal, sample_trade_plan):
        """Test closing a trade with P&L calculation."""
        # Record and execute trade
        trade_id = journal.record_trade(sample_trade_plan, {})
        journal.update_execution(trade_id, 150.00, datetime.now())
        
        # Close trade with profit
        exit_time = datetime.now()
        journal.close_trade(trade_id, 160.00, exit_time)
        
        # Verify P&L
        trades = journal.get_recent_trades(limit=1)
        assert trades[0]['actual_exit_price'] == 160.00
        assert trades[0]['pnl_eur'] == 20.00  # (160-150) * 2 shares
        assert trades[0]['pnl_percent'] == pytest.approx(6.67, rel=0.01)
        assert trades[0]['status'] == 'closed'
        
    def test_get_trades_by_date_range(self, journal, sample_trade_plan):
        """Test filtering trades by date range."""
        # Record trades on different days
        yesterday = datetime.now() - timedelta(days=1)
        today = datetime.now()
        tomorrow = datetime.now() + timedelta(days=1)
        
        journal.record_trade(sample_trade_plan, {}, yesterday)
        journal.record_trade(sample_trade_plan, {}, today)
        journal.record_trade(sample_trade_plan, {}, tomorrow)
        
        # Get trades for today only
        trades = journal.get_trades_by_date_range(
            today.replace(hour=0, minute=0),
            today.replace(hour=23, minute=59)
        )
        
        assert len(trades) == 1
        
    def test_export_to_csv(self, journal, sample_trade_plan, tmp_path):
        """Test CSV export functionality."""
        # Record some trades
        for i in range(3):
            journal.record_trade(sample_trade_plan, {"test": i})
            
        # Export to CSV
        csv_path = tmp_path / "test_export.csv"
        journal.export_to_csv(str(csv_path))
        
        assert csv_path.exists()
        
        # Verify CSV content
        import csv
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
        assert len(rows) == 3
        assert 'symbol' in rows[0]
        assert rows[0]['symbol'] == 'AAPL'


class TestPerformanceMetrics:
    """Test performance metrics functionality."""
    
    @pytest.fixture
    def temp_dbs(self):
        """Create temporary database files."""
        fd1, path1 = tempfile.mkstemp(suffix='.db')
        fd2, path2 = tempfile.mkstemp(suffix='.db')
        os.close(fd1)
        os.close(fd2)
        yield path1, path2
        os.unlink(path1)
        os.unlink(path2)
        
    @pytest.fixture
    def metrics_with_trades(self, temp_dbs):
        """Create metrics with some closed trades."""
        journal_db, metrics_db = temp_dbs
        journal = TradeJournal(db_path=journal_db)
        metrics = PerformanceMetrics(journal, db_path=metrics_db)
        
        # Create and close some trades
        plans = [
            ("AAPL", 150, 160, 10),   # Win: +€20
            ("GOOGL", 100, 95, -5),    # Loss: -€10
            ("MSFT", 200, 220, 10),    # Win: +€40
        ]
        
        for symbol, entry, exit, pnl_pct in plans:
            plan = TradePlan(
                symbol=symbol,
                score=70.0,
                direction="long",
                entry_strategy=EntryStrategy.MARKET,
                entry_price=entry,
                stop_loss=entry * 0.97,
                stop_loss_percent=3.0,
                target_price=entry * 1.10,
                target_percent=10.0,
                exit_strategy=ExitStrategy.FIXED_TARGET,
                position_size_eur=200.0,
                position_size_shares=2,
                max_risk_eur=6.0,
                risk_reward_ratio=3.3
            )
            
            trade_id = journal.record_trade(plan, {})
            journal.update_execution(trade_id, entry, datetime.now())
            journal.close_trade(trade_id, exit, datetime.now())
            
        return metrics
        
    def test_calculate_daily_metrics(self, metrics_with_trades):
        """Test daily metrics calculation."""
        metrics = metrics_with_trades
        
        # Calculate today's metrics
        daily = metrics.calculate_daily_metrics(datetime.now())
        
        assert daily['total_trades'] == 3
        assert daily['winning_trades'] == 2
        assert daily['losing_trades'] == 1
        assert daily['total_pnl'] == 50.0  # 20 + 40 - 10
        assert daily['win_rate'] == pytest.approx(0.667, rel=0.01)
        
    def test_calculate_weekly_metrics(self, metrics_with_trades):
        """Test weekly metrics calculation."""
        metrics = metrics_with_trades
        
        # Calculate this week's metrics
        weekly = metrics.calculate_weekly_metrics(datetime.now())
        
        assert weekly['total_trades'] == 3
        assert weekly['winning_trades'] == 2
        assert weekly['total_pnl'] == 50.0
        
    def test_calculate_monthly_metrics(self, metrics_with_trades):
        """Test monthly metrics calculation."""
        metrics = metrics_with_trades
        
        # Calculate this month's metrics
        monthly = metrics.calculate_monthly_metrics(datetime.now())
        
        assert monthly['total_trades'] == 3
        assert monthly['winning_trades'] == 2
        assert monthly['total_pnl'] == 50.0
        
    def test_get_overall_metrics(self, metrics_with_trades):
        """Test overall metrics calculation."""
        metrics = metrics_with_trades
        
        overall = metrics.get_overall_metrics()
        
        assert overall['total_trades'] >= 3
        assert overall['closed_trades'] == 3
        assert overall['winning_trades'] == 2
        assert overall['losing_trades'] == 1
        assert overall['total_pnl'] == 50.0
        assert overall['win_rate'] == pytest.approx(0.667, rel=0.01)
        
    def test_sharpe_ratio_calculation(self):
        """Test Sharpe ratio calculation."""
        metrics = PerformanceMetrics()
        
        # Test with sample returns
        returns = [0.02, -0.01, 0.03, 0.01, -0.02, 0.04]
        sharpe = metrics._calculate_sharpe_ratio(returns)
        
        assert sharpe is not None
        assert isinstance(sharpe, float)
        
    def test_max_drawdown_calculation(self):
        """Test maximum drawdown calculation."""
        metrics = PerformanceMetrics()
        
        # Create trades with known drawdown
        trades = [
            {'timestamp': datetime.now(), 'pnl_eur': 100},
            {'timestamp': datetime.now(), 'pnl_eur': 50},
            {'timestamp': datetime.now(), 'pnl_eur': -200},  # Drawdown here
            {'timestamp': datetime.now(), 'pnl_eur': 100},
        ]
        
        max_dd = metrics._calculate_max_drawdown(trades)
        
        # From peak of 150 to low of -50 = 200/150 = 133%
        assert max_dd == pytest.approx(133.33, rel=0.01)