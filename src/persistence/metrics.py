"""Performance Metrics - Calculate and track trading performance metrics.

This module calculates various performance metrics including:
- Win rate and hit rate
- Average returns
- Sharpe ratio
- Maximum drawdown
- Daily/weekly/monthly performance
"""

import sqlite3
import json
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from src.utils.logger import setup_logger
from src.persistence.journal import TradeJournal

logger = setup_logger(__name__)


class MetricsCollector:
    """Simple metrics collector for testing."""
    
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize metrics database."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                metric_name TEXT NOT NULL,
                value REAL NOT NULL,
                tags TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    def record_metric(self, metric_name: str, value: float, tags: Dict[str, Any] = None, timestamp: datetime = None):
        """Record a metric value."""
        conn = sqlite3.connect(str(self.db_path))
        ts = timestamp or datetime.now()
        tags_json = json.dumps(tags or {})
        
        conn.execute(
            "INSERT INTO metrics (timestamp, metric_name, value, tags) VALUES (?, ?, ?, ?)",
            (ts.isoformat(), metric_name, value, tags_json)
        )
        conn.commit()
        conn.close()
    
    def get_metric_series(self, metric_name: str, start_time: datetime, end_time: datetime = None) -> List[Dict[str, Any]]:
        """Get time series data for a metric."""
        conn = sqlite3.connect(str(self.db_path))
        end_time = end_time or datetime.now()
        
        cursor = conn.execute(
            """SELECT timestamp, value, tags FROM metrics 
               WHERE metric_name = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp DESC""",
            (metric_name, start_time.isoformat(), end_time.isoformat())
        )
        
        results = []
        for row in cursor:
            results.append({
                "timestamp": datetime.fromisoformat(row[0]),
                "value": row[1],
                "tags": json.loads(row[2]) if row[2] else {}
            })
        
        conn.close()
        return results


class PerformanceMetrics:
    """Calculate and track trading performance metrics."""
    
    def __init__(self, journal: Optional[TradeJournal] = None, db_path: str = "data/metrics.db"):
        """Initialize Performance Metrics.
        
        Args:
            journal: Trade journal instance (optional, will create if not provided)
            db_path: Path to metrics database
        """
        self.journal = journal or TradeJournal()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize metrics database
        self._init_database()
        
    def _init_database(self):
        """Initialize metrics database schema."""
        conn = sqlite3.connect(str(self.db_path))
        
        # Daily metrics table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                date DATE PRIMARY KEY,
                total_trades INTEGER NOT NULL,
                winning_trades INTEGER NOT NULL,
                losing_trades INTEGER NOT NULL,
                total_pnl REAL NOT NULL,
                win_rate REAL NOT NULL,
                avg_win REAL NOT NULL,
                avg_loss REAL NOT NULL,
                largest_win REAL NOT NULL,
                largest_loss REAL NOT NULL,
                calculated_at DATETIME NOT NULL
            )
        """)
        
        # Weekly metrics table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_metrics (
                week_start DATE PRIMARY KEY,
                week_end DATE NOT NULL,
                total_trades INTEGER NOT NULL,
                winning_trades INTEGER NOT NULL,
                losing_trades INTEGER NOT NULL,
                total_pnl REAL NOT NULL,
                win_rate REAL NOT NULL,
                sharpe_ratio REAL,
                calculated_at DATETIME NOT NULL
            )
        """)
        
        # Monthly metrics table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_metrics (
                month DATE PRIMARY KEY,
                total_trades INTEGER NOT NULL,
                winning_trades INTEGER NOT NULL,
                losing_trades INTEGER NOT NULL,
                total_pnl REAL NOT NULL,
                win_rate REAL NOT NULL,
                sharpe_ratio REAL,
                max_drawdown REAL,
                calculated_at DATETIME NOT NULL
            )
        """)
        
        conn.commit()
        conn.close()
        
        logger.info(f"Performance metrics database initialized at {self.db_path}")
        
    def calculate_daily_metrics(self, date: datetime) -> Dict[str, Any]:
        """Calculate metrics for a specific day.
        
        Args:
            date: Date to calculate metrics for
            
        Returns:
            Dictionary of daily metrics
        """
        # Get trades for the day
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        trades = self.journal.get_trades_by_date_range(start_of_day, end_of_day, status='closed')
        
        if not trades:
            return {
                'date': date.date(),
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'total_pnl': 0.0,
                'win_rate': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'largest_win': 0.0,
                'largest_loss': 0.0
            }
            
        # Calculate metrics
        winning_trades = [t for t in trades if t['pnl_eur'] > 0]
        losing_trades = [t for t in trades if t['pnl_eur'] < 0]
        
        metrics = {
            'date': date.date(),
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'total_pnl': sum(t['pnl_eur'] for t in trades),
            'win_rate': len(winning_trades) / len(trades) if trades else 0.0,
            'avg_win': np.mean([t['pnl_eur'] for t in winning_trades]) if winning_trades else 0.0,
            'avg_loss': np.mean([t['pnl_eur'] for t in losing_trades]) if losing_trades else 0.0,
            'largest_win': max([t['pnl_eur'] for t in winning_trades]) if winning_trades else 0.0,
            'largest_loss': min([t['pnl_eur'] for t in losing_trades]) if losing_trades else 0.0
        }
        
        # Store in database
        self._store_daily_metrics(metrics)
        
        return metrics
        
    def calculate_weekly_metrics(self, week_start: datetime) -> Dict[str, Any]:
        """Calculate metrics for a week.
        
        Args:
            week_start: Monday of the week to calculate
            
        Returns:
            Dictionary of weekly metrics
        """
        # Ensure we start on Monday
        days_since_monday = week_start.weekday()
        week_start = week_start - timedelta(days=days_since_monday)
        week_end = week_start + timedelta(days=6)
        
        # Get trades for the week
        trades = self.journal.get_trades_by_date_range(week_start, week_end, status='closed')
        
        if not trades:
            return {
                'week_start': week_start.date(),
                'week_end': week_end.date(),
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'total_pnl': 0.0,
                'win_rate': 0.0,
                'sharpe_ratio': None
            }
            
        # Calculate basic metrics
        winning_trades = [t for t in trades if t['pnl_eur'] > 0]
        losing_trades = [t for t in trades if t['pnl_eur'] < 0]
        
        # Calculate daily returns for Sharpe ratio
        daily_returns = self._calculate_daily_returns(week_start, week_end)
        sharpe_ratio = self._calculate_sharpe_ratio(daily_returns) if len(daily_returns) >= 3 else None
        
        metrics = {
            'week_start': week_start.date(),
            'week_end': week_end.date(),
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'total_pnl': sum(t['pnl_eur'] for t in trades),
            'win_rate': len(winning_trades) / len(trades) if trades else 0.0,
            'sharpe_ratio': sharpe_ratio
        }
        
        # Store in database
        self._store_weekly_metrics(metrics)
        
        return metrics
        
    def calculate_monthly_metrics(self, month: datetime) -> Dict[str, Any]:
        """Calculate metrics for a month.
        
        Args:
            month: Any day in the target month
            
        Returns:
            Dictionary of monthly metrics
        """
        # Get first and last day of month
        first_day = month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Get last day by going to next month and subtracting a day
        if month.month == 12:
            last_day = first_day.replace(year=month.year + 1, month=1) - timedelta(days=1)
        else:
            last_day = first_day.replace(month=month.month + 1) - timedelta(days=1)
            
        last_day = last_day.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Get trades for the month
        trades = self.journal.get_trades_by_date_range(first_day, last_day, status='closed')
        
        if not trades:
            return {
                'month': first_day.date(),
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'total_pnl': 0.0,
                'win_rate': 0.0,
                'sharpe_ratio': None,
                'max_drawdown': 0.0
            }
            
        # Calculate basic metrics
        winning_trades = [t for t in trades if t['pnl_eur'] > 0]
        losing_trades = [t for t in trades if t['pnl_eur'] < 0]
        
        # Calculate daily returns for Sharpe ratio
        daily_returns = self._calculate_daily_returns(first_day, last_day)
        sharpe_ratio = self._calculate_sharpe_ratio(daily_returns) if len(daily_returns) >= 5 else None
        
        # Calculate maximum drawdown
        max_drawdown = self._calculate_max_drawdown(trades)
        
        metrics = {
            'month': first_day.date(),
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'total_pnl': sum(t['pnl_eur'] for t in trades),
            'win_rate': len(winning_trades) / len(trades) if trades else 0.0,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown
        }
        
        # Store in database
        self._store_monthly_metrics(metrics)
        
        return metrics
        
    def get_overall_metrics(self) -> Dict[str, Any]:
        """Get overall performance metrics across all time.
        
        Returns:
            Dictionary of overall metrics
        """
        summary = self.journal.get_performance_summary()
        
        # Add additional calculations
        if summary['closed_trades'] > 0:
            # Profit factor
            total_wins = sum(t['pnl_eur'] for t in self.journal.get_recent_trades(10000) 
                           if t.get('pnl_eur', 0) > 0)
            total_losses = abs(sum(t['pnl_eur'] for t in self.journal.get_recent_trades(10000) 
                                 if t.get('pnl_eur', 0) < 0))
            
            summary['profit_factor'] = total_wins / total_losses if total_losses > 0 else float('inf')
            
            # Expected value
            avg_win = total_wins / summary['winning_trades'] if summary['winning_trades'] > 0 else 0
            avg_loss = total_losses / summary['losing_trades'] if summary['losing_trades'] > 0 else 0
            
            summary['expected_value'] = (
                summary['win_rate'] * avg_win - 
                (1 - summary['win_rate']) * avg_loss
            )
            
        return summary
        
    def _calculate_daily_returns(self, start_date: datetime, end_date: datetime) -> List[float]:
        """Calculate daily returns for a date range.
        
        Args:
            start_date: Start date
            end_date: End date
            
        Returns:
            List of daily returns
        """
        current = start_date
        daily_returns = []
        
        while current <= end_date:
            if current.weekday() < 5:  # Trading days only
                day_trades = self.journal.get_trades_by_date_range(
                    current.replace(hour=0, minute=0),
                    current.replace(hour=23, minute=59),
                    status='closed'
                )
                
                if day_trades:
                    day_pnl = sum(t['pnl_eur'] for t in day_trades)
                    # Assume €500 bankroll from PRD
                    daily_return = day_pnl / 500.0
                    daily_returns.append(daily_return)
                    
            current += timedelta(days=1)
            
        return daily_returns
        
    def _calculate_sharpe_ratio(self, returns: List[float], risk_free_rate: float = 0.02) -> Optional[float]:
        """Calculate Sharpe ratio from returns.
        
        Args:
            returns: List of returns
            risk_free_rate: Annual risk-free rate (default 2%)
            
        Returns:
            Sharpe ratio or None if insufficient data
        """
        if len(returns) < 2:
            return None
            
        # Convert to numpy array
        returns = np.array(returns)
        
        # Calculate metrics
        avg_return = np.mean(returns)
        std_return = np.std(returns, ddof=1)
        
        if std_return == 0:
            return None
            
        # Annualize (assuming 252 trading days)
        annual_return = avg_return * 252
        annual_std = std_return * np.sqrt(252)
        
        # Calculate Sharpe ratio
        sharpe = (annual_return - risk_free_rate) / annual_std
        
        return sharpe
        
    def _calculate_max_drawdown(self, trades: List[Dict[str, Any]]) -> float:
        """Calculate maximum drawdown from trades.
        
        Args:
            trades: List of trades (must be sorted by time)
            
        Returns:
            Maximum drawdown percentage
        """
        if not trades:
            return 0.0
            
        # Sort by timestamp to ensure chronological order
        sorted_trades = sorted(trades, key=lambda t: t['timestamp'])
        
        # Calculate cumulative P&L
        cumulative_pnl = []
        running_total = 0.0
        
        for trade in sorted_trades:
            running_total += trade.get('pnl_eur', 0)
            cumulative_pnl.append(running_total)
            
        # Calculate drawdown
        peak = cumulative_pnl[0]
        max_drawdown = 0.0
        
        for value in cumulative_pnl:
            if value > peak:
                peak = value
            else:
                drawdown = (peak - value) / peak if peak > 0 else 0
                max_drawdown = max(max_drawdown, drawdown)
                
        return max_drawdown * 100  # Return as percentage
        
    def _store_daily_metrics(self, metrics: Dict[str, Any]):
        """Store daily metrics in database."""
        conn = sqlite3.connect(str(self.db_path))
        
        conn.execute("""
            INSERT OR REPLACE INTO daily_metrics (
                date, total_trades, winning_trades, losing_trades,
                total_pnl, win_rate, avg_win, avg_loss,
                largest_win, largest_loss, calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metrics['date'],
            metrics['total_trades'],
            metrics['winning_trades'],
            metrics['losing_trades'],
            metrics['total_pnl'],
            metrics['win_rate'],
            metrics['avg_win'],
            metrics['avg_loss'],
            metrics['largest_win'],
            metrics['largest_loss'],
            datetime.now()
        ))
        
        conn.commit()
        conn.close()
        
    def _store_weekly_metrics(self, metrics: Dict[str, Any]):
        """Store weekly metrics in database."""
        conn = sqlite3.connect(str(self.db_path))
        
        conn.execute("""
            INSERT OR REPLACE INTO weekly_metrics (
                week_start, week_end, total_trades, winning_trades,
                losing_trades, total_pnl, win_rate, sharpe_ratio,
                calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metrics['week_start'],
            metrics['week_end'],
            metrics['total_trades'],
            metrics['winning_trades'],
            metrics['losing_trades'],
            metrics['total_pnl'],
            metrics['win_rate'],
            metrics['sharpe_ratio'],
            datetime.now()
        ))
        
        conn.commit()
        conn.close()
        
    def _store_monthly_metrics(self, metrics: Dict[str, Any]):
        """Store monthly metrics in database."""
        conn = sqlite3.connect(str(self.db_path))
        
        conn.execute("""
            INSERT OR REPLACE INTO monthly_metrics (
                month, total_trades, winning_trades, losing_trades,
                total_pnl, win_rate, sharpe_ratio, max_drawdown,
                calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metrics['month'],
            metrics['total_trades'],
            metrics['winning_trades'],
            metrics['losing_trades'],
            metrics['total_pnl'],
            metrics['win_rate'],
            metrics['sharpe_ratio'],
            metrics['max_drawdown'],
            datetime.now()
        ))
        
        conn.commit()
        conn.close()
        
    def get_metrics_by_period(self, period: str = 'daily', limit: int = 30) -> List[Dict[str, Any]]:
        """Get historical metrics by period.
        
        Args:
            period: 'daily', 'weekly', or 'monthly'
            limit: Number of periods to return
            
        Returns:
            List of metrics for the period
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        
        table_name = f"{period}_metrics"
        order_field = 'date' if period == 'daily' else 'week_start' if period == 'weekly' else 'month'
        
        cursor = conn.execute(f"""
            SELECT * FROM {table_name}
            ORDER BY {order_field} DESC
            LIMIT ?
        """, (limit,))
        
        metrics = [dict(row) for row in cursor]
        conn.close()
        
        return metrics