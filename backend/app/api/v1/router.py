"""Aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1 import candidates, health, job_descriptions, resumes, system

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(system.router)
api_router.include_router(candidates.router)
api_router.include_router(resumes.router)
api_router.include_router(job_descriptions.router)
