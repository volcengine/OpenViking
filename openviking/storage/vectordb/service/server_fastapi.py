# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import asyncio
import random
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from openviking.storage.vectordb.service import api_fastapi
from openviking.storage.vectordb.service.api_fastapi import VikingDBException, error_response
from openviking_cli.utils.logger import default_logger as logger

# Active requests counter
active_requests = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events"""
    # Startup
    logger.info("============ start =============")
    random.seed(time.time_ns())
    yield
    # Shutdown
    logger.info("Waiting for active requests to complete...")
    while active_requests > 0:
        await asyncio.sleep(0.1)
    api_fastapi.clear_resource()
    logger.info("============ exit =============")


# Create FastAPI app
app = FastAPI(
    title="VikingDB API",
    description="Vector database service API",
    version="1.0.0",
    lifespan=lifespan,
)


# Exception handler
@app.exception_handler(VikingDBException)
async def vikingdb_exception_handler(request: Request, exc: VikingDBException):
    return JSONResponse(
        status_code=200, content=error_response(exc.message, exc.code.value, request=request)
    )


# Middleware to track request time and active requests
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    global active_requests
    active_requests += 1
    start_time = time.time()

    # Store start time in request state
    request.state.start_time = start_time

    try:
        response = await call_next(request)

        # Calculate time cost
        time_cost = time.time() - start_time

        # Add time cost header
        response.headers["X-Process-Time"] = str(round(time_cost, 6))

        return response
    finally:
        active_requests -= 1


# Register routers
app.include_router(api_fastapi.collection_router)
app.include_router(api_fastapi.data_router)
app.include_router(api_fastapi.index_router)
app.include_router(api_fastapi.search_router)


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "VikingDB API Server", "version": "1.0.0"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "active_requests": active_requests}


if __name__ == "__main__":
    try:
        uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
