from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import date

from ..database import get_db
from ..models import (
    Institution, InstitutionLicense, Practitioner, PractitionerQualification,
    QualificationType, InstitutionAuthorizedProcedure,
    PractitionerAuthorizedProcedure, Procedure, ActualProcedureRecord,
    ViolationClue, ClueStatus,
    EvidenceChangeType, RejudgmentReason, SnapshotConclusion,
    ComplianceJudgment, JudgmentEvidenceItem, ProcedureRecordParticipant,
)
from .. import schemas
from .. import evidence_audit as ea
from ..compliance_utils import (
    check_institution_license_valid,
    check_practitioner_qualification_valid,
    check_practitioner_core_qualifications,
    count_unlicensed_practitioners,
    create_over_range_clue,
    recalculate_all_over_range_records
)

router = APIRouter()


@router.get("/institution/{institution_id}", response_model=schemas.InstitutionComplianceCheck)
def check_institution_compliance(institution_id: int, db: Session = Depends(get_db)):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")

    has_valid_license, license_detail, _ = check_institution_license_valid(db, institution_id)

    authorized_count = db.query(InstitutionAuthorizedProcedure).filter(
        InstitutionAuthorizedProcedure.institution_id == institution_id
    ).count()

    actual_records = db.query(ActualProcedureRecord).filter(
        ActualProcedureRecord.institution_id == institution_id
    ).all()

    over_range_count = sum(1 for r in actual_records if r.is_over_range)

    unlicensed_count, total_practitioners = count_unlicensed_practitioners(db, institution_id)

    clues_count = db.query(ViolationClue).filter(
        ViolationClue.institution_id == institution_id
    ).count()

    verified_count = db.query(ViolationClue).filter(
        ViolationClue.institution_id == institution_id,
        ViolationClue.status == ClueStatus.VERIFIED
    ).count()

    return schemas.InstitutionComplianceCheck(
        institution_id=institution.id,
        institution_name=institution.name,
        has_valid_license=has_valid_license,
        license_detail=license_detail,
        authorized_procedure_count=authorized_count,
        actual_procedure_count=len(actual_records),
        over_range_count=over_range_count,
        unlicensed_practitioners=unlicensed_count,
        total_practitioners=total_practitioners,
        clues_count=clues_count,
        verified_violations=verified_count
    )


@router.get("/practitioner/{practitioner_id}", response_model=schemas.PractitionerComplianceCheck)
def check_practitioner_compliance(practitioner_id: int, db: Session = Depends(get_db)):
    practitioner = db.query(Practitioner).filter(Practitioner.id == practitioner_id).first()
    if not practitioner:
        raise HTTPException(status_code=404, detail="人员不存在")

    has_doctor, _ = check_practitioner_qualification_valid(
        db, practitioner_id, QualificationType.DOCTOR
    )
    has_practice, _ = check_practitioner_qualification_valid(
        db, practitioner_id, QualificationType.PRACTICE
    )
    has_cosmetology, _ = check_practitioner_qualification_valid(
        db, practitioner_id, QualificationType.COSMETOLOGY
    )

    authorized_procs = db.query(PractitionerAuthorizedProcedure).filter(
        PractitionerAuthorizedProcedure.practitioner_id == practitioner_id
    ).all()
    authorized_names = [ap.procedure.name for ap in authorized_procs if ap.procedure]

    actual_records = db.query(ActualProcedureRecord).filter(
        ActualProcedureRecord.practitioner_id == practitioner_id
    ).all()
    actual_names = list(set([r.procedure.name for r in actual_records if r.procedure]))

    over_range_names = list(set([
        r.procedure.name for r in actual_records
        if r.is_over_range and r.procedure
    ]))

    core_issues = check_practitioner_core_qualifications(db, practitioner_id)
    is_unlicensed = len(core_issues) > 0

    return schemas.PractitionerComplianceCheck(
        practitioner_id=practitioner.id,
        name=practitioner.name,
        has_valid_doctor_license=has_doctor,
        has_valid_practice_license=has_practice,
        has_cosmetology_license=has_cosmetology,
        authorized_procedures=authorized_names,
        actual_procedures=actual_names,
        over_range_procedures=over_range_names,
        is_unlicensed=is_unlicensed
    )


@router.post("/actual-procedure", response_model=schemas.ActualProcedureRecord)
def record_actual_procedure(
    record_data: schemas.ActualProcedureRecordCreate,
    db: Session = Depends(get_db)
):
    institution = db.query(Institution).filter(
        Institution.id == record_data.institution_id
    ).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")

    practitioner = db.query(Practitioner).filter(
        Practitioner.id == record_data.practitioner_id
    ).first()
    if not practitioner:
        raise HTTPException(status_code=404, detail="人员不存在")

    procedure = db.query(Procedure).filter(
        Procedure.id == record_data.procedure_id
    ).first()
    if not procedure:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 先记录后补证亦允许登记：时点引擎按治疗日期选版，缺失证据会在初始
    # 判定快照中标为“治疗时缺失”，事后补证再触发追加式重新判定。
    db_record = ActualProcedureRecord(
        institution_id=record_data.institution_id,
        practitioner_id=record_data.practitioner_id,
        procedure_id=record_data.procedure_id,
        procedure_date=record_data.procedure_date,
        patient_count=record_data.patient_count,
        remark=record_data.remark,
    )
    db.add(db_record)
    db.flush()

    extra_participants = getattr(record_data, "participants", None)
    judgment = ea.create_judgment(
        db, db_record, RejudgmentReason.INITIAL,
        trigger_remark="项目记录登记时的初次判定",
        extra_participants=extra_participants,
    )

    is_over_range = judgment.conclusion == SnapshotConclusion.VIOLATION
    over_range_issues = [
        item.detail for item in judgment.evidence_items if item.status.value != "当时有效"
    ]
    db.commit()
    db.refresh(db_record)

    if is_over_range:
        try:
            create_over_range_clue(
                db,
                record_data.institution_id,
                record_data.practitioner_id,
                record_data.procedure_id,
                db_record.id,
                over_range_issues
            )
            db.commit()
        except Exception:
            db.rollback()

    return db_record


@router.get("/actual-procedures", response_model=List[schemas.ActualProcedureRecord])
def list_actual_procedures(
    institution_id: int = None,
    only_over_range: bool = False,
    db: Session = Depends(get_db)
):
    query = db.query(ActualProcedureRecord)
    if institution_id:
        query = query.filter(ActualProcedureRecord.institution_id == institution_id)
    if only_over_range:
        query = query.filter(ActualProcedureRecord.is_over_range == True)
    return query.all()


@router.get("/institution/{institution_id}/over-range", response_model=List[schemas.ActualProcedureRecord])
def list_institution_over_range(institution_id: int, db: Session = Depends(get_db)):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    return db.query(ActualProcedureRecord).filter(
        ActualProcedureRecord.institution_id == institution_id,
        ActualProcedureRecord.is_over_range == True
    ).all()


@router.post("/recalculate", tags=["超范围执业重算"])
def recalculate_over_range(db: Session = Depends(get_db)):
    result = recalculate_all_over_range_records(db)
    return {
        "message": "超范围执业判定重算完成",
        "detail": result
    }


@router.get(
    "/check/single",
    response_model=schemas.SingleCheckEvidence,
    tags=["超范围执业校验"]
)
def check_single_over_range(
    institution_id: int,
    practitioner_id: int,
    procedure_id: int,
    procedure_date: date = None,
    db: Session = Depends(get_db)
):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    practitioner = db.query(Practitioner).filter(Practitioner.id == practitioner_id).first()
    if not practitioner:
        raise HTTPException(status_code=404, detail="人员不存在")
    procedure = db.query(Procedure).filter(Procedure.id == procedure_id).first()
    if not procedure:
        raise HTTPException(status_code=404, detail="项目不存在")

    check_date = procedure_date or date.today()
    evaluation = ea.evaluate_at(
        db, institution_id, procedure_id,
        [(practitioner_id, "主操作人")], check_date
    )
    return _serialize_single_check(
        institution, practitioner, procedure, check_date, evaluation
    )


# ---------------------------------------------------------------------------
# 时点证据审计
# ---------------------------------------------------------------------------

_CHANGE_TYPE_MAP = {
    "暂停": EvidenceChangeType.SUSPEND,
    "恢复": EvidenceChangeType.RESTORE,
    "变更": EvidenceChangeType.CHANGE,
    "追溯更正": EvidenceChangeType.RETROACTIVE_CORRECTION,
    "事后补录": EvidenceChangeType.BACKFILL,
}


def _serialize_evidence_item(row: JudgmentEvidenceItem) -> dict:
    backfilled = None
    if row.recorded_at is not None and row.judgment and row.judgment.record:
        backfilled = row.recorded_at.date() > row.judgment.record.procedure_date
    return {
        "side": row.side,
        "dimension": row.dimension,
        "item_key": row.item_key,
        "status": row.status.value,
        "version_id": row.version_id,
        "version_no": row.version_no,
        "change_type": row.change_type.value if row.change_type else None,
        "valid_from": row.valid_from,
        "valid_until": row.valid_until,
        "recorded_at": row.recorded_at,
        "backfilled_after_event": backfilled,
        "detail": row.detail,
    }


def _serialize_judgment(db: Session, judgment: ComplianceJudgment) -> dict:
    inst_rows = [r for r in judgment.evidence_items if r.side == "institution"]

    participant_payload = []
    bindings = db.query(ProcedureRecordParticipant).filter(
        ProcedureRecordParticipant.record_id == judgment.record_id
    ).all()
    prac_cache = {}
    for b in bindings:
        prac = prac_cache.setdefault(
            b.practitioner_id,
            db.query(Practitioner).filter(Practitioner.id == b.practitioner_id).first()
        )
        rows = [
            r for r in judgment.evidence_items
            if r.side == "practitioner" and r.participant_id == b.id
        ]
        participant_payload.append({
            "participant_id": b.id,
            "practitioner_id": b.practitioner_id,
            "practitioner_name": prac.name if prac else f"人员#{b.practitioner_id}",
            "role": b.role,
            "is_primary": b.is_primary,
            "evidence": [_serialize_evidence_item(r) for r in rows],
        })

    return {
        "sequence_no": judgment.sequence_no,
        "concluded_at": judgment.concluded_at,
        "conclusion": judgment.conclusion.value,
        "reason": judgment.reason.value,
        "trigger_remark": judgment.trigger_remark,
        "changed_from_previous": judgment.changed_from_previous,
        "disposition_impact": judgment.disposition_impact,
        "evidence_summary": judgment.evidence_summary,
        "institution_evidence": [_serialize_evidence_item(r) for r in inst_rows],
        "participants": participant_payload,
    }


def _serialize_single_check(institution, practitioner, procedure, check_date, evaluation) -> dict:
    participant_payload = []
    for pid, items in evaluation["participant_items"].items():
        participant_payload.append({
            "participant_id": None,
            "practitioner_id": pid,
            "practitioner_name": practitioner.name if pid == practitioner.id else f"人员#{pid}",
            "role": "主操作人",
            "is_primary": True,
            "evidence": [
                _serialize_evidence_item_from_eval(it, check_date) for it in items
            ],
        })
    return {
        "institution_name": institution.name,
        "practitioner_name": practitioner.name,
        "procedure_name": procedure.name,
        "procedure_date": check_date,
        "is_over_range": evaluation["conclusion"].value == "当时违规",
        "conclusion": evaluation["conclusion"].value,
        "issues": evaluation["issues"],
        "institution_evidence": [
            _serialize_evidence_item_from_eval(it, check_date)
            for it in evaluation["institution_items"]
        ],
        "participants": participant_payload,
    }


def _serialize_evidence_item_from_eval(item: dict, check_date: date) -> dict:
    v = item["version"]
    backfilled = bool(v and v.recorded_at and v.recorded_at.date() > check_date)
    return {
        "side": item["side"],
        "dimension": item["dimension"],
        "item_key": item["item_key"],
        "status": item["status"].value,
        "version_id": v.id if v else None,
        "version_no": v.version_no if v else None,
        "change_type": v.change_type.value if v else None,
        "valid_from": v.valid_from if v else None,
        "valid_until": v.valid_until if v else None,
        "recorded_at": v.recorded_at if v else None,
        "backfilled_after_event": backfilled,
        "detail": item["detail"],
    }


@router.get(
    "/actual-procedures/{record_id}/audit",
    response_model=schemas.RecordAuditOut,
    tags=["时点证据审计"]
)
def get_record_audit_trail(record_id: int, db: Session = Depends(get_db)):
    record = db.query(ActualProcedureRecord).filter(
        ActualProcedureRecord.id == record_id
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="项目记录不存在")

    judgments = db.query(ComplianceJudgment).filter(
        ComplianceJudgment.record_id == record_id
    ).order_by(ComplianceJudgment.sequence_no.asc()).all()

    latest = judgments[-1] if judgments else None
    return {
        "record_id": record.id,
        "institution_id": record.institution_id,
        "institution_name": record.institution.name if record.institution else f"机构#{record.institution_id}",
        "practitioner_id": record.practitioner_id,
        "practitioner_name": record.practitioner.name if record.practitioner else f"人员#{record.practitioner_id}",
        "procedure_id": record.procedure_id,
        "procedure_name": record.procedure.name if record.procedure else f"项目#{record.procedure_id}",
        "procedure_date": record.procedure_date,
        "current_is_over_range": record.is_over_range,
        "current_detail": record.over_range_detail,
        "latest_conclusion": latest.conclusion.value if latest else None,
        "judgment_count": len(judgments),
        "judgments": [_serialize_judgment(db, j) for j in judgments],
    }


@router.post(
    "/actual-procedures/{record_id}/participants",
    response_model=schemas.RecordAuditOut,
    tags=["时点证据审计"]
)
def add_record_participant(
    record_id: int,
    payload: schemas.ParticipantAddRequest,
    db: Session = Depends(get_db)
):
    record = db.query(ActualProcedureRecord).filter(
        ActualProcedureRecord.id == record_id
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="项目记录不存在")
    prac = db.query(Practitioner).filter(
        Practitioner.id == payload.practitioner_id
    ).first()
    if not prac:
        raise HTTPException(status_code=404, detail="人员不存在")

    exists = db.query(ProcedureRecordParticipant).filter(
        ProcedureRecordParticipant.record_id == record_id,
        ProcedureRecordParticipant.practitioner_id == payload.practitioner_id,
    ).first()
    if not exists:
        db.add(ProcedureRecordParticipant(
            record_id=record_id,
            practitioner_id=payload.practitioner_id,
            role=payload.role or "参与人",
            is_primary=False,
        ))
        db.flush()
        # 增加参与人导致证据面变化：追加一次重新判定，原判定保留
        ea.create_judgment(
            db, record, RejudgmentReason.MANUAL,
            trigger_remark=f"追加参与人：{prac.name}（{payload.role}）"
        )
        db.commit()
    return get_record_audit_trail(record_id, db)


def _parse_change_type(raw: str) -> EvidenceChangeType:
    if raw in _CHANGE_TYPE_MAP:
        return _CHANGE_TYPE_MAP[raw]
    try:
        return EvidenceChangeType(raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="change_type 必须为：暂停/恢复/变更/追溯更正/事后补录"
        )


def _apply_evidence_change(
    db: Session,
    dimension: str,
    register_fn,
    target_id: int,
    payload: schemas.EvidenceChangeRequest,
) -> schemas.EvidenceChangeResult:
    change_type = _parse_change_type(payload.change_type)
    try:
        version = register_fn(
            db,
            target_id,
            change_type,
            effective_date=payload.effective_date,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            is_active=payload.is_active,
            remark=payload.remark,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    ctx = _change_context(db, dimension, target_id)
    judgments = ea.rejudge_after_evidence_change(
        db,
        dimension=dimension,
        change_type=change_type,
        institution_id=ctx.get("institution_id"),
        practitioner_id=ctx.get("practitioner_id"),
        procedure_id=ctx.get("procedure_id"),
        valid_from=payload.valid_from or payload.effective_date,
        valid_until=payload.valid_until,
        trigger_remark=payload.remark,
    )

    affected = [j.record_id for j in judgments]
    changed = sum(1 for j in judgments if j.changed_from_previous)
    db.commit()
    return schemas.EvidenceChangeResult(
        dimension=dimension,
        version_id=version.id,
        version_no=version.version_no,
        change_type=change_type.value,
        valid_from=version.valid_from,
        valid_until=version.valid_until,
        rejudged_records=len(affected),
        changed_conclusions=changed,
        affected_record_ids=affected,
    )


def _change_context(db: Session, dimension: str, target_id: int) -> dict:
    if dimension == "license":
        lic = db.query(InstitutionLicense).filter(InstitutionLicense.id == target_id).first()
        return {"institution_id": lic.institution_id if lic else None}
    if dimension == "institution_auth":
        auth = db.query(InstitutionAuthorizedProcedure).filter(
            InstitutionAuthorizedProcedure.id == target_id
        ).first()
        return {
            "institution_id": auth.institution_id if auth else None,
            "procedure_id": auth.procedure_id if auth else None,
        }
    if dimension == "qualification":
        qual = db.query(PractitionerQualification).filter(
            PractitionerQualification.id == target_id
        ).first()
        return {"practitioner_id": qual.practitioner_id if qual else None}
    auth = db.query(PractitionerAuthorizedProcedure).filter(
        PractitionerAuthorizedProcedure.id == target_id
    ).first()
    return {
        "practitioner_id": auth.practitioner_id if auth else None,
        "procedure_id": auth.procedure_id if auth else None,
    }


@router.post(
    "/evidence/licenses/{license_id}/change",
    response_model=schemas.EvidenceChangeResult,
    tags=["证据变更与重新判定"]
)
def change_license_evidence(
    license_id: int,
    payload: schemas.EvidenceChangeRequest,
    db: Session = Depends(get_db)
):
    return _apply_evidence_change(
        db, "license", ea.register_license_version, license_id, payload
    )


@router.post(
    "/evidence/institution-authorizations/{authorization_id}/change",
    response_model=schemas.EvidenceChangeResult,
    tags=["证据变更与重新判定"]
)
def change_institution_auth_evidence(
    authorization_id: int,
    payload: schemas.EvidenceChangeRequest,
    db: Session = Depends(get_db)
):
    return _apply_evidence_change(
        db, "institution_auth",
        ea.register_institution_auth_version, authorization_id, payload
    )


@router.post(
    "/evidence/qualifications/{qualification_id}/change",
    response_model=schemas.EvidenceChangeResult,
    tags=["证据变更与重新判定"]
)
def change_qualification_evidence(
    qualification_id: int,
    payload: schemas.EvidenceChangeRequest,
    db: Session = Depends(get_db)
):
    return _apply_evidence_change(
        db, "qualification",
        ea.register_qualification_version, qualification_id, payload
    )


@router.post(
    "/evidence/practitioner-authorizations/{authorization_id}/change",
    response_model=schemas.EvidenceChangeResult,
    tags=["证据变更与重新判定"]
)
def change_practitioner_auth_evidence(
    authorization_id: int,
    payload: schemas.EvidenceChangeRequest,
    db: Session = Depends(get_db)
):
    return _apply_evidence_change(
        db, "practitioner_auth",
        ea.register_practitioner_auth_version, authorization_id, payload
    )
