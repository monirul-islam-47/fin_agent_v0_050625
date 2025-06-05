"""Unit tests for quota usage logging functionality."""

import pytest
import tempfile
import os
import csv
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

from src.utils.quota import QuotaGuard, QuotaInfo, QuotaPeriod, rate_limit


class TestQuotaLogging:
    """Test quota usage logging functionality."""
    
    @pytest.fixture
    def temp_files(self):
        """Create temporary files for quota state and usage log."""
        fd1, state_path = tempfile.mkstemp(suffix='.json')
        fd2, log_path = tempfile.mkstemp(suffix='.csv')
        os.close(fd1)
        os.close(fd2)
        yield state_path, log_path
        os.unlink(state_path)
        os.unlink(log_path)
        
    @pytest.fixture
    def quota_guard(self, temp_files):
        """Create quota guard with temporary files."""
        state_path, log_path = temp_files
        return QuotaGuard(
            quota_file=Path(state_path),
            usage_log_file=Path(log_path)
        )
        
    @pytest.mark.asyncio
    async def test_usage_logging(self, quota_guard):
        """Test that API usage is logged to CSV."""
        # Consume some quota
        await quota_guard.consume_quota("finnhub", 5, "get_quote")
        await quota_guard.consume_quota("newsapi", 2, "search_news")
        
        # Verify CSV log exists and has correct entries
        with open(quota_guard.usage_log_file, 'r') as f:
            reader = csv.DictReader(f)
            logs = list(reader)
            
        assert len(logs) == 2
        
        # Check first entry
        assert logs[0]['provider'] == 'finnhub'
        assert logs[0]['endpoint'] == 'get_quote'
        assert logs[0]['count'] == '5'
        assert logs[0]['success'] == 'True'
        
        # Check second entry
        assert logs[1]['provider'] == 'newsapi'
        assert logs[1]['endpoint'] == 'search_news'
        assert logs[1]['count'] == '2'
        
    @pytest.mark.asyncio
    async def test_failed_quota_logging(self, quota_guard):
        """Test that failed quota attempts are logged."""
        # Exhaust quota
        quota_guard.quotas['finnhub'].used = 60  # At limit
        
        # Try to consume more
        with pytest.raises(Exception):  # QuotaExhausted
            await quota_guard.consume_quota("finnhub", 1, "test_endpoint")
            
        # Check that failure was logged
        with open(quota_guard.usage_log_file, 'r') as f:
            reader = csv.DictReader(f)
            logs = list(reader)
            
        assert len(logs) == 1
        assert logs[0]['success'] == 'False'
        assert logs[0]['error_message'] == 'Quota exhausted'
        
    def test_usage_summary(self, quota_guard):
        """Test usage summary calculation."""
        # Add some test data to the log
        with open(quota_guard.usage_log_file, 'a', newline='') as f:
            writer = csv.writer(f)
            # Add entries for today
            today = datetime.now()
            writer.writerow([
                today.isoformat(), 'finnhub', 'quote', 10,
                0, 10, 60, 16.67, 'minute', True, ''
            ])
            writer.writerow([
                today.isoformat(), 'newsapi', 'search', 5,
                0, 5, 1000, 0.5, 'day', True, ''
            ])
            # Add entry for yesterday (should be excluded)
            yesterday = today - timedelta(days=2)
            writer.writerow([
                yesterday.isoformat(), 'finnhub', 'quote', 20,
                10, 30, 60, 50.0, 'minute', True, ''
            ])
            
        # Get summary for last day
        summary = quota_guard.get_usage_summary(days=1)
        
        assert summary['total_calls'] == 15  # 10 + 5
        assert summary['by_provider']['finnhub']['total_calls'] == 10
        assert summary['by_provider']['newsapi']['total_calls'] == 5
        
    def test_daily_export(self, quota_guard, tmp_path):
        """Test daily summary export."""
        # Add test data
        today = datetime.now()
        with open(quota_guard.usage_log_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                today.isoformat(), 'finnhub', 'quote', 30,
                0, 30, 60, 50.0, 'minute', True, ''
            ])
            writer.writerow([
                today.isoformat(), 'finnhub', 'news', 10,
                30, 40, 60, 66.67, 'minute', True, ''
            ])
            writer.writerow([
                today.isoformat(), 'newsapi', 'search', 100,
                0, 100, 1000, 10.0, 'day', True, ''
            ])
            
        # Export daily summary
        quota_guard.config.system.logs_dir = tmp_path
        summary_path = quota_guard.export_daily_summary()
        
        assert summary_path is not None
        assert summary_path.exists()
        
        # Verify summary content
        with open(summary_path, 'r') as f:
            content = f.read()
            
        assert 'finnhub' in content
        assert '40' in content  # Total calls for finnhub
        assert 'quote(30)' in content  # Top endpoint
        
    @pytest.mark.asyncio
    async def test_rate_limit_decorator_logging(self, quota_guard, monkeypatch):
        """Test that rate_limit decorator logs usage."""
        # Monkeypatch the get_quota_guard function to return our test instance
        import src.utils.quota
        monkeypatch.setattr(src.utils.quota, '_quota_guard', quota_guard)
        
        # Create a test function with rate limiting
        @rate_limit("finnhub", count=3, endpoint="test_function")
        async def test_api_call():
            return "success"
            
        # Call the function
        result = await test_api_call()
        assert result == "success"
        
        # Verify usage was logged
        with open(quota_guard.usage_log_file, 'r') as f:
            reader = csv.DictReader(f)
            logs = list(reader)
            
        assert len(logs) == 1
        assert logs[0]['provider'] == 'finnhub'
        assert logs[0]['endpoint'] == 'test_function'
        assert logs[0]['count'] == '3'
        
    def test_quota_with_endpoint_tracking(self, quota_guard):
        """Test that different endpoints are tracked separately."""
        # Run multiple calls with different endpoints
        loop = asyncio.get_event_loop()
        loop.run_until_complete(quota_guard.consume_quota("finnhub", 1, "quote"))
        loop.run_until_complete(quota_guard.consume_quota("finnhub", 2, "news"))
        loop.run_until_complete(quota_guard.consume_quota("finnhub", 1, "quote"))
        
        # Get usage summary
        summary = quota_guard.get_usage_summary(days=1)
        
        # Verify total
        assert summary['by_provider']['finnhub']['total_calls'] == 4
        
        # Check log for endpoint details
        with open(quota_guard.usage_log_file, 'r') as f:
            reader = csv.DictReader(f)
            logs = list(reader)
            
        endpoints = [log['endpoint'] for log in logs]
        assert endpoints.count('quote') == 2
        assert endpoints.count('news') == 1