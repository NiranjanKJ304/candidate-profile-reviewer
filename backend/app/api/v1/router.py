"""Aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1 import (
    candidates,
    codeforces,
    github,
    health,
    job_descriptions,
    leetcode,
    resumes,
    system,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(system.router)
api_router.include_router(candidates.router)
api_router.include_router(resumes.router)
api_router.include_router(job_descriptions.router)
api_router.include_router(github.router)
api_router.include_router(codeforces.router)
api_router.include_router(leetcode.router)
