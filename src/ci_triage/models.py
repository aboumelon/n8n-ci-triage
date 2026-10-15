import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Repository(BaseModel):
    full_name: str = Field(max_length=250)

    @field_validator("full_name")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise ValueError("Invalid repository name")
        return value.lower()


class WorkflowRun(BaseModel):
    id: int = Field(gt=0)
    run_attempt: int = Field(default=1, gt=0)
    run_number: int = Field(gt=0)
    workflow_id: int = Field(gt=0)
    name: str = Field(max_length=250)
    head_branch: str = Field(min_length=1, max_length=250)
    head_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    status: str
    conclusion: str | None


class WorkflowEvent(BaseModel):
    action: str
    repository: Repository
    workflow_run: WorkflowRun


class Claim(BaseModel):
    run_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    lease_token: str = Field(min_length=32, max_length=100)


class Finding(BaseModel):
    fingerprint: str
    job: str
    step: str
    category: Literal["dependency", "test", "lint", "build", "unknown"]
    evidence: str
    suggestion: str
    log_truncated: bool = False
    log_available: bool = True
