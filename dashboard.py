"""
ODTA Streamlit Dashboard
Interactive UI for the One-Day Trading Agent with real-time event integration
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, time, timedelta
import asyncio
from pathlib import Path
import threading
import queue
import json
from typing import Dict, List, Optional, Any
import pytz

# Import ODTA components
from src.orchestration.event_bus import EventBus
from src.orchestration.events import (
    Event, ScanRequest, DataUpdate, TradeSignal, 
    RiskAlert, SystemStatus, QuotaWarning, ErrorEvent,
    PersistenceEvent
)
from src.orchestration.coordinator import Coordinator
from src.data.market import MarketDataManager
from src.data.news_manager import NewsManager
from src.domain.universe import UniverseManager
from src.domain.scanner import GapScanner
from src.domain.scoring import FactorModel
from src.domain.planner import TradePlanner
from src.domain.risk import RiskManager
from src.config.settings import get_config
from src.utils.logger import setup_logger

# Page configuration
st.set_page_config(
    page_title="ODTA - One-Day Trading Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize logger
logger = setup_logger(__name__)

# Initialize session state
if "event_queue" not in st.session_state:
    st.session_state.event_queue = queue.Queue()
    
if "scan_results" not in st.session_state:
    st.session_state.scan_results = []
    
if "last_update" not in st.session_state:
    st.session_state.last_update = None
    
if "quota_status" not in st.session_state:
    st.session_state.quota_status = {
        "finnhub": {"used": 0, "limit": 60, "per": "minute"},
        "alpha_vantage": {"used": 0, "limit": 25, "per": "day"},
        "newsapi": {"used": 0, "limit": 1000, "per": "day"}
    }
    
if "live_prices" not in st.session_state:
    st.session_state.live_prices = {}
    
if "system_status" not in st.session_state:
    st.session_state.system_status = {
        "market_data": "🔴 Offline",
        "news": "🔴 Offline",
        "scanner": "🔴 Offline",
        "risk": "🔴 Offline"
    }
    
if "risk_alerts" not in st.session_state:
    st.session_state.risk_alerts = []

if "factor_weights" not in st.session_state:
    st.session_state.factor_weights = {
        "momentum": 0.4,
        "news_catalyst": 0.3,
        "sentiment": 0.1,
        "liquidity": 0.2
    }

if "event_thread" not in st.session_state:
    st.session_state.event_thread = None
    
if "coordinator" not in st.session_state:
    st.session_state.coordinator = None


class DashboardEventHandler:
    """Handles events from the EventBus and updates session state"""
    
    def __init__(self, event_queue: queue.Queue):
        self.event_queue = event_queue
        
    async def handle_trade_signal(self, event: TradeSignal):
        """Handle incoming trade signals"""
        self.event_queue.put({
            "type": "trade_signal",
            "data": {
                "symbol": event.symbol,
                "score": event.score,
                "trade_plan": event.trade_plan.__dict__ if event.trade_plan else None,
                "factors": event.factors,
                "timestamp": event.timestamp.isoformat()
            }
        })
        
    async def handle_data_update(self, event: DataUpdate):
        """Handle real-time price updates"""
        self.event_queue.put({
            "type": "data_update",
            "data": {
                "symbol": event.symbol,
                "data_type": event.data_type,
                "data": event.data,
                "timestamp": event.timestamp.isoformat()
            }
        })
        
    async def handle_quota_warning(self, event: QuotaWarning):
        """Handle quota warnings"""
        self.event_queue.put({
            "type": "quota_warning",
            "data": {
                "service": event.service,
                "used": event.used,
                "limit": event.limit,
                "reset_time": event.reset_time.isoformat() if event.reset_time else None
            }
        })
        
    async def handle_risk_alert(self, event: RiskAlert):
        """Handle risk alerts"""
        self.event_queue.put({
            "type": "risk_alert",
            "data": {
                "alert_type": event.alert_type,
                "message": event.message,
                "severity": event.severity,
                "timestamp": event.timestamp.isoformat()
            }
        })
        
    async def handle_system_status(self, event: SystemStatus):
        """Handle system status updates"""
        self.event_queue.put({
            "type": "system_status",
            "data": {
                "component": event.component,
                "status": event.status,
                "message": event.message,
                "timestamp": event.timestamp.isoformat()
            }
        })
        
    async def handle_error(self, event: ErrorEvent):
        """Handle error events"""
        self.event_queue.put({
            "type": "error",
            "data": {
                "source": event.source,
                "error": str(event.error),
                "severity": event.severity,
                "timestamp": event.timestamp.isoformat()
            }
        })
        
    async def handle_persistence_event(self, event: PersistenceEvent):
        """Handle persistence events"""
        self.event_queue.put({
            "type": "persistence",
            "timestamp": event.timestamp,
            "data": {
                "operation": event.operation,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "success": event.success,
                "details": event.details
            }
        })


def run_event_loop(event_queue: queue.Queue):
    """Run the async event loop in a separate thread"""
    async def _run():
        try:
            # Initialize components
            config = get_config()
            event_bus = EventBus()
            
            # Create handler
            handler = DashboardEventHandler(event_queue)
            
            # Subscribe to events
            await event_bus.subscribe(TradeSignal, handler.handle_trade_signal)
            await event_bus.subscribe(DataUpdate, handler.handle_data_update)
            await event_bus.subscribe(QuotaWarning, handler.handle_quota_warning)
            await event_bus.subscribe(RiskAlert, handler.handle_risk_alert)
            await event_bus.subscribe(SystemStatus, handler.handle_system_status)
            await event_bus.subscribe(ErrorEvent, handler.handle_error)
            await event_bus.subscribe(PersistenceEvent, handler.handle_persistence_event)
            
            # Initialize coordinator
            coordinator = Coordinator(event_bus)
            st.session_state.coordinator = coordinator
            
            # Initialize trade journal and subscribe to events
            from src.persistence.journal import TradeJournal
            journal = TradeJournal()
            await journal.subscribe_to_events(event_bus)
            
            # Keep the event loop running
            while True:
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Event loop error: {e}")
            event_queue.put({
                "type": "error",
                "data": {
                    "source": "event_loop",
                    "error": str(e),
                    "severity": "high",
                    "timestamp": datetime.now().isoformat()
                }
            })
    
    # Create and run the event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())


def process_events():
    """Process events from the queue and update session state"""
    while not st.session_state.event_queue.empty():
        try:
            event = st.session_state.event_queue.get_nowait()
            
            if event["type"] == "trade_signal":
                # Add to scan results
                st.session_state.scan_results.append(event["data"])
                # Keep only top 5
                st.session_state.scan_results = sorted(
                    st.session_state.scan_results,
                    key=lambda x: x["score"],
                    reverse=True
                )[:5]
                st.session_state.last_update = datetime.now()
                
            elif event["type"] == "data_update":
                # Update live prices
                symbol = event["data"]["symbol"]
                if event["data"]["data_type"] == "quote":
                    st.session_state.live_prices[symbol] = event["data"]["data"]
                    
            elif event["type"] == "quota_warning":
                # Update quota status
                service = event["data"]["service"]
                if service in st.session_state.quota_status:
                    st.session_state.quota_status[service]["used"] = event["data"]["used"]
                    
            elif event["type"] == "risk_alert":
                # Add risk alert
                st.session_state.risk_alerts.append(event["data"])
                # Keep only last 10 alerts
                st.session_state.risk_alerts = st.session_state.risk_alerts[-10:]
                
            elif event["type"] == "system_status":
                # Update system status
                component = event["data"]["component"]
                status = event["data"]["status"]
                if status == "online":
                    st.session_state.system_status[component] = "🟢 Online"
                elif status == "warning":
                    st.session_state.system_status[component] = "🟡 Warning"
                else:
                    st.session_state.system_status[component] = "🔴 Offline"
                    
            elif event["type"] == "error":
                # Show error
                st.error(f"System error: {event['data']['error']}")
                
            elif event["type"] == "persistence":
                # Handle persistence events
                if event["data"]["operation"] == "trade_recorded":
                    st.toast(f"✅ Trade recorded: {event['data']['details'].get('symbol', 'Unknown')}", icon="💾")
                elif event["data"]["operation"] == "metrics_updated":
                    st.toast("📊 Performance metrics updated", icon="📊")
                
        except queue.Empty:
            break
        except Exception as e:
            logger.error(f"Error processing event: {e}")


def trigger_scan(scan_type: str = "primary"):
    """Trigger a market scan"""
    if st.session_state.coordinator:
        # Create scan request
        scan_request = ScanRequest(
            scan_type=scan_type,
            parameters={
                "factor_weights": st.session_state.factor_weights,
                "max_results": 5
            }
        )
        
        # Run in separate thread to avoid blocking
        def _run_scan():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                st.session_state.coordinator.event_bus.publish(scan_request)
            )
            
        thread = threading.Thread(target=_run_scan)
        thread.start()
        
        return True
    return False


# Start event loop thread if not running
if st.session_state.event_thread is None or not st.session_state.event_thread.is_alive():
    st.session_state.event_thread = threading.Thread(
        target=run_event_loop,
        args=(st.session_state.event_queue,),
        daemon=True
    )
    st.session_state.event_thread.start()

# Process any pending events
process_events()

# Sidebar
with st.sidebar:
    st.title("🎯 ODTA Control Panel")
    
    # System Status
    st.subheader("📊 System Status")
    col1, col2 = st.columns(2)
    with col1:
        # Overall status based on components
        all_online = all("🟢" in status for status in st.session_state.system_status.values())
        overall = "🟢 Online" if all_online else "🟡 Partial" if any("🟢" in s for s in st.session_state.system_status.values()) else "🔴 Offline"
        st.metric("Overall", overall)
    with col2:
        tz = pytz.timezone('Europe/Berlin')
        current_time = datetime.now(tz).strftime("%H:%M")
        st.metric("Time (CET)", current_time)
    
    # Component status
    with st.expander("Component Status", expanded=False):
        for component, status in st.session_state.system_status.items():
            st.write(f"{status} {component.replace('_', ' ').title()}")
    
    # API Quota Status
    st.subheader("📈 API Quotas")
    for api, quota in st.session_state.quota_status.items():
        usage_pct = (quota["used"] / quota["limit"]) * 100 if quota["limit"] > 0 else 0
        color = "🟢" if usage_pct < 80 else "🟡" if usage_pct < 95 else "🔴"
        st.progress(usage_pct / 100)
        st.caption(f"{color} {api.title()}: {quota['used']}/{quota['limit']} per {quota['per']}")
    
    # Factor Weights
    st.subheader("⚖️ Factor Weights")
    st.session_state.factor_weights["momentum"] = st.slider(
        "Momentum (Gap %)", 0.0, 1.0, st.session_state.factor_weights["momentum"], 0.05
    )
    st.session_state.factor_weights["news_catalyst"] = st.slider(
        "News Catalyst", 0.0, 1.0, st.session_state.factor_weights["news_catalyst"], 0.05
    )
    st.session_state.factor_weights["sentiment"] = st.slider(
        "Sentiment", 0.0, 1.0, st.session_state.factor_weights["sentiment"], 0.05
    )
    st.session_state.factor_weights["liquidity"] = st.slider(
        "Liquidity", 0.0, 1.0, st.session_state.factor_weights["liquidity"], 0.05
    )
    
    # Normalize weights
    total_weight = sum(st.session_state.factor_weights.values())
    if total_weight > 0:
        st.caption(f"Total: {total_weight:.2f} (will be normalized)")
    
    # Action buttons
    st.subheader("🚀 Actions")
    if st.button("🔄 Run Primary Scan", type="primary", use_container_width=True):
        if trigger_scan("primary"):
            st.success("Primary scan initiated!")
        else:
            st.error("System not ready. Please wait...")
    
    if st.button("🌙 Run Second Look", use_container_width=True):
        if trigger_scan("second_look"):
            st.success("Second-look scan initiated!")
        else:
            st.error("System not ready. Please wait...")
    
    # Risk Alerts
    if st.session_state.risk_alerts:
        st.subheader("⚠️ Risk Alerts")
        for alert in st.session_state.risk_alerts[-3:]:  # Show last 3
            severity_icon = "🔴" if alert["severity"] == "high" else "🟡"
            st.warning(f"{severity_icon} {alert['message']}")

# Main content area
st.title("📈 One-Day Trading Agent")
if st.session_state.last_update:
    st.caption(f"Last updated: {st.session_state.last_update.strftime('%Y-%m-%d %H:%M:%S')}")
else:
    st.caption("No scans performed yet")

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["🎯 Top Picks", "📊 Live Prices", "📰 Market Events", "📋 Performance"])

with tab1:
    st.header("Top 5 Trading Opportunities")
    
    if st.session_state.scan_results:
        # Convert to DataFrame
        df_data = []
        for result in st.session_state.scan_results:
            if result.get("trade_plan"):
                plan = result["trade_plan"]
                df_data.append({
                    "Symbol": result["symbol"],
                    "Score": result["score"],
                    "Gap %": result.get("factors", {}).get("momentum", 0) * 100,
                    "Entry": plan.get("entry_price", 0),
                    "Stop": plan.get("stop_loss", 0),
                    "Target": plan.get("target_price", 0),
                    "Size (€)": plan.get("position_size", 0),
                    "Risk (€)": plan.get("max_loss", 0),
                    "Reward (€)": plan.get("expected_profit", 0),
                    "R:R": plan.get("risk_reward_ratio", 0),
                    "Status": "Ready"
                })
        
        if df_data:
            df = pd.DataFrame(df_data)
            
            # Style the dataframe
            def style_dataframe(df):
                return df.style.format({
                    "Score": "{:.2f}",
                    "Gap %": "{:.1f}%",
                    "Entry": "${:.2f}",
                    "Stop": "${:.2f}",
                    "Target": "${:.2f}",
                    "Size (€)": "€{:.0f}",
                    "Risk (€)": "€{:.2f}",
                    "Reward (€)": "€{:.2f}",
                    "R:R": "{:.1f}"
                }).background_gradient(subset=["Score"], cmap="RdYlGn")
            
            st.dataframe(
                style_dataframe(df),
                use_container_width=True,
                hide_index=True
            )
            
            # Risk summary
            col1, col2, col3, col4 = st.columns(4)
            total_risk = df["Risk (€)"].sum()
            avg_rr = df["R:R"].mean()
            expected_value = df["Reward (€)"].sum() - df["Risk (€)"].sum()
            
            with col1:
                risk_color = "✅" if total_risk <= 33 else "⚠️"
                st.metric("Total Risk", f"€{total_risk:.2f}", f"{risk_color} Daily limit: €33")
            with col2:
                st.metric("Avg R:R", f"{avg_rr:.1f}", "Target: 3.0")
            with col3:
                st.metric("Positions", len(df), "Max: 5")
            with col4:
                ev_pct = (expected_value / df["Size (€)"].sum() * 100) if df["Size (€)"].sum() > 0 else 0
                st.metric("Expected Value", f"€{expected_value:.2f}", f"+{ev_pct:.1f}%")
    else:
        st.info("👆 Click 'Run Primary Scan' to find trading opportunities")
        
        # Demo message
        with st.expander("ℹ️ How it works"):
            st.write("""
            1. **Primary Scan (14:00 CET)**: Searches for stocks with >4% pre-market gaps
            2. **Scoring**: Ranks opportunities using momentum, news, sentiment, and liquidity
            3. **Planning**: Creates entry/exit strategies with 3:1 risk/reward targets
            4. **Risk Management**: Enforces €33 daily loss limit and €250 position caps
            5. **Second Look (18:15 CET)**: Re-evaluates positions before US market close
            """)

with tab2:
    st.header("Live Market Data")
    
    if st.session_state.live_prices:
        # Create price grid
        cols = st.columns(3)
        for i, (symbol, quote) in enumerate(st.session_state.live_prices.items()):
            with cols[i % 3]:
                with st.container():
                    st.subheader(symbol)
                    if isinstance(quote, dict):
                        price = quote.get("price", quote.get("last", 0))
                        change = quote.get("change", 0)
                        change_pct = quote.get("change_percent", 0)
                        
                        # Price color
                        color = "green" if change >= 0 else "red"
                        delta_str = f"+{change:.2f} ({change_pct:.2f}%)" if change >= 0 else f"{change:.2f} ({change_pct:.2f}%)"
                        
                        st.metric("Price", f"${price:.2f}", delta_str)
                        
                        # Bid/Ask spread
                        if "bid" in quote and "ask" in quote:
                            st.caption(f"Bid: ${quote['bid']:.2f} | Ask: ${quote['ask']:.2f}")
    else:
        st.info("Live prices will appear here during market hours")

with tab3:
    st.header("Market Events & News")
    
    # Placeholder for market events
    st.subheader("📅 Upcoming Events")
    events_df = pd.DataFrame({
        "Time": ["14:00", "14:30", "15:30", "16:00", "18:15"],
        "Event": [
            "Primary Market Scan",
            "ECB Rate Decision",
            "US GDP Data Release",
            "Fed Chair Speech",
            "Second-Look Scan"
        ],
        "Impact": ["System", "High", "High", "Medium", "System"]
    })
    st.dataframe(events_df, use_container_width=True, hide_index=True)
    
    st.subheader("📰 Latest News")
    st.info("News feed will populate during market scans")

with tab4:
    st.header("Performance Tracking")
    
    # Import persistence modules
    from src.persistence.journal import TradeJournal
    from src.persistence.metrics import PerformanceMetrics
    
    journal = TradeJournal()
    metrics = PerformanceMetrics(journal)
    
    # Performance metrics cards
    col1, col2, col3, col4 = st.columns(4)
    
    # Get overall metrics
    overall_metrics = metrics.get_overall_metrics()
    
    with col1:
        win_rate = overall_metrics.get('win_rate', 0) * 100
        st.metric("Win Rate", f"{win_rate:.1f}%", 
                 "🟢" if win_rate >= 60 else "🔴" if win_rate < 50 else "🟡")
    with col2:
        total_pnl = overall_metrics.get('total_pnl', 0)
        st.metric("Total P&L", f"€{total_pnl:.2f}",
                 "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪")
    with col3:
        avg_return = overall_metrics.get('avg_return_percent', 0)
        st.metric("Avg Return", f"{avg_return:.1f}%",
                 "Target: +7-10%")
    with col4:
        profit_factor = overall_metrics.get('profit_factor', 0)
        st.metric("Profit Factor", f"{profit_factor:.2f}",
                 "🟢" if profit_factor > 1.5 else "🔴")
    
    st.divider()
    
    # Trade History section
    st.subheader("📜 Trade History")
    
    # Date range filter
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        start_date = st.date_input("From", value=datetime.now().date() - timedelta(days=7))
    with col2:
        end_date = st.date_input("To", value=datetime.now().date())
    with col3:
        # CSV Export button
        if st.button("📥 Export to CSV", type="primary"):
            try:
                # Export trades
                export_path = f"data/exports/trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                journal.export_to_csv(
                    export_path,
                    datetime.combine(start_date, datetime.min.time()),
                    datetime.combine(end_date, datetime.max.time())
                )
                st.success(f"✅ Exported trades to {export_path}")
            except Exception as e:
                st.error(f"❌ Export failed: {str(e)}")
    
    # Get recent trades
    recent_trades = journal.get_recent_trades(limit=20)
    
    if recent_trades:
        # Convert to DataFrame for display
        trades_df = pd.DataFrame(recent_trades)
        
        # Select and format columns
        display_cols = ['timestamp', 'symbol', 'direction', 'entry_price', 
                       'actual_entry_price', 'target_price', 'stop_loss',
                       'actual_exit_price', 'pnl_eur', 'pnl_percent', 'status']
        
        # Filter columns that exist
        display_cols = [col for col in display_cols if col in trades_df.columns]
        trades_df = trades_df[display_cols]
        
        # Format timestamps
        trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp']).dt.strftime('%Y-%m-%d %H:%M')
        
        # Style the dataframe
        def style_trades(df):
            def color_pnl(val):
                if pd.isna(val) or val == 0:
                    return ''
                color = 'green' if val > 0 else 'red'
                return f'color: {color}'
            
            def status_color(val):
                colors = {
                    'pending': 'color: gray',
                    'executed': 'color: blue',
                    'closed': 'color: black',
                    'cancelled': 'color: orange'
                }
                return colors.get(val, '')
            
            return df.style.map(color_pnl, subset=['pnl_eur', 'pnl_percent']).map(status_color, subset=['status'])
        
        st.dataframe(
            style_trades(trades_df),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No trades recorded yet. Trades will appear here after signal generation.")
    
    st.divider()
    
    # Performance Chart
    st.subheader("📈 Performance Over Time")
    
    # Get daily metrics for the last 30 days
    daily_metrics = metrics.get_metrics_by_period('daily', 30)
    
    if daily_metrics:
        # Create cumulative P&L chart
        metrics_df = pd.DataFrame(daily_metrics)
        metrics_df['date'] = pd.to_datetime(metrics_df['date'])
        metrics_df = metrics_df.sort_values('date')
        metrics_df['cumulative_pnl'] = metrics_df['total_pnl'].cumsum()
        
        # Plot using Plotly
        import plotly.graph_objects as go
        
        fig = go.Figure()
        
        # Add cumulative P&L line
        fig.add_trace(go.Scatter(
            x=metrics_df['date'],
            y=metrics_df['cumulative_pnl'],
            mode='lines+markers',
            name='Cumulative P&L',
            line=dict(color='green' if metrics_df['cumulative_pnl'].iloc[-1] > 0 else 'red', width=2),
            marker=dict(size=6)
        ))
        
        # Add daily P&L bars
        fig.add_trace(go.Bar(
            x=metrics_df['date'],
            y=metrics_df['total_pnl'],
            name='Daily P&L',
            marker_color=['green' if x > 0 else 'red' for x in metrics_df['total_pnl']],
            yaxis='y2',
            opacity=0.6
        ))
        
        # Update layout
        fig.update_layout(
            title='Trading Performance',
            xaxis_title='Date',
            yaxis_title='Cumulative P&L (€)',
            yaxis2=dict(
                title='Daily P&L (€)',
                overlaying='y',
                side='right'
            ),
            hovermode='x unified',
            height=400
        )
        
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Performance chart will be available after recording trades.")
    
    st.divider()
    
    # Quota Usage section
    st.subheader("🔧 API Quota Usage")
    
    from src.utils.quota import get_quota_guard
    quota_guard = get_quota_guard()
    
    # Get usage summary
    usage_summary = quota_guard.get_usage_summary(days=1)
    
    # Display quota metrics
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Total API Calls Today", usage_summary.get('total_calls', 0))
    with col2:
        st.metric("Estimated Cost", f"€{usage_summary.get('estimated_cost', 0):.2f}")
    with col3:
        if st.button("📊 Export Quota Report"):
            try:
                report_path = quota_guard.export_daily_summary()
                if report_path:
                    st.success(f"✅ Exported quota report to {report_path}")
            except Exception as e:
                st.error(f"❌ Export failed: {str(e)}")

# Footer
st.divider()
st.caption("⚠️ **Disclaimer**: This tool is for educational purposes only. Trading involves substantial risk of loss.")

# Auto-refresh
placeholder = st.empty()
if st.checkbox("Enable auto-refresh (5s)", value=True):
    # Rerun every 5 seconds to update with new events
    import time
    time.sleep(5)
    st.rerun()


def main():
    """Entry point for the dashboard"""
    # Dashboard is already running
    pass


if __name__ == "__main__":
    main()