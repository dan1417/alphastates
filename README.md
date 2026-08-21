# Bittensor Blockchain Indexer & API

A scalable Python-based system for indexing and querying Bittensor blockchain data. The system consists of multiple independent indexers and a FastAPI server, all sharing PostgreSQL and Redis for data storage and caching.

## Architecture

The system is composed of the following independent services:

- **OHLC Indexer**: Indexes trade data and generates OHLC (Open, High, Low, Close) candles
- **Holder Indexer**: Indexes token holder data (placeholder for future implementation)
- **API Server**: FastAPI-based REST API for querying indexed data
- **Frontend**: Lightweight Alpine.js web interface for data visualization
- **PostgreSQL**: Shared database for all services
- **Redis**: Shared cache for all services

## Requirements

- Python 3.10+
- Docker & Docker Compose

## Project Structure

```
server/
├── docker-compose.yml              # All service definitions
├── Dockerfile.ohlc-indexer         # OHLC indexer container
├── Dockerfile.holder-indexer       # Holder indexer container
├── Dockerfile.api                  # API server container
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment variables template
├── static/                         # Frontend static files
│   ├── css/                        # Stylesheets
│   ├── js/                         # JavaScript files
│   ├── index.html                  # Landing page
│   └── subnet.html                 # Subnet analytics page
├── src/
│   ├── __init__.py
│   ├── common/                     # Shared utilities
│   │   ├── __init__.py
│   │   ├── config.py               # Configuration settings
│   │   ├── db.py                   # PostgreSQL connection
│   │   ├── cache.py                # Redis connection
│   │   └── models.py               # SQLAlchemy models
│   ├── indexers/                   # Blockchain indexers
│   │   ├── __init__.py
│   │   ├── ohlc/                   # OHLC indexer
│   │   │   ├── __init__.py
│   │   │   ├── main.py             # Entry point
│   │   │   └── processor.py       # Processing logic
│   │   └── holder/                 # Holder indexer (placeholder)
│   │       ├── __init__.py
│   │       ├── main.py             # Entry point
│   │       └── processor.py       # Processing logic
│   └── api/                        # FastAPI server
│       ├── __init__.py
│       ├── main.py                 # FastAPI app
│       └── routes/                 # API routes
│           ├── __init__.py
│           ├── ohlc.py             # OHLC endpoints
│           └── holder.py           # Holder endpoints
└── test.py
```

## Quick Start

### Using Docker Compose (Recommended)

1. (Optional) Create a `.env` file to customize configuration:
   ```bash
   # Create .env file with your custom values
   echo "RPC_URL=ws://your-rpc-url:9944" >> .env
   echo "BATCH_SIZE=5000" >> .env
   echo "API_PORT=8000" >> .env
   ```

2. Start all services (OHLC indexer + API):
   ```bash
   docker compose up -d
   ```

3. Start with holder indexer included:
   ```bash
   docker compose --profile holder up -d
   ```

4. Start only specific services:
   ```bash
   # Only OHLC indexer
   docker compose up -d postgres redis ohlc-indexer
   
   # Only API server
   docker compose up -d postgres redis api
   
   # Only holder indexer
   docker compose up -d postgres redis holder-indexer
   ```

5. View logs:
   ```bash
   # All services
   docker compose logs -f
   
   # Specific service
   docker compose logs -f ohlc-indexer
   docker compose logs -f holder-indexer
   docker compose logs -f api
   ```

6. Stop services:
   ```bash
   docker compose down
   ```

### Local Development

1. Create virtual environment:
   ```bash
   python3.10 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Start PostgreSQL and Redis:
   ```bash
   docker compose up -d postgres redis
   ```

4. Run individual services:
   ```bash
   # OHLC indexer
   python -m src.indexers.ohlc.main
   
   # Holder indexer (not yet implemented)
   python -m src.indexers.holder.main
   
   # API server
   uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
   ```

## Configuration

All configuration is done via environment variables. Create a `.env` file in the project root with the following options:

### Database & Cache

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `indexer` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `indexer` | PostgreSQL password |
| `POSTGRES_DB` | `bittensor` | PostgreSQL database name |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |

### Bittensor Blockchain (Indexers)

| Variable | Default | Description |
|----------|---------|-------------|
| `RPC_URL` | `ws://185.189.45.20:9944` | Bittensor RPC endpoint |
| `DTAO_START_BLOCK` | `4920351` | Starting block for indexing |
| `BATCH_SIZE` | `10000` | Number of blocks to process per batch |
| `MAX_WORKERS` | `50` | Number of worker processes |
| `RESET_DB` | `false` | Delete all data before starting indexing |

### API Server

| Variable | Default | Description |
|----------|---------|-------------|
| `API_PORT` | `8000` | API server port |

## API Endpoints

Once the API server is running, you can access:

- **Documentation**: http://localhost:8000/docs (Swagger UI)
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

### OHLC Endpoints (Placeholder)

- `GET /api/v1/ohlc/candles/{interval}/{netuid}` - Get OHLC candles
- `GET /api/v1/ohlc/latest/{interval}/{netuid}` - Get latest candle
- `GET /api/v1/ohlc/trades/{netuid}` - Get recent trades

### Holder Endpoints (Placeholder)

- `GET /api/v1/holder/holders/{netuid}` - Get token holders
- `GET /api/v1/holder/holder/{address}/{netuid}` - Get holder history
- `GET /api/v1/holder/distribution/{netuid}` - Get holder distribution

## Frontend Web Interface

The API server also serves a lightweight web interface for data visualization.

### Accessing the Frontend

- **Landing Page**: http://localhost:8000/
- **Subnet Analytics**: http://localhost:8000/subnet/{netuid}

### Features

- **OHLC Candlestick Charts**: Interactive charts with multiple time intervals (1m, 5m, 15m, 30m, 1h, 4h, 1d)
- **Holder Distribution**: Histogram visualization of token holder distribution (mock data for now)
- **Real-time Stats**: Current price, 24h volume, high/low prices
- **Responsive Design**: Works on desktop, tablet, and mobile devices

### Technology Stack

- **Alpine.js**: Lightweight reactive JavaScript framework
- **Lightweight Charts**: TradingView's charting library for financial data
- **Vanilla CSS**: Custom styling with CSS variables for theming

### Documentation

For detailed frontend documentation, see [FRONTEND.md](FRONTEND.md).

## Service Independence

Each indexer runs completely independently:

- **OHLC Indexer**: Processes trade events and generates OHLC candles
- **Holder Indexer**: Processes holder data (to be implemented)
- **API Server**: Queries indexed data from the shared database

You can run any combination of services based on your needs. All services share the same PostgreSQL database and Redis cache.

## Testing

```bash
source .venv/bin/activate
pytest
```

## Development Notes

### Adding a New Indexer

1. Create a new directory under `src/indexers/your_indexer/`
2. Implement `main.py` and `processor.py`
3. Create a new Dockerfile: `Dockerfile.your-indexer`
4. Add service definition to `docker-compose.yml`
5. Update this README

### Adding New API Endpoints

1. Create a new route file in `src/api/routes/your_route.py`
2. Import and include the router in `src/api/main.py`
3. Implement endpoint logic using shared models from `src/common/models.py`

## License

TBD
