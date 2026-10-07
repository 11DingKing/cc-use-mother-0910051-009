"""发生时判定子系统的请求/响应模型。"""
from datetime import date, datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from .models import (
    VersionStatus, VersionChangeKind, DeterminationTrigger,
    DeterminationResult, QualificationType,
)


class VersionBaseIn(BaseModel):
    change_kind: VersionChangeKind = VersionChangeKind.INITIAL
    effective_from: date
    effective_to: Optional[date] = None
    reason: Optional[str] = None
    recorded_at: Optional[datetime] = Field(
        default=None,
        description="证据进入系统的时间，默认当前时间；数据迁移/补登时可显式指定",
    )


class InstitutionLicenseVersionIn(VersionBaseIn):
    institution_id: int
    license_number: str
    issuing_authority: Optional[str] = None
    approved_scope: Optional[str] = None


class InstitutionAuthVersionIn(VersionBaseIn):
    institution_id: int
    procedure_id: int


class PractitionerQualVersionIn(VersionBaseIn):
    practitioner_id: int
    qualification_type: QualificationType
    certificate_number: str
    practice_scope: Optional[str] = None


class PractitionerAuthVersionIn(VersionBaseIn):
    practitioner_id: int
    procedure_id: int
    institution_id: Optional[int] = Field(
        default=None,
        description="授权限定的执业机构；为空表示仅覆盖人员登记机构，"
                    "跨机构执业需登记限定到治疗机构的版本",
    )


class VersionOut(BaseModel):
    id: int
    status: VersionStatus
    change_kind: VersionChangeKind
    effective_from: date
    effective_to: Optional[date] = None
    recorded_at: datetime
    superseded_at: Optional[datetime] = None
    supersedes_id: Optional[int] = None
    reason: Optional[str] = None

    class Config:
        from_attributes = True


class RedeterminationRunOut(BaseModel):
    id: int
    trigger: DeterminationTrigger
    source_table: Optional[str] = None
    source_version_id: Optional[int] = None
    events_scanned: int
    events_redetermined: int
    results_flipped: int
    detail: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class VersionRegisterResult(BaseModel):
    version_id: int
    status: VersionStatus
    change_kind: VersionChangeKind
    redetermination: RedeterminationRunOut


class ParticipantIn(BaseModel):
    practitioner_id: int
    role: str = "主诊医师"
    is_primary: bool = False


class TreatmentEventIn(BaseModel):
    institution_id: int
    procedure_id: int
    treatment_date: date
    participants: List[ParticipantIn] = Field(..., min_length=1)
    remark: Optional[str] = None


class ParticipantOut(BaseModel):
    id: int
    practitioner_id: int
    practitioner_name: Optional[str] = None
    role: str
    is_primary: bool
    registered_institution_id: Optional[int] = None


class DeterminationOut(BaseModel):
    id: int
    event_id: int
    participant_id: Optional[int] = None
    seq: int
    determined_at: datetime
    as_of_date: date
    result: DeterminationResult
    trigger: DeterminationTrigger
    trigger_detail: Optional[str] = None
    license_version_id: Optional[int] = None
    inst_auth_version_id: Optional[int] = None
    prac_auth_version_id: Optional[int] = None
    qual_version_ids: List[int] = []
    issues: List[str] = []
    is_current: bool
    supersedes_id: Optional[int] = None


class DeterminationDetail(DeterminationOut):
    evidence: Dict[str, Any] = {}


class TreatmentEventOut(BaseModel):
    id: int
    event_code: str
    institution_id: int
    procedure_id: int
    treatment_date: date
    current_result: Optional[DeterminationResult] = None
    remark: Optional[str] = None
    created_at: datetime
    participants: List[ParticipantOut] = []


class EventCreateResult(BaseModel):
    event: TreatmentEventOut
    initial_determinations: int
    current_result: DeterminationResult
    issues: List[str] = []


class DispositionImpactOut(BaseModel):
    notice_id: int
    clue_id: int
    clue_status: Optional[str] = None
    clue_conclusion: Optional[str] = None
    determination_id: int
    impact_detail: str
    detected_at: datetime


class EventExplanation(BaseModel):
    """监管查询：当时通过/违规的双主体证据 + 后续变化对处置的影响。"""
    event: TreatmentEventOut
    current_determination: DeterminationDetail
    participant_determinations: List[DeterminationDetail] = []
    dual_subject_summary: str
    retroactive_flags: List[str] = []
    history: List[DeterminationOut] = []
    disposition_impacts: List[DispositionImpactOut] = []
    later_changes_affect_disposition: bool
