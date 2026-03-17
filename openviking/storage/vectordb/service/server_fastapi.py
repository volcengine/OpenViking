# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""FastAPI server for VikingDB vector database service.

This module provides a REST API server for VikingDB operations including
collection management, data operations, indexing, and vector search.
"""

import asyncio
import random
import time
from contextlib import asynccontextmanager
from typing import Dict, Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from openviking.storage.vectordb.service import api_fastapi
from openviking.storage.vectordb.service.api_fastapi import VikingDBException, error_response
from openviking_cli.utils.logger import default_logger as logger

# Global counter for tracking active requests
_active_requests = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events.
    
    Manages resource initialization and cleanup, ensuring graceful shutdown
    by waiting for all active requests to complete.
    
    Args:
        app: The FastAPI application instance
    """
    # Startup
    logger.info("============ VikingDB Server Starting =============")
    random.seed(time.time_ns())
    
    yield
    
    # Shutdown
    logger.info("Waiting for active requests to complete...")
    while _active_requests > 0:
        await asyncio.sleep(0.1)
    api_fastapi.clear_resource()
    logger.info("============ VikingDB Server Stopped =============")


# Create FastAPI application instance
app = FastAPI(
    title="VikingDB API",
    description="Vector database service API for managing collections, data, indexes, and search operations",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(VikingDBException)
async def vikingdb_exception_handler(request: Request, exc: VikingDBException) -> JSONResponse:
    """Handle VikingDB-specific exceptions.
    
    Args:
        request: The incoming HTTP request
        exc: The VikingDBException that was raised
        
    Returns:
        JSONResponse with error details
    """
    return JSONResponse(
        status_code=200, 
        content=error_response(exc.message, exc.code.value, request=request)
    )


@app.middleware("http")
async def request_tracking_middleware(request: Request, call_next):
    """Middleware to track request processing time and active request count.
    
    Increments active request counter, measures processing time,
    and adds processing time header to response.
    
    Args:
        request: The incoming HTTP request
        call_next: The next middleware/handler in the chain
        
    Returns:
        Response with added X-Process-Time header
    """
    global _active_requests
    _active_requests += 1
    start_time = time.time()

    # Store start time in request state for potential future use
    request.state.start_time = start_time

    try:
        response = await call_next(request)

        # Calculate and add processing time header
        time_cost = time.time() - start_time
        response.headers["X-Process-Time"] = str(round(time_cost, 6))

        return response
    finally:
        _active_requests -= 1


# Register API routers for different operation types
app.include_router(api_fastapi.collection_router)
app.include_router(api_fastapi.data_router)
app.include_router(api_fastapi.index_router)
app.include_router(api_fastapi.search_router)


@app.get("/")
async def root() -> Dict[str, str]:
    """Root endpoint providing basic server information.
    
    Returns:
        Dict containing server name and version
    """
    return {"message": "VikingDB API Server", "version": "1.0.0"}


@app.get("/health")
async def health() -> Dict[str, Any]:
    """Health check endpoint for monitoring server status.
    
    Returns:
        Dict containing health status and current active request count
    """
    return {"status": "healthy", "active_requests": _active_requests}


if __name__ == "__main__":
    try:
        logger.info("Starting VikingDB server on 0.0.0.0:5000")
        uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")
    except Exception as e:
        logger.error(f"Failed to start VikingDB server: {e}")