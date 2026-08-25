from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google.auth.exceptions import GoogleAuthError

from app.api.health import router as health_router
from app.api.v1.router import router as v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.db.session import create_engine, create_session_factory
from app.services.ai_service import (
    BriefGenerator,
    OutlineGenerator,
    TalkingPointsGenerator,
    VertexGeminiBriefGenerator,
    VertexGeminiOutlineGenerator,
    VertexGeminiTalkingPointsGenerator,
)
from app.services.openrouter_ai import (
    OpenRouterBriefGenerator,
    OpenRouterJSONClient,
    OpenRouterOutlineGenerator,
    OpenRouterSectionInterviewGenerator,
    OpenRouterTalkingPointsGenerator,
)
from app.services.section_interview_ai import (
    SectionInterviewGenerator,
    VertexGeminiSectionInterviewGenerator,
)

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_app(
    settings: Settings | None = None,
    *,
    brief_generator: BriefGenerator | None = None,
    outline_generator: OutlineGenerator | None = None,
    talking_points_generator: TalkingPointsGenerator | None = None,
    section_interview_generator: SectionInterviewGenerator | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(resolved_settings)
        managed_generator: VertexGeminiBriefGenerator | None = None
        managed_outline_generator: VertexGeminiOutlineGenerator | None = None
        managed_talking_points_generator: VertexGeminiTalkingPointsGenerator | None = None
        managed_section_interview_generator: VertexGeminiSectionInterviewGenerator | None = None
        managed_openrouter_client: OpenRouterJSONClient | None = None
        resolved_generator = brief_generator
        resolved_outline_generator = outline_generator
        resolved_talking_points_generator = talking_points_generator
        resolved_section_interview_generator = section_interview_generator
        if (
            resolved_settings.ai_provider == "vertex"
            and resolved_settings.vertex_project_id is not None
        ):
            try:
                if resolved_generator is None:
                    managed_generator = VertexGeminiBriefGenerator(resolved_settings)
                    resolved_generator = managed_generator
                if resolved_outline_generator is None:
                    managed_outline_generator = VertexGeminiOutlineGenerator(resolved_settings)
                    resolved_outline_generator = managed_outline_generator
                if resolved_talking_points_generator is None:
                    managed_talking_points_generator = VertexGeminiTalkingPointsGenerator(
                        resolved_settings
                    )
                    resolved_talking_points_generator = managed_talking_points_generator
                if resolved_section_interview_generator is None:
                    managed_section_interview_generator = VertexGeminiSectionInterviewGenerator(
                        resolved_settings
                    )
                    resolved_section_interview_generator = managed_section_interview_generator
            except GoogleAuthError:
                logger.exception("Vertex AI credentials could not be initialized")
        elif (
            resolved_settings.ai_provider == "openrouter"
            and resolved_settings.openrouter_api_key is not None
            and any(
                generator is None
                for generator in (
                    resolved_generator,
                    resolved_outline_generator,
                    resolved_talking_points_generator,
                    resolved_section_interview_generator,
                )
            )
        ):
            managed_openrouter_client = OpenRouterJSONClient(resolved_settings)
            if resolved_generator is None:
                resolved_generator = OpenRouterBriefGenerator(managed_openrouter_client)
            if resolved_outline_generator is None:
                resolved_outline_generator = OpenRouterOutlineGenerator(managed_openrouter_client)
            if resolved_talking_points_generator is None:
                resolved_talking_points_generator = OpenRouterTalkingPointsGenerator(
                    managed_openrouter_client
                )
            if resolved_section_interview_generator is None:
                resolved_section_interview_generator = OpenRouterSectionInterviewGenerator(
                    managed_openrouter_client
                )
        application.state.engine = engine
        application.state.session_factory = create_session_factory(engine)
        application.state.brief_generator = resolved_generator
        application.state.outline_generator = resolved_outline_generator
        application.state.talking_points_generator = resolved_talking_points_generator
        application.state.section_interview_generator = resolved_section_interview_generator
        try:
            yield
        finally:
            if managed_generator is not None:
                await managed_generator.close()
            if managed_outline_generator is not None:
                await managed_outline_generator.close()
            if managed_talking_points_generator is not None:
                await managed_talking_points_generator.close()
            if managed_section_interview_generator is not None:
                await managed_section_interview_generator.close()
            if managed_openrouter_client is not None:
                await managed_openrouter_client.close()
            await engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        debug=resolved_settings.debug,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    register_exception_handlers(application)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Retry-After"],
    )
    application.include_router(health_router)
    application.include_router(v1_router, prefix=resolved_settings.api_v1_prefix)
    return application


app = create_app()
