"""Phase 5 evaluation endpoints with a fake session and processor (no DB, no LLM)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.api.deps import get_evaluation_processor
from app.core.enums import EvaluationStatus, ParseStatus
from app.db.session import get_session
from app.models import Candidate, Evaluation, JobDescription, Resume
from app.services.evaluation.processor import jd_requirements_source
from tests import evaluation_fakes as ef
from tests.conftest import AppFactory, client_for

DB_DOWN = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused 10.0.0.7"))
CID, JID, RID = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _jd(**kw: Any) -> JobDescription:
    values: dict[str, Any] = {"id": JID, "parse_status": ParseStatus.COMPLETED,
                              "analysis": ef.jd_dict(), "reviewed_data": None}  # fmt: skip
    values.update(kw)
    return JobDescription(**values)


def _resume(**kw: Any) -> Resume:
    values: dict[str, Any] = {"id": RID, "candidate_id": CID, "parse_status": ParseStatus.COMPLETED,
                              "parsed_data": ef.resume_dict()}  # fmt: skip
    values.update(kw)
    return Resume(**values)


def _evaluation(**kw: Any) -> Evaluation:
    now = datetime.now(UTC)
    values: dict[str, Any] = {"id": uuid.uuid4(), "candidate_id": CID, "job_description_id": JID,
                              "resume_id": RID, "status": EvaluationStatus.COMPLETED,
                              "created_at": now,
                              "updated_at": now - timedelta(hours=1)}  # fmt: skip
    values.update(kw)
    return Evaluation(**values)


class FakeSession:
    def __init__(
        self,
        *,
        candidate: bool = True,
        jd: JobDescription | None = None,
        resume: Resume | None = None,
        evaluation: Evaluation | None = None,
        scalars: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.objects: dict[type, Any] = {
            Candidate: Candidate(id=CID, full_name="Jordan Sample") if candidate else None,
            JobDescription: jd,
            Resume: resume,
            Evaluation: evaluation,
        }
        self.scalar_results = list(scalars or [])
        self.error = error
        self.added: list[Any] = []
        self.deleted: list[Any] = []

    def _check(self) -> None:
        if self.error:
            raise self.error

    async def get(self, model: type, _ident: Any) -> Any:
        self._check()
        return self.objects.get(model)

    async def scalar(self, _stmt: Any) -> Any:
        self._check()
        return self.scalar_results.pop(0) if self.scalar_results else None

    async def scalars(self, _stmt: Any) -> Any:
        self._check()

        class _R:
            def __init__(self, rows: list[Any]) -> None:
                self.rows = rows

            def all(self) -> list[Any]:
                return self.rows

        return _R([o for o in [self.objects[Evaluation]] if o is not None])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def commit(self) -> None:
        self._check()

    async def refresh(self, obj: Any) -> None:
        obj.id = obj.id or uuid.uuid4()
        obj.created_at = obj.created_at or datetime.now(UTC)
        obj.updated_at = datetime.now(UTC)


class FakeProcessor:
    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.calls: list[uuid.UUID] = []

    async def process(self, evaluation_id: uuid.UUID) -> None:
        self.calls.append(evaluation_id)


def _app(
    make_app: AppFactory, session: FakeSession, processor: FakeProcessor | None = None
) -> FastAPI:
    app = make_app()

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_evaluation_processor] = lambda: processor or FakeProcessor()
    return app


BODY = {"candidate_id": str(CID), "job_description_id": str(JID)}


class TestCreate:
    async def test_defaults_to_latest_parsed_resume_and_schedules(
        self, make_app: AppFactory
    ) -> None:
        processor = FakeProcessor()
        session = FakeSession(jd=_jd(), scalars=[_resume(), None])  # latest resume, no running eval
        async with client_for(_app(make_app, session, processor)) as client:
            resp = await client.post("/api/v1/evaluations", json=BODY)
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["resume_id"] == str(RID)
        assert body["overall_score"] is None
        assert body["result"] is None
        assert processor.calls == [uuid.UUID(body["id"])]

    async def test_without_any_resume(self, make_app: AppFactory) -> None:
        session = FakeSession(jd=_jd(), scalars=[None, None])
        async with client_for(_app(make_app, session)) as client:
            resp = await client.post("/api/v1/evaluations", json=BODY)
        assert resp.status_code == 202
        assert resp.json()["resume_id"] is None

    async def test_explicit_resume(self, make_app: AppFactory) -> None:
        session = FakeSession(jd=_jd(), resume=_resume(), scalars=[None])
        async with client_for(_app(make_app, session)) as client:
            resp = await client.post("/api/v1/evaluations", json={**BODY, "resume_id": str(RID)})
        assert resp.status_code == 202
        assert resp.json()["resume_id"] == str(RID)

    @pytest.mark.parametrize(
        ("resume", "status", "code"),
        [
            (None, 422, "invalid_resume"),
            ("other", 422, "invalid_resume"),
            ("pending", 409, "resume_not_parsed"),
        ],
    )
    async def test_bad_resume(
        self, make_app: AppFactory, resume: str | None, status: int, code: str
    ) -> None:
        obj = None
        if resume == "other":
            obj = _resume(candidate_id=uuid.uuid4())
        elif resume == "pending":
            obj = _resume(parse_status=ParseStatus.PENDING)
        session = FakeSession(jd=_jd(), resume=obj)
        async with client_for(_app(make_app, session)) as client:
            resp = await client.post("/api/v1/evaluations", json={**BODY, "resume_id": str(RID)})
        assert resp.status_code == status
        assert resp.json()["error"]["code"] == code

    @pytest.mark.parametrize(
        "jd",
        [
            _jd(parse_status=ParseStatus.PROCESSING),
            _jd(analysis=None),
            _jd(analysis={"required_skills": "broken"}),
        ],
    )
    async def test_jd_not_parsed(self, make_app: AppFactory, jd: JobDescription) -> None:
        processor = FakeProcessor()
        async with client_for(_app(make_app, FakeSession(jd=jd), processor)) as client:
            resp = await client.post("/api/v1/evaluations", json=BODY)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "jd_not_parsed"
        assert processor.calls == []

    async def test_unknown_candidate_or_jd(self, make_app: AppFactory) -> None:
        async with client_for(_app(make_app, FakeSession(candidate=False, jd=_jd()))) as client:
            assert (await client.post("/api/v1/evaluations", json=BODY)).status_code == 404
        async with client_for(_app(make_app, FakeSession(jd=None))) as client:
            assert (await client.post("/api/v1/evaluations", json=BODY)).status_code == 404

    async def test_llm_not_configured(self, make_app: AppFactory) -> None:
        processor = FakeProcessor(configured=False)
        async with client_for(_app(make_app, FakeSession(jd=_jd()), processor)) as client:
            resp = await client.post("/api/v1/evaluations", json=BODY)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "llm_not_configured"

    async def test_in_progress(self, make_app: AppFactory) -> None:
        running = _evaluation(status=EvaluationStatus.RUNNING, updated_at=datetime.now(UTC))
        session = FakeSession(jd=_jd(), scalars=[_resume(), running])
        async with client_for(_app(make_app, session)) as client:
            resp = await client.post("/api/v1/evaluations", json=BODY)
        assert resp.status_code == 409
        assert resp.json()["error"]["details"] == [{"evaluation_id": str(running.id)}]

    async def test_stale_running_does_not_block(self, make_app: AppFactory) -> None:
        stale = _evaluation(status=EvaluationStatus.RUNNING)  # updated an hour ago
        session = FakeSession(jd=_jd(), scalars=[_resume(), stale])
        async with client_for(_app(make_app, session)) as client:
            assert (await client.post("/api/v1/evaluations", json=BODY)).status_code == 202

    async def test_schema(self, make_app: AppFactory) -> None:
        async with client_for(_app(make_app, FakeSession(jd=_jd()))) as client:
            assert (await client.post("/api/v1/evaluations", json={})).status_code == 422
            bad = {**BODY, "score": 100}
            assert (await client.post("/api/v1/evaluations", json=bad)).status_code == 422


class TestReadRerunDelete:
    async def test_get_with_unreadable_result(self, make_app: AppFactory) -> None:
        ev = _evaluation(result={"unexpected": 1}, overall_score=Decimal("70.0"), warnings=["w"])
        async with client_for(_app(make_app, FakeSession(evaluation=ev))) as client:
            resp = await client.get(f"/api/v1/evaluations/{ev.id}")
        body = resp.json()
        assert resp.headers["cache-control"] == "no-store"
        assert body["result"] is None
        assert body["fit_label"] is None
        assert body["warnings"] == ["w"]

    async def test_list(self, make_app: AppFactory) -> None:
        ev = _evaluation()
        session = FakeSession(evaluation=ev, scalars=[1])
        async with client_for(_app(make_app, session)) as client:
            resp = await client.get(
                f"/api/v1/evaluations?candidate_id={CID}&job_description_id={JID}"
            )
        page = resp.json()
        assert page["total"] == 1
        assert page["items"][0]["id"] == str(ev.id)
        assert "result" not in page["items"][0]

    async def test_rerun(self, make_app: AppFactory) -> None:
        ev = _evaluation(
            status=EvaluationStatus.FAILED, error_code="llm_timeout", error_message="slow"
        )
        processor = FakeProcessor()
        async with client_for(_app(make_app, FakeSession(evaluation=ev), processor)) as client:
            resp = await client.post(f"/api/v1/evaluations/{ev.id}/rerun")
        assert resp.status_code == 202
        assert resp.json()["status"] == "pending"
        assert resp.json()["error_code"] is None
        assert processor.calls == [ev.id]

    async def test_rerun_resolves_missing_resume(self, make_app: AppFactory) -> None:
        ev = _evaluation(resume_id=None)
        session = FakeSession(evaluation=ev, scalars=[_resume()])
        async with client_for(_app(make_app, session)) as client:
            resp = await client.post(f"/api/v1/evaluations/{ev.id}/rerun")
        assert resp.json()["resume_id"] == str(RID)

    async def test_rerun_busy_and_unconfigured(self, make_app: AppFactory) -> None:
        busy = _evaluation(status=EvaluationStatus.RUNNING, updated_at=datetime.now(UTC))
        async with client_for(_app(make_app, FakeSession(evaluation=busy))) as client:
            assert (await client.post(f"/api/v1/evaluations/{busy.id}/rerun")).status_code == 409
        app = _app(make_app, FakeSession(evaluation=_evaluation()), FakeProcessor(configured=False))
        async with client_for(app) as client:
            assert (
                await client.post(f"/api/v1/evaluations/{uuid.uuid4()}/rerun")
            ).status_code == 503

    async def test_delete_and_404(self, make_app: AppFactory) -> None:
        ev = _evaluation()
        session = FakeSession(evaluation=ev)
        async with client_for(_app(make_app, session)) as client:
            assert (await client.delete(f"/api/v1/evaluations/{ev.id}")).status_code == 204
        assert session.deleted == [ev]
        async with client_for(_app(make_app, FakeSession())) as client:
            for method, suffix in (("GET", ""), ("POST", "/rerun"), ("DELETE", "")):
                resp = await client.request(method, f"/api/v1/evaluations/{uuid.uuid4()}{suffix}")
                assert resp.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("POST", "", BODY), ("GET", "", None), ("GET", f"/{uuid.uuid4()}", None),
     ("POST", f"/{uuid.uuid4()}/rerun", None), ("DELETE", f"/{uuid.uuid4()}", None)],
)  # fmt: skip
async def test_database_down(make_app: AppFactory, method: str, path: str, body: Any) -> None:
    async with client_for(_app(make_app, FakeSession(error=DB_DOWN))) as client:
        resp = await client.request(method, f"/api/v1/evaluations{path}", json=body)
    assert resp.status_code == 503
    assert "10.0.0.7" not in resp.text


def test_reviewed_jd_takes_precedence() -> None:
    reviewed = ef.jd_dict(job_title="Reviewed Title")
    out = jd_requirements_source(_jd(reviewed_data=reviewed))
    assert out is not None
    assert out[1] == "reviewed"
    assert out[0].job_title == "Reviewed Title"
    broken = jd_requirements_source(_jd(reviewed_data={"bad": 1}))
    assert broken is not None
    assert broken[1] == "extracted"  # falls back when the reviewed copy is unreadable
