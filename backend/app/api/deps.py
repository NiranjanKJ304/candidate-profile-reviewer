"""Shared FastAPI dependencies for v1 routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.integrations.codeforces import CodeforcesClient
from app.integrations.github import GitHubClient
from app.services.codeforces.sync import CodeforcesSyncService
from app.services.evaluation.processor import EvaluationProcessor
from app.services.github.sync import GitHubSyncService
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


def get_github_sync(request: Request) -> GitHubSyncService:
    syncer: GitHubSyncService = request.app.state.github_sync
    return syncer


def get_github_client(request: Request) -> GitHubClient:
    client: GitHubClient = request.app.state.github_client
    return client


def get_codeforces_sync(request: Request) -> CodeforcesSyncService:
    syncer: CodeforcesSyncService = request.app.state.codeforces_sync
    return syncer


def get_codeforces_client(request: Request) -> CodeforcesClient:
    client: CodeforcesClient = request.app.state.codeforces_client
    return client


def get_evaluation_processor(request: Request) -> EvaluationProcessor:
    processor: EvaluationProcessor = request.app.state.evaluation_processor
    return processor


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ProcessorDep = Annotated[ResumeProcessor, Depends(get_resume_processor)]
StorageDep = Annotated[ResumeStorage, Depends(get_resume_storage)]
JDProcessorDep = Annotated[JobDescriptionProcessor, Depends(get_jd_processor)]
JDStorageDep = Annotated[ResumeStorage, Depends(get_jd_storage)]
GitHubSyncDep = Annotated[GitHubSyncService, Depends(get_github_sync)]
GitHubClientDep = Annotated[GitHubClient, Depends(get_github_client)]
CodeforcesSyncDep = Annotated[CodeforcesSyncService, Depends(get_codeforces_sync)]
CodeforcesClientDep = Annotated[CodeforcesClient, Depends(get_codeforces_client)]
EvaluationProcessorDep = Annotated[EvaluationProcessor, Depends(get_evaluation_processor)]
