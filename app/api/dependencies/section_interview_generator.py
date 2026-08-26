from typing import Annotated

from fastapi import Depends, Request

from app.core.exceptions import AppError
from app.services.section_interview_ai import SectionInterviewGenerator


def _get_generator(request: Request, *, operation: str) -> SectionInterviewGenerator:
    generator: SectionInterviewGenerator | None = request.app.state.section_interview_generator
    if generator is None:
        raise AppError(
            status_code=503,
            code=f"{operation}_generation_unavailable",
            message="Section generation is not configured",
        )
    return generator


def get_section_questions_generator(request: Request) -> SectionInterviewGenerator:
    return _get_generator(request, operation="section_questions")


def get_section_draft_generator(request: Request) -> SectionInterviewGenerator:
    return _get_generator(request, operation="section_draft")


CurrentSectionQuestionsGenerator = Annotated[
    SectionInterviewGenerator, Depends(get_section_questions_generator)
]
CurrentSectionDraftGenerator = Annotated[
    SectionInterviewGenerator, Depends(get_section_draft_generator)
]
CurrentDirectSectionDraftGenerator = Annotated[
    SectionInterviewGenerator, Depends(get_section_draft_generator)
]
