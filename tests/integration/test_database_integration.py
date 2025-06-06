"""Integration tests for database functionality."""

import pytest
import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import threading
import time

from src.persistence.journal import TradeJournal
from src.persistence.metrics import PerformanceMetrics, MetricsCollector
from src.domain.planner import TradePlan, EntryStrategy, ExitStrategy


class TestDatabaseIntegration:
    """Test database integration scenarios."""

    @pytest.fixture
    def trade_journal(self, tmp_path):
        """Create a trade journal instance."""
        db_path = tmp_path / "test_trades.db"
        return TradeJournal(str(db_path))

    @pytest.fixture
    def metrics_collector(self, tmp_path):
        """Create a metrics collector instance."""
        db_path = tmp_path / "test_metrics.db"
        return MetricsCollector(str(db_path))

    @pytest.fixture
    def sample_trades(self):
        """Generate sample trade plans."""
        trades = []
        for i in range(10):
            trade = TradePlan(
                symbol=f"TEST{i % 3}",
                score=70.0 + i * 2,
                direction="long",
                entry_strategy=EntryStrategy.VWAP,
                entry_price=100.0 + i,
                stop_loss=95.0 + i,
                stop_loss_percent=5.0,
                target_price=110.0 + i,
                target_percent=10.0,
                exit_strategy=ExitStrategy.FIXED_TARGET,
                position_size_eur=250.0,
                position_size_shares=2,
                max_risk_eur=10.0,
                risk_reward_ratio=2.0
            )
            trades.append(trade)
        return trades

    def test_database_initialization(self, trade_journal, tmp_path):
        """Test database initialization and schema creation."""
        db_path = tmp_path / "test_trades.db"
        
        # Verify database file exists
        assert db_path.exists()
        
        # Verify schema
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        assert "trades" in tables
        
        # Check trades table columns
        cursor.execute("PRAGMA table_info(trades)")
        columns = [row[1] for row in cursor.fetchall()]
        
        expected_columns = [
            "id", "timestamp", "symbol", "direction", "entry_price",
            "stop_loss", "target_price", "position_size_eur",
            "position_size_shares", "score", "factors"
        ]
        
        for col in expected_columns:
            assert col in columns
        
        conn.close()

    def test_concurrent_writes(self, trade_journal, sample_trades):
        """Test concurrent write operations."""
        def write_trades(journal, trades, thread_id):
            for i, trade in enumerate(trades):
                # Modify symbol to include thread ID
                trade.symbol = f"{trade.symbol}_T{thread_id}"
                # Add sample factors for the trade
                factors = {
                    "gap": 0.8,
                    "volume": 0.7,
                    "momentum": 0.6,
                    "volatility": 0.5,
                    "news": 0.7
                }
                journal.record_trade(trade, factors)
                time.sleep(0.01)  # Small delay to increase concurrency
        
        # Split trades among threads
        thread_count = 4
        trades_per_thread = len(sample_trades) // thread_count
        
        threads = []
        for i in range(thread_count):
            start_idx = i * trades_per_thread
            # For the last thread, include any remaining trades
            if i == thread_count - 1:
                end_idx = len(sample_trades)
            else:
                end_idx = start_idx + trades_per_thread
            thread_trades = sample_trades[start_idx:end_idx]
            
            thread = threading.Thread(
                target=write_trades,
                args=(trade_journal, thread_trades, i)
            )
            threads.append(thread)
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # Verify all trades recorded
        all_trades = trade_journal.get_recent_trades(limit=100)
        assert len(all_trades) == len(sample_trades)
        
        # Verify data integrity
        thread_symbols = set()
        for trade in all_trades:
            thread_symbols.add(trade["symbol"])
        
        # Should have trades from all threads
        # Thread distribution: T0=2, T1=2, T2=2, T3=4 (includes remainder)
        assert len([s for s in thread_symbols if "_T0" in s]) >= trades_per_thread
        assert len([s for s in thread_symbols if "_T1" in s]) >= trades_per_thread
        assert len([s for s in thread_symbols if "_T2" in s]) >= trades_per_thread
        assert len([s for s in thread_symbols if "_T3" in s]) >= trades_per_thread

    def test_transaction_rollback(self, trade_journal):
        """Test transaction rollback on error."""
        # Create a trade that will cause an error
        invalid_trade = TradePlan(
            symbol="TEST",
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
            position_size_shares=2,
            max_risk_eur=10.0,
            risk_reward_ratio=2.0
        )
        
        # Record valid trade
        factors = {"gap": 0.8, "volume": 0.7, "momentum": 0.6, "volatility": 0.5, "news": 0.7}
        trade_journal.record_trade(invalid_trade, factors)
        
        # Get initial count
        initial_count = len(trade_journal.get_recent_trades())
        
        # Try to record with simulated error
        with pytest.raises(Exception):
            # Force an error by closing the connection
            trade_journal._conn.close()
            trade_journal.record_trade(invalid_trade, factors)
        
        # Reopen connection
        trade_journal._conn = sqlite3.connect(trade_journal.db_path)
        
        # Count should remain the same (rollback)
        final_count = len(trade_journal.get_recent_trades())
        assert final_count == initial_count

    def test_performance_metrics_aggregation(self, trade_journal, metrics_collector):
        """Test performance metrics calculation and storage."""
        # Record trades with different outcomes
        trades = [
            # Winning trades (with 2 shares each, entry at 100)
            {"symbol": "AAPL", "pnl": 20.0, "pnl_percent": 10.0},   # (110-100)*2 = 20
            {"symbol": "GOOGL", "pnl": 12.0, "pnl_percent": 6.0},   # (106-100)*2 = 12
            {"symbol": "MSFT", "pnl": 16.0, "pnl_percent": 8.0},    # (108-100)*2 = 16
            # Losing trades
            {"symbol": "TSLA", "pnl": -8.0, "pnl_percent": -4.0},   # (96-100)*2 = -8
            {"symbol": "AMZN", "pnl": -4.0, "pnl_percent": -2.0},   # (98-100)*2 = -4
        ]
        
        # Record trades
        for trade_data in trades:
            trade = TradePlan(
                symbol=trade_data["symbol"],
                score=75.0,
                direction="long",
                entry_strategy=EntryStrategy.VWAP,
                entry_price=100.0,
                stop_loss=95.0,
                stop_loss_percent=5.0,
                target_price=110.0,
                target_percent=10.0,
                exit_strategy=ExitStrategy.FIXED_TARGET,
                position_size_eur=250.0,
                position_size_shares=2,
                max_risk_eur=10.0,
                risk_reward_ratio=2.0
            )
            # Add sample factors for the trade
            factors = {"gap": 0.8, "volume": 0.7, "momentum": 0.6, "volatility": 0.5, "news": 0.7}
            trade_id = trade_journal.record_trade(trade, factors)
            
            # First execute the trade
            trade_journal.update_execution(
                trade_id,
                actual_entry_price=100.0,
                actual_entry_time=datetime.now()
            )
            
            # Then close the trade with exit details
            trade_journal.close_trade(
                trade_id,
                actual_exit_price=100.0 * (1 + trade_data["pnl_percent"] / 100),
                actual_exit_time=datetime.now()
            )
        
        # Calculate metrics
        metrics = trade_journal.get_performance_summary()
        
        assert metrics["total_trades"] == 5
        assert metrics["winning_trades"] == 3
        assert metrics["losing_trades"] == 2
        assert metrics["win_rate"] == 0.6  # 60%
        assert abs(metrics["total_pnl"] - 36.0) < 0.01  # 48 - 12 = 36
        assert abs(metrics["average_win"] - 16.0) < 0.01  # 48 / 3 = 16
        assert abs(metrics["average_loss"] - (-6.0)) < 0.01  # -12 / 2 = -6

    def test_database_query_performance(self, trade_journal, sample_trades):
        """Test query performance with large dataset."""
        # Insert many trades
        for i in range(1000):
            trade = sample_trades[i % len(sample_trades)]
            trade.symbol = f"TEST{i:04d}"
            # Add sample factors for the trade
            factors = {"gap": 0.8, "volume": 0.7, "momentum": 0.6, "volatility": 0.5, "news": 0.7}
            trade_journal.record_trade(trade, factors)
        
        # Test various query patterns
        start_time = time.time()
        
        # Recent trades query
        recent = trade_journal.get_recent_trades(limit=100)
        assert len(recent) == 100
        
        # Date range query
        date_range = trade_journal.get_trades_by_date_range(
            start_date=datetime.now() - timedelta(hours=1),
            end_date=datetime.now()
        )
        assert len(date_range) == 1000
        
        # Performance summary
        summary = trade_journal.get_performance_summary()
        assert summary["total_trades"] == 1000
        
        query_time = time.time() - start_time
        
        # Queries should be fast even with 1000 records
        assert query_time < 1.0  # Less than 1 second

    def test_database_backup_and_restore(self, trade_journal, sample_trades, tmp_path):
        """Test database backup and restore functionality."""
        # Record some trades
        for trade in sample_trades:
            # Add sample factors for the trade
            factors = {"gap": 0.8, "volume": 0.7, "momentum": 0.6, "volatility": 0.5, "news": 0.7}
            trade_journal.record_trade(trade, factors)
        
        # Get current state
        original_trades = trade_journal.get_recent_trades()
        
        # Create backup
        backup_path = tmp_path / "backup_trades.db"
        import shutil
        shutil.copy2(trade_journal.db_path, backup_path)
        
        # Simulate database corruption by deleting it
        Path(trade_journal.db_path).unlink()
        
        # Restore from backup
        shutil.copy2(backup_path, trade_journal.db_path)
        
        # Reinitialize journal
        restored_journal = TradeJournal(str(trade_journal.db_path))
        
        # Verify data restored
        restored_trades = restored_journal.get_recent_trades()
        assert len(restored_trades) == len(original_trades)

    def test_metrics_time_series(self, metrics_collector):
        """Test time series metrics collection."""
        # Record metrics over time
        base_time = datetime.now()
        
        for hour in range(24):
            timestamp = base_time - timedelta(hours=23-hour)
            
            metrics = {
                "scan_duration": 5.0 + hour * 0.1,
                "api_calls": 100 + hour * 10,
                "cache_hits": 50 + hour * 5,
                "error_count": hour % 3  # Some errors
            }
            
            for metric_name, value in metrics.items():
                metrics_collector.record_metric(
                    metric_name, 
                    value,
                    {"source": "test"},
                    timestamp
                )
        
        # Query time series
        scan_metrics = metrics_collector.get_metric_series(
            "scan_duration",
            start_time=base_time - timedelta(days=1)
        )
        
        assert len(scan_metrics) == 24
        # Verify ordering (newest first)
        assert scan_metrics[0]["value"] > scan_metrics[-1]["value"]

    def test_database_connection_pooling(self, tmp_path):
        """Test connection pooling under load."""
        db_path = tmp_path / "pool_test.db"
        
        # Create multiple journal instances
        journals = [TradeJournal(str(db_path)) for _ in range(5)]
        
        # Perform concurrent operations
        def perform_operations(journal, journal_id):
            for i in range(20):
                if i % 2 == 0:
                    # Write operation
                    trade = TradePlan(
                        symbol=f"TEST_{journal_id}_{i}",
                        score=75.0,
                        direction="long",
                        entry_strategy=EntryStrategy.VWAP,
                        entry_price=100.0,
                        stop_loss=95.0,
                        stop_loss_percent=5.0,
                        target_price=110.0,
                        target_percent=10.0,
                        exit_strategy=ExitStrategy.FIXED_TARGET,
                        position_size_eur=250.0,
                        position_size_shares=2,
                        max_risk_eur=10.0,
                        risk_reward_ratio=2.0
                    )
                    # Add sample factors for the trade
                    factors = {
                        "gap": 0.8,
                        "volume": 0.7,
                        "momentum": 0.6,
                        "volatility": 0.5,
                        "news": 0.7
                    }
                    journal.record_trade(trade, factors)
                else:
                    # Read operation
                    journal.get_recent_trades(limit=10)
                
                time.sleep(0.01)  # Small delay
        
        # Run operations in threads
        threads = []
        for i, journal in enumerate(journals):
            thread = threading.Thread(
                target=perform_operations,
                args=(journal, i)
            )
            threads.append(thread)
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # Verify all operations completed
        total_trades = journals[0].get_recent_trades(limit=100)
        assert len(total_trades) == 50  # 5 journals * 10 writes each

    def test_database_migration(self, tmp_path):
        """Test database creation from scratch."""
        db_path = tmp_path / "new_db.db"
        
        # Initialize journal (should create new database)
        journal = TradeJournal(str(db_path))
        
        # Verify schema exists
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(trades)")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Should have all expected columns
        expected_columns = [
            "id", "timestamp", "symbol", "score", "direction",
            "entry_strategy", "entry_price", "stop_loss", "stop_loss_percent",
            "target_price", "target_percent", "position_size_eur",
            "position_size_shares", "max_risk_eur", "risk_reward_ratio",
            "win_probability", "factors", "notes", "created_at"
        ]
        
        for col in expected_columns:
            assert col in columns
        
        # Test inserting a new trade
        trade = TradePlan(
            symbol="TEST",
            score=75.0,
            direction="long",
            entry_strategy=EntryStrategy.VWAP,
            entry_price=100.0,
            stop_loss=95.0,
            stop_loss_percent=5.0,
            target_price=110.0,
            target_percent=10.0,
            exit_strategy=ExitStrategy.FIXED_TARGET,
            position_size_eur=250.0,
            position_size_shares=2,
            max_risk_eur=10.0,
            risk_reward_ratio=2.0
        )
        factors = {"gap": 0.8, "volume": 0.7}
        trade_id = journal.record_trade(trade, factors)
        
        # Verify trade was inserted
        cursor.execute("SELECT COUNT(*) FROM trades")
        count = cursor.fetchone()[0]
        assert count == 1
        
        conn.close()