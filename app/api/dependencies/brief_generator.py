from typing import Annotated

from fastapi import Depends, Request

from app.core.exceptions import AppError
from app.services.ai_service import BriefGenerator


def get_brief_generator(request: Request) -> BriefGenerator:
    generator: BriefGenerator | None = request.app.state.brief_generator
    if generator is None:
        raise AppError(
            status_code=503,
            code="brief_generation_unavailable",
            message="Brief generation is not configured",
        )
    return generator


CurrentBriefGenerator = Annotated[BriefGenerator, Depends(get_brief_generator)]
