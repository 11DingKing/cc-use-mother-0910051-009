from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, Enum, Float, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from .database import Base


class InstitutionType(str, enum.Enum):
    CLINIC = "医疗美容诊所"
    HOSPITAL = "医疗美容医院"
    DEPARTMENT = "医院美容科"
    OUTPATIENT = "医疗美容门诊部"


class ProcedureCategory(str, enum.Enum):
    SURGERY = "手术类"
    INJECTION = "注射类"
    PHOTOELECTRIC = "光电类"
    SKINCARE = "皮肤护理类"
    ORAL = "口腔美容类"


class SurgeryLevel(str, enum.Enum):
    LEVEL_1 = "一级"
    LEVEL_2 = "二级"
    LEVEL_3 = "三级"
    LEVEL_4 = "四级"


class QualificationType(str, enum.Enum):
    DOCTOR = "医师资格证"
    PRACTICE = "医师执业证"
    NURSE = "护士执业证"
    ANESTHESIA = "麻醉医师资格证"
    COSMETOLOGY = "医疗美容主诊医师资格证"


class ClueType(str, enum.Enum):
    QUICK_TRAINING = "疑似速成班培训"
    UNLICENSED_STAFF = "无证人员上岗"
    FALSE_ADVERTISEMENT = "广告虚假宣传"
    OVER_RANGE_PRACTICE = "超范围执业"


class ClueStatus(str, enum.Enum):
    PENDING = "待分派"
    ASSIGNED = "核查中"
    VERIFIED = "已核实违规"
    DISMISSED = "已排除"


class CluePriority(str, enum.Enum):
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class ComplianceGrade(str, enum.Enum):
    EXCELLENT = "A"
    GOOD = "B"
    FAIR = "C"
    POOR = "D"


class ScoreItem(str, enum.Enum):
    LICENSE_VALID = "许可证有效性"
    LICENSE_COMPLETE = "许可证完整性"
    NO_OVER_RANGE = "无超范围执业"
    ALL_STAFF_LICENSED = "从业人员全部持证"
    NO_QUICK_TRAINING = "无速成班线索"
    NO_FALSE_ADVERTISEMENT = "无虚假宣传线索"
    NO_VERIFIED_VIOLATION = "无已核实违规记录"


class InspectionFrequency(str, enum.Enum):
    QUARTERLY = "每季度一次"
    BIANNUAL = "每半年一次"
    ANNUAL = "每年一次"
    EXTENDED = "每两年一次"


class PlanStatus(str, enum.Enum):
    PENDING = "待执行"
    IN_PROGRESS = "进行中"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


class VersionStatus(str, enum.Enum):
    ACTIVE = "有效"
    SUSPENDED = "暂停"
    REVOKED = "注销"


class VersionChangeKind(str, enum.Enum):
    INITIAL = "初始登记"
    RENEWAL = "续期"
    AMENDMENT = "变更"
    CORRECTION = "追溯更正"
    SUSPENSION = "暂停"
    REINSTATE = "恢复"
    REVOCATION = "注销"


class DeterminationTrigger(str, enum.Enum):
    INITIAL_RECORD = "诊疗记录登记"
    LICENSE_VERSION = "机构许可证版本变化"
    INST_AUTH_VERSION = "机构项目授权版本变化"
    QUAL_VERSION = "人员资质版本变化"
    PRAC_AUTH_VERSION = "人员项目授权版本变化"
    MANUAL = "人工重新判定"


class DeterminationResult(str, enum.Enum):
    COMPLIANT = "合规"
    VIOLATION = "违规"


class Institution(Base):
    __tablename__ = "institutions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    unified_social_code = Column(String(50), unique=True, index=True)
    institution_type = Column(Enum(InstitutionType), nullable=False)
    legal_person = Column(String(100))
    address = Column(String(500))
    phone = Column(String(50))
    registration_date = Column(Date)
    business_scope = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    licenses = relationship("InstitutionLicense", back_populates="institution", cascade="all, delete-orphan")
    authorized_procedures = relationship("InstitutionAuthorizedProcedure", back_populates="institution", cascade="all, delete-orphan")
    practitioners = relationship("Practitioner", back_populates="institution")
    actual_procedure_records = relationship("ActualProcedureRecord", back_populates="institution")
    clues = relationship("ViolationClue", back_populates="institution")
    compliance_scores = relationship("ComplianceScore", back_populates="institution", cascade="all, delete-orphan")
    supervision_plans = relationship("SupervisionPlan", back_populates="institution", cascade="all, delete-orphan")


class InstitutionLicense(Base):
    __tablename__ = "institution_licenses"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    license_number = Column(String(100), unique=True, nullable=False, index=True)
    issuing_authority = Column(String(200))
    issue_date = Column(Date)
    valid_until = Column(Date)
    approved_surgeries = Column(Text)
    is_valid = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution", back_populates="licenses")


class Practitioner(Base):
    __tablename__ = "practitioners"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    id_card = Column(String(18), unique=True, index=True)
    gender = Column(String(10))
    birth_date = Column(Date)
    institution_id = Column(Integer, ForeignKey("institutions.id"))
    position = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    institution = relationship("Institution", back_populates="practitioners")
    qualifications = relationship("PractitionerQualification", back_populates="practitioner", cascade="all, delete-orphan")
    authorized_procedures = relationship("PractitionerAuthorizedProcedure", back_populates="practitioner", cascade="all, delete-orphan")
    actual_procedure_records = relationship("ActualProcedureRecord", back_populates="practitioner")


class PractitionerQualification(Base):
    __tablename__ = "practitioner_qualifications"

    id = Column(Integer, primary_key=True, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False)
    qualification_type = Column(Enum(QualificationType), nullable=False)
    certificate_number = Column(String(100), nullable=False, index=True)
    issuing_authority = Column(String(200))
    issue_date = Column(Date)
    valid_until = Column(Date)
    practice_scope = Column(String(500))
    is_valid = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    practitioner = relationship("Practitioner", back_populates="qualifications")


class Procedure(Base):
    __tablename__ = "procedures"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, unique=True, index=True)
    code = Column(String(50), unique=True, index=True)
    category = Column(Enum(ProcedureCategory), nullable=False)
    surgery_level = Column(Enum(SurgeryLevel))
    description = Column(Text)
    requires_qualification = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution_authorizations = relationship("InstitutionAuthorizedProcedure", back_populates="procedure")
    practitioner_authorizations = relationship("PractitionerAuthorizedProcedure", back_populates="procedure")
    actual_records = relationship("ActualProcedureRecord", back_populates="procedure")


class InstitutionAuthorizedProcedure(Base):
    __tablename__ = "institution_authorized_procedures"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    authorized_date = Column(Date)
    remark = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution", back_populates="authorized_procedures")
    procedure = relationship("Procedure", back_populates="institution_authorizations")


class PractitionerAuthorizedProcedure(Base):
    __tablename__ = "practitioner_authorized_procedures"

    id = Column(Integer, primary_key=True, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    authorized_date = Column(Date)
    remark = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    practitioner = relationship("Practitioner", back_populates="authorized_procedures")
    procedure = relationship("Procedure", back_populates="practitioner_authorizations")


class ActualProcedureRecord(Base):
    __tablename__ = "actual_procedure_records"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    procedure_date = Column(Date, nullable=False)
    patient_count = Column(Integer, default=1)
    remark = Column(String(500))
    is_over_range = Column(Boolean, default=False)
    over_range_detail = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution", back_populates="actual_procedure_records")
    practitioner = relationship("Practitioner", back_populates="actual_procedure_records")
    procedure = relationship("Procedure", back_populates="actual_records")


class ViolationClue(Base):
    __tablename__ = "violation_clues"

    id = Column(Integer, primary_key=True, index=True)
    clue_type = Column(Enum(ClueType), nullable=False)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=False)
    institution_id = Column(Integer, ForeignKey("institutions.id"))
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"))
    procedure_id = Column(Integer, ForeignKey("procedures.id"))
    source = Column(String(200))
    priority = Column(Enum(CluePriority), default=CluePriority.MEDIUM)
    status = Column(Enum(ClueStatus), default=ClueStatus.PENDING)
    assignee = Column(String(100))
    assigned_at = Column(DateTime)
    conclusion = Column(Text)
    verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    institution = relationship("Institution", back_populates="clues")
    procedure = relationship("Procedure")
    inspection_records = relationship("InspectionRecord", back_populates="clue", cascade="all, delete-orphan")


class InspectionRecord(Base):
    __tablename__ = "inspection_records"

    id = Column(Integer, primary_key=True, index=True)
    clue_id = Column(Integer, ForeignKey("violation_clues.id"), nullable=False)
    inspector = Column(String(100), nullable=False)
    inspection_date = Column(Date, nullable=False)
    content = Column(Text, nullable=False)
    finding = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    clue = relationship("ViolationClue", back_populates="inspection_records")


class ComplianceScore(Base):
    __tablename__ = "compliance_scores"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    total_score = Column(Float, nullable=False, default=100.0)
    grade = Column(Enum(ComplianceGrade), nullable=False)
    license_valid_score = Column(Float, default=15.0)
    license_complete_score = Column(Float, default=10.0)
    no_over_range_score = Column(Float, default=20.0)
    all_staff_licensed_score = Column(Float, default=20.0)
    no_quick_training_score = Column(Float, default=15.0)
    no_false_advertisement_score = Column(Float, default=10.0)
    no_verified_violation_score = Column(Float, default=10.0)
    deduction_details = Column(Text)
    inspection_frequency = Column(Enum(InspectionFrequency), nullable=False)
    scored_at = Column(DateTime, default=datetime.utcnow)
    scoring_period = Column(String(50))
    remark = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    institution = relationship("Institution", back_populates="compliance_scores")
    supervision_plans = relationship("SupervisionPlan", back_populates="compliance_score", cascade="all, delete-orphan")


class SupervisionPlan(Base):
    __tablename__ = "supervision_plans"

    id = Column(Integer, primary_key=True, index=True)
    compliance_score_id = Column(Integer, ForeignKey("compliance_scores.id"), nullable=False)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    plan_title = Column(String(300), nullable=False)
    plan_content = Column(Text, nullable=False)
    planned_date = Column(Date, nullable=False)
    inspector = Column(String(100))
    status = Column(Enum(PlanStatus), default=PlanStatus.PENDING)
    priority = Column(Enum(CluePriority), default=CluePriority.MEDIUM)
    focus_areas = Column(Text)
    actual_inspection_date = Column(Date)
    result = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    compliance_score = relationship("ComplianceScore", back_populates="supervision_plans")
    institution = relationship("Institution", back_populates="supervision_plans")


# ---------------------------------------------------------------------------
# 发生时判定（双时态版本链 + 可审计重新判定）
#
# 设计要点：
# 1. 四类证据（机构许可证、机构项目授权、人员资质、人员项目授权）均以“版本行”
#    追加登记，永不修改或删除历史行。
# 2. 每行携带业务有效期 [effective_from, effective_to]（两端含当日）与系统录入
#    时间 recorded_at；追溯更正/变更/续期会关闭旧行（superseded_at），
#    暂停/注销/恢复为叠加行，不关闭旧行，由“录入最晚者覆盖”规则解析。
# 3. 每次判定生成一条 ComplianceDetermination，绑定当时解析到的版本号，
#    旧判定保留（is_current=False），新判定通过 supersedes_id 链接。
# ---------------------------------------------------------------------------


class InstitutionLicenseVersion(Base):
    __tablename__ = "institution_license_versions"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False, index=True)
    license_number = Column(String(100), nullable=False)
    issuing_authority = Column(String(200))
    approved_scope = Column(Text)
    status = Column(Enum(VersionStatus), nullable=False, default=VersionStatus.ACTIVE)
    change_kind = Column(Enum(VersionChangeKind), nullable=False, default=VersionChangeKind.INITIAL)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date)
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    superseded_at = Column(DateTime)
    supersedes_id = Column(Integer, ForeignKey("institution_license_versions.id"))
    reason = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution")


class InstitutionProcedureAuthVersion(Base):
    __tablename__ = "institution_procedure_auth_versions"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False, index=True)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False, index=True)
    status = Column(Enum(VersionStatus), nullable=False, default=VersionStatus.ACTIVE)
    change_kind = Column(Enum(VersionChangeKind), nullable=False, default=VersionChangeKind.INITIAL)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date)
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    superseded_at = Column(DateTime)
    supersedes_id = Column(Integer, ForeignKey("institution_procedure_auth_versions.id"))
    reason = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution")
    procedure = relationship("Procedure")


class PractitionerQualificationVersion(Base):
    __tablename__ = "practitioner_qualification_versions"

    id = Column(Integer, primary_key=True, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False, index=True)
    qualification_type = Column(Enum(QualificationType), nullable=False)
    certificate_number = Column(String(100), nullable=False)
    practice_scope = Column(String(500))
    status = Column(Enum(VersionStatus), nullable=False, default=VersionStatus.ACTIVE)
    change_kind = Column(Enum(VersionChangeKind), nullable=False, default=VersionChangeKind.INITIAL)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date)
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    superseded_at = Column(DateTime)
    supersedes_id = Column(Integer, ForeignKey("practitioner_qualification_versions.id"))
    reason = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    practitioner = relationship("Practitioner")


class PractitionerProcedureAuthVersion(Base):
    __tablename__ = "practitioner_procedure_auth_versions"

    id = Column(Integer, primary_key=True, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False, index=True)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), index=True)
    status = Column(Enum(VersionStatus), nullable=False, default=VersionStatus.ACTIVE)
    change_kind = Column(Enum(VersionChangeKind), nullable=False, default=VersionChangeKind.INITIAL)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date)
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    superseded_at = Column(DateTime)
    supersedes_id = Column(Integer, ForeignKey("practitioner_procedure_auth_versions.id"))
    reason = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    practitioner = relationship("Practitioner")
    procedure = relationship("Procedure")


class TreatmentEvent(Base):
    __tablename__ = "treatment_events"

    id = Column(Integer, primary_key=True, index=True)
    event_code = Column(String(50), unique=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    treatment_date = Column(Date, nullable=False)
    current_result = Column(Enum(DeterminationResult))
    remark = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution")
    procedure = relationship("Procedure")
    participants = relationship("TreatmentParticipant", back_populates="event", cascade="all, delete-orphan")
    determinations = relationship("ComplianceDetermination", back_populates="event", cascade="all, delete-orphan")


class TreatmentParticipant(Base):
    __tablename__ = "treatment_participants"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("treatment_events.id"), nullable=False, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False)
    role = Column(String(50), default="主诊医师")
    is_primary = Column(Boolean, default=False)
    registered_institution_id = Column(Integer, ForeignKey("institutions.id"))

    event = relationship("TreatmentEvent", back_populates="participants")
    practitioner = relationship("Practitioner")


class ComplianceDetermination(Base):
    __tablename__ = "compliance_determinations"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("treatment_events.id"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("treatment_participants.id"), index=True)
    seq = Column(Integer, nullable=False, default=1)
    determined_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    as_of_date = Column(Date, nullable=False)
    knowledge_cutoff = Column(DateTime, nullable=False)
    result = Column(Enum(DeterminationResult), nullable=False)
    trigger = Column(Enum(DeterminationTrigger), nullable=False)
    trigger_detail = Column(String(500))
    license_version_id = Column(Integer)
    inst_auth_version_id = Column(Integer)
    prac_auth_version_id = Column(Integer)
    qual_version_ids = Column(Text)
    evidence = Column(Text)
    issues = Column(Text)
    is_current = Column(Boolean, default=True)
    supersedes_id = Column(Integer, ForeignKey("compliance_determinations.id"))

    event = relationship("TreatmentEvent", back_populates="determinations")
    participant = relationship("TreatmentParticipant")


class RedeterminationRun(Base):
    __tablename__ = "redetermination_runs"

    id = Column(Integer, primary_key=True, index=True)
    trigger = Column(Enum(DeterminationTrigger), nullable=False)
    source_table = Column(String(100))
    source_version_id = Column(Integer)
    events_scanned = Column(Integer, default=0)
    events_redetermined = Column(Integer, default=0)
    results_flipped = Column(Integer, default=0)
    detail = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)


class ClueEventLink(Base):
    __tablename__ = "clue_event_links"

    id = Column(Integer, primary_key=True, index=True)
    clue_id = Column(Integer, ForeignKey("violation_clues.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("treatment_events.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    clue = relationship("ViolationClue")
    event = relationship("TreatmentEvent")


class DispositionImpactNotice(Base):
    __tablename__ = "disposition_impact_notices"

    id = Column(Integer, primary_key=True, index=True)
    clue_id = Column(Integer, ForeignKey("violation_clues.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("treatment_events.id"), nullable=False)
    determination_id = Column(Integer, ForeignKey("compliance_determinations.id"), nullable=False)
    impact_detail = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    clue = relationship("ViolationClue")
    event = relationship("TreatmentEvent")
    determination = relationship("ComplianceDetermination")
