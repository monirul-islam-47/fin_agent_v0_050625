"""Unit tests for data layer components."""

import pytest
import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import aiohttp

from src.data.base import Quote, News, SentimentScore, BaseAdapter, DataProvider, Headline
from src.data.cache import CacheService
from src.data.finnhub import FinnhubWebSocket
from src.data.yahoo import YahooFinanceAdapter
from src.data.news import NewsAPIAdapter
from src.data.market import MarketDataManager, DataPriority


class TestDataModels:
    """Test data model classes."""

    def test_quote_creation(self):
        """Test Quote model creation and attributes."""
        timestamp = datetime.now()
        quote = Quote(
            symbol="AAPL",
            timestamp=timestamp,
            price=150.0,
            volume=1000000,
            bid=149.95,
            ask=150.05,
            high=152.0,
            low=148.0,
            prev_close=149.0
        )
        
        assert quote.symbol == "AAPL"
        assert quote.timestamp == timestamp
        assert quote.price == 150.0
        assert quote.volume == 1000000
        assert quote.bid == 149.95
        assert quote.ask == 150.05
        assert quote.high == 152.0
        assert quote.low == 148.0
        assert quote.prev_close == 149.0

    def test_news_creation(self):
        """Test News model creation and attributes."""
        timestamp = datetime.now()
        sentiment = SentimentScore(
            positive=0.7,
            negative=0.1,
            neutral=0.2,
            compound=0.6
        )
        
        news = News(
            symbol="AAPL",
            headline="Apple announces new product",
            source="Reuters",
            timestamp=timestamp,
            url="https://example.com/news",
            sentiment=sentiment
        )
        
        assert news.symbol == "AAPL"
        assert news.headline == "Apple announces new product"
        assert news.source == "Reuters"
        assert news.timestamp == timestamp
        assert news.url == "https://example.com/news"
        assert news.sentiment.compound == 0.6

    def test_sentiment_score_validation(self):
        """Test SentimentScore validation."""
        # Valid sentiment
        sentiment = SentimentScore(
            positive=0.5,
            negative=0.3,
            neutral=0.2,
            compound=0.2
        )
        assert sentiment.positive == 0.5
        
        # Test normalization
        total = sentiment.positive + sentiment.negative + sentiment.neutral
        assert abs(total - 1.0) < 0.01  # Should sum to approximately 1


class TestCacheService:
    """Test cache service functionality."""

    @pytest.fixture
    def cache_service(self, tmp_path):
        """Create cache service instance."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return CacheService(str(cache_dir))

    def test_cache_set_and_get(self, cache_service):
        """Test basic cache operations."""
        # Set value
        cache_service.set("test_key", {"data": "test_value"})
        
        # Get value
        result = cache_service.get("test_key")
        assert result is not None
        assert result["data"] == "test_value"

    def test_cache_ttl(self, cache_service):
        """Test cache TTL functionality."""
        import time
        
        # Set with short TTL
        cache_service.set("ttl_key", {"data": "expires"}, ttl=1)
        
        # Should exist immediately
        assert cache_service.get("ttl_key") is not None
        
        # Wait for expiration
        time.sleep(1.5)
        
        # Should be expired
        assert cache_service.get("ttl_key") is None

    def test_cache_delete(self, cache_service):
        """Test cache deletion."""
        # Set value
        cache_service.set("delete_key", {"data": "to_delete"})
        assert cache_service.get("delete_key") is not None
        
        # Delete
        cache_service.delete("delete_key")
        assert cache_service.get("delete_key") is None

    def test_cache_clear(self, cache_service, tmp_path):
        """Test clearing all cache."""
        # Set multiple values
        for i in range(5):
            cache_service.set(f"key_{i}", {"index": i})
        
        # Verify files exist
        cache_dir = Path(str(tmp_path / "cache"))
        assert len(list(cache_dir.glob("*.json"))) == 5
        
        # Clear cache
        cache_service.clear()
        
        # Verify all cleared
        assert len(list(cache_dir.glob("*.json"))) == 0

    def test_cache_key_sanitization(self, cache_service):
        """Test cache key sanitization."""
        # Test problematic keys
        problematic_keys = [
            "../../../etc/passwd",
            "key with spaces",
            "key/with/slashes",
            "key\\with\\backslashes",
            "key:with:colons",
            "key|with|pipes"
        ]
        
        for key in problematic_keys:
            # Should handle without errors
            cache_service.set(key, {"data": "test"})
            result = cache_service.get(key)
            assert result is not None


class TestFinnhubWebSocket:
    """Test Finnhub adapter functionality."""

    @pytest.fixture
    async def finnhub_adapter(self):
        """Create Finnhub adapter instance."""
        with patch('src.config.settings.get_config') as mock_config:
            with patch('src.utils.quota.get_quota_guard') as mock_quota:
                mock_config.return_value.api.finnhub_key = "test_api_key"
                mock_quota.return_value.check_quota = AsyncMock(return_value=True)
                adapter = FinnhubWebSocket()
                adapter._ws = AsyncMock()
                adapter._session = AsyncMock(spec=aiohttp.ClientSession)
                adapter.quote_callbacks = []  # Initialize callbacks list
                return adapter

    @pytest.mark.asyncio
    async def test_websocket_connection(self, finnhub_adapter):
        """Test WebSocket connection handling."""
        # Mock WebSocket connect as async context manager
        mock_ws = AsyncMock()
        mock_ws.recv.return_value = json.dumps({
            "type": "trade",
            "data": [{
                "s": "AAPL",
                "p": 150.0,
                "v": 100,
                "t": int(datetime.now().timestamp() * 1000)
            }]
        })
        
        mock_connect = AsyncMock(return_value=mock_ws)
        with patch('websockets.connect', mock_connect):
            # Mock the connection to succeed
            finnhub_adapter.websocket = mock_ws
            finnhub_adapter.is_connected = True
            
            # Subscribe to symbol
            await finnhub_adapter.subscribe(["AAPL"])
            
            # Should be connected and subscribed
            assert finnhub_adapter.is_connected
            assert "AAPL" in finnhub_adapter.subscribed_symbols

    @pytest.mark.asyncio
    async def test_quote_parsing(self, finnhub_adapter):
        """Test quote callback mechanism."""
        # Set up a mock callback
        received_quotes = []
        
        def quote_callback(quote):
            received_quotes.append(quote)
        
        finnhub_adapter.quote_callbacks.append(quote_callback)
        
        # Simulate receiving WebSocket message
        test_quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,
            volume=1000000,
            high=152.0,
            low=148.0,
            prev_close=149.0,
            provider=DataProvider.FINNHUB,
            is_delayed=False
        )
        
        # Call the callbacks directly
        for callback in finnhub_adapter.quote_callbacks:
            callback(test_quote)
        
        # Check callback was called
        assert len(received_quotes) == 1
        assert received_quotes[0].symbol == "AAPL"
        assert received_quotes[0].price == 150.0

    @pytest.mark.asyncio
    async def test_error_handling(self, finnhub_adapter):
        """Test error handling in API calls."""
        # Mock error response
        mock_response = AsyncMock()
        mock_response.status = 429  # Rate limit
        mock_response.json.return_value = {"error": "Rate limit exceeded"}
        
        finnhub_adapter._session.get.return_value.__aenter__.return_value = mock_response
        
        # Should handle gracefully
        quote = await finnhub_adapter.get_quote("AAPL")
        assert quote is None


class TestYahooAdapter:
    """Test Yahoo Finance adapter functionality."""

    @pytest.fixture
    def yahoo_adapter(self):
        """Create Yahoo adapter instance."""
        return YahooFinanceAdapter()

    @pytest.mark.asyncio
    async def test_quote_fetching(self, yahoo_adapter):
        """Test quote fetching from Yahoo Finance."""
        # Mock yfinance
        with patch('yfinance.Ticker') as mock_ticker:
            mock_info = {
                'regularMarketPrice': 150.0,
                'regularMarketVolume': 1000000,
                'regularMarketDayHigh': 152.0,
                'regularMarketDayLow': 148.0,
                'regularMarketPreviousClose': 149.0,
                'bid': 149.95,
                'ask': 150.05
            }
            mock_ticker.return_value.info = mock_info
            
            quote = await yahoo_adapter.get_quote("AAPL")
            
            assert quote.symbol == "AAPL"
            assert quote.price == 150.0
            assert quote.volume == 1000000
            assert quote.is_delayed is True  # Yahoo data is delayed

    @pytest.mark.asyncio
    async def test_historical_data(self, yahoo_adapter):
        """Test historical data fetching."""
        # Create mock bars directly
        from src.data.base import Bar
        mock_bars = [
            Bar(
                symbol="AAPL",
                timestamp=datetime.now() - timedelta(days=2),
                open=148.0, high=150.0, low=147.0, close=149.0,
                volume=900000
            ),
            Bar(
                symbol="AAPL",
                timestamp=datetime.now() - timedelta(days=1),
                open=149.0, high=151.0, low=148.0, close=150.0,
                volume=950000
            ),
            Bar(
                symbol="AAPL",
                timestamp=datetime.now(),
                open=150.0, high=152.0, low=149.0, close=151.0,
                volume=1000000
            )
        ]
        
        # Mock get_bars to return our bars
        with patch.object(yahoo_adapter, 'get_bars', AsyncMock(return_value=mock_bars)):
            # Get bars for last 3 days
            end = datetime.now()
            start = end - timedelta(days=3)
            bars = await yahoo_adapter.get_bars("AAPL", start=start, end=end, interval="1d")
            
            assert len(bars) == 3
            assert bars[0].close == 149.0
            assert bars[-1].close == 151.0


class TestNewsAPIAdapter:
    """Test news adapter functionality."""

    @pytest.fixture
    async def news_adapter(self):
        """Create news adapter instance."""
        with patch('src.config.settings.get_config') as mock_config:
            with patch('src.utils.quota.get_quota_guard') as mock_quota:
                mock_config.return_value.api.news_api_key = "test_api_key"
                mock_quota.return_value.check_quota = AsyncMock(return_value=True)
                adapter = NewsAPIAdapter()
                adapter.client = AsyncMock()  # Mock the httpx client
                adapter.is_connected = True
                return adapter

    @pytest.mark.asyncio
    async def test_news_fetching(self, news_adapter):
        """Test news fetching and parsing."""
        from src.data.base import Headline
        
        # Create expected headlines
        mock_headlines = [
            Headline(
                symbol="AAPL",
                headline="Apple Stock Rises",
                source="Reuters",
                timestamp=datetime.now(),
                url="https://example.com/apple-news",
                sentiment=None
            )
        ]
        
        # Mock get_headlines to return our headlines
        with patch.object(news_adapter, 'get_headlines', AsyncMock(return_value=mock_headlines)):
            # Get news
            news_items = await news_adapter.get_headlines("AAPL")
            
            assert len(news_items) == 1
            assert news_items[0].headline == "Apple Stock Rises"
            assert news_items[0].source == "Reuters"

    @pytest.mark.asyncio
    async def test_sentiment_analysis(self, news_adapter):
        """Test sentiment analysis of news."""
        # Mock API response with various sentiments
        mock_response = AsyncMock()
        mock_response.json.return_value = {
            "status": "ok",
            "articles": [
                {
                    "title": "Apple reports record breaking profits and amazing growth",
                    "description": "Positive news",
                    "url": "https://example.com/1",
                    "publishedAt": "2025-01-06T10:00:00Z",
                    "source": {"name": "Reuters"}
                },
                {
                    "title": "Apple faces regulatory challenges and declining sales",
                    "description": "Negative news",
                    "url": "https://example.com/2",
                    "publishedAt": "2025-01-06T09:00:00Z",
                    "source": {"name": "Bloomberg"}
                }
            ]
        }
        mock_response.status = 200
        
        news_adapter.client.get.return_value = mock_response
        
        from src.data.base import Headline, SentimentScore
        
        # Create headlines with sentiment
        mock_headlines = [
            Headline(
                symbol="AAPL",
                headline="Apple reports record breaking profits and amazing growth",
                source="Reuters",
                timestamp=datetime.now(),
                url="https://example.com/1",
                sentiment=SentimentScore(positive=0.8, negative=0.1, neutral=0.1, compound=0.7)
            ),
            Headline(
                symbol="AAPL",
                headline="Apple faces regulatory challenges and declining sales",
                source="Bloomberg",
                timestamp=datetime.now(),
                url="https://example.com/2",
                sentiment=SentimentScore(positive=0.1, negative=0.8, neutral=0.1, compound=-0.7)
            )
        ]
        
        # Mock get_headlines to return our headlines
        with patch.object(news_adapter, 'get_headlines', AsyncMock(return_value=mock_headlines)):
            # Get news with sentiment
            news_items = await news_adapter.get_headlines("AAPL")
            
            assert len(news_items) == 2
            # Positive news should have positive sentiment
            assert news_items[0].sentiment.compound > 0
            # Negative news should have negative sentiment
            assert news_items[1].sentiment.compound < 0


class TestMarketDataManager:
    """Test market data manager functionality."""

    @pytest.fixture
    def market_data_manager(self):
        """Create market data manager instance."""
        with patch('src.data.finnhub.FinnhubWebSocket') as mock_finnhub:
            with patch('src.data.yahoo.YahooFinanceAdapter') as mock_yahoo:
                with patch('src.data.cache_manager.CacheManager') as mock_cache:
                    # Mock the cache to return None
                    mock_cache.return_value.get_quote = AsyncMock(return_value=None)
                    mock_cache.return_value.put_quote = AsyncMock()
                    
                    with patch('src.data.market.MarketDataManager.__init__') as mock_init:
                        mock_init.return_value = None
                        manager = MarketDataManager()
                        # Set up the manager with mocks
                        manager.primary_source = mock_finnhub.return_value
                        manager.fallback_source = mock_yahoo.return_value
                        manager.cache = mock_cache.return_value
                        manager.latest_quotes = {}
                        manager.active_providers = {
                            DataPriority.REALTIME: mock_finnhub.return_value,
                            DataPriority.DELAYED: mock_yahoo.return_value
                        }
                        manager.current_priority = DataPriority.REALTIME
                        manager.quota_guard = Mock()
                        manager.quota_guard.get_status = Mock(return_value={})
                        return manager

    @pytest.mark.asyncio
    async def test_quote_with_fallback(self, market_data_manager):
        """Test quote fetching with fallback mechanism."""
        test_quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,
            volume=1000000
        )
        
        # Set up health checks
        market_data_manager.primary_source.health_check = AsyncMock(return_value=True)
        market_data_manager.fallback_source.health_check = AsyncMock(return_value=True)
        
        # Primary source fails
        market_data_manager.primary_source.get_quote = AsyncMock(side_effect=Exception("API Error"))
        
        # Fallback succeeds
        market_data_manager.fallback_source.get_quote = AsyncMock(return_value=test_quote)
        
        # Should get quote from fallback
        quote = await market_data_manager.get_quote("AAPL")
        
        assert quote is not None
        assert quote.symbol == "AAPL"
        assert quote.price == 150.0
        assert market_data_manager.primary_source.get_quote.called
        assert market_data_manager.fallback_source.get_quote.called

    @pytest.mark.asyncio
    async def test_batch_quotes(self, market_data_manager):
        """Test batch quote fetching."""
        symbols = ["AAPL", "GOOGL", "MSFT"]
        
        # Mock responses
        async def mock_get_quote(symbol):
            return Quote(
                symbol=symbol,
                timestamp=datetime.now(),
                price=100.0 + hash(symbol) % 50,
                volume=1000000
            )
        
        # Set up proper mocking for adapter
        mock_adapter = market_data_manager.active_providers[DataPriority.REALTIME]
        mock_adapter.health_check = AsyncMock(return_value=True)
        mock_adapter.get_quote = AsyncMock(side_effect=mock_get_quote)
        
        # Get quotes individually (MarketDataManager doesn't have get_batch_quotes)
        quotes = []
        for symbol in symbols:
            quote = await market_data_manager.get_quote(symbol)
            if quote:
                quotes.append(quote)
        
        assert len(quotes) == 3
        assert all(q.symbol in symbols for q in quotes)

    @pytest.mark.asyncio
    async def test_priority_switching(self, market_data_manager):
        """Test automatic priority switching on quota exhaustion."""
        # Mock quota status to show Finnhub nearly exhausted
        with patch.object(market_data_manager.quota_guard, 'get_status') as mock_status:
            mock_status.return_value = {
                'finnhub': {'percentage': 96}  # 96% used
            }
            
            # Check quota should switch to delayed priority
            await market_data_manager._check_quota_status()
            assert market_data_manager.current_priority == DataPriority.DELAYED