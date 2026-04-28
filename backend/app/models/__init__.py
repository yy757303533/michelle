"""SQLModel definitions. Import order matters — register in order of dependency."""

from app.models.case import TestCase  # noqa: F401
from app.models.diagnosis import Diagnosis  # noqa: F401
from app.models.pattern import Pattern  # noqa: F401
from app.models.project import PRD, Project  # noqa: F401
from app.models.run import Run, StepEvent  # noqa: F401
from app.models.runtime_settings import RuntimeSetting  # noqa: F401
