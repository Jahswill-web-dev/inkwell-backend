from fastapi import APIRouter

from app.api.v1 import (
    article_briefs,
    articles,
    auth,
    clients,
    drafts,
    interview_invitations,
    jobs,
    outlines,
    reviews,
    section_interviews,
    workspaces,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(workspaces.router)
router.include_router(clients.router)
router.include_router(articles.router)
router.include_router(interview_invitations.router)
router.include_router(article_briefs.router)
router.include_router(outlines.router)
router.include_router(drafts.router)
router.include_router(section_interviews.router)
router.include_router(reviews.router)
router.include_router(jobs.router)
