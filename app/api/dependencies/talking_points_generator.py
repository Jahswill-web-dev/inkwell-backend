from typing import Annotated

from fastapi import Depends, Request

from app.core.exceptions import AppError
from app.services.ai_service import TalkingPointsGenerator


def get_talking_points_generator(request: Request) -> TalkingPointsGenerator:
    generator: TalkingPointsGenerator | None = request.app.state.talking_points_generator
    if generator is None:
        raise AppError(
            status_code=503,
            code="talking_points_generation_unavailable",
            message="Talking-point generation is not configured",
        )
    return generator


CurrentTalkingPointsGenerator = Annotated[
    TalkingPointsGenerator, Depends(get_talking_points_generator)
]
