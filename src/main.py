"""
Main entry point for ODTA
Provides CLI commands and orchestration
"""

import asyncio
import click
from datetime import datetime
from pathlib import Path
import signal
import sys

from src.config.settings import get_config
from src.utils.logger import get_logger
from src.utils.quota import QuotaGuard
from src.data.cache_manager import CacheManager
from src.orchestration import EventBus, Scheduler, Coordinator

logger = get_logger(__name__)

@click.group()
def cli():
    """One-Day Trading Agent CLI"""
    pass

@cli.command()
def status():
    """Check system status and configuration"""
    config = get_config()
    logger.info("ODTA System Status Check")
    
    # Check configuration
    click.echo("\n📋 Configuration Status:")
    click.echo(f"  • Log Level: {config.system.log_level}")
    click.echo(f"  • Timezone: {config.system.timezone}")
    click.echo(f"  • Cache TTL: {config.system.cache_ttl_minutes} minutes")
    click.echo(f"  • Paper Trading: {'Enabled' if config.system.enable_paper_trading else 'Disabled'}")
    
    # Check API keys
    click.echo("\n🔑 API Keys:")
    api_keys = [
        ("Finnhub", bool(config.api.finnhub_key)),
        ("Alpha Vantage", bool(config.api.alpha_vantage_key)),
        ("NewsAPI", bool(config.api.news_api_key))
    ]
    
    for name, is_set in api_keys:
        status = "✅ Set" if is_set else "❌ Missing"
        click.echo(f"  • {name}: {status}")
    
    # Check directories
    click.echo("\n📁 Directories:")
    dirs = [
        ("Data", Path("data")),
        ("Cache", Path("data/cache")),
        ("Logs", Path("logs")),
        ("Universe", Path("data/universe"))
    ]
    
    for name, path in dirs:
        exists = "✅" if path.exists() else "❌"
        click.echo(f"  • {name}: {exists} {path}")
    
    # Check quotas
    click.echo("\n📊 API Quotas:")
    guard = QuotaGuard()
    quota_status = guard.get_status()
    
    for provider, info in quota_status.items():
        used = info.get('used', 0)
        limit = info.get('limit', 0)
        usage = f"{used}/{limit}"
        pct = (used / limit * 100) if limit > 0 else 0
        emoji = "🟢" if pct < 80 else "🟡" if pct < 95 else "🔴"
        period = info.get('period', 'unknown')
        click.echo(f"  • {provider.title()}: {emoji} {usage} ({pct:.0f}%) per {period}")
    
    click.echo("\n✅ System check complete!")

@cli.command()
@click.option('--test', is_flag=True, help='Run in test mode with limited symbols')
def scan(test):
    """Run primary market scan"""
    current_time = datetime.now().strftime("%H:%M")
    logger.info(f"Starting primary scan at {current_time}")
    
    if test:
        click.echo("🧪 Running in test mode with limited symbols...")
    else:
        click.echo(f"🔍 Starting market scan at {current_time} CET...")
    
    asyncio.run(run_scan(test_mode=test))

@cli.command()
def second_look():
    """Run second-look scan"""
    current_time = datetime.now().strftime("%H:%M")
    logger.info(f"Starting second-look scan at {current_time}")
    
    click.echo(f"🌙 Starting second-look scan at {current_time} CET...")
    asyncio.run(run_second_look())

@cli.command()
@click.option('--auto/--no-auto', default=True, help='Enable automatic scheduled scans')
def orchestrate(auto):
    """Run the orchestration system with scheduler"""
    click.echo("🚀 Starting ODTA Orchestration System...")
    
    if auto:
        click.echo("📅 Automatic scans enabled (14:00 and 18:15 CET)")
    else:
        click.echo("🔧 Manual mode - use 'scan' and 'second-look' commands")
    
    asyncio.run(run_orchestration(auto_schedule=auto))

async def run_scan(test_mode=False):
    """Execute primary market scan using orchestration"""
    logger.info("Executing primary scan workflow")
    
    # Initialize components
    cache = CacheManager()
    event_bus = EventBus()
    
    # In test mode, use mocked components
    if test_mode:
        # Create mock data in cache for test symbols
        from src.data.base import Quote, Bar
        from datetime import datetime, timedelta
        
        # Pre-populate cache with test data
        test_symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
        for symbol in test_symbols:
            # Mock quote
            quote = Quote(
                symbol=symbol,
                timestamp=datetime.now(),
                price=100.0 + hash(symbol) % 50,
                volume=1000000,
                provider="mock"
            )
            await cache.set_quote(symbol, quote)
            
            # Mock history (5 days)
            bars = []
            for i in range(5):
                bar = Bar(
                    symbol=symbol,
                    timestamp=datetime.now() - timedelta(days=i),
                    open=95.0 + i,
                    high=105.0 + i,
                    low=90.0 + i,
                    close=100.0 + i,
                    volume=1000000,
                    provider="mock"
                )
                bars.append(bar)
            await cache.set_bars(symbol, bars)
    
    coordinator = Coordinator(event_bus, cache)
    
    try:
        # Start event bus and coordinator
        await event_bus.start()
        await coordinator.start()
        
        # Run scan with timeout
        if test_mode:
            # Test with limited symbols
            test_symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
            click.echo("📝 Running with test symbols: " + ", ".join(test_symbols))
            
            # Set shorter timeout for test mode
            try:
                results = await asyncio.wait_for(
                    coordinator._execute_scan("primary", test_symbols),
                    timeout=30.0  # 30 second timeout for test
                )
            except asyncio.TimeoutError:
                click.echo("\n❌ Scan timed out - check API connectivity")
                return
        else:
            # Production scan with 20 second target
            try:
                results = await asyncio.wait_for(
                    coordinator.run_primary_scan(),
                    timeout=25.0  # 25 seconds (slightly above 20s target)
                )
            except asyncio.TimeoutError:
                click.echo("\n❌ Scan exceeded 25 second timeout")
                return
        
        # Display results
        if hasattr(results, 'total_symbols'):
            click.echo(f"\n📊 Scan Results:")
            click.echo(f"  • Total symbols: {results.total_symbols}")
            click.echo(f"  • Gaps found: {results.gaps_found}")
            click.echo(f"  • Candidates scored: {results.candidates_scored}")
            click.echo(f"  • Trades planned: {results.trades_planned}")
            click.echo(f"  • Trades approved: {results.trades_approved}")
            click.echo(f"  • Execution time: {results.execution_time:.2f}s")
            
            if results.top_trades:
                click.echo("\n🎯 Top Picks:")
                for i, trade in enumerate(results.top_trades[:5], 1):
                    click.echo(f"  {i}. {trade.symbol} - Entry: ${trade.entry_price:.2f}, Target: ${trade.target_price:.2f}")
            
            if results.errors:
                click.echo(f"\n⚠️ Errors encountered: {len(results.errors)}")
        
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        click.echo(f"\n❌ Scan failed: {str(e)}")
        
    finally:
        # Cleanup
        await coordinator.stop()
        await event_bus.stop()
    
    logger.info("Primary scan complete")

async def run_second_look():
    """Execute second-look scan using orchestration"""
    logger.info("Executing second-look scan workflow")
    
    # Initialize components
    cache = CacheManager()
    event_bus = EventBus()
    coordinator = Coordinator(event_bus, cache)
    
    try:
        # Start event bus and coordinator
        await event_bus.start()
        await coordinator.start()
        
        # Run scan
        results = await coordinator.run_second_look_scan()
        
        # Display results
        click.echo(f"\n📊 Second-Look Results:")
        click.echo(f"  • Trades updated: {len(results)}")
        
        if results:
            click.echo("\n🔄 Updated Picks:")
            for i, trade in enumerate(results[:5], 1):
                click.echo(f"  {i}. {trade.symbol} - Entry: ${trade.entry_price:.2f}, Target: ${trade.target_price:.2f}")
        
    finally:
        # Cleanup
        await coordinator.stop()
        await event_bus.stop()
    
    logger.info("Second-look scan complete")

async def run_orchestration(auto_schedule=True):
    """Run the full orchestration system"""
    logger.info("Starting orchestration system")
    
    # Initialize components
    cache = CacheManager()
    event_bus = EventBus()
    scheduler = Scheduler(event_bus)
    coordinator = Coordinator(event_bus, cache)
    
    # Handle shutdown signals
    shutdown_event = asyncio.Event()
    
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        shutdown_event.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start all components
        await event_bus.start()
        await coordinator.start()
        
        if auto_schedule:
            await scheduler.start()
            click.echo("✅ Orchestration system running. Press Ctrl+C to stop.")
            
            # Wait for shutdown
            await shutdown_event.wait()
        else:
            click.echo("✅ Orchestration system ready for manual commands.")
            click.echo("Use 'odta scan' or 'odta second-look' to trigger scans.")
    
    finally:
        # Graceful shutdown
        click.echo("\n🛑 Shutting down...")
        if auto_schedule:
            await scheduler.stop()
        await coordinator.stop()
        await event_bus.stop()
        click.echo("👋 Goodbye!")

def run_scan_cli():
    """Entry point for direct scan execution"""
    asyncio.run(run_scan())

if __name__ == "__main__":
    cli()