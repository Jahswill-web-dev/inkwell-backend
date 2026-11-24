from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.prompts.consultant import DIRECT_DRAFT_INSTRUCTION
from app.schemas.consultant import ConsultantArticleInput
from app.services.consultant_writer import ConsultantWriter


@pytest.mark.parametrize("length", ["short", "standard", "long", "extended", "in_depth"])
def test_consultant_accepts_supported_lengths(length: str) -> None:
    article = ConsultantArticleInput.model_validate(
        {"title": "An idea", "description": "A perspective", "length": length}
    )
    assert article.length == length


def test_unknown_length_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConsultantArticleInput.model_validate(
            {"title": "An idea", "description": "A perspective", "length": "unlimited"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "length,budget", [("standard", 2500), ("extended", 12000), ("in_depth", 20000)]
)
async def test_draft_budget_and_instructions_support_longer_articles(
    length: str, budget: int
) -> None:
    writer = object.__new__(ConsultantWriter)
    writer.parse = AsyncMock()  # type: ignore[method-assign]
    await writer.write({"article": {"title": "Handoffs", "length": length}, "turns": []})
    args = writer.parse.call_args
    assert args.kwargs["max_output_tokens"] == budget
    assert args.args[2]["article"]["length"] == length
    assert args.kwargs["instructions_override"] == DIRECT_DRAFT_INSTRUCTION
    assert "in_depth 3000-5000" in DIRECT_DRAFT_INSTRUCTION
    assert "never pad, repeat, or invent" in DIRECT_DRAFT_INSTRUCTION
