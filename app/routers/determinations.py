"""发生时判定与审计 API：版本登记、诊疗事件、判定历史、监管解释查询。"""
import json
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Institution, Practitioner, Procedure,
    InstitutionLicenseVersion, InstitutionProcedureAuthVersion,
    PractitionerQualificationVersion, PractitionerProcedureAuthVersion,
    TreatmentEvent, ComplianceDetermination, RedeterminationRun,
    ClueEventLink, DispositionImpactNotice, ViolationClue,
    DeterminationTrigger, QualificationType,
)
from .. import schemas_determination as sd
from .. import determination as det

router = APIRouter()


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------

def _det_out(row: ComplianceDetermination) -> sd.DeterminationOut:
    return sd.DeterminationOut(
        id=row.id,
        event_id=row.event_id,
        participant_id=row.participant_id,
        seq=row.seq,
        determined_at=row.determined_at,
        as_of_date=row.as_of_date,
        result=row.result,
        trigger=row.trigger,
        trigger_detail=row.trigger_detail,
        license_version_id=row.license_version_id,
        inst_auth_version_id=row.inst_auth_version_id,
        prac_auth_version_id=row.prac_auth_version_id,
        qual_version_ids=json.loads(row.qual_version_ids or "[]"),
        issues=json.loads(row.issues or "[]"),
        is_current=row.is_current,
        supersedes_id=row.supersedes_id,
    )


def _det_detail(row: ComplianceDetermination) -> sd.DeterminationDetail:
    base = _det_out(row).model_dump()
    base["evidence"] = json.loads(row.evidence or "{}")
    return sd.DeterminationDetail(**base)


def _event_out(db: Session, event: TreatmentEvent) -> sd.TreatmentEventOut:
    participants = []
    for p in event.participants:
        practitioner = db.query(Practitioner).filter(
            Practitioner.id == p.practitioner_id
        ).first()
        participants.append(sd.ParticipantOut(
            id=p.id,
            practitioner_id=p.practitioner_id,
            practitioner_name=practitioner.name if practitioner else None,
            role=p.role,
            is_primary=p.is_primary,
            registered_institution_id=p.registered_institution_id,
        ))
    return sd.TreatmentEventOut(
        id=event.id,
        event_code=event.event_code,
        institution_id=event.institution_id,
        procedure_id=event.procedure_id,
        treatment_date=event.treatment_date,
        current_result=event.current_result,
        remark=event.remark,
        created_at=event.created_at,
        participants=participants,
    )


def _get_event_or_404(db: Session, event_id: int) -> TreatmentEvent:
    event = db.query(TreatmentEvent).filter(TreatmentEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="诊疗事件不存在")
    return event


# ---------------------------------------------------------------------------
# 证据版本登记（任一侧的登记/暂停/变更/追溯更正都会触发追加式重新判定）
# ---------------------------------------------------------------------------

def _finish_register(db: Session, table, chain_filter, values, payload) -> sd.VersionRegisterResult:
    row, run = det.register_version(
        db, table, chain_filter, values,
        change_kind=payload.change_kind,
        reason=payload.reason,
        recorded_at=payload.recorded_at,
    )
    db.commit()
    return sd.VersionRegisterResult(
        version_id=row.id,
        status=row.status,
        change_kind=row.change_kind,
        redetermination=sd.RedeterminationRunOut.model_validate(run),
    )


@router.post("/versions/institution-licenses", response_model=sd.VersionRegisterResult)
def register_institution_license_version(
    payload: sd.InstitutionLicenseVersionIn, db: Session = Depends(get_db)
):
    if not db.query(Institution).filter(Institution.id == payload.institution_id).first():
        raise HTTPException(status_code=404, detail="机构不存在")
    return _finish_register(
        db,
        InstitutionLicenseVersion,
        {"institution_id": payload.institution_id},
        {
            "license_number": payload.license_number,
            "issuing_authority": payload.issuing_authority,
            "approved_scope": payload.approved_scope,
            "effective_from": payload.effective_from,
            "effective_to": payload.effective_to,
        },
        payload,
    )


@router.post("/versions/institution-authorizations", response_model=sd.VersionRegisterResult)
def register_institution_auth_version(
    payload: sd.InstitutionAuthVersionIn, db: Session = Depends(get_db)
):
    if not db.query(Institution).filter(Institution.id == payload.institution_id).first():
        raise HTTPException(status_code=404, detail="机构不存在")
    if not db.query(Procedure).filter(Procedure.id == payload.procedure_id).first():
        raise HTTPException(status_code=404, detail="项目不存在")
    return _finish_register(
        db,
        InstitutionProcedureAuthVersion,
        {"institution_id": payload.institution_id, "procedure_id": payload.procedure_id},
        {"effective_from": payload.effective_from, "effective_to": payload.effective_to},
        payload,
    )


@router.post("/versions/practitioner-qualifications", response_model=sd.VersionRegisterResult)
def register_practitioner_qual_version(
    payload: sd.PractitionerQualVersionIn, db: Session = Depends(get_db)
):
    if not db.query(Practitioner).filter(Practitioner.id == payload.practitioner_id).first():
        raise HTTPException(status_code=404, detail="人员不存在")
    return _finish_register(
        db,
        PractitionerQualificationVersion,
        {
            "practitioner_id": payload.practitioner_id,
            "qualification_type": payload.qualification_type,
        },
        {
            "certificate_number": payload.certificate_number,
            "practice_scope": payload.practice_scope,
            "effective_from": payload.effective_from,
            "effective_to": payload.effective_to,
        },
        payload,
    )


@router.post("/versions/practitioner-authorizations", response_model=sd.VersionRegisterResult)
def register_practitioner_auth_version(
    payload: sd.PractitionerAuthVersionIn, db: Session = Depends(get_db)
):
    if not db.query(Practitioner).filter(Practitioner.id == payload.practitioner_id).first():
        raise HTTPException(status_code=404, detail="人员不存在")
    if not db.query(Procedure).filter(Procedure.id == payload.procedure_id).first():
        raise HTTPException(status_code=404, detail="项目不存在")
    if payload.institution_id is not None and not db.query(Institution).filter(
        Institution.id == payload.institution_id
    ).first():
        raise HTTPException(status_code=404, detail="授权限定机构不存在")
    return _finish_register(
        db,
        PractitionerProcedureAuthVersion,
        {
            "practitioner_id": payload.practitioner_id,
            "procedure_id": payload.procedure_id,
            "institution_id": payload.institution_id,
        },
        {"effective_from": payload.effective_from, "effective_to": payload.effective_to},
        payload,
    )


_VERSION_KINDS = {
    "institution-licenses": InstitutionLicenseVersion,
    "institution-authorizations": InstitutionProcedureAuthVersion,
    "practitioner-qualifications": PractitionerQualificationVersion,
    "practitioner-authorizations": PractitionerProcedureAuthVersion,
}


@router.get("/versions/{kind}")
def list_versions(
    kind: str,
    institution_id: Optional[int] = None,
    practitioner_id: Optional[int] = None,
    procedure_id: Optional[int] = None,
    qualification_type: Optional[QualificationType] = None,
    db: Session = Depends(get_db),
):
    table = _VERSION_KINDS.get(kind)
    if table is None:
        raise HTTPException(status_code=404, detail="未知的版本类型")
    query = db.query(table)
    if institution_id is not None and hasattr(table, "institution_id"):
        query = query.filter(table.institution_id == institution_id)
    if practitioner_id is not None and hasattr(table, "practitioner_id"):
        query = query.filter(table.practitioner_id == practitioner_id)
    if procedure_id is not None and hasattr(table, "procedure_id"):
        query = query.filter(table.procedure_id == procedure_id)
    if qualification_type is not None and hasattr(table, "qualification_type"):
        query = query.filter(table.qualification_type == qualification_type)
    rows = query.order_by(table.recorded_at, table.id).all()
    return [
        {
            c.name: getattr(r, c.name)
            for c in table.__table__.columns
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 诊疗事件（可含多名参与人员）
# ---------------------------------------------------------------------------

@router.post("/events", response_model=sd.EventCreateResult)
def create_event(payload: sd.TreatmentEventIn, db: Session = Depends(get_db)):
    if not db.query(Institution).filter(Institution.id == payload.institution_id).first():
        raise HTTPException(status_code=404, detail="机构不存在")
    if not db.query(Procedure).filter(Procedure.id == payload.procedure_id).first():
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        event = det.create_treatment_event(
            db,
            institution_id=payload.institution_id,
            procedure_id=payload.procedure_id,
            treatment_date=payload.treatment_date,
            participants=[p.model_dump() for p in payload.participants],
            remark=payload.remark,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    db.commit()
    db.refresh(event)

    current = det._current_determination(db, event.id, None)
    return sd.EventCreateResult(
        event=_event_out(db, event),
        initial_determinations=len(event.participants) + 1,
        current_result=event.current_result,
        issues=json.loads(current.issues or "[]") if current else [],
    )


@router.get("/events", response_model=List[sd.TreatmentEventOut])
def list_events(
    institution_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = db.query(TreatmentEvent)
    if institution_id is not None:
        query = query.filter(TreatmentEvent.institution_id == institution_id)
    return [_event_out(db, e) for e in query.order_by(TreatmentEvent.id).all()]


@router.get("/events/{event_id}", response_model=sd.TreatmentEventOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    return _event_out(db, _get_event_or_404(db, event_id))


@router.get("/events/{event_id}/history", response_model=List[sd.DeterminationOut])
def event_history(event_id: int, db: Session = Depends(get_db)):
    _get_event_or_404(db, event_id)
    rows = db.query(ComplianceDetermination).filter(
        ComplianceDetermination.event_id == event_id
    ).order_by(ComplianceDetermination.determined_at, ComplianceDetermination.id).all()
    return [_det_out(r) for r in rows]


class ManualRedetermineIn(BaseModel):
    reason: Optional[str] = None


@router.post("/events/{event_id}/redetermine")
def manual_redetermine(
    event_id: int, payload: ManualRedetermineIn, db: Session = Depends(get_db)
):
    event = _get_event_or_404(db, event_id)
    outcome = det.determine_event(
        db, event, DeterminationTrigger.MANUAL,
        payload.reason or "人工触发重新判定",
        material_only=True,
    )
    db.commit()
    return {
        "event_id": event_id,
        "appended": outcome["appended"],
        "current_result": event.current_result,
        "message": "已生成新的判定" if outcome["appended"] else "证据与结论无变化，未生成新判定",
    }


# ---------------------------------------------------------------------------
# 监管解释查询：当时通过/违规的双主体证据 + 后续变化是否影响处置
# ---------------------------------------------------------------------------

def _leaf_label(leaf: dict) -> str:
    if not leaf.get("found"):
        return "无版本"
    label = f"版本#{leaf['version_id']}（{leaf['status']}）"
    if leaf.get("retroactive_entry"):
        label += "［补录］"
    return label


def _collect_retroactive_flags(leaves_with_subject: List[tuple]) -> List[str]:
    flags = []
    for subject, leaf in leaves_with_subject:
        if not leaf.get("found"):
            continue
        if leaf.get("retroactive_entry"):
            flags.append(
                f"{subject}版本#{leaf['version_id']}系补录：录入时间 "
                f"{leaf['recorded_at']} 晚于治疗日，当时是否真实有效需人工核实"
            )
        elif leaf.get("entered_after_record"):
            flags.append(
                f"{subject}版本#{leaf['version_id']}录入晚于诊疗记录登记时间，"
                f"初始判定时该证据尚不可知"
            )
    return flags


@router.get("/events/{event_id}/explanation", response_model=sd.EventExplanation)
def explain_event(event_id: int, db: Session = Depends(get_db)):
    event = _get_event_or_404(db, event_id)

    current_event_row = det._current_determination(db, event.id, None)
    if current_event_row is None:
        raise HTTPException(status_code=409, detail="事件尚无判定记录")
    participant_rows = [
        det._current_determination(db, event.id, p.id) for p in event.participants
    ]
    participant_rows = [r for r in participant_rows if r is not None]

    event_evidence = json.loads(current_event_row.evidence or "{}")
    inst_side = event_evidence.get("institution_side", {})

    summary_parts = [
        f"机构侧（{inst_side.get('institution_name', '')}）："
        f"执业许可证{_leaf_label(inst_side.get('license', {}))}，"
        f"项目授权{_leaf_label(inst_side.get('procedure_authorization', {}))}"
    ]
    leaves_with_subject = [
        ("机构侧执业许可证", inst_side.get("license", {})),
        ("机构侧项目授权", inst_side.get("procedure_authorization", {})),
    ]
    for row in participant_rows:
        ev = json.loads(row.evidence or "{}")
        side = ev.get("practitioner_side", {})
        name = side.get("practitioner_name", "")
        quals = side.get("qualifications", {})
        qual_txt = "，".join(
            f"{qtype}{_leaf_label(leaf)}" for qtype, leaf in quals.items()
        )
        auth_txt = _leaf_label(side.get("procedure_authorization", {}))
        cross_txt = "，跨机构执业" if side.get("cross_institution") else ""
        summary_parts.append(
            f"人员侧（{name}{cross_txt}）：{qual_txt}，项目授权{auth_txt}"
        )
        for qtype, leaf in quals.items():
            leaves_with_subject.append((f"人员侧（{name}）{qtype}", leaf))
        leaves_with_subject.append(
            (f"人员侧（{name}）项目授权", side.get("procedure_authorization", {}))
        )

    retroactive_flags = _collect_retroactive_flags(leaves_with_subject)

    history_rows = db.query(ComplianceDetermination).filter(
        ComplianceDetermination.event_id == event_id
    ).order_by(ComplianceDetermination.determined_at, ComplianceDetermination.id).all()

    notices = db.query(DispositionImpactNotice).filter(
        DispositionImpactNotice.event_id == event_id
    ).order_by(DispositionImpactNotice.id).all()
    clue_ids = [n.clue_id for n in notices]
    clues = {
        c.id: c
        for c in db.query(ViolationClue).filter(ViolationClue.id.in_(clue_ids)).all()
    } if clue_ids else {}
    impacts = [
        sd.DispositionImpactOut(
            notice_id=n.id,
            clue_id=n.clue_id,
            clue_status=clues[n.clue_id].status.value if n.clue_id in clues else None,
            clue_conclusion=clues[n.clue_id].conclusion if n.clue_id in clues else None,
            determination_id=n.determination_id,
            impact_detail=n.impact_detail,
            detected_at=n.created_at,
        )
        for n in notices
    ]

    return sd.EventExplanation(
        event=_event_out(db, event),
        current_determination=_det_detail(current_event_row),
        participant_determinations=[_det_detail(r) for r in participant_rows],
        dual_subject_summary="；".join(summary_parts),
        retroactive_flags=retroactive_flags,
        history=[_det_out(r) for r in history_rows],
        disposition_impacts=impacts,
        later_changes_affect_disposition=len(impacts) > 0,
    )


@router.get("/events/{event_id}/clues")
def event_clues(event_id: int, db: Session = Depends(get_db)):
    _get_event_or_404(db, event_id)
    links = db.query(ClueEventLink).filter(ClueEventLink.event_id == event_id).all()
    clue_ids = [l.clue_id for l in links]
    if not clue_ids:
        return []
    clues = db.query(ViolationClue).filter(ViolationClue.id.in_(clue_ids)).all()
    return [
        {
            "clue_id": c.id,
            "title": c.title,
            "status": c.status.value,
            "conclusion": c.conclusion,
            "verified_at": c.verified_at,
        }
        for c in clues
    ]


@router.get("/runs", response_model=List[sd.RedeterminationRunOut])
def list_runs(db: Session = Depends(get_db)):
    rows = db.query(RedeterminationRun).order_by(RedeterminationRun.id.desc()).all()
    return [sd.RedeterminationRunOut.model_validate(r) for r in rows]
