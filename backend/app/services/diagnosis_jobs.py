"""Background diagnosis job orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app import db as db_mod
from app.auth import audit
from app.models import Diagnosis, DiagnosisJob, Run
from app.services.diagnoser import diagnose_run


async def create_diagnosis_job(
    *,
    run: Run,
    actor: dict | None,
    include_dev_context: bool,
    overwrite_existing: bool,
    prefer_provider: str | None,
    session: AsyncSession,
) -> DiagnosisJob:
    if not overwrite_existing:
        existing = (
            (
                await session.execute(
                    select(Diagnosis)
                    .where(Diagnosis.run_id == run.run_id)
                    .order_by(desc(Diagnosis.created_at))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            job = DiagnosisJob(
                job_id="diagjob_" + uuid4().hex[:12],
                run_id=run.run_id,
                project_id=run.project_id,
                status="done",
                include_dev_context=include_dev_context,
                overwrite_existing=overwrite_existing,
                prefer_provider=prefer_provider or "",
                diag_id=existing.diag_id,
                error="reused existing diagnosis",
                created_by=str((actor or {}).get("username") or ""),
                updated_at=datetime.now(UTC),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    job = DiagnosisJob(
        job_id="diagjob_" + uuid4().hex[:12],
        run_id=run.run_id,
        project_id=run.project_id,
        status="pending",
        include_dev_context=include_dev_context,
        overwrite_existing=overwrite_existing,
        prefer_provider=prefer_provider or "",
        created_by=str((actor or {}).get("username") or ""),
        updated_at=datetime.now(UTC),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def run_diagnosis_job(*, job_id: str, actor: dict | None = None) -> None:
    async with db_mod.async_session_maker() as session:
        job = await session.get(DiagnosisJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.updated_at = datetime.now(UTC)
        await session.commit()
        try:
            diag = await diagnose_run(
                run_id=job.run_id,
                session=session,
                prefer_provider=job.prefer_provider or None,
                overwrite_existing=job.overwrite_existing,
                include_dev_context=job.include_dev_context,
            )
        except Exception as exc:  # noqa: BLE001 - persisted for polling UI
            job.status = "failed"
            job.error = str(exc)[:1000]
            job.updated_at = datetime.now(UTC)
            await session.commit()
            return
        job.status = "done"
        job.diag_id = diag.diag_id
        job.error = ""
        job.updated_at = datetime.now(UTC)
        await audit(
            actor=actor,
            action="diagnosis.job_completed",
            target_type="diagnosis_job",
            target_id=job.job_id,
            detail=f"run_id={job.run_id}; diag_id={diag.diag_id}; include_dev_context={job.include_dev_context}",
            session=session,
        )
        await session.commit()
