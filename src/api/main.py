"""FastAPI server for querying indexed blockchain data."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from jose import JWTError, jwt

from src.common.config import settings
from src.common.db import init_db, close_db
from src.common.cache import init_cache, close_cache
from src.common.subtensor_pool import init_subtensor_pool, close_subtensor_pool
from src.api.routes import ohlc, holder, trade, transfer, address, auth, events, stake, config, proxy, subnets

# Cache busting version - update this when deploying new static files
# Or read from a VERSION file
try:
    with open("VERSION", "r") as f:
        STATIC_VERSION = f.read().strip()
except FileNotFoundError:
    # Fallback to timestamp if VERSION file doesn't exist
    STATIC_VERSION = str(int(datetime.utcnow().timestamp()))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    # Startup
    logger.info("Starting API server...")
    await init_db()
    await init_cache()
    # Initialize subtensor connection pool
    await init_subtensor_pool(settings.local_rpc_url, pool_size=settings.subtensor_pool_size)
    logger.info("API server ready")
    
    yield
    
    # Shutdown
    logger.info("Shutting down API server...")
    await close_subtensor_pool()
    await close_cache()
    await close_db()
    logger.info("API server stopped")


app = FastAPI(
    title="Bittensor Blockchain API",
    description="API for querying indexed Bittensor blockchain data",
    version="1.0.0",
    lifespan=lifespan,
)

def get_user_from_token(request: Request) -> Optional[str]:
    """Extract user email from JWT token in Authorization header."""
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        
        token = auth_header.split(" ")[1]
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        email = payload.get("sub")
        return email if email else None
    except (JWTError, KeyError, IndexError):
        return None


# Request logging and cache control middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests with user information and add cache control headers."""
    start_time = time.time()
    
    # Extract user information from token
    user_email = get_user_from_token(request)
    user_info = f" [{user_email}]" if user_email else ""
    
    # Log the request with user info
    logger.info(f"→ {request.method} {request.url.path}{user_info}")
    
    # Process the request
    response = await call_next(request)
    
    # Add cache control headers for static files to prevent caching issues
    if request.url.path.startswith("/static/"):
        # For CSS, JS, and HTML files, don't cache or revalidate
        if any(request.url.path.endswith(ext) for ext in [".css", ".js", ".html"]):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        # For images and other assets, allow short-term caching
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"  # 1 hour
    
    # Log the response with user info
    process_time = time.time() - start_time
    logger.info(f"← {request.method} {request.url.path} - {response.status_code} ({process_time:.3f}s){user_info}")
    
    return response

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
# Auth routes (public)
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])

# Protected API routes
app.include_router(ohlc.router, prefix="/api/v1/ohlc", tags=["OHLC"])
app.include_router(holder.router, prefix="/api/v1/holder", tags=["Holder"])
app.include_router(trade.router, prefix="/api/v1/trade", tags=["Trade"])
app.include_router(transfer.router, prefix="/api/v1/transfer", tags=["Transfer"])
app.include_router(address.router, prefix="/api/v1/address", tags=["Address"])
app.include_router(events.router, prefix="/api/v1/events", tags=["Events"])
app.include_router(stake.router, prefix="/api/v1/stake", tags=["Stake"])
app.include_router(config.router, prefix="/api/v1/config", tags=["Config"])
app.include_router(proxy.router, prefix="/api/v1/proxy", tags=["Proxy"])
app.include_router(subnets.router, prefix="/api/v1/subnets", tags=["Subnets"])


def inject_version_and_no_cache(file_path: str) -> HTMLResponse:
    """Inject version parameter into static file references and add no-cache headers."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Inject version parameter into CSS, JS references
    content = content.replace('href="/static/css/', f'href="/static/css/')
    content = content.replace('src="/static/js/', f'src="/static/js/')
    content = content.replace('.css"', f'.css?v={STATIC_VERSION}"')
    content = content.replace('.js"', f'.js?v={STATIC_VERSION}"')
    
    # Add cache control meta tags if not already present
    if '<meta' in content and 'http-equiv="Cache-Control"' not in content:
        meta_tags = '''
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">'''
        content = content.replace('<meta charset="UTF-8">', '<meta charset="UTF-8">' + meta_tags)
    
    response = HTMLResponse(content=content)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/")
async def root():
    """Root endpoint - redirects to auth page."""
    return inject_version_and_no_cache("static/auth.html")


@app.get("/home")
async def home():
    """Home page (requires authentication on client side)."""
    return inject_version_and_no_cache("static/index.html")


@app.get("/subnet/{netuid}")
async def subnet_page(netuid: int):
    """Serve subnet page."""
    return inject_version_and_no_cache("static/subnet.html")


@app.get("/subnets")
async def subnets_page():
    """Serve all-subnets table page."""
    return inject_version_and_no_cache("static/subnets.html")


@app.get("/connections/{address}")
async def connections_page_with_address(address: str):
    """Serve connections page with specific address."""
    return inject_version_and_no_cache("static/connections.html")


@app.get("/addresses")
async def addresses_page():
    """Serve address management page."""
    return inject_version_and_no_cache("static/addresses.html")


@app.get("/account/{coldkey_address}")
async def account_page(coldkey_address: str):
    """Serve account page with stake information."""
    return inject_version_and_no_cache("static/account.html")


@app.get("/strategy/{address}")
async def strategy_page(address: str):
    """Serve strategy analysis page for a proxy delegate address."""
    return inject_version_and_no_cache("static/strategy.html")


@app.get("/about")
async def about_page():
    """Serve about page."""
    return inject_version_and_no_cache("static/about.html")


@app.get("/terms")
async def terms_page():
    """Serve terms of service page."""
    return inject_version_and_no_cache("static/terms.html")


@app.get("/test")
async def test_page():
    """Test page."""
    return inject_version_and_no_cache("static/test.html")


@app.get("/events")
async def events_page():
    """Events dashboard page."""
    return inject_version_and_no_cache("static/events.html")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

