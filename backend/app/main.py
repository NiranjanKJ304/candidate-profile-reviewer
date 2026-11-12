"""FastAPI application factory.

Run with the factory flag so settings are only loaded at startup::

    uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.cache.redis import create_redis
from app.core.config import Settings, get_settings
from app.core.errors import UnhandledExceptionMiddleware, register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.db.session import create_engine, create_session_factory
from app.integrations.llm import ChatCompletionClient
from app.services.job_description.processor import JobDescriptionProcessor
from app.services.job_description.structuring import JobDescriptionStructurer
from app.services.resume.extraction import OcrConfig, ocr_available
from app.services.resume.processor import ResumeProcessor
from app.services.resume.storage import ResumeStorage
from app.services.resume.structuring import ResumeStructurer

logger = logging.getLogger("app")


def build_resume_pipeline(app: FastAPI, settings: Settings) -> None:
    """Create the resume storage, LLM client and processor on ``app.state``."""
    storage = ResumeStorage(settings.resume_storage_dir)
    storage.ensure_ready()
    ocr = OcrConfig(
        enabled=settings.ocr_enabled,
        tessdata_dir=settings.ocr_tessdata_dir,
        language=settings.ocr_language,
        dpi=settings.ocr_dpi,
    )
    llm_client = ChatCompletionClient.from_settings(settings, app.state.http_client)
    structurer = ResumeStructurer(
        llm_client,
        max_attempts=settings.llm_max_attempts,
        max_input_chars=settings.llm_max_input_chars,
    )
    app.state.resume_storage = storage
    app.state.ocr_config = ocr
    app.state.llm_client = llm_client
    app.state.resume_processor = ResumeProcessor(
        session_factory=app.state.session_factory,
        storage=storage,
        structurer=structurer,
        ocr=ocr,
    )

    # Job descriptions reuse OCR, the LLM client and the storage implementation.
    jd_storage = ResumeStorage(
        settings.job_description_storage_dir, extensions=("pdf", "docx", "txt")
    )
    jd_storage.ensure_ready()
    app.state.jd_storage = jd_storage
    app.state.jd_processor = JobDescriptionProcessor(
        session_factory=app.state.session_factory,
        storage=jd_storage,
        structurer=JobDescriptionStructurer(
            llm_client,
            max_attempts=settings.llm_max_attempts,
            max_input_chars=settings.llm_max_input_chars,
        ),
        ocr=ocr,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db_engine = create_engine(settings)
        app.state.session_factory = create_session_factory(app.state.db_engine)
        app.state.redis = create_redis(settings)
        app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
            follow_redirects=False,
        )
        build_resume_pipeline(app, settings)
        logger.info(
            "application started",
            extra={
                "environment": settings.environment,
                "version": __version__,
                "database": settings.database_url_redacted,
                "enabled_platforms": [p.value for p in settings.enabled_platforms],
                "llm_provider": settings.llm_provider,
                "llm_model": settings.llm_resolved_model,
                "llm_configured": app.state.llm_client.configured,
                "ocr_available": ocr_available(app.state.ocr_config),
                "resume_storage": str(app.state.resume_storage.root),
            },
        )
        try:
            yield
        finally:
            await app.state.http_client.aclose()
            await app.state.redis.aclose()
            await app.state.db_engine.dispose()
            logger.info("application stopped")

    docs_enabled = not settings.is_production
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    # Routes resolve settings through this dependency; pin it to the instance used here.
    app.dependency_overrides[get_settings] = lambda: settings

    register_exception_handlers(app)

    # Middleware: the last added is the outermost.
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app
