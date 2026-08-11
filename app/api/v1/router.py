from fastapi import APIRouter

from app.api.v1 import articles, auth, drafts, jobs, outlines, reviews

router = APIRouter()
router.include_router(auth.router)
router.include_router(articles.router)
router.include_router(outlines.router)
router.include_router(drafts.router)
router.include_router(reviews.router)
router.include_router(jobs.router)
