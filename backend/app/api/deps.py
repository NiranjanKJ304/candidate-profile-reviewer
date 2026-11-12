"""Shared FastAPI dependencies for v1 routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.services.job_description.processor import JobDescriptionProcessor
from app.services.resume.processor import ResumeProcessor
from app.services.resume.storage import ResumeStorage


def get_resume_processor(request: Request) -> ResumeProcessor:
    processor: ResumeProcessor = request.app.state.resume_processor
    return processor


def get_resume_storage(request: Request) -> ResumeStorage:
    storage: ResumeStorage = request.app.state.resume_storage
    return storage


def get_jd_processor(request: Request) -> JobDescriptionProcessor:
    processor: JobDescriptionProcessor = request.app.state.jd_processor
    return processor


def get_jd_storage(request: Request) -> ResumeStorage:
    storage: ResumeStorage = request.app.state.jd_storage
    return storage


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ProcessorDep = Annotated[ResumeProcessor, Depends(get_resume_processor)]
StorageDep = Annotated[ResumeStorage, Depends(get_resume_storage)]
JDProcessorDep = Annotated[JobDescriptionProcessor, Depends(get_jd_processor)]
JDStorageDep = Annotated[ResumeStorage, Depends(get_jd_storage)]
