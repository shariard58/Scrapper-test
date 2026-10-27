from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import uvicorn
from app.database import init_db, close_db
from app.routers import coupon_scanner, collect_coupons, collect_links
from app.lib.logger import get_logger

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting FastAPI application...")
    init_db()
    yield
    # Shutdown
    logger.info("Shutting down FastAPI application...")
    close_db()

app = FastAPI(
    title="Coupon Scanner API",
    description="FastAPI application for coupon scanning and collection",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers
app.include_router(coupon_scanner.router, prefix="/api/v1", tags=["Coupon Scanner"])
app.include_router(collect_coupons.router, prefix="/api/v1", tags=["Collect Coupons"])
app.include_router(collect_links.router, prefix="/api/v1", tags=["Collect Links"])

@app.get("/")
async def root():
    return {"message": "Coupon Scanner API", "version": "1.0.0", "status": "running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
