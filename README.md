# One-Day Trading Agent (ODTA)

A free-tier Python trading assistant that recommends 5 US stocks daily with 7-10% intraday profit potential for a €500 bankroll via Revolut.

## Features

- 🎯 **Smart Stock Screening**: Identifies high-probability intraday opportunities
- 📊 **Real-time Data**: WebSocket streaming with intelligent fallback
- 🛡️ **Risk Management**: Enforced €33 daily loss cap and €250 position limits
- 📈 **Interactive Dashboard**: Streamlit UI with live updates and charts
- 🆓 **Free-Tier Friendly**: Smart quota management across multiple APIs
- 🤖 **Automated Scanning**: Scheduled scans at 14:00 and 18:15 CET

## Quick Start

### Prerequisites

- Python 3.11+
- Revolut trading account
- Free API keys from:
  - [Finnhub](https://finnhub.io/)
  - Yahoo Finance (delayed quotes)
  - [Alpha Vantage](https://www.alphavantage.co/)
  - [NewsAPI](https://newsapi.org/)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/fin_agent_v0_050625.git
cd fin_agent_v0_050625
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment:
```bash
cp .env.template .env
# Edit .env with your API keys
```

5. Add your Revolut universe:
```bash
cp data/universe/revolut_universe_template.csv data/universe/revolut_universe.csv
# Edit with your tradable symbols
```

### Running the Agent

#### Interactive Dashboard
```bash
streamlit run dashboard.py
```

#### Command Line
```bash
# Run primary scan
python -m src.main scan

# Run second-look scan
python -m src.main second-look

# Check system status
python -m src.main status
```

## Architecture

```
├── src/
│   ├── config/        # Configuration management
│   ├── data/          # API adapters and caching
│   ├── domain/        # Core trading logic
│   ├── orchestration/ # Workflow coordination
│   ├── persistence/   # Logging and journaling
│   └── utils/         # Shared utilities
├── dashboard.py       # Streamlit UI
├── data/             # Cache and universe files
├── logs/             # Trading journal and quotas
└── tests/            # Test suite
```

## Usage Guide

### Daily Workflow

1. **14:00 CET**: Primary scan runs automatically
2. **14:20 CET**: Review Top-5 recommendations in dashboard
3. **15:30-16:15 CET**: Execute trades on Revolut
4. **18:15 CET**: Optional second-look scan for late opportunities

### Dashboard Features

- **Sidebar**: Adjust factor weights and settings
- **Main Panel**: Top-5 recommendations with trade plans
- **Charts**: Interactive candlestick charts
- **Alerts**: Real-time notifications for quotas and errors

### Risk Controls

- Maximum daily loss: €33
- Maximum position size: €250
- Automatic stop-loss: -3% or 2×ATR
- PRIIPs compliance checking

## API Quota Management

The agent intelligently manages free-tier quotas:

| Provider | Limit | Usage |
|----------|-------|--------|
| Finnhub | 60/min | Real-time quotes |
| IEX Cloud | 50k/month | Fallback quotes |
| Alpha Vantage | 25/day | Historical data |
| NewsAPI | 1000/day | Headlines |

## Development

### Running Tests
```bash
pytest tests/ -v --cov=src
```

### Code Quality
```bash
black src/ tests/
flake8 src/ tests/
mypy src/
```

### Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## Troubleshooting

### Common Issues

1. **WebSocket Connection Failed**
   - Check Finnhub API key
   - Verify internet connectivity
   - System will auto-fallback to REST APIs

2. **Quota Exceeded**
   - Check logs/quotas.csv
   - Wait for quota reset
   - Consider upgrading API plan

3. **No Recommendations**
   - Verify market hours (US markets)
   - Check universe file has valid symbols
   - Review logs for filtering issues

## License

MIT License - see LICENSE file for details

## Disclaimer

This software is for educational purposes only. Trading involves substantial risk of loss. Past performance does not guarantee future results. Always do your own research and trade responsibly.

## Support

- Documentation: [docs/](docs/)
- Issues: [GitHub Issues](https://github.com/yourusername/fin_agent_v0_050625/issues)
- Email: support@example.com

---

Built with ❤️ for retail traders by [Your Name]