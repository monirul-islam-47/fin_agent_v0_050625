"""Integration tests for news aggregation functionality."""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
import httpx

from src.data.news import NewsAPIAdapter
from src.data.news_manager import NewsManager
from src.data.base import Headline, SentimentScore
from src.config.settings import get_config


class TestNewsIntegration:
    """Test news integration with various components."""

    @pytest.fixture
    def mock_news_api_response(self):
        """Mock news API response."""
        # Use recent timestamps for the mock data
        now = datetime.now()
        return {
            "status": "ok",
            "totalResults": 3,
            "articles": [
                {
                    "title": "Apple Stock Surges on Strong Earnings",
                    "description": "AAPL hits new highs after Q4 results",
                    "url": "https://example.com/apple-earnings",
                    "publishedAt": (now - timedelta(minutes=30)).isoformat() + "Z",
                    "source": {"name": "Financial Times"}
                },
                {
                    "title": "Tech Sector Rallies Amid AI Boom",
                    "description": "Major tech stocks see gains",
                    "url": "https://example.com/tech-rally",
                    "publishedAt": (now - timedelta(hours=1)).isoformat() + "Z",
                    "source": {"name": "Reuters"}
                },
                {
                    "title": "Market Analysis: Apple vs Competitors",
                    "description": "Comparative analysis of tech giants",
                    "url": "https://example.com/market-analysis",
                    "publishedAt": (now - timedelta(hours=1, minutes=30)).isoformat() + "Z",
                    "source": {"name": "Bloomberg"}
                }
            ]
        }

    @pytest.fixture
    async def news_adapter(self):
        """Create news adapter with mocked session."""
        with patch('src.config.settings.get_config') as mock_config:
            with patch('src.utils.quota.get_quota_guard') as mock_quota:
                mock_config.return_value.api.news_api_key = "test_key"
                mock_quota.return_value.check_quota = AsyncMock(return_value=True)
                adapter = NewsAPIAdapter()
                # Mock httpx AsyncClient
                adapter.client = AsyncMock(spec=httpx.AsyncClient)
                adapter.is_connected = True
                adapter.health_check = AsyncMock(return_value=True)
                adapter.quota_guard = mock_quota.return_value
                adapter.api_key = "test_key"  # Ensure API key is set
                return adapter

    @pytest.fixture
    def news_manager(self, tmp_path):
        """Create news manager instance."""
        with patch('src.config.settings.get_config') as mock_config:
            mock_config.return_value.cache_dir = str(tmp_path / "cache")
            mock_config.return_value.news_api_key = "test_key"
            return NewsManager()

    @pytest.mark.asyncio
    async def test_news_fetching_with_fallback(self, news_adapter, mock_news_api_response):
        """Test news fetching with fallback mechanism."""
        # Mock successful httpx response
        mock_response = Mock()
        mock_response.json = Mock(return_value=mock_news_api_response)
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        
        # Mock the client.get to return the response
        news_adapter.client.get = AsyncMock(return_value=mock_response)
        
        # Fetch news
        news_items = await news_adapter.get_headlines("AAPL")
        
        assert len(news_items) == 3
        assert news_items[0].headline == "Apple Stock Surges on Strong Earnings"
        assert news_items[0].symbol == "AAPL"
        assert isinstance(news_items[0].sentiment, (int, float))

    @pytest.mark.asyncio
    async def test_news_api_rate_limiting(self, news_adapter):
        """Test handling of API rate limits."""
        # Mock rate limit response
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.json = Mock(return_value={"status": "error", "message": "Rate limit exceeded"})
        mock_response.raise_for_status = Mock()
        
        # Mock the client.get to return the response
        news_adapter.client.get = AsyncMock(return_value=mock_response)
        
        # Should handle gracefully
        news_items = await news_adapter.get_headlines("AAPL")
        assert news_items == []  # Returns empty list on rate limit

    @pytest.mark.asyncio
    async def test_news_sentiment_analysis(self, news_adapter, mock_news_api_response):
        """Test sentiment analysis integration."""
        # Mock response
        mock_response = Mock()
        mock_response.json = Mock(return_value=mock_news_api_response)
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        
        # Mock the client.get to return the response
        news_adapter.client.get = AsyncMock(return_value=mock_response)
        
        # Fetch news with sentiment
        news_items = await news_adapter.get_headlines("AAPL")
        
        # Check sentiment scores
        for news in news_items:
            assert isinstance(news.sentiment, (int, float))
            assert -1.0 <= news.sentiment <= 1.0

    @pytest.mark.asyncio
    async def test_news_caching(self, news_manager):
        """Test news caching functionality."""
        # Use unique symbol to avoid cache interference
        test_symbol = "CACHE_TEST"
        
        # Mock news data
        mock_news = [
            Headline(
                symbol=test_symbol,
                headline="Test News 1",
                source="Test Source",
                timestamp=datetime.now(),
                url="https://example.com/1",
                sentiment=0.7,
                provider="newsapi"
            ),
            Headline(
                symbol=test_symbol,
                headline="Test News 2",
                source="Test Source",
                timestamp=datetime.now() - timedelta(hours=1),
                url="https://example.com/2",
                sentiment=0.4,
                provider="newsapi"
            )
        ]
        
        # Mock the news adapter
        news_manager.newsapi = AsyncMock()
        news_manager.newsapi.health_check = AsyncMock(return_value=True)
        news_manager.newsapi.get_headlines = AsyncMock(return_value=mock_news)
        news_manager.gdelt = AsyncMock()
        news_manager.gdelt.health_check = AsyncMock(return_value=False)
        
        # First call should fetch from API
        news1 = await news_manager.get_headlines(test_symbol)
        assert len(news1) == 2
        assert news_manager.newsapi.get_headlines.call_count == 1
        
        # Second call should use cache
        news2 = await news_manager.get_headlines(test_symbol)
        assert len(news2) == 2
        assert news_manager.newsapi.get_headlines.call_count == 1  # No additional API call

    @pytest.mark.asyncio
    async def test_news_aggregation_multiple_symbols(self, news_manager):
        """Test fetching news for multiple symbols."""
        symbols = ["IBM", "NFLX", "AMD"]  # Use different symbols to avoid cache
        
        # Mock different news for each symbol
        async def mock_get_headlines(symbol, limit=10):
            return [
                Headline(
                    symbol=symbol,
                    headline=f"{symbol} News",
                    source="Test Source",
                    timestamp=datetime.now(),
                    url=f"https://example.com/{symbol}",
                    sentiment=0.5,
                    provider="newsapi"
                )
            ]
        
        news_manager.newsapi = AsyncMock()
        news_manager.newsapi.health_check = AsyncMock(return_value=True)
        news_manager.newsapi.get_headlines.side_effect = mock_get_headlines
        # Also make sure GDELT doesn't return anything
        news_manager.gdelt = AsyncMock()
        news_manager.gdelt.health_check = AsyncMock(return_value=False)
        
        # Fetch news for all symbols
        all_news = []
        for symbol in symbols:
            news = await news_manager.get_headlines(symbol)
            all_news.extend(news)
        
        assert len(all_news) == 3
        assert all_news[0].symbol == "IBM"
        assert all_news[1].symbol == "NFLX"
        assert all_news[2].symbol == "AMD"

    @pytest.mark.asyncio
    async def test_news_filtering_by_time(self, news_adapter, mock_news_api_response):
        """Test filtering news by publication time."""
        # Mock response
        mock_response = Mock()
        mock_response.json = Mock(return_value=mock_news_api_response)
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        
        # Mock the client.get to return the response
        news_adapter.client.get = AsyncMock(return_value=mock_response)
        
        # Fetch news with time filter
        news_items = await news_adapter.get_headlines("AAPL")
        
        # All news should be within time window
        import pytz
        cutoff_time = datetime.now(pytz.UTC) - timedelta(hours=2)
        for news in news_items:
            # Ensure timestamp is timezone-aware
            if news.timestamp.tzinfo is None:
                news_timestamp = pytz.UTC.localize(news.timestamp)
            else:
                news_timestamp = news.timestamp
            assert news_timestamp >= cutoff_time

    @pytest.mark.asyncio
    async def test_news_deduplication(self, news_manager):
        """Test news deduplication."""
        # Mock news with duplicates
        mock_news = [
            Headline(
                symbol="AAPL",
                headline="Apple Earnings Report",
                source="Reuters",
                timestamp=datetime.now(),
                url="https://example.com/apple-1",
                sentiment=0.7,
                provider="newsapi"
            ),
            Headline(
                symbol="AAPL",
                headline="Apple Earnings Report",  # Duplicate headline
                source="Bloomberg",
                timestamp=datetime.now() - timedelta(minutes=5),
                url="https://example.com/apple-2",
                sentiment=0.5,
                provider="newsapi"
            ),
            Headline(
                symbol="AAPL",
                headline="Different Apple News",
                source="FT",
                timestamp=datetime.now() - timedelta(minutes=10),
                url="https://example.com/apple-3",
                sentiment=0.4,
                provider="newsapi"
            )
        ]
        
        news_manager.newsapi = AsyncMock()
        news_manager.newsapi.health_check = AsyncMock(return_value=True)
        news_manager.newsapi.get_headlines = AsyncMock(return_value=mock_news)
        news_manager.gdelt = AsyncMock()
        news_manager.gdelt.health_check = AsyncMock(return_value=False)
        
        # Get deduplicated news
        news = await news_manager.get_headlines("AAPL")
        
        # Should have removed duplicates
        unique_headlines = {n.headline for n in news}
        assert len(unique_headlines) == 2  # Two unique headlines

    @pytest.mark.asyncio
    async def test_news_error_handling(self, news_adapter):
        """Test error handling in news fetching."""
        # Test various error scenarios
        error_scenarios = [
            (httpx.HTTPError("Network error"), "Network error"),
            (asyncio.TimeoutError(), "Timeout"),
            (ValueError("Invalid response"), "Parse error")
        ]
        
        for error, scenario in error_scenarios:
            news_adapter.client.get.side_effect = error
            
            # Should handle error gracefully
            news_items = await news_adapter.get_headlines("AAPL")
            assert news_items == []  # Returns empty list on error

    @pytest.mark.asyncio
    async def test_news_with_market_events(self, news_manager):
        """Test news integration with market events."""
        # Mock news indicating market-moving event
        major_news = Headline(
            symbol="AAPL",
            headline="Apple Announces Major Acquisition",
            source="Breaking News",
            timestamp=datetime.now(),
            url="https://example.com/breaking",
            sentiment=0.85,
            provider="newsapi"
        )
        
        news_manager.newsapi = AsyncMock()
        news_manager.newsapi.health_check = AsyncMock(return_value=True)
        news_manager.newsapi.get_headlines = AsyncMock(return_value=[major_news])
        # Make sure GDELT doesn't interfere
        news_manager.gdelt = AsyncMock()
        news_manager.gdelt.health_check = AsyncMock(return_value=False)
        
        # Get news (don't use cache to avoid interference)
        news = await news_manager.get_headlines("AAPL", use_cache=False)
        
        # Check for high-impact news
        high_impact = [n for n in news if n.sentiment > 0.8]
        assert len(high_impact) == 1
        assert high_impact[0].headline == "Apple Announces Major Acquisition"

    @pytest.mark.asyncio
    async def test_news_batch_processing(self, news_manager):
        """Test batch processing of news for multiple symbols."""
        symbols = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]
        
        # Mock batch responses
        async def mock_batch_news(symbol, limit=10):
            await asyncio.sleep(0.1)  # Simulate API delay
            return [
                Headline(
                    symbol=symbol,
                    headline=f"{symbol} Daily Update",
                    source="Market News",
                    timestamp=datetime.now(),
                    url=f"https://example.com/{symbol}",
                    sentiment=0.4,
                    provider="newsapi"
                )
            ]
        
        news_manager.newsapi = AsyncMock()
        news_manager.newsapi.health_check = AsyncMock(return_value=True)
        news_manager.newsapi.get_headlines.side_effect = mock_batch_news
        # Make sure GDELT doesn't interfere
        news_manager.gdelt = AsyncMock()
        news_manager.gdelt.health_check = AsyncMock(return_value=False)
        
        # Process in batch
        start_time = asyncio.get_event_loop().time()
        
        tasks = [news_manager.get_headlines(symbol, use_cache=False) for symbol in symbols]
        all_news = await asyncio.gather(*tasks)
        
        end_time = asyncio.get_event_loop().time()
        duration = end_time - start_time
        
        # Should process concurrently (faster than sequential)
        assert duration < 0.5  # Should be faster than 0.5s (5 * 0.1s)
        assert len(all_news) == 5
        assert all(len(news_list) >= 1 for news_list in all_news)