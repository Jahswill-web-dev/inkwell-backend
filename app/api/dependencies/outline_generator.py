from typing import Annotated

from fastapi import Depends, Request

from app.core.exceptions import AppError
from app.services.ai_service import OutlineGenerator


def get_outline_generator(request: Request) -> OutlineGenerator:
    generator: OutlineGenerator | None = request.app.state.outline_generator
    if generator is None:
        raise AppError(
            status_code=503,
            code="outline_generation_unavailable",
            message="Outline generation is not configured",
        )
    return generator


CurrentOutlineGenerator = Annotated[OutlineGenerator, Depends(get_outline_generator)]
