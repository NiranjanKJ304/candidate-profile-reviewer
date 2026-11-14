"""Phase 5 evaluation workflow against live PostgreSQL (isolated temporary schema).

Real: HTTP API, background processing, evidence bundle from stored resume / JD /
platform snapshots, guards, scoring, persistence, constraints (migration 0006).
Deterministic: the LLM is a fake that reads the request (tests/evaluation_fakes.py).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.enums import CollectionStatus, ParseStatus, Platform, SyncStatus
from app.integrations.llm import LLMTimeoutError
from app.main import create_app
from app.models import DeveloperProfile, Evaluation, JobDescription, Resume
from app.services.evaluation.assessment import EvaluationAssessor
from app.services.evaluation.processor import EvaluationProcessor
from app.services.resume.storage import ResumeStorage
from tests import evaluation_fakes as ef
from tests import github_fakes as gf
from tests.conftest import SettingsFactory, client_for
from tests.llm_fakes import FakeLLM

pytestmark = pytest.mark.integration


class Harness:
    def __init__(self, app: FastAPI, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.app = app
        self.sessions = sessions
        self.client: httpx.AsyncClient
        self.llm = FakeLLM(ef.auto_reply)

    def use(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.app.state.evaluation_processor = EvaluationProcessor(
            session_factory=self.sessions, assessor=EvaluationAssessor(llm, max_attempts=2)
        )

    async def candidate(self, name: str = ef.NAME) -> uuid.UUID:
        resp = await self.client.post("/api/v1/candidates", json={"full_name": name})
        assert resp.status_code == 201
        return uuid.UUID(resp.json()["id"])

    async def resume(self, cid: uuid.UUID, **kw: Any) -> uuid.UUID:
        async with self.sessions() as s:
            row = Resume(
                candidate_id=cid, original_filename="cv.pdf", content_type="application/pdf",
                file_size_bytes=100, sha256=uuid.uuid4().hex * 2,
                parse_status=kw.get("status", ParseStatus.COMPLETED),
                parsed_data=kw.get("data", ef.resume_dict()),
            )  # fmt: skip
            s.add(row)
            await s.commit()
            return row.id

    async def jd(self, **kw: Any) -> uuid.UUID:
        async with self.sessions() as s:
            row = JobDescription(
                title="Senior Backend Engineer",
                parse_status=kw.get("status", ParseStatus.COMPLETED),
                analysis=kw.get("analysis", ef.jd_dict()), reviewed_data=kw.get("reviewed"),
            )  # fmt: skip
            s.add(row)
            await s.commit()
            return row.id

    async def github(self, cid: uuid.UUID) -> None:
        snap = await ef.github_snapshot()
        async with self.sessions() as s:
            s.add(DeveloperProfile(
                candidate_id=cid, platform=Platform.GITHUB, username=gf.LOGIN,
                collection_status=CollectionStatus.COLLECTED, sync_status=SyncStatus.COMPLETED,
                collected_data=snap.model_dump(mode="json"), collected_at=snap.collected_at,
            ))  # fmt: skip
            await s.commit()

    async def evaluate(self, cid: uuid.UUID, jid: uuid.UUID, **extra: Any) -> httpx.Response:
        body = {"candidate_id": str(cid), "job_description_id": str(jid), **extra}
        return await self.client.post("/api/v1/evaluations", json=body)

    async def get(self, eid: str) -> dict[str, Any]:
        resp = await self.client.get(f"/api/v1/evaluations/{eid}")
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        return data


@pytest.fixture
async def h(
    migrated_engine: AsyncEngine, make_settings: SettingsFactory, tmp_path: Path
) -> AsyncIterator[Harness]:
    app = create_app(make_settings())
    app.state.jd_storage = ResumeStorage(tmp_path, extensions=("pdf", "docx", "txt"))
    sessions = async_sessionmaker(migrated_engine, expire_on_commit=False)
    app.state.db_engine = migrated_engine
    app.state.session_factory = sessions
    harness = Harness(app, sessions)
    harness.use(FakeLLM(ef.auto_reply))
    async with client_for(app) as client:
        harness.client = client
        yield harness


async def test_full_evaluation(h: Harness) -> None:
    cid = await h.candidate()
    rid = await h.resume(cid)
    await h.github(cid)
    jid = await h.jd()
    resp = await h.evaluate(cid, jid)
    assert resp.status_code == 202, resp.text
    body = await h.get(resp.json()["id"])  # background task finished during the request
    assert body["status"] == "completed", body
    assert body["resume_id"] == str(rid)
    assert body["model_name"] == "fake-model-served"
    assert body["error_code"] is None
    result = body["result"]
    assert result["jd_version"] == "extracted"
    assert result["job_title"] == "Senior Backend Engineer"
    reqs = {r["text"]: r for r in result["requirements"]}
    assert reqs["Python"]["verdict"] == "met"
    assert reqs["Python"]["baseline_status"] == "platform_evidence"  # GitHub Python repositories
    assert reqs["Rust"]["verdict"] == "insufficient_evidence"  # never "not met" for no evidence
    score = result["score"]
    assert score["overall"] is not None
    assert body["overall_score"] == pytest.approx(score["overall"])
    assert body["fit_label"] == score["fit_label"]
    assert {s["source"]: s["status"] for s in result["sources"]} == {
        "resume": "used", "github": "used", "codeforces": "not_linked", "leetcode": "not_linked",
    }  # fmt: skip
    assert result["privacy"]["identity_removed"] is True
    assert "Decision support only" in result["decision_support_notice"]

    async with h.sessions() as s:
        row = await s.get(Evaluation, uuid.UUID(body["id"]))
        assert row is not None
        assert row.completed_at is not None
        assert float(row.overall_score or 0) == pytest.approx(score["overall"])


async def test_llm_never_receives_identity(h: Harness) -> None:
    cid = await h.candidate()
    await h.resume(cid)
    await h.github(cid)
    jid = await h.jd()
    resp = await h.evaluate(cid, jid)
    await h.get(resp.json()["id"])
    sent = " ".join(m.content for call in h.llm.calls for m in call)
    for secret in (ef.NAME, ef.EMAIL, "98765", "linkedin", ef.LOCATION, ef.INSTITUTION, gf.LOGIN,
                   gf.PRIVATE_EMAIL, "https://github.com"):  # fmt: skip
        assert secret not in sent, secret


async def test_reviewed_jd_is_used(h: Harness) -> None:
    cid = await h.candidate()
    await h.resume(cid)
    reviewed = ef.jd_dict(required_skills=[{"name": "Python"}], preferred_skills=[],
                          minimum_experience=None, education_requirements=[],
                          required_qualifications=[])  # fmt: skip
    jid = await h.jd(reviewed=reviewed)
    body = await h.get((await h.evaluate(cid, jid)).json()["id"])
    assert body["result"]["jd_version"] == "reviewed"
    assert [r["text"] for r in body["result"]["requirements"]] == ["Python"]


async def test_platform_only_evaluation(h: Harness) -> None:
    cid = await h.candidate()
    await h.github(cid)
    jid = await h.jd()
    body = await h.get((await h.evaluate(cid, jid)).json()["id"])
    assert body["status"] == "completed"
    assert body["resume_id"] is None
    assert any("platform evidence only" in w for w in body["warnings"])


async def test_no_evidence_fails_with_code(h: Harness) -> None:
    cid = await h.candidate()
    jid = await h.jd()
    body = await h.get((await h.evaluate(cid, jid)).json()["id"])
    assert body["status"] == "failed"
    assert body["error_code"] == "no_evidence"
    assert body["overall_score"] is None
    assert h.llm.calls == []


async def test_jd_without_requirements_fails(h: Harness) -> None:
    cid = await h.candidate()
    await h.resume(cid)
    empty = {"job_title": "Vague role"}
    jid = await h.jd(analysis=empty)
    body = await h.get((await h.evaluate(cid, jid)).json()["id"])
    assert body["error_code"] == "jd_has_no_requirements"


async def test_llm_failure_is_recorded_and_rerun_succeeds(h: Harness) -> None:
    h.use(FakeLLM(LLMTimeoutError("slow")))
    cid = await h.candidate()
    await h.resume(cid)
    jid = await h.jd()
    eid = (await h.evaluate(cid, jid)).json()["id"]
    failed = await h.get(eid)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "llm_timeout"

    h.use(FakeLLM(ef.auto_reply))
    assert (await h.client.post(f"/api/v1/evaluations/{eid}/rerun")).status_code == 202
    done = await h.get(eid)
    assert done["status"] == "completed"
    assert done["error_code"] is None


async def test_unparsed_jd_and_resume_are_rejected(h: Harness) -> None:
    cid = await h.candidate()
    pending_resume = await h.resume(cid, status=ParseStatus.PENDING)
    jid = await h.jd(status=ParseStatus.PROCESSING)
    assert (await h.evaluate(cid, jid)).json()["error"]["code"] == "jd_not_parsed"
    ok_jd = await h.jd()
    resp = await h.evaluate(cid, ok_jd, resume_id=str(pending_resume))
    assert resp.json()["error"]["code"] == "resume_not_parsed"


async def test_list_filters(h: Harness) -> None:
    cid = await h.candidate()
    await h.resume(cid)
    j1, j2 = await h.jd(), await h.jd()
    for jid in (j1, j2):
        await h.evaluate(cid, jid)
    page = (await h.client.get("/api/v1/evaluations", params={"candidate_id": str(cid)})).json()
    assert page["total"] == 2
    one = (await h.client.get("/api/v1/evaluations", params={"job_description_id": str(j1)})).json()
    assert one["total"] == 1
    assert one["items"][0]["fit_label"] is not None


async def test_jd_with_evaluations_cannot_be_deleted_but_candidate_cascades(h: Harness) -> None:
    cid = await h.candidate()
    await h.resume(cid)
    jid = await h.jd()
    eid = (await h.evaluate(cid, jid)).json()["id"]
    assert (await h.client.delete(f"/api/v1/job-descriptions/{jid}")).status_code == 409
    async with h.sessions() as s:
        await s.execute(text("DELETE FROM candidates WHERE id = :id"), {"id": cid})
        await s.commit()
    assert (await h.client.get(f"/api/v1/evaluations/{eid}")).status_code == 404


async def test_error_code_constraint(h: Harness) -> None:
    cid = await h.candidate()
    jid = await h.jd()
    async with h.sessions() as s:
        with pytest.raises(Exception, match="ck_evaluations_error_code"):
            await s.execute(
                text("INSERT INTO evaluations (candidate_id, job_description_id, error_code) "
                     "VALUES (:c, :j, 'made_up')"),
                {"c": cid, "j": jid},
            )  # fmt: skip


async def test_logs_contain_no_identity(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    cid = await h.candidate()
    await h.resume(cid)
    jid = await h.jd()
    await h.get((await h.evaluate(cid, jid)).json()["id"])
    assert ef.EMAIL not in caplog.text
    assert "98765" not in caplog.text


async def _profile(h: Harness, cid: uuid.UUID, platform: Platform, **kw: Any) -> None:
    async with h.sessions() as s:
        s.add(
            DeveloperProfile(candidate_id=cid, platform=platform, username=kw.pop("username"), **kw)
        )
        await s.commit()


async def test_all_four_sources_with_trust_levels(h: Harness) -> None:
    cid = await h.candidate()
    await h.resume(cid)
    await h.github(cid)
    cf_snap = await ef.codeforces_snapshot()
    await _profile(
        h, cid, Platform.CODEFORCES, username="algo_dev",
        collection_status=CollectionStatus.COLLECTED, sync_status=SyncStatus.COMPLETED,
        collected_data=cf_snap.model_dump(mode="json"), collected_at=cf_snap.collected_at,
    )  # fmt: skip
    lc = ef.leetcode_evidence()
    await _profile(
        h, cid, Platform.LEETCODE, username="lc_user",
        collection_status=CollectionStatus.UNAVAILABLE, sync_status=SyncStatus.NOT_APPLICABLE,
        self_reported_data=lc.model_dump(mode="json"), self_reported_at=lc.submitted_at,
    )  # fmt: skip
    jid = await h.jd()
    body = await h.get((await h.evaluate(cid, jid)).json()["id"])
    result = body["result"]
    assert {s["source"]: s["status"] for s in result["sources"]} == {
        "resume": "used", "github": "used", "codeforces": "used", "leetcode": "used",
    }  # fmt: skip
    trusts = {e["id"][0]: e["trust"] for e in result["evidence"]}
    assert trusts == {"R": "resume_claim", "G": "platform_collected", "C": "platform_collected",
                      "L": "self_reported"}  # fmt: skip
    rust = next(r for r in result["requirements"] if r["text"] == "Rust")
    assert rust["baseline_status"] == "self_reported_only"  # only LeetCode mentions Rust
    assert rust["verdict"] == "met"
    assert rust["credit"] == 1.5  # self-reported only: x0.75
    sent = " ".join(m.content for call in h.llm.calls for m in call)
    assert "algo_dev" not in sent
    assert "lc_user" not in sent


async def test_linked_but_not_collected_and_unusable_resume(h: Harness) -> None:
    cid = await h.candidate()
    rid = await h.resume(cid, data={"unexpected": "shape"})
    await _profile(h, cid, Platform.GITHUB, username=gf.LOGIN)  # linked, nothing collected
    lc_snap = await ef.codeforces_snapshot()
    await _profile(
        h, cid, Platform.CODEFORCES, username="algo_dev",
        collected_data=lc_snap.model_dump(mode="json"), collected_at=lc_snap.collected_at,
    )  # fmt: skip
    await _profile(h, cid, Platform.LEETCODE, username="lc_user")  # no self-reported evidence
    jid = await h.jd()
    body = await h.get((await h.evaluate(cid, jid, resume_id=str(rid))).json()["id"])
    assert body["status"] == "completed", body
    statuses = {s["source"]: s["status"] for s in body["result"]["sources"]}
    assert statuses == {"resume": "unavailable", "github": "not_collected", "codeforces": "used",
                        "leetcode": "not_collected"}  # fmt: skip
    assert any("no usable parsed data" in w for w in body["warnings"])
    assert body["result"]["resume_id"] is None


async def test_unexpected_error_is_recorded_not_stuck(h: Harness) -> None:
    def boom(_messages: list[Any]) -> str:
        raise RuntimeError("unexpected")

    h.use(FakeLLM(boom))
    cid = await h.candidate()
    await h.resume(cid)
    jid = await h.jd()
    body = await h.get((await h.evaluate(cid, jid)).json()["id"])
    assert body["status"] == "failed"
    assert body["error_code"] == "internal_error"
    assert "RuntimeError" in body["error_message"]
    assert body["completed_at"] is not None
