from fastapi import APIRouter

from app.api.v1 import (
    article_briefs,
    articles,
    auth,
    drafts,
    jobs,
    outlines,
    reviews,
    section_interviews,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(articles.router)
router.include_router(article_briefs.router)
router.include_router(outlines.router)
router.include_router(drafts.router)
router.include_router(section_interviews.router)
router.include_router(reviews.router)
router.include_router(jobs.router)
