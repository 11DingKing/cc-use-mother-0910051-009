"""
时点证据与追加式判定引擎
========================

把每次项目记录的合规判定绑定到“治疗发生时”的四类证据版本：

1. 机构执业许可证版本          InstitutionLicenseVersion
2. 机构项目授权版本            InstitutionAuthVersion
3. 人员资质版本（逐证书）      QualificationVersion
4. 人员项目授权版本            PractitionerAuthVersion

证据链只追加、不覆盖：暂停、恢复、变更、追溯更正、事后补录都追加新版本，
并对受影响的历史项目记录生成一次新的 ``ComplianceJudgment``（重新判定）。
原判定及其逐项证据永久保留，结论翻转时在新判定上标注处置影响。
"""

from datetime import date, datetime
from typing import List, Optional, Tuple, Dict, Any

from sqlalchemy.orm import Session

from .models import (
    Institution, InstitutionLicense, Practitioner, PractitionerQualification,
    QualificationType, Procedure,
    InstitutionAuthorizedProcedure, PractitionerAuthorizedProcedure,
    ActualProcedureRecord, ViolationClue, ClueType, CluePriority,
    InstitutionLicenseVersion, InstitutionAuthVersion,
    QualificationVersion, PractitionerAuthVersion,
    ProcedureRecordParticipant, ComplianceJudgment, JudgmentEvidenceItem,
    EvidenceItemStatus, EvidenceChangeType,
    SnapshotConclusion, RejudgmentReason,
)

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

# 档案中起始日期缺失时的兜底起始日（仅用于历史档案补登的版本链）
_FALLBACK_FROM = date(1900, 1, 1)

_STATUS = EvidenceItemStatus
_CHANGE = EvidenceChangeType


def _now() -> datetime:
    return datetime.utcnow()


def _covers(v, d: date) -> bool:
    if v.valid_from is not None and d < v.valid_from:
        return False
    if v.valid_until is not None and d > v.valid_until:
        return False
    return True


def pick_version_at(versions: List, d: date) -> Tuple[Optional[Any], Optional[EvidenceItemStatus]]:
    """
    在版本链中为日期 d 选版。

    规则（版本链只追加、不修改旧版本）：
    - 所有覆盖 d（valid_from <= d <= valid_until，空端视为无限）的版本中，
      版本号最高者胜：它是关于该日的“最新主张”；
    - 若中选版本 is_active=False（暂停），返回 INACTIVE；
    - 无覆盖版本：最早版本生效日晚于 d → 尚未生效；否则视为已过期/不覆盖。
    """
    if not versions:
        return None, _STATUS.MISSING

    covering = [v for v in versions if _covers(v, d)]
    if covering:
        chosen = max(covering, key=lambda v: v.version_no)
        return chosen, (None if chosen.is_active else _STATUS.INACTIVE)

    earliest_from = min(v.valid_from for v in versions if v.valid_from is not None)
    if d < earliest_from:
        return None, _STATUS.NOT_EFFECTIVE
    return None, _STATUS.EXPIRED


# ---------------------------------------------------------------------------
# 版本链维护（只追加）
# ---------------------------------------------------------------------------

def _chain(db: Session, model, **filters) -> List:
    q = db.query(model).filter_by(**filters)
    return q.order_by(model.version_no.asc()).all()


def _next_no(versions: List) -> int:
    return (max(v.version_no for v in versions) + 1) if versions else 1


def register_license_version(
    db: Session,
    license_id: int,
    change_type: EvidenceChangeType,
    effective_date: Optional[date] = None,
    valid_from: Optional[date] = None,
    valid_until: Optional[date] = None,
    is_active: bool = True,
    remark: Optional[str] = None,
) -> InstitutionLicenseVersion:
    lic = db.query(InstitutionLicense).filter(InstitutionLicense.id == license_id).first()
    if not lic:
        raise ValueError("许可证不存在")

    versions = _chain(db, InstitutionLicenseVersion, license_id=license_id)
    effective_date = effective_date or valid_from or lic.issue_date or date.today()
    vf = valid_from or effective_date
    vu = valid_until

    if change_type == _CHANGE.SUSPEND:
        vf, vu, is_active = effective_date, None, False
    elif change_type == _CHANGE.RESTORE:
        vf, vu, is_active = effective_date, None, True
    elif change_type == _CHANGE.CHANGE:
        vf, vu = effective_date, valid_until
    elif change_type in (_CHANGE.RETROACTIVE_CORRECTION, _CHANGE.BACKFILL):
        # 旧版本原样保留作为历史主张；同区间内版本号更高的新主张胜出
        vf, vu = valid_from, valid_until

    row = InstitutionLicenseVersion(
        institution_id=lic.institution_id,
        license_id=license_id,
        version_no=_next_no(versions),
        license_number=lic.license_number,
        issuing_authority=lic.issuing_authority,
        valid_from=vf,
        valid_until=vu,
        approved_surgeries=lic.approved_surgeries,
        is_active=is_active,
        change_type=change_type,
        recorded_at=_now(),
        remark=remark,
    )
    db.add(row)
    db.flush()
    return row


def register_institution_auth_version(
    db: Session,
    authorization_id: int,
    change_type: EvidenceChangeType,
    effective_date: Optional[date] = None,
    valid_from: Optional[date] = None,
    valid_until: Optional[date] = None,
    is_active: bool = True,
    remark: Optional[str] = None,
) -> InstitutionAuthVersion:
    auth = db.query(InstitutionAuthorizedProcedure).filter(
        InstitutionAuthorizedProcedure.id == authorization_id
    ).first()
    if not auth:
        raise ValueError("机构项目授权不存在")

    versions = _chain(db, InstitutionAuthVersion, authorization_id=authorization_id)
    effective_date = effective_date or valid_from or auth.authorized_date or date.today()
    vf = valid_from or effective_date
    vu = valid_until

    if change_type == _CHANGE.SUSPEND:
        vf, vu, is_active = effective_date, None, False
    elif change_type == _CHANGE.RESTORE:
        vf, vu, is_active = effective_date, None, True
    elif change_type == _CHANGE.CHANGE:
        vf, vu = effective_date, valid_until
    elif change_type in (_CHANGE.RETROACTIVE_CORRECTION, _CHANGE.BACKFILL):
        # 旧版本原样保留作为历史主张；同区间内版本号更高的新主张胜出
        vf, vu = valid_from, valid_until

    row = InstitutionAuthVersion(
        institution_id=auth.institution_id,
        authorization_id=authorization_id,
        procedure_id=auth.procedure_id,
        version_no=_next_no(versions),
        valid_from=vf,
        valid_until=vu,
        is_active=is_active,
        change_type=change_type,
        recorded_at=_now(),
        remark=remark,
    )
    db.add(row)
    db.flush()
    return row


def register_qualification_version(
    db: Session,
    qualification_id: int,
    change_type: EvidenceChangeType,
    effective_date: Optional[date] = None,
    valid_from: Optional[date] = None,
    valid_until: Optional[date] = None,
    is_active: bool = True,
    remark: Optional[str] = None,
) -> QualificationVersion:
    qual = db.query(PractitionerQualification).filter(
        PractitionerQualification.id == qualification_id
    ).first()
    if not qual:
        raise ValueError("人员资质不存在")

    versions = _chain(db, QualificationVersion, qualification_id=qualification_id)
    effective_date = effective_date or valid_from or qual.issue_date or date.today()
    vf = valid_from or effective_date
    vu = valid_until if valid_until is not None else qual.valid_until

    if change_type == _CHANGE.SUSPEND:
        vf, vu, is_active = effective_date, None, False
    elif change_type == _CHANGE.RESTORE:
        vf, vu, is_active = effective_date, None, True
    elif change_type == _CHANGE.CHANGE:
        vf, vu = effective_date, valid_until
    elif change_type in (_CHANGE.RETROACTIVE_CORRECTION, _CHANGE.BACKFILL):
        # 旧版本原样保留作为历史主张；同区间内版本号更高的新主张胜出
        vf, vu = valid_from, valid_until

    row = QualificationVersion(
        practitioner_id=qual.practitioner_id,
        qualification_id=qualification_id,
        qualification_type=qual.qualification_type,
        version_no=_next_no(versions),
        certificate_number=qual.certificate_number,
        issuing_authority=qual.issuing_authority,
        valid_from=vf,
        valid_until=vu,
        practice_scope=qual.practice_scope,
        is_active=is_active,
        change_type=change_type,
        recorded_at=_now(),
        remark=remark,
    )
    db.add(row)
    db.flush()
    return row


def register_practitioner_auth_version(
    db: Session,
    authorization_id: int,
    change_type: EvidenceChangeType,
    effective_date: Optional[date] = None,
    valid_from: Optional[date] = None,
    valid_until: Optional[date] = None,
    is_active: bool = True,
    remark: Optional[str] = None,
) -> PractitionerAuthVersion:
    auth = db.query(PractitionerAuthorizedProcedure).filter(
        PractitionerAuthorizedProcedure.id == authorization_id
    ).first()
    if not auth:
        raise ValueError("人员项目授权不存在")

    versions = _chain(db, PractitionerAuthVersion, authorization_id=authorization_id)
    effective_date = effective_date or valid_from or auth.authorized_date or date.today()
    vf = valid_from or effective_date
    vu = valid_until

    if change_type == _CHANGE.SUSPEND:
        vf, vu, is_active = effective_date, None, False
    elif change_type == _CHANGE.RESTORE:
        vf, vu, is_active = effective_date, None, True
    elif change_type == _CHANGE.CHANGE:
        vf, vu = effective_date, valid_until
    elif change_type in (_CHANGE.RETROACTIVE_CORRECTION, _CHANGE.BACKFILL):
        # 旧版本原样保留作为历史主张；同区间内版本号更高的新主张胜出
        vf, vu = valid_from, valid_until

    row = PractitionerAuthVersion(
        practitioner_id=auth.practitioner_id,
        authorization_id=authorization_id,
        procedure_id=auth.procedure_id,
        version_no=_next_no(versions),
        valid_from=vf,
        valid_until=vu,
        is_active=is_active,
        change_type=change_type,
        recorded_at=_now(),
        remark=remark,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# 历史档案补登：为旧表中直接创建、尚无版本链的行合成 v1
# ---------------------------------------------------------------------------

def _ensure_license_chains(db: Session, institution_id: int) -> List[InstitutionLicenseVersion]:
    out: List[InstitutionLicenseVersion] = []
    rows = db.query(InstitutionLicense).filter(
        InstitutionLicense.institution_id == institution_id
    ).all()
    for lic in rows:
        versions = _chain(db, InstitutionLicenseVersion, license_id=lic.id)
        if not versions:
            versions = [register_license_version(
                db, lic.id, _CHANGE.INITIAL,
                valid_from=lic.issue_date or _FALLBACK_FROM,
                valid_until=lic.valid_until,
                is_active=bool(lic.is_valid),
                remark="历史档案补登版本",
            )]
        out.extend(versions)
    return out


def _ensure_inst_auth_chains(db: Session, institution_id: int, procedure_id: int) -> List[InstitutionAuthVersion]:
    rows = db.query(InstitutionAuthorizedProcedure).filter(
        InstitutionAuthorizedProcedure.institution_id == institution_id,
        InstitutionAuthorizedProcedure.procedure_id == procedure_id,
    ).all()
    out: List[InstitutionAuthVersion] = []
    for auth in rows:
        versions = _chain(db, InstitutionAuthVersion, authorization_id=auth.id)
        if not versions:
            versions = [register_institution_auth_version(
                db, auth.id, _CHANGE.INITIAL,
                valid_from=auth.authorized_date or _FALLBACK_FROM,
                is_active=True,
                remark="历史档案补登版本",
            )]
        out.extend(versions)
    return out


def _ensure_qualification_chains(
    db: Session, practitioner_id: int, qtype: QualificationType
) -> List[QualificationVersion]:
    rows = db.query(PractitionerQualification).filter(
        PractitionerQualification.practitioner_id == practitioner_id,
        PractitionerQualification.qualification_type == qtype,
    ).all()
    out: List[QualificationVersion] = []
    for qual in rows:
        versions = _chain(db, QualificationVersion, qualification_id=qual.id)
        if not versions:
            versions = [register_qualification_version(
                db, qual.id, _CHANGE.INITIAL,
                valid_from=qual.issue_date or _FALLBACK_FROM,
                valid_until=qual.valid_until,
                is_active=bool(qual.is_valid),
                remark="历史档案补登版本",
            )]
        out.extend(versions)
    return out


def _ensure_prac_auth_chains(db: Session, practitioner_id: int, procedure_id: int) -> List[PractitionerAuthVersion]:
    rows = db.query(PractitionerAuthorizedProcedure).filter(
        PractitionerAuthorizedProcedure.practitioner_id == practitioner_id,
        PractitionerAuthorizedProcedure.procedure_id == procedure_id,
    ).all()
    out: List[PractitionerAuthVersion] = []
    for auth in rows:
        versions = _chain(db, PractitionerAuthVersion, authorization_id=auth.id)
        if not versions:
            versions = [register_practitioner_auth_version(
                db, auth.id, _CHANGE.INITIAL,
                valid_from=auth.authorized_date or _FALLBACK_FROM,
                is_active=True,
                remark="历史档案补登版本",
            )]
        out.extend(versions)
    return out


# ---------------------------------------------------------------------------
# 时点判定（双主体证据）
# ---------------------------------------------------------------------------

def _evidence_item(
    side: str,
    dimension: str,
    item_key: str,
    versions: List,
    d: date,
    missing_message: str,
    expired_message_tpl: str,
    practitioner_id: Optional[int] = None,
    participant_id: Optional[int] = None,
) -> Dict[str, Any]:
    chosen, status = pick_version_at(versions, d)
    if chosen is None:
        if status == _STATUS.NOT_EFFECTIVE:
            earliest = min(v.valid_from for v in versions if v.valid_from)
            detail = f"{dimension}在治疗之日尚未生效（最早生效日 {earliest}）"
        elif status == _STATUS.EXPIRED:
            latest = max(versions, key=lambda v: v.version_no)
            detail = expired_message_tpl.format(valid_until=latest.valid_until)
        else:
            detail = missing_message
        return {
            "side": side, "dimension": dimension, "item_key": item_key,
            "status": status, "version": None, "detail": detail,
            "practitioner_id": practitioner_id, "participant_id": participant_id,
        }

    if status == _STATUS.INACTIVE:
        detail = f"{dimension}在治疗之日处于暂停/失效状态（自 {chosen.valid_from} 起）"
    elif (
        chosen.change_type in (_CHANGE.BACKFILL, _CHANGE.RETROACTIVE_CORRECTION)
        and chosen.recorded_at
        and chosen.recorded_at.date() > d
    ):
        # 事后补录/追溯更正的证据：治疗发生时该版本尚不存在，无法自证
        status = _STATUS.VALID_BACKFILLED
        detail = (
            f"{dimension}系{chosen.change_type.value}（登记于 {chosen.recorded_at.date()}，"
            f"晚于治疗日 {d}）：治疗发生时该证据是否满足无法证明"
        )
    else:
        bits = [f"版本v{chosen.version_no}", f"生效 {chosen.valid_from}"]
        if chosen.valid_until:
            bits.append(f"至 {chosen.valid_until}")
        bits.append(chosen.change_type.value)
        if chosen.recorded_at and chosen.recorded_at.date() > d:
            bits.append(f"注意：该证据登记于 {chosen.recorded_at.date()}，晚于治疗日（事后补录）")
        detail = f"{dimension}有效（{', '.join(bits)}）"

    return {
        "side": side, "dimension": dimension, "item_key": item_key,
        "status": status if status else _STATUS.VALID,
        "version": chosen, "detail": detail,
        "practitioner_id": practitioner_id, "participant_id": participant_id,
    }


def evaluate_at(
    db: Session,
    institution_id: int,
    procedure_id: int,
    participants: List[Tuple[int, str]],  # (practitioner_id, role)
    d: date,
) -> Dict[str, Any]:
    """
    返回治疗日 d 的双主体时点证据：
    {
      "institution_items": [...],
      "participant_items": {practitioner_id: [items...]},
      "issues": [中文问题描述],
      "conclusion": SnapshotConclusion,
    }
    """
    institution_items: List[Dict[str, Any]] = []
    participant_items: Dict[int, List[Dict[str, Any]]] = {}
    issues: List[str] = []
    unprovable: List[str] = []

    def account(item: Dict[str, Any]) -> None:
        if item["status"] == _STATUS.VALID_BACKFILLED:
            unprovable.append(item["detail"])
        elif item["status"] != _STATUS.VALID:
            issues.append(item["detail"])

    # —— 机构侧：执业许可证 ——
    license_versions = _ensure_license_chains(db, institution_id)
    lic_item = _evidence_item(
        "institution", "机构执业许可证", f"机构#{institution_id}/许可证",
        license_versions, d,
        "机构无有效医疗机构执业许可证（治疗时无任何许可证版本）",
        "机构执业许可证已过期（有效期至 {valid_until}）",
    )
    institution_items.append(lic_item)
    account(lic_item)

    # —— 机构侧：项目授权 ——
    inst_auth_versions = _ensure_inst_auth_chains(db, institution_id, procedure_id)
    ia_item = _evidence_item(
        "institution", "机构项目授权", f"机构#{institution_id}/项目#{procedure_id}",
        inst_auth_versions, d,
        "机构未获得该项目执业授权（治疗时无授权版本）",
        "机构该项目授权已失效（有效期至 {valid_until}）",
    )
    institution_items.append(ia_item)
    account(ia_item)

    # —— 人员侧：逐人核验资质与项目授权 ——
    for practitioner_id, role in participants:
        items: List[Dict[str, Any]] = []

        doc_versions = _ensure_qualification_chains(db, practitioner_id, QualificationType.DOCTOR)
        doc_item = _evidence_item(
            "practitioner", "人员医师资格证",
            f"人员#{practitioner_id}/医师资格证",
            doc_versions, d,
            "人员无有效医师资格证（治疗时无资质版本）",
            "人员医师资格证已过期（有效期至 {valid_until}）",
            practitioner_id=practitioner_id,
        )
        items.append(doc_item)
        account(doc_item)

        prac_versions = _ensure_qualification_chains(db, practitioner_id, QualificationType.PRACTICE)
        pr_item = _evidence_item(
            "practitioner", "人员医师执业证",
            f"人员#{practitioner_id}/医师执业证",
            prac_versions, d,
            "人员无有效医师执业证（治疗时无资质版本）",
            "人员医师执业证已过期（有效期至 {valid_until}）",
            practitioner_id=practitioner_id,
        )
        items.append(pr_item)
        account(pr_item)

        pa_versions = _ensure_prac_auth_chains(db, practitioner_id, procedure_id)
        pa_item = _evidence_item(
            "practitioner", "人员项目授权",
            f"人员#{practitioner_id}/项目#{procedure_id}",
            pa_versions, d,
            "人员未获得该项目操作授权（治疗时无授权版本）",
            "人员该项目授权已失效（有效期至 {valid_until}）",
            practitioner_id=practitioner_id,
        )
        items.append(pa_item)
        account(pa_item)

        participant_items[practitioner_id] = items

    # 三态结论：硬缺陷→违规；无硬缺陷但存在事后补录→治疗时无法判定；否则合规
    if issues:
        conclusion = SnapshotConclusion.VIOLATION
    elif unprovable:
        conclusion = SnapshotConclusion.INCONCLUSIVE
    else:
        conclusion = SnapshotConclusion.COMPLIANT
    return {
        "institution_items": institution_items,
        "participant_items": participant_items,
        "issues": issues,
        "unprovable": unprovable,
        "conclusion": conclusion,
    }


# ---------------------------------------------------------------------------
# 判定快照（只追加）
# ---------------------------------------------------------------------------

def _persist_evidence_items(db: Session, judgment_id: int, evaluation: Dict[str, Any]) -> None:
    def add(item: Dict[str, Any]) -> None:
        v = item["version"]
        db.add(JudgmentEvidenceItem(
            judgment_id=judgment_id,
            side=item["side"],
            participant_id=item.get("participant_id"),
            practitioner_id=item.get("practitioner_id"),
            dimension=item["dimension"],
            item_key=item["item_key"],
            status=item["status"],
            version_id=v.id if v else None,
            version_no=v.version_no if v else None,
            change_type=v.change_type if v else None,
            valid_from=v.valid_from if v else None,
            valid_until=v.valid_until if v else None,
            recorded_at=v.recorded_at if v else None,
            detail=item["detail"],
        ))

    for item in evaluation["institution_items"]:
        add(item)
    for items in evaluation["participant_items"].values():
        for item in items:
            add(item)


def _disposition_text(previous: Optional[SnapshotConclusion], current: SnapshotConclusion,
                      reason: RejudgmentReason) -> Optional[str]:
    if previous is None or previous == current:
        return None
    if current == SnapshotConclusion.VIOLATION:
        return (
            f"后续证据变化（{reason.value}）使该记录由「{previous.value}」改判为「{current.value}」："
            "原判定保留备查，应据此新增违规线索与监管处置。"
        )
    if current == SnapshotConclusion.COMPLIANT:
        return (
            f"后续补证/更正（{reason.value}）使该记录由「{previous.value}」改判为「{current.value}」："
            "原违规判定及已启动处置保留备查，建议撤销线索或不予追究。"
        )
    return (
        f"后续补录/更正（{reason.value}）使该记录由「{previous.value}」改判为「无法判定」："
        "原判定保留备查；治疗时证据是否满足无法证明，需结合原始材料进一步核查后再决定处置。"
    )


def ensure_participants(db: Session, record: ActualProcedureRecord,
                        extra: Optional[List[Any]] = None) -> List[ProcedureRecordParticipant]:
    """保证主操作人存在参与绑定，并追加其他参与人。"""
    existing = {p.practitioner_id for p in record.participants}
    if record.practitioner_id not in existing:
        db.add(ProcedureRecordParticipant(
            record_id=record.id, practitioner_id=record.practitioner_id,
            role="主操作人", is_primary=True,
        ))
    for p in extra or []:
        pid = getattr(p, "practitioner_id", None) or p["practitioner_id"]
        role = getattr(p, "role", None) or p.get("role") or "参与人"
        if pid in existing or pid == record.practitioner_id:
            continue
        db.add(ProcedureRecordParticipant(
            record_id=record.id, practitioner_id=pid,
            role=role, is_primary=False,
        ))
    db.flush()
    return db.query(ProcedureRecordParticipant).filter(
        ProcedureRecordParticipant.record_id == record.id
    ).order_by(ProcedureRecordParticipant.is_primary.desc()).all()


def create_judgment(
    db: Session,
    record: ActualProcedureRecord,
    reason: RejudgmentReason,
    trigger_remark: Optional[str] = None,
    extra_participants: Optional[List[Dict[str, Any]]] = None,
) -> ComplianceJudgment:
    """对一条项目记录执行一次判定并持久化不可变快照。"""
    participants = ensure_participants(db, record, extra_participants)
    participant_list = [(p.practitioner_id, p.role) for p in participants]

    # 绑定参与人到本次评估结果
    pid_to_binding = {p.practitioner_id: p.id for p in participants}
    evaluation = evaluate_at(
        db, record.institution_id, record.procedure_id,
        participant_list, record.procedure_date,
    )
    for pid, items in evaluation["participant_items"].items():
        for item in items:
            item["participant_id"] = pid_to_binding.get(pid)

    previous = db.query(ComplianceJudgment).filter(
        ComplianceJudgment.record_id == record.id
    ).order_by(ComplianceJudgment.sequence_no.desc()).first()

    sequence_no = (previous.sequence_no + 1) if previous else 1
    conclusion = evaluation["conclusion"]
    changed = bool(previous and previous.conclusion != conclusion)

    if evaluation["issues"]:
        summary = "；".join(evaluation["issues"])
    elif evaluation.get("unprovable"):
        summary = "治疗时无法判定：" + "；".join(evaluation["unprovable"])
    else:
        summary = "双主体四类证据在治疗日均有效"

    judgment = ComplianceJudgment(
        record_id=record.id,
        sequence_no=sequence_no,
        concluded_at=_now(),
        conclusion=conclusion,
        reason=reason,
        trigger_remark=trigger_remark,
        changed_from_previous=changed,
        disposition_impact=_disposition_text(previous.conclusion if previous else None, conclusion, reason),
        evidence_summary=summary,
    )
    db.add(judgment)
    db.flush()
    _persist_evidence_items(db, judgment.id, evaluation)

    # 同步记录上的冗余判定列（供既有统计与列表使用），不删除任何历史判定
    record.is_over_range = conclusion == SnapshotConclusion.VIOLATION
    record.judgment_conclusion = conclusion.value
    record.over_range_detail = (
        "; ".join(evaluation["issues"]) if evaluation["issues"] else None
    )
    db.flush()
    return judgment


# ---------------------------------------------------------------------------
# 证据变更 → 受影响记录 → 追加重新判定
# ---------------------------------------------------------------------------

def _current_signature(db: Session, record_id: int, evaluation: Dict[str, Any]) -> List[tuple]:
    bindings = {
        p.practitioner_id: p.id for p in db.query(ProcedureRecordParticipant).filter(
            ProcedureRecordParticipant.record_id == record_id
        ).all()
    }
    sig = [("institution", None, it["item_key"], it["status"].name)
           for it in evaluation["institution_items"]]
    for pid, items in evaluation["participant_items"].items():
        for it in items:
            sig.append(("practitioner", bindings.get(pid), it["item_key"], it["status"].name))
    return sorted(sig)


def _stored_signature(db: Session, judgment: ComplianceJudgment) -> List[tuple]:
    rows = db.query(JudgmentEvidenceItem).filter(
        JudgmentEvidenceItem.judgment_id == judgment.id
    ).all()
    return sorted(
        (r.side, r.participant_id, r.item_key, r.status.name) for r in rows
    )


def create_judgment_if_changed(
    db: Session,
    record: ActualProcedureRecord,
    reason: RejudgmentReason,
    trigger_remark: Optional[str] = None,
) -> Optional[ComplianceJudgment]:
    """
    重新判定：仅当双主体证据状态或结论相对上一判定发生变化时才追加新判定；
    无变化时返回 None，避免噪声判定。首判必定追加。
    """
    participants = ensure_participants(db, record)
    participant_list = [(p.practitioner_id, p.role) for p in participants]
    evaluation = evaluate_at(
        db, record.institution_id, record.procedure_id,
        participant_list, record.procedure_date,
    )
    pid_to_binding = {p.practitioner_id: p.id for p in participants}
    for pid, items in evaluation["participant_items"].items():
        for item in items:
            item["participant_id"] = pid_to_binding.get(pid)

    last = db.query(ComplianceJudgment).filter(
        ComplianceJudgment.record_id == record.id
    ).order_by(ComplianceJudgment.sequence_no.desc()).first()

    if last is not None:
        new_sig = _current_signature(db, record.id, evaluation)
        if _stored_signature(db, last) == new_sig and last.conclusion == evaluation["conclusion"]:
            return None

    return create_judgment(db, record, reason, trigger_remark)


_REASON_BY_DIMENSION = {
    "license": {
        _CHANGE.SUSPEND: RejudgmentReason.LICENSE_SUSPENDED,
        _CHANGE.RESTORE: RejudgmentReason.LICENSE_RESTORED,
        _CHANGE.CHANGE: RejudgmentReason.LICENSE_CORRECTED,
        _CHANGE.RETROACTIVE_CORRECTION: RejudgmentReason.LICENSE_CORRECTED,
        _CHANGE.BACKFILL: RejudgmentReason.LICENSE_CORRECTED,
        _CHANGE.INITIAL: RejudgmentReason.MANUAL,
    },
    "institution_auth": {
        _CHANGE.SUSPEND: RejudgmentReason.INSTITUTION_AUTH_SUSPENDED,
        _CHANGE.RESTORE: RejudgmentReason.INSTITUTION_AUTH_RESTORED,
        _CHANGE.CHANGE: RejudgmentReason.INSTITUTION_AUTH_CORRECTED,
        _CHANGE.RETROACTIVE_CORRECTION: RejudgmentReason.INSTITUTION_AUTH_CORRECTED,
        _CHANGE.BACKFILL: RejudgmentReason.INSTITUTION_AUTH_CORRECTED,
        _CHANGE.INITIAL: RejudgmentReason.MANUAL,
    },
    "qualification": {
        _CHANGE.SUSPEND: RejudgmentReason.QUALIFICATION_SUSPENDED,
        _CHANGE.RESTORE: RejudgmentReason.QUALIFICATION_RESTORED,
        _CHANGE.CHANGE: RejudgmentReason.QUALIFICATION_CORRECTED,
        _CHANGE.RETROACTIVE_CORRECTION: RejudgmentReason.QUALIFICATION_CORRECTED,
        _CHANGE.BACKFILL: RejudgmentReason.QUALIFICATION_CORRECTED,
        _CHANGE.INITIAL: RejudgmentReason.MANUAL,
    },
    "practitioner_auth": {
        _CHANGE.SUSPEND: RejudgmentReason.PRACTITIONER_AUTH_SUSPENDED,
        _CHANGE.RESTORE: RejudgmentReason.PRACTITIONER_AUTH_RESTORED,
        _CHANGE.CHANGE: RejudgmentReason.PRACTITIONER_AUTH_CORRECTED,
        _CHANGE.RETROACTIVE_CORRECTION: RejudgmentReason.PRACTITIONER_AUTH_CORRECTED,
        _CHANGE.BACKFILL: RejudgmentReason.PRACTITIONER_AUTH_CORRECTED,
        _CHANGE.INITIAL: RejudgmentReason.MANUAL,
    },
}


def _records_for_practitioner(db: Session, practitioner_id: int,
                              procedure_id: Optional[int] = None):
    q = db.query(ActualProcedureRecord).filter(
        (ActualProcedureRecord.practitioner_id == practitioner_id)
        | (ActualProcedureRecord.id.in_(
            db.query(ProcedureRecordParticipant.record_id).filter(
                ProcedureRecordParticipant.practitioner_id == practitioner_id
            )
        ))
    )
    if procedure_id is not None:
        q = q.filter(ActualProcedureRecord.procedure_id == procedure_id)
    return q.all()


def rejudge_after_evidence_change(
    db: Session,
    dimension: str,
    change_type: EvidenceChangeType,
    institution_id: Optional[int] = None,
    practitioner_id: Optional[int] = None,
    procedure_id: Optional[int] = None,
    valid_from: Optional[date] = None,
    valid_until: Optional[date] = None,
    trigger_remark: Optional[str] = None,
) -> List[ComplianceJudgment]:
    """证据版本变化后，对全部受影响历史记录追加一次重新判定。"""
    if dimension in ("license", "institution_auth"):
        q = db.query(ActualProcedureRecord).filter(
            ActualProcedureRecord.institution_id == institution_id
        )
        if dimension == "institution_auth":
            q = q.filter(ActualProcedureRecord.procedure_id == procedure_id)
        records = q.all()
    else:
        records = _records_for_practitioner(db, practitioner_id, procedure_id)

    reason = _REASON_BY_DIMENSION[dimension][change_type]
    judgments: List[ComplianceJudgment] = []
    for record in records:
        d = record.procedure_date
        if valid_from and d < valid_from:
            continue
        if valid_until and d > valid_until:
            continue
        judgment = create_judgment_if_changed(db, record, reason, trigger_remark)
        if judgment is None:
            continue
        judgments.append(judgment)

        # 合规 → 违规翻转：自动登记超范围执业线索（同主体同项目去重）
        if judgment.changed_from_previous and judgment.conclusion == SnapshotConclusion.VIOLATION:
            existing = db.query(ViolationClue).filter(
                ViolationClue.institution_id == record.institution_id,
                ViolationClue.practitioner_id == record.practitioner_id,
                ViolationClue.procedure_id == record.procedure_id,
                ViolationClue.clue_type == ClueType.OVER_RANGE_PRACTICE,
            ).first()
            if not existing:
                inst = db.query(Institution).filter(
                    Institution.id == record.institution_id
                ).first()
                prac = db.query(Practitioner).filter(
                    Practitioner.id == record.practitioner_id
                ).first()
                proc = db.query(Procedure).filter(
                    Procedure.id == record.procedure_id
                ).first()
                db.add(ViolationClue(
                    clue_type=ClueType.OVER_RANGE_PRACTICE,
                    title=(
                        f"证据变更改判超范围执业：{inst.name if inst else record.institution_id}"
                        f" - {prac.name if prac else record.practitioner_id}"
                    ),
                    description=(
                        f"因{reason.value}，记录#{record.id}（治疗日 {d}，"
                        f"项目{proc.name if proc else record.procedure_id}）由合规改判违规。\n"
                        f"问题：{judgment.evidence_summary}"
                    ),
                    institution_id=record.institution_id,
                    practitioner_id=record.practitioner_id,
                    procedure_id=record.procedure_id,
                    source="证据变更自动重新判定",
                    priority=CluePriority.HIGH,
                ))
    return judgments


# ---------------------------------------------------------------------------
# 兼容旧接口的判定包装
# ---------------------------------------------------------------------------

def judge_over_range_at(
    db: Session,
    institution_id: int,
    practitioner_id: int,
    procedure_id: int,
    procedure_date: date,
) -> Tuple[bool, List[str]]:
    """供旧版 judge_over_range_practice / 单条校验复用。"""
    evaluation = evaluate_at(
        db, institution_id, procedure_id,
        [(practitioner_id, "主操作人")], procedure_date
    )
    return evaluation["conclusion"] == SnapshotConclusion.VIOLATION, evaluation["issues"]
