from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import settings
from app.models import Run, StepEvent, TestCase
from app.services.dev_context.evidence import collect_run_dev_context


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _seed_run(session: AsyncSession) -> None:
    session.add(
        TestCase(
            case_id="TC-CTX",
            project_id="demo",
            name="language preference save",
            intent="save language preference",
            module="settings",
            steps=[{"intent": "POST /api/language/preference"}],
            review_status="approved",
        )
    )
    session.add(
        Run(
            run_id="RUN-CTX",
            trace_id="trace",
            project_id="demo",
            case_id="TC-CTX",
            status="failed",
            error_message="POST /api/language/preference returned 500 NullPointerException",
        )
    )
    session.add(
        StepEvent(
            run_id="RUN-CTX",
            step_index=4,
            event="agent.step.executed",
            tool_name="browser_network",
            intent="save language preference",
            status="failed",
            error_message="500 NullPointerException LanguagePreferenceService",
            tool_result={"page_url": "http://demo/settings/language"},
        )
    )


@pytest.mark.asyncio
async def test_collect_run_dev_context_finds_workspace_code(
    db, monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    source_dir = workspace / "zstack" / "plugin" / "iam"
    source_dir.mkdir(parents=True)
    (source_dir / "LanguagePreferenceService.java").write_text(
        "class LanguagePreferenceService { void save(){ /* /api/language/preference */ } }",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "michelle_workspace_root", str(workspace))
    monkeypatch.setattr(settings, "michelle_dev_context_repos", "zstack")
    _seed_run(db)
    await db.commit()

    evidence = await collect_run_dev_context(run_id="RUN-CTX", session=db)

    assert evidence["code_context"]["candidate_files"]
    first = evidence["code_context"]["candidate_files"][0]
    assert first["repo"] == "zstack"
    assert first["path"].endswith("LanguagePreferenceService.java")
    assert "LanguagePreferenceService" in first["matches"][0]["line"]
