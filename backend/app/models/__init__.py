"""SQLModel definitions. Import order matters — register in order of dependency."""

from app.models._types import TZDateTime  # noqa: F401  (custom column type)
from app.models.auth import AuditLog, ProjectMember, User  # noqa: F401
from app.models.case_feedback import CaseGenerationFeedback  # noqa: F401
from app.models.case import TestCase  # noqa: F401
from app.models.diagnosis import Diagnosis  # noqa: F401
from app.models.pattern import Pattern  # noqa: F401
from app.models.prd_generation_job import PRDGenerationJob  # noqa: F401
from app.models.project import PRD, Project  # noqa: F401
from app.models.run import LLMCall, Run, StepEvent  # noqa: F401
from app.models.runtime_settings import RuntimeSetting  # noqa: F401
