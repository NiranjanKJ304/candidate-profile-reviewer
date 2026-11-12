"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.models.candidate import Candidate
from app.models.developer_profile import DeveloperProfile
from app.models.evaluation import Evaluation
from app.models.job_description import JobDescription
from app.models.resume import Resume

__all__ = ["Candidate", "DeveloperProfile", "Evaluation", "JobDescription", "Resume"]
