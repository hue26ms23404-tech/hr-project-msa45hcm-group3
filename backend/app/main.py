from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.api import api_router
from app.core.ai_errors import install_ai_errors
from app.ai.providers.gemini import readiness
import json
import logging

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    docs_url=f"{settings.API_V1_PREFIX}/docs",
    redoc_url=f"{settings.API_V1_PREFIX}/redoc",
)


@app.on_event("startup")
async def log_ai_readiness():
    logging.getLogger(__name__).info(json.dumps({"event": "ai_provider_readiness", **readiness()}))
install_ai_errors(app, (f"{settings.API_V1_PREFIX}/ai", f"{settings.API_V1_PREFIX}/reports"))

# Set up CORS
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health", tags=["Health"])
async def health_check():
    """Service health check endpoint"""
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "version": "1.0.0",
    }


# Include all v1 API routers
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# Docker copies the React production build to /app/static.
# Local API-only development continues to use the separate Vite server.
from app.frontend import mount_frontend

mount_frontend(app)
