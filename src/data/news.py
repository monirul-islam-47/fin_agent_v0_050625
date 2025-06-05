"""
News data adapters for GDELT and NewsAPI
Includes sentiment analysis using VADER
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import httpx
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from ..config import get_config
from ..utils import get_logger, get_quota_guard
from .base import NewsAdapter, DataProvider, Headline

logger = get_logger(__name__)

class NewsAPIAdapter(NewsAdapter):
    """
    NewsAPI.org adapter for news headlines
    Limited to 1000 requests/day on free tier
    """
    
    def __init__(self):
        super().__init__(DataProvider.NEWSAPI)
        self.config = get_config()
        self.quota_guard = get_quota_guard()
        self.base_url = "https://newsapi.org/v2"
        self.api_key = self.config.api.news_api_key
        self.client = None
        self.sentiment_analyzer = SentimentIntensityAnalyzer()
        
    async def connect(self):
        """Initialize HTTP client"""
        if not self.client:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(30))
            self.is_connected = True
            logger.info("NewsAPI client initialized")
            
    async def disconnect(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
            self.is_connected = False
            
    async def health_check(self) -> bool:
        """Check NewsAPI health"""
        if not self.client or not self.api_key:
            return False
            
        try:
            # Simple sources endpoint check (doesn't count against quota)
            response = await self.client.get(
                f"{self.base_url}/sources",
                headers={"X-Api-Key": self.api_key},
                params={"language": "en", "country": "us"}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"NewsAPI health check failed: {e}")
            return False
            
    async def get_headlines(self, symbol: str, limit: int = 10) -> List[Headline]:
        """Get recent headlines for symbol"""
        if not self.quota_guard.check_and_update('newsapi', 1):
            logger.warning(f"NewsAPI quota exceeded, cannot fetch headlines for {symbol}")
            return []
            
        try:
            if not self.client:
                await self.connect()
                
            # Search for company news
            # Note: Free tier doesn't allow sorting by popularity
            response = await self.client.get(
                f"{self.base_url}/everything",
                headers={"X-Api-Key": self.api_key},
                params={
                    "q": f'"{symbol}" stock',  # Search query
                    "language": "en",
                    "sortBy": "publishedAt",  # Most recent first
                    "pageSize": limit,
                    "from": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") != "ok":
                logger.error(f"NewsAPI error: {data.get('message', 'Unknown error')}")
                return []
                
            headlines = []
            for article in data.get("articles", []):
                # Skip if no title
                if not article.get("title"):
                    continue
                    
                # Combine title and description for sentiment
                text = article["title"]
                if article.get("description"):
                    text += " " + article["description"]
                    
                # Analyze sentiment
                sentiment_scores = self.sentiment_analyzer.polarity_scores(text)
                
                headlines.append(Headline(
                    symbol=symbol,
                    timestamp=datetime.fromisoformat(article["publishedAt"].replace("Z", "+00:00")),
                    headline=article["title"],
                    source=article.get("source", {}).get("name", "Unknown"),
                    url=article.get("url"),
                    sentiment=sentiment_scores["compound"],  # -1 to 1 scale
                    provider=self.provider.value
                ))
                
            return headlines
            
        except Exception as e:
            logger.error(f"Failed to get NewsAPI headlines for {symbol}: {e}")
            return []
            
    async def search_news(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 20
    ) -> List[Headline]:
        """Search news by query and date range"""
        if not self.quota_guard.check_and_update('newsapi', 1):
            logger.warning("NewsAPI quota exceeded")
            return []
            
        try:
            if not self.client:
                await self.connect()
                
            response = await self.client.get(
                f"{self.base_url}/everything",
                headers={"X-Api-Key": self.api_key},
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": limit,
                    "from": start.strftime("%Y-%m-%d"),
                    "to": end.strftime("%Y-%m-%d")
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            headlines = []
            for article in data.get("articles", []):
                if not article.get("title"):
                    continue
                    
                # Sentiment analysis
                text = article["title"] + " " + (article.get("description") or "")
                sentiment_scores = self.sentiment_analyzer.polarity_scores(text)
                
                headlines.append(Headline(
                    symbol="",  # Generic search, no specific symbol
                    timestamp=datetime.fromisoformat(article["publishedAt"].replace("Z", "+00:00")),
                    headline=article["title"],
                    source=article.get("source", {}).get("name", "Unknown"),
                    url=article.get("url"),
                    sentiment=sentiment_scores["compound"],
                    provider=self.provider.value
                ))
                
            return headlines
            
        except Exception as e:
            logger.error(f"Failed to search NewsAPI: {e}")
            return []


class GDELTAdapter(NewsAdapter):
    """
    GDELT Project adapter for global news monitoring
    Free and unlimited but requires more processing
    """
    
    def __init__(self):
        super().__init__(DataProvider.GDELT)
        self.config = get_config()
        self.base_url = "https://api.gdeltproject.org/api/v2"
        self.client = None
        self.sentiment_analyzer = SentimentIntensityAnalyzer()
        
    async def connect(self):
        """Initialize HTTP client"""
        if not self.client:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(60))  # GDELT can be slow
            self.is_connected = True
            logger.info("GDELT client initialized")
            
    async def disconnect(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
            self.is_connected = False
            
    async def health_check(self) -> bool:
        """Check GDELT API health"""
        if not self.client:
            return False
            
        try:
            # Simple test query
            response = await self.client.get(
                f"{self.base_url}/doc/doc",
                params={
                    "query": "test",
                    "mode": "artlist",
                    "maxrecords": 1,
                    "format": "json"
                }
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"GDELT health check failed: {e}")
            return False
            
    async def get_headlines(self, symbol: str, limit: int = 10) -> List[Headline]:
        """Get recent headlines for symbol from GDELT"""
        try:
            if not self.client:
                await self.connect()
                
            # GDELT query syntax
            # Note: GDELT doesn't have a direct stock symbol search
            # We need to be creative with the query
            query = f'("{symbol}" OR "{self._get_company_name(symbol)}") (stock OR shares OR earnings OR "wall street")'
            
            response = await self.client.get(
                f"{self.base_url}/doc/doc",
                params={
                    "query": query,
                    "mode": "artlist",
                    "maxrecords": limit,
                    "format": "json",
                    "timespan": "7d",  # Last 7 days
                    "sort": "datedesc"
                }
            )
            
            if response.status_code != 200:
                logger.error(f"GDELT API error: {response.status_code}")
                return []
                
            data = response.json()
            articles = data.get("articles", [])
            
            headlines = []
            for article in articles:
                # GDELT provides tone score, but we'll use VADER for consistency
                title = article.get("title", "")
                if not title:
                    continue
                    
                # Parse GDELT's date format (YYYYMMDDHHMMSS)
                date_str = str(article.get("seendate", ""))
                try:
                    timestamp = datetime.strptime(date_str, "%Y%m%d%H%M%S")
                except:
                    timestamp = datetime.now()
                    
                # Sentiment analysis
                sentiment_scores = self.sentiment_analyzer.polarity_scores(title)
                
                headlines.append(Headline(
                    symbol=symbol,
                    timestamp=timestamp,
                    headline=title,
                    source=article.get("domain", "Unknown"),
                    url=article.get("url"),
                    sentiment=sentiment_scores["compound"],
                    provider=self.provider.value
                ))
                
            return headlines
            
        except Exception as e:
            logger.error(f"Failed to get GDELT headlines for {symbol}: {e}")
            return []
            
    async def search_news(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 20
    ) -> List[Headline]:
        """Search GDELT news by query and date range"""
        try:
            if not self.client:
                await self.connect()
                
            # Calculate timespan in days
            days = (end - start).days
            if days <= 0:
                days = 1
                
            response = await self.client.get(
                f"{self.base_url}/doc/doc",
                params={
                    "query": query,
                    "mode": "artlist",
                    "maxrecords": limit,
                    "format": "json",
                    "timespan": f"{days}d",
                    "sort": "datedesc"
                }
            )
            
            if response.status_code != 200:
                logger.error(f"GDELT search error: {response.status_code}")
                return []
                
            data = response.json()
            articles = data.get("articles", [])
            
            headlines = []
            for article in articles:
                title = article.get("title", "")
                if not title:
                    continue
                    
                # Parse date
                date_str = str(article.get("seendate", ""))
                try:
                    timestamp = datetime.strptime(date_str, "%Y%m%d%H%M%S")
                except:
                    timestamp = datetime.now()
                    
                # Skip if outside date range
                if timestamp < start or timestamp > end:
                    continue
                    
                # Sentiment
                sentiment_scores = self.sentiment_analyzer.polarity_scores(title)
                
                headlines.append(Headline(
                    symbol="",
                    timestamp=timestamp,
                    headline=title,
                    source=article.get("domain", "Unknown"),
                    url=article.get("url"),
                    sentiment=sentiment_scores["compound"],
                    provider=self.provider.value
                ))
                
            return headlines
            
        except Exception as e:
            logger.error(f"Failed to search GDELT: {e}")
            return []
            
    def _get_company_name(self, symbol: str) -> str:
        """Get company name from symbol (simplified mapping)"""
        # In production, this would use a proper symbol-to-name mapping
        common_mappings = {
            "AAPL": "Apple",
            "MSFT": "Microsoft",
            "GOOGL": "Google",
            "AMZN": "Amazon",
            "TSLA": "Tesla",
            "META": "Meta",
            "NVDA": "Nvidia",
            "JPM": "JPMorgan",
            "BAC": "Bank of America",
            "WMT": "Walmart"
        }
        return common_mappings.get(symbol, symbol)