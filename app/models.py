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


class EvidenceItemStatus(str, enum.Enum):
    VALID = "当时有效"
    VALID_BACKFILLED = "事后补录（治疗时无法证明）"
    MISSING = "治疗时缺失"
    NOT_EFFECTIVE = "治疗时尚未生效"
    EXPIRED = "治疗时已过期"
    INACTIVE = "治疗时处于暂停/失效状态"


class SnapshotConclusion(str, enum.Enum):
    COMPLIANT = "当时合规"
    VIOLATION = "当时违规"
    INCONCLUSIVE = "无法判定"


class EvidenceChangeType(str, enum.Enum):
    INITIAL = "初次登记"
    SUSPEND = "暂停"
    RESTORE = "恢复"
    CHANGE = "变更"
    RETROACTIVE_CORRECTION = "追溯更正"
    BACKFILL = "事后补录"


class RejudgmentReason(str, enum.Enum):
    INITIAL = "初次判定"
    MANUAL = "人工/批量重新判定"
    LICENSE_SUSPENDED = "机构许可证暂停"
    LICENSE_RESTORED = "机构许可证恢复"
    LICENSE_CORRECTED = "机构许可证追溯更正/补录"
    INSTITUTION_AUTH_SUSPENDED = "机构项目授权暂停"
    INSTITUTION_AUTH_RESTORED = "机构项目授权恢复"
    INSTITUTION_AUTH_CORRECTED = "机构项目授权追溯更正/补录"
    QUALIFICATION_SUSPENDED = "人员资质暂停"
    QUALIFICATION_RESTORED = "人员资质恢复"
    QUALIFICATION_CORRECTED = "人员资质追溯更正/补录"
    PRACTITIONER_AUTH_SUSPENDED = "人员项目授权暂停"
    PRACTITIONER_AUTH_RESTORED = "人员项目授权恢复"
    PRACTITIONER_AUTH_CORRECTED = "人员项目授权追溯更正/补录"


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
    license_versions = relationship(
        "InstitutionLicenseVersion", back_populates="institution",
        cascade="all, delete-orphan", order_by="InstitutionLicenseVersion.version_no"
    )
    procedure_authorization_versions = relationship(
        "InstitutionAuthVersion", back_populates="institution",
        cascade="all, delete-orphan", order_by="InstitutionAuthVersion.version_no"
    )


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
    versions = relationship(
        "InstitutionLicenseVersion", back_populates="license",
        cascade="all, delete-orphan", order_by="InstitutionLicenseVersion.version_no"
    )


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
    qualification_versions = relationship(
        "QualificationVersion", back_populates="practitioner",
        cascade="all, delete-orphan", order_by="QualificationVersion.version_no"
    )
    procedure_grant_versions = relationship(
        "PractitionerAuthVersion", back_populates="practitioner",
        cascade="all, delete-orphan", order_by="PractitionerAuthVersion.version_no"
    )
    participant_bindings = relationship(
        "ProcedureRecordParticipant", back_populates="practitioner",
        cascade="all, delete-orphan"
    )


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
    versions = relationship(
        "QualificationVersion", back_populates="qualification",
        cascade="all, delete-orphan", order_by="QualificationVersion.version_no"
    )


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
    versions = relationship(
        "InstitutionAuthVersion", back_populates="authorization",
        cascade="all, delete-orphan", order_by="InstitutionAuthVersion.version_no"
    )


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
    versions = relationship(
        "PractitionerAuthVersion", back_populates="authorization",
        cascade="all, delete-orphan", order_by="PractitionerAuthVersion.version_no"
    )


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
    # 三态判定结论：当时合规 / 当时违规 / 无法判定
    judgment_conclusion = Column(String(20), default="当时合规")
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution", back_populates="actual_procedure_records")
    practitioner = relationship("Practitioner", back_populates="actual_procedure_records")
    procedure = relationship("Procedure", back_populates="actual_records")
    participants = relationship(
        "ProcedureRecordParticipant", back_populates="record",
        cascade="all, delete-orphan"
    )
    judgments = relationship(
        "ComplianceJudgment", back_populates="record",
        cascade="all, delete-orphan", order_by="ComplianceJudgment.sequence_no"
    )


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
# 时点证据版本链
#
# 四类证据（机构许可证 / 机构项目授权 / 人员资质 / 人员项目授权）均采用
# “只追加”的版本链：暂停、恢复、变更、追溯更正都不覆盖旧版本，而是追加
# 新版本。每个版本声明自己的主张生效区间 [valid_from, valid_until]、系统
# 登记时刻 recorded_at 与变更类型，判定时按治疗日期在版本链中选版。
# ---------------------------------------------------------------------------


class InstitutionLicenseVersion(Base):
    __tablename__ = "institution_license_versions"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False, index=True)
    license_id = Column(Integer, ForeignKey("institution_licenses.id"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    license_number = Column(String(100), nullable=False)
    issuing_authority = Column(String(200))
    valid_from = Column(Date, nullable=False)
    valid_until = Column(Date)
    approved_surgeries = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)
    change_type = Column(Enum(EvidenceChangeType), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    remark = Column(String(500))

    institution = relationship("Institution", back_populates="license_versions")
    license = relationship("InstitutionLicense", back_populates="versions")


class InstitutionAuthVersion(Base):
    """机构项目授权版本（机构侧 / 项目粒度）。"""
    __tablename__ = "institution_auth_versions"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False, index=True)
    authorization_id = Column(Integer, ForeignKey("institution_authorized_procedures.id"), nullable=False, index=True)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_until = Column(Date)
    is_active = Column(Boolean, default=True, nullable=False)
    change_type = Column(Enum(EvidenceChangeType), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    remark = Column(String(500))

    institution = relationship("Institution", back_populates="procedure_authorization_versions")
    authorization = relationship("InstitutionAuthorizedProcedure", back_populates="versions")
    procedure = relationship("Procedure")


class QualificationVersion(Base):
    """人员资质版本（人员侧），按资质证书逐条成链。"""
    __tablename__ = "qualification_versions"

    id = Column(Integer, primary_key=True, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False, index=True)
    qualification_id = Column(Integer, ForeignKey("practitioner_qualifications.id"), nullable=False, index=True)
    qualification_type = Column(Enum(QualificationType), nullable=False)
    version_no = Column(Integer, nullable=False)
    certificate_number = Column(String(100), nullable=False)
    issuing_authority = Column(String(200))
    valid_from = Column(Date, nullable=False)
    valid_until = Column(Date)
    practice_scope = Column(String(500))
    is_active = Column(Boolean, default=True, nullable=False)
    change_type = Column(Enum(EvidenceChangeType), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    remark = Column(String(500))

    practitioner = relationship("Practitioner", back_populates="qualification_versions")
    qualification = relationship("PractitionerQualification", back_populates="versions")


class PractitionerAuthVersion(Base):
    """人员项目授权版本（人员侧 / 项目粒度）。"""
    __tablename__ = "practitioner_auth_versions"

    id = Column(Integer, primary_key=True, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False, index=True)
    authorization_id = Column(Integer, ForeignKey("practitioner_authorized_procedures.id"), nullable=False, index=True)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_until = Column(Date)
    is_active = Column(Boolean, default=True, nullable=False)
    change_type = Column(Enum(EvidenceChangeType), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    remark = Column(String(500))

    practitioner = relationship("Practitioner", back_populates="procedure_grant_versions")
    authorization = relationship("PractitionerAuthorizedProcedure", back_populates="versions")
    procedure = relationship("Procedure")


class ProcedureRecordParticipant(Base):
    """
    一项治疗涉及多名人员时的参与人绑定。主操作人同时写在项目记录上，
    其余参与人（麻醉、护理等）逐行绑定，判定时逐人给出双主体证据。
    """
    __tablename__ = "procedure_record_participants"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("actual_procedure_records.id"), nullable=False, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False, index=True)
    role = Column(String(50), default="操作人")
    is_primary = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    record = relationship("ActualProcedureRecord", back_populates="participants")
    practitioner = relationship("Practitioner", back_populates="participant_bindings")


class ComplianceJudgment(Base):
    """
    项目记录的一次判定（初次判定或重新判定），只追加、不覆盖。
    sequence_no 从 1 递增；处置影响只在结论翻转时非空。
    """
    __tablename__ = "compliance_judgments"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("actual_procedure_records.id"), nullable=False, index=True)
    sequence_no = Column(Integer, nullable=False)
    concluded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    conclusion = Column(Enum(SnapshotConclusion), nullable=False)
    reason = Column(Enum(RejudgmentReason), nullable=False)
    trigger_remark = Column(String(500))
    changed_from_previous = Column(Boolean, default=False, nullable=False)
    disposition_impact = Column(String(500))
    evidence_summary = Column(Text)

    record = relationship("ActualProcedureRecord", back_populates="judgments")
    evidence_items = relationship(
        "JudgmentEvidenceItem", back_populates="judgment",
        cascade="all, delete-orphan"
    )


class JudgmentEvidenceItem(Base):
    """
    判定中的单条证据：精确绑定到某一证据版本；治疗时证据尚不存在
    （先记录后补证）时 version_id 为空并标明 MISSING。
    side: institution / practitioner 标明双主体归属。
    """
    __tablename__ = "judgment_evidence_items"

    id = Column(Integer, primary_key=True, index=True)
    judgment_id = Column(Integer, ForeignKey("compliance_judgments.id"), nullable=False, index=True)
    side = Column(String(20), nullable=False)
    participant_id = Column(Integer, ForeignKey("procedure_record_participants.id"))
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"))
    dimension = Column(String(40), nullable=False)
    item_key = Column(String(120), nullable=False)
    status = Column(Enum(EvidenceItemStatus), nullable=False)
    version_id = Column(Integer)
    version_no = Column(Integer)
    change_type = Column(Enum(EvidenceChangeType))
    valid_from = Column(Date)
    valid_until = Column(Date)
    recorded_at = Column(DateTime)
    detail = Column(String(500))

    judgment = relationship("ComplianceJudgment", back_populates="evidence_items")
