"""发生时判定引擎（双时态版本解析 + 追加式可审计重新判定）。

一致性规则（对所有判定统一适用）：
1. 同日生效边界：版本有效期为闭区间 [effective_from, effective_to]，
   生效日当日即可用于治疗日，effective_to 为空表示长期有效。
2. 知识时点：每次判定只使用 recorded_at <= 判定时刻 且在该时刻未被
   更正关闭（superseded_at 为空或晚于判定时刻）的版本行。
3. 覆盖解析：同一链条上，对治疗日有多行覆盖时，取 recorded_at 最晚的一行，
   其 status 决定有效性（暂停/注销行会压过更早的有效行）。
4. 先记录后补证：录入日期晚于治疗日的版本在证据中标记 retroactive_entry；
   录入晚于诊疗记录登记时间的版本标记 entered_after_record。
5. 跨机构执业：参与人员登记机构与治疗机构不一致时，人员项目授权必须
   显式限定到治疗机构（institution_id 等于治疗机构）；未限定机构的授权
   仅覆盖其登记机构。
6. 多名人员：每位参与人员独立判定（机构侧证据共享），事件级结论为全体
   参与人员结论的“与”，保证同一治疗事件结论一致。
7. 任何一侧的暂停、变更或追溯更正触发重新判定时，旧判定行保留，
   新判定行追加并链接 supersedes_id，绝不覆盖原结论。
"""
from datetime import date, datetime
from typing import List, Optional, Tuple, Dict, Any, Type
import json

from sqlalchemy.orm import Session

from .models import (
    Institution, Practitioner, Procedure,
    InstitutionLicenseVersion, InstitutionProcedureAuthVersion,
    PractitionerQualificationVersion, PractitionerProcedureAuthVersion,
    TreatmentEvent, TreatmentParticipant, ComplianceDetermination,
    RedeterminationRun, ClueEventLink, DispositionImpactNotice,
    ViolationClue, ClueType, ClueStatus, CluePriority,
    VersionStatus, VersionChangeKind, DeterminationTrigger, DeterminationResult,
    QualificationType,
)

CORE_QUAL_TYPES = (QualificationType.DOCTOR, QualificationType.PRACTICE)

SUPERSEDING_KINDS = (
    VersionChangeKind.INITIAL,
    VersionChangeKind.RENEWAL,
    VersionChangeKind.AMENDMENT,
    VersionChangeKind.CORRECTION,
)

STATUS_BY_KIND = {
    VersionChangeKind.INITIAL: VersionStatus.ACTIVE,
    VersionChangeKind.RENEWAL: VersionStatus.ACTIVE,
    VersionChangeKind.AMENDMENT: VersionStatus.ACTIVE,
    VersionChangeKind.CORRECTION: VersionStatus.ACTIVE,
    VersionChangeKind.SUSPENSION: VersionStatus.SUSPENDED,
    VersionChangeKind.REINSTATE: VersionStatus.ACTIVE,
    VersionChangeKind.REVOCATION: VersionStatus.REVOKED,
}

TRIGGER_BY_TABLE = {
    "institution_license_versions": DeterminationTrigger.LICENSE_VERSION,
    "institution_procedure_auth_versions": DeterminationTrigger.INST_AUTH_VERSION,
    "practitioner_qualification_versions": DeterminationTrigger.QUAL_VERSION,
    "practitioner_procedure_auth_versions": DeterminationTrigger.PRAC_AUTH_VERSION,
}


# ---------------------------------------------------------------------------
# 版本解析
# ---------------------------------------------------------------------------

def resolve_version(chain: list, as_of: date, cutoff: datetime):
    """在链条上解析“知识时点 cutoff 时，关于业务日期 as_of 的最新断言”。

    返回命中的版本行或 None。命中行的 status 决定有效性。
    """
    candidates = [
        v for v in chain
        if v.recorded_at <= cutoff
        and (v.superseded_at is None or v.superseded_at > cutoff)
        and v.effective_from <= as_of
        and (v.effective_to is None or v.effective_to >= as_of)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda v: (v.recorded_at, v.id))


def _evidence_leaf(
    version,
    treatment_date: date,
    event_created_at: Optional[datetime],
    missing_detail: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """生成单侧单项证据快照，含补录标记。"""
    if version is None:
        leaf = {
            "found": False,
            "valid": False,
            "version_id": None,
            "status": None,
            "effective_from": None,
            "effective_to": None,
            "recorded_at": None,
            "retroactive_entry": False,
            "entered_after_record": False,
            "detail": missing_detail,
        }
        if extra:
            leaf.update(extra)
        return leaf

    valid = version.status == VersionStatus.ACTIVE
    retroactive = version.recorded_at.date() > treatment_date
    entered_after = bool(event_created_at) and version.recorded_at > event_created_at

    detail = (
        f"版本#{version.id}（{version.change_kind.value}，状态：{version.status.value}，"
        f"有效期 {version.effective_from} 至 {version.effective_to or '长期'}，"
        f"录入于 {version.recorded_at.strftime('%Y-%m-%d %H:%M:%S')}）"
    )
    if not valid:
        detail = f"治疗日处于「{version.status.value}」状态：" + detail
    if retroactive:
        detail += "；该证据为补录（录入日期晚于治疗日）"
    elif entered_after:
        detail += "；该证据录入晚于诊疗记录登记时间"

    leaf = {
        "found": True,
        "valid": valid,
        "version_id": version.id,
        "status": version.status.value,
        "change_kind": version.change_kind.value,
        "effective_from": version.effective_from.isoformat(),
        "effective_to": version.effective_to.isoformat() if version.effective_to else None,
        "recorded_at": version.recorded_at.isoformat(),
        "retroactive_entry": retroactive,
        "entered_after_record": entered_after,
        "detail": detail,
    }
    if extra:
        leaf.update(extra)
    return leaf


# ---------------------------------------------------------------------------
# 双主体检查
# ---------------------------------------------------------------------------

def check_institution_side(
    db: Session, event: TreatmentEvent, cutoff: datetime
) -> Tuple[Dict[str, Any], List[str], Optional[int], Optional[int]]:
    d = event.treatment_date
    issues: List[str] = []

    lic_chain = db.query(InstitutionLicenseVersion).filter(
        InstitutionLicenseVersion.institution_id == event.institution_id
    ).all()
    lic = resolve_version(lic_chain, d, cutoff)
    lic_leaf = _evidence_leaf(
        lic, d, event.created_at,
        "机构侧：治疗日无已知有效的机构执业许可证版本",
        extra={"license_number": lic.license_number if lic else None},
    )
    if lic is None:
        issues.append("机构侧：治疗日无已知有效的机构执业许可证版本")
    elif lic.status != VersionStatus.ACTIVE:
        issues.append(
            f"机构侧：治疗日机构执业许可证处于「{lic.status.value}」状态（版本#{lic.id}）"
        )

    auth_chain = db.query(InstitutionProcedureAuthVersion).filter(
        InstitutionProcedureAuthVersion.institution_id == event.institution_id,
        InstitutionProcedureAuthVersion.procedure_id == event.procedure_id,
    ).all()
    auth = resolve_version(auth_chain, d, cutoff)
    auth_leaf = _evidence_leaf(
        auth, d, event.created_at,
        "机构侧：治疗日无已知有效的机构项目授权版本",
    )
    if auth is None:
        issues.append("机构侧：治疗日无已知有效的机构项目授权版本")
    elif auth.status != VersionStatus.ACTIVE:
        issues.append(
            f"机构侧：治疗日机构项目授权处于「{auth.status.value}」状态（版本#{auth.id}）"
        )

    institution = db.query(Institution).filter(Institution.id == event.institution_id).first()
    side = {
        "institution_id": event.institution_id,
        "institution_name": institution.name if institution else f"机构#{event.institution_id}",
        "license": lic_leaf,
        "procedure_authorization": auth_leaf,
    }
    return side, issues, lic.id if lic else None, auth.id if auth else None


def check_practitioner_side(
    db: Session, event: TreatmentEvent, participant: TreatmentParticipant, cutoff: datetime
) -> Tuple[Dict[str, Any], List[str], List[int], Optional[int]]:
    d = event.treatment_date
    issues: List[str] = []

    practitioner = db.query(Practitioner).filter(
        Practitioner.id == participant.practitioner_id
    ).first()
    name = practitioner.name if practitioner else f"人员#{participant.practitioner_id}"

    cross = participant.registered_institution_id != event.institution_id

    qual_leaves: Dict[str, Any] = {}
    qual_version_ids: List[int] = []
    for qt in CORE_QUAL_TYPES:
        chain = db.query(PractitionerQualificationVersion).filter(
            PractitionerQualificationVersion.practitioner_id == participant.practitioner_id,
            PractitionerQualificationVersion.qualification_type == qt,
        ).all()
        picked = resolve_version(chain, d, cutoff)
        leaf = _evidence_leaf(
            picked, d, event.created_at,
            f"人员侧（{name}）：治疗日无已知有效的{qt.value}版本",
            extra={
                "certificate_number": picked.certificate_number if picked else None,
                "practice_scope": picked.practice_scope if picked else None,
            },
        )
        qual_leaves[qt.value] = leaf
        if picked is None:
            issues.append(f"人员侧（{name}）：治疗日无已知有效的{qt.value}版本")
        else:
            qual_version_ids.append(picked.id)
            if picked.status != VersionStatus.ACTIVE:
                issues.append(
                    f"人员侧（{name}）：治疗日{qt.value}处于「{picked.status.value}」状态"
                    f"（版本#{picked.id}）"
                )

    auth_chain = db.query(PractitionerProcedureAuthVersion).filter(
        PractitionerProcedureAuthVersion.practitioner_id == participant.practitioner_id,
        PractitionerProcedureAuthVersion.procedure_id == event.procedure_id,
    ).all()
    if cross:
        scoped_chain = [v for v in auth_chain if v.institution_id == event.institution_id]
    else:
        scoped_chain = [
            v for v in auth_chain
            if v.institution_id is None or v.institution_id == event.institution_id
        ]
    auth = resolve_version(scoped_chain, d, cutoff)
    auth_leaf = _evidence_leaf(
        auth, d, event.created_at,
        f"人员侧（{name}）：治疗日无已知有效的人员项目授权版本",
        extra={"auth_institution_id": auth.institution_id if auth else None},
    )
    if auth is None:
        if cross:
            issues.append(
                f"人员侧（{name}）：跨机构执业，治疗日无覆盖治疗机构的人员项目授权版本"
                f"（需限定到治疗机构的授权/多点执业备案）"
            )
        else:
            issues.append(f"人员侧（{name}）：治疗日无已知有效的人员项目授权版本")
    elif auth.status != VersionStatus.ACTIVE:
        issues.append(
            f"人员侧（{name}）：治疗日人员项目授权处于「{auth.status.value}」状态"
            f"（版本#{auth.id}）"
        )

    side = {
        "participant_id": participant.id,
        "practitioner_id": participant.practitioner_id,
        "practitioner_name": name,
        "role": participant.role,
        "registered_institution_id": participant.registered_institution_id,
        "cross_institution": cross,
        "qualifications": qual_leaves,
        "procedure_authorization": auth_leaf,
    }
    return side, issues, qual_version_ids, auth.id if auth else None


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def _current_determination(db: Session, event_id: int, participant_id: Optional[int]):
    query = db.query(ComplianceDetermination).filter(
        ComplianceDetermination.event_id == event_id,
        ComplianceDetermination.is_current == True,
    )
    if participant_id is None:
        query = query.filter(ComplianceDetermination.participant_id.is_(None))
    else:
        query = query.filter(ComplianceDetermination.participant_id == participant_id)
    return query.first()


def _latest_seq(db: Session, event_id: int, participant_id: Optional[int]) -> int:
    query = db.query(ComplianceDetermination).filter(
        ComplianceDetermination.event_id == event_id
    )
    if participant_id is None:
        query = query.filter(ComplianceDetermination.participant_id.is_(None))
    else:
        query = query.filter(ComplianceDetermination.participant_id == participant_id)
    rows = query.all()
    return max((r.seq for r in rows), default=0)


def _binding_key(result, lic_id, inst_auth_id, qual_ids, prac_auth_id, issues):
    return (
        result,
        lic_id,
        inst_auth_id,
        tuple(sorted(qual_ids)),
        prac_auth_id,
        tuple(issues),
    )


def _row_binding_key(row: ComplianceDetermination):
    return _binding_key(
        row.result,
        row.license_version_id,
        row.inst_auth_version_id,
        tuple(json.loads(row.qual_version_ids or "[]")),
        row.prac_auth_version_id,
        tuple(json.loads(row.issues or "[]")),
    )


def _append_determination(
    db: Session,
    event: TreatmentEvent,
    participant_id: Optional[int],
    result: DeterminationResult,
    trigger: DeterminationTrigger,
    trigger_detail: str,
    cutoff: datetime,
    lic_id: Optional[int],
    inst_auth_id: Optional[int],
    qual_ids: List[int],
    prac_auth_id: Optional[int],
    evidence: Dict[str, Any],
    issues: List[str],
    material_only: bool,
) -> Tuple[Optional[ComplianceDetermination], bool]:
    """追加一条判定。material_only=True 时若绑定与结论未变化则跳过。

    返回 (新行或 None, 是否追加了行)。
    """
    new_key = _binding_key(result, lic_id, inst_auth_id, qual_ids, prac_auth_id, issues)
    prev = _current_determination(db, event.id, participant_id)
    if material_only and prev is not None and _row_binding_key(prev) == new_key:
        return None, False

    if prev is not None:
        prev.is_current = False
        db.add(prev)

    row = ComplianceDetermination(
        event_id=event.id,
        participant_id=participant_id,
        seq=_latest_seq(db, event.id, participant_id) + 1,
        determined_at=cutoff,
        as_of_date=event.treatment_date,
        knowledge_cutoff=cutoff,
        result=result,
        trigger=trigger,
        trigger_detail=trigger_detail,
        license_version_id=lic_id,
        inst_auth_version_id=inst_auth_id,
        prac_auth_version_id=prac_auth_id,
        qual_version_ids=json.dumps(sorted(qual_ids)),
        evidence=json.dumps(evidence, ensure_ascii=False),
        issues=json.dumps(issues, ensure_ascii=False),
        is_current=True,
        supersedes_id=prev.id if prev else None,
    )
    db.add(row)
    db.flush()
    return row, True


def determine_event(
    db: Session,
    event: TreatmentEvent,
    trigger: DeterminationTrigger,
    trigger_detail: str = "",
    cutoff: Optional[datetime] = None,
    material_only: bool = False,
) -> Dict[str, Any]:
    """对事件全体参与人员及事件级做判定并追加判定行。

    返回 {"appended": 追加行数, "event_row": 事件级新行或None,
          "flipped": 事件级结论是否翻转}。
    """
    cutoff = cutoff or datetime.utcnow()

    inst_side, inst_issues, lic_id, inst_auth_id = check_institution_side(db, event, cutoff)

    appended = 0
    participant_summaries = []
    all_ok = len(inst_issues) == 0

    for participant in event.participants:
        prac_side, prac_issues, qual_ids, prac_auth_id = check_practitioner_side(
            db, event, participant, cutoff
        )
        issues = inst_issues + prac_issues
        result = DeterminationResult.VIOLATION if issues else DeterminationResult.COMPLIANT
        evidence = {
            "treatment_date": event.treatment_date.isoformat(),
            "knowledge_cutoff": cutoff.isoformat(),
            "institution_side": inst_side,
            "practitioner_side": prac_side,
        }
        row, did = _append_determination(
            db, event, participant.id, result, trigger, trigger_detail, cutoff,
            lic_id, inst_auth_id, qual_ids, prac_auth_id, evidence, issues,
            material_only,
        )
        if did:
            appended += 1
        all_ok = all_ok and result == DeterminationResult.COMPLIANT
        participant_summaries.append({
            "participant_id": participant.id,
            "practitioner_name": prac_side["practitioner_name"],
            "result": result.value,
            "issues": issues,
        })

    event_issues = []
    for s in participant_summaries:
        event_issues.extend(s["issues"])
    event_result = (
        DeterminationResult.COMPLIANT if all_ok else DeterminationResult.VIOLATION
    )
    event_evidence = {
        "treatment_date": event.treatment_date.isoformat(),
        "knowledge_cutoff": cutoff.isoformat(),
        "institution_side": inst_side,
        "participants": participant_summaries,
    }
    prev_event_row = _current_determination(db, event.id, None)
    event_row, did = _append_determination(
        db, event, None, event_result, trigger, trigger_detail, cutoff,
        lic_id, inst_auth_id, [], None, event_evidence, event_issues,
        material_only,
    )
    if did:
        appended += 1

    flipped = bool(
        event_row is not None
        and prev_event_row is not None
        and prev_event_row.result != event_row.result
    )

    current_row = event_row or prev_event_row
    if current_row is not None:
        event.current_result = current_row.result
        db.add(event)

    if event_row is not None:
        _handle_disposition_and_clues(db, event, event_row, prev_event_row)

    return {"appended": appended, "event_row": event_row, "flipped": flipped}


# ---------------------------------------------------------------------------
# 线索联动与处置影响
# ---------------------------------------------------------------------------

def _linked_clues(db: Session, event_id: int) -> List[ViolationClue]:
    links = db.query(ClueEventLink).filter(ClueEventLink.event_id == event_id).all()
    clue_ids = [l.clue_id for l in links]
    if not clue_ids:
        return []
    return db.query(ViolationClue).filter(ViolationClue.id.in_(clue_ids)).all()


def _handle_disposition_and_clues(
    db: Session,
    event: TreatmentEvent,
    new_row: ComplianceDetermination,
    prev_row: Optional[ComplianceDetermination],
) -> None:
    clues = _linked_clues(db, event.id)
    concluded = [c for c in clues if c.status in (ClueStatus.VERIFIED, ClueStatus.DISMISSED)]

    if new_row.result == DeterminationResult.VIOLATION:
        actionable = [
            c for c in clues
            if c.status in (ClueStatus.PENDING, ClueStatus.ASSIGNED, ClueStatus.VERIFIED)
        ]
        if not actionable:
            clue = _create_event_clue(db, event, new_row)
            db.add(ClueEventLink(clue_id=clue.id, event_id=event.id))
            db.flush()
        for c in concluded:
            if c.status == ClueStatus.DISMISSED:
                _add_impact_notice(
                    db, c, event, new_row,
                    f"线索#{c.id}已作「已排除」处置，但后续重新判定（判定#{new_row.id}，"
                    f"触发：{new_row.trigger.value}）结论为违规，原排除处置需复核。",
                )
    else:
        for c in concluded:
            if c.status == ClueStatus.VERIFIED:
                _add_impact_notice(
                    db, c, event, new_row,
                    f"线索#{c.id}已作「已核实违规」处置，但后续重新判定（判定#{new_row.id}，"
                    f"触发：{new_row.trigger.value}）结论为合规，原处置可能受影响，需复核。",
                )


def _add_impact_notice(
    db: Session,
    clue: ViolationClue,
    event: TreatmentEvent,
    determination: ComplianceDetermination,
    detail: str,
) -> None:
    existing = db.query(DispositionImpactNotice).filter(
        DispositionImpactNotice.clue_id == clue.id,
        DispositionImpactNotice.determination_id == determination.id,
    ).first()
    if existing:
        return
    db.add(DispositionImpactNotice(
        clue_id=clue.id,
        event_id=event.id,
        determination_id=determination.id,
        impact_detail=detail,
    ))
    db.flush()


def _create_event_clue(
    db: Session, event: TreatmentEvent, row: ComplianceDetermination
) -> ViolationClue:
    institution = db.query(Institution).filter(Institution.id == event.institution_id).first()
    procedure = db.query(Procedure).filter(Procedure.id == event.procedure_id).first()
    primary = next((p for p in event.participants if p.is_primary), None)
    primary = primary or (event.participants[0] if event.participants else None)
    practitioner = None
    if primary is not None:
        practitioner = db.query(Practitioner).filter(
            Practitioner.id == primary.practitioner_id
        ).first()

    inst_name = institution.name if institution else f"机构#{event.institution_id}"
    proc_name = procedure.name if procedure else f"项目#{event.procedure_id}"
    prac_name = practitioner.name if practitioner else "多名人员"
    issues = json.loads(row.issues or "[]")

    clue = ViolationClue(
        clue_type=ClueType.OVER_RANGE_PRACTICE,
        title=f"超范围执业：{inst_name} - {prac_name} 开展 {proc_name}",
        description=(
            f"发生时判定引擎判定诊疗事件 {event.event_code}（治疗日 {event.treatment_date}）"
            f"存在违规。\n具体问题：{'；'.join(issues)}\n"
            f"关联判定ID：{row.id}（触发：{row.trigger.value}）"
        ),
        institution_id=event.institution_id,
        practitioner_id=practitioner.id if practitioner else None,
        procedure_id=event.procedure_id,
        source="发生时判定引擎",
        priority=CluePriority.HIGH,
    )
    db.add(clue)
    db.flush()
    return clue


# ---------------------------------------------------------------------------
# 版本登记与重新判定触发
# ---------------------------------------------------------------------------

def register_version(
    db: Session,
    table: Type,
    chain_filter: Dict[str, Any],
    values: Dict[str, Any],
    change_kind: VersionChangeKind,
    reason: Optional[str] = None,
    recorded_at: Optional[datetime] = None,
) -> Tuple[Any, RedeterminationRun]:
    """登记一个证据版本行，并对受影响事件做追加式重新判定。

    - 更正/变更/续期/初始类：关闭链上所有未关闭行（superseded_at），新行替代。
    - 暂停/注销/恢复类：叠加行，不关闭旧行，由覆盖解析规则决定有效性。
    """
    now = datetime.utcnow()
    recorded_at = recorded_at or now
    status = STATUS_BY_KIND[change_kind]

    closed: list = []
    if change_kind in SUPERSEDING_KINDS:
        closed = db.query(table).filter(
            *[getattr(table, k) == v for k, v in chain_filter.items()],
            table.superseded_at.is_(None),
        ).all()
        for head in closed:
            head.superseded_at = recorded_at
            db.add(head)

    supersedes_id = max((h.id for h in closed), default=None)
    row = table(
        **chain_filter,
        **values,
        status=status,
        change_kind=change_kind,
        recorded_at=recorded_at,
        supersedes_id=supersedes_id,
        reason=reason,
    )
    db.add(row)
    db.flush()

    trigger = TRIGGER_BY_TABLE[table.__tablename__]
    trigger_detail = (
        f"{table.__tablename__} 版本#{row.id}（{change_kind.value}，"
        f"有效期 {row.effective_from} 至 {row.effective_to or '长期'}）"
    )
    run = _redetermine_affected(
        db, table, row, closed, trigger, trigger_detail, cutoff=now
    )
    return row, run


def _affected_events(db: Session, table: Type, row) -> List[TreatmentEvent]:
    if table in (InstitutionLicenseVersion, InstitutionProcedureAuthVersion):
        return db.query(TreatmentEvent).filter(
            TreatmentEvent.institution_id == row.institution_id
        ).all()
    participant_event_ids = (
        db.query(TreatmentParticipant.event_id)
        .filter(TreatmentParticipant.practitioner_id == row.practitioner_id)
        .all()
    )
    ids = [eid for (eid,) in participant_event_ids]
    if not ids:
        return []
    return db.query(TreatmentEvent).filter(TreatmentEvent.id.in_(ids)).all()


def _event_bound_version_ids(db: Session, event_id: int) -> set:
    rows = db.query(ComplianceDetermination).filter(
        ComplianceDetermination.event_id == event_id,
        ComplianceDetermination.is_current == True,
    ).all()
    ids = set()
    for r in rows:
        for vid in (r.license_version_id, r.inst_auth_version_id, r.prac_auth_version_id):
            if vid is not None:
                ids.add(vid)
        for vid in json.loads(r.qual_version_ids or "[]"):
            ids.add(vid)
    return ids


def _redetermine_affected(
    db: Session,
    table: Type,
    new_row,
    closed_rows: list,
    trigger: DeterminationTrigger,
    trigger_detail: str,
    cutoff: datetime,
) -> RedeterminationRun:
    chain_rows = list(closed_rows) + [new_row]
    chain_ids = {r.id for r in chain_rows}
    events = _affected_events(db, table, new_row)

    scanned = 0
    redetermined = 0
    flipped = 0
    for event in events:
        in_window = any(
            r.effective_from <= event.treatment_date
            and (r.effective_to is None or event.treatment_date <= r.effective_to)
            for r in chain_rows
        )
        bound = bool(_event_bound_version_ids(db, event.id) & chain_ids)
        if not (in_window or bound):
            continue
        scanned += 1
        outcome = determine_event(
            db, event, trigger, trigger_detail, cutoff=cutoff, material_only=True
        )
        if outcome["appended"] > 0:
            redetermined += 1
        if outcome["flipped"]:
            flipped += 1

    run = RedeterminationRun(
        trigger=trigger,
        source_table=new_row.__tablename__,
        source_version_id=new_row.id,
        events_scanned=scanned,
        events_redetermined=redetermined,
        results_flipped=flipped,
        detail=trigger_detail,
    )
    db.add(run)
    db.flush()
    return run


# ---------------------------------------------------------------------------
# 事件创建
# ---------------------------------------------------------------------------

def create_treatment_event(
    db: Session,
    institution_id: int,
    procedure_id: int,
    treatment_date: date,
    participants: List[Dict[str, Any]],
    remark: Optional[str] = None,
    event_code: Optional[str] = None,
) -> TreatmentEvent:
    import uuid

    now = datetime.utcnow()
    event = TreatmentEvent(
        event_code=event_code or f"EV-{uuid.uuid4().hex[:12]}",
        institution_id=institution_id,
        procedure_id=procedure_id,
        treatment_date=treatment_date,
        remark=remark,
        created_at=now,
    )
    db.add(event)
    db.flush()

    for p in participants:
        practitioner = db.query(Practitioner).filter(
            Practitioner.id == p["practitioner_id"]
        ).first()
        if practitioner is None:
            raise ValueError(f"人员#{p['practitioner_id']}不存在")
        db.add(TreatmentParticipant(
            event_id=event.id,
            practitioner_id=practitioner.id,
            role=p.get("role") or "主诊医师",
            is_primary=bool(p.get("is_primary")),
            registered_institution_id=practitioner.institution_id,
        ))
    db.flush()
    db.refresh(event)

    determine_event(
        db, event, DeterminationTrigger.INITIAL_RECORD,
        f"诊疗事件 {event.event_code} 登记", cutoff=now,
    )
    return event
