"""Non-secret capability report used by the frontend to explain what will work."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.services.resume.extraction import OcrConfig, ocr_available

router = APIRouter(prefix="/system", tags=["system"])


class LLMCapability(BaseModel):
    configured: bool
    provider: str
    model: str


class OCRCapability(BaseModel):
    enabled: bool
    available: bool
    language: str


class ResumeCapability(BaseModel):
    max_bytes: int
    max_pages: int
    accepted_extensions: list[str]


class GitHubCapability(BaseModel):
    enabled: bool
    authenticated: bool
    accepted_inputs: list[str]


class CodeforcesCapability(BaseModel):
    enabled: bool
    accepted_inputs: list[str]
    submission_limit: int


class LeetCodeCapability(BaseModel):
    enabled: bool
    integration_mode: str
    sync_supported: bool
    accepted_inputs: list[str]


class Capabilities(BaseModel):
    llm: LLMCapability
    ocr: OCRCapability
    resume: ResumeCapability
    job_description: ResumeCapability
    github: GitHubCapability
    codeforces: CodeforcesCapability
    leetcode: LeetCodeCapability


@router.get(
    "/capabilities", response_model=Capabilities, summary="What parsing features are available"
)
async def capabilities(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Capabilities:
    ocr = OcrConfig(
        enabled=settings.ocr_enabled,
        tessdata_dir=settings.ocr_tessdata_dir,
        language=settings.ocr_language,
        dpi=settings.ocr_dpi,
    )
    llm_client = getattr(request.app.state, "llm_client", None)
    return Capabilities(
        llm=LLMCapability(
            configured=bool(llm_client.configured)
            if llm_client
            else settings.llm_api_key is not None,
            provider=settings.llm_provider,
            model=settings.llm_resolved_model,
        ),
        ocr=OCRCapability(
            enabled=settings.ocr_enabled,
            available=ocr_available(ocr),
            language=settings.ocr_language,
        ),
        resume=ResumeCapability(
            max_bytes=settings.resume_max_bytes,
            max_pages=settings.resume_max_pages,
            accepted_extensions=[".pdf", ".docx"],
        ),
        github=GitHubCapability(
            enabled=settings.github_enabled,
            authenticated=settings.github_token_value is not None,
            accepted_inputs=["username", "https://github.com/{username}"],
        ),
        codeforces=CodeforcesCapability(
            enabled=settings.codeforces_enabled,
            accepted_inputs=["handle", "https://codeforces.com/profile/{handle}"],
            submission_limit=settings.codeforces_submission_limit,
        ),
        leetcode=LeetCodeCapability(
            enabled=settings.leetcode_enabled,
            integration_mode="manual_self_reported",
            sync_supported=False,
            accepted_inputs=["username", "https://leetcode.com/u/{username}/"],
        ),
        job_description=ResumeCapability(
            max_bytes=settings.job_description_max_bytes,
            max_pages=settings.job_description_max_pages,
            accepted_extensions=[".pdf", ".docx", ".txt"],
        ),
    )
