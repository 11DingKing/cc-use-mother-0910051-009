from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..models import Institution, InstitutionLicense, InstitutionType
from .. import schemas

router = APIRouter()


@router.post("/", response_model=schemas.Institution)
def create_institution(institution: schemas.InstitutionCreate, db: Session = Depends(get_db)):
    existing = db.query(Institution).filter(
        Institution.unified_social_code == institution.unified_social_code
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="统一社会信用代码已存在")
    db_institution = Institution(**institution.model_dump())
    db.add(db_institution)
    db.commit()
    db.refresh(db_institution)
    return db_institution


@router.get("/", response_model=List[schemas.Institution])
def list_institutions(
    skip: int = 0,
    limit: int = 100,
    institution_type: Optional[InstitutionType] = None,
    keyword: Optional[str] = Query(None, description="机构名称关键词"),
    db: Session = Depends(get_db)
):
    query = db.query(Institution)
    if institution_type:
        query = query.filter(Institution.institution_type == institution_type)
    if keyword:
        query = query.filter(Institution.name.like(f"%{keyword}%"))
    return query.offset(skip).limit(limit).all()


@router.get("/{institution_id}", response_model=schemas.Institution)
def get_institution(institution_id: int, db: Session = Depends(get_db)):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    return institution


@router.put("/{institution_id}", response_model=schemas.Institution)
def update_institution(
    institution_id: int,
    institution_update: schemas.InstitutionUpdate,
    db: Session = Depends(get_db)
):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    update_data = institution_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(institution, key, value)
    db.commit()
    db.refresh(institution)
    return institution


@router.delete("/{institution_id}")
def delete_institution(institution_id: int, db: Session = Depends(get_db)):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    db.delete(institution)
    db.commit()
    return {"message": "删除成功"}


@router.post("/{institution_id}/licenses", response_model=schemas.InstitutionLicense)
def add_institution_license(
    institution_id: int,
    license_data: schemas.InstitutionLicenseCreate,
    db: Session = Depends(get_db)
):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    existing = db.query(InstitutionLicense).filter(
        InstitutionLicense.license_number == license_data.license_number
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="许可证编号已存在")
    db_license = InstitutionLicense(**license_data.model_dump())
    db.add(db_license)
    db.flush()
    # 登记证据版本链：主张生效日早于系统登记日的，属倒签/事后补录
    from .. import evidence_audit as ea
    from ..models import EvidenceChangeType
    from datetime import date as _date
    ct = (
        EvidenceChangeType.BACKFILL
        if db_license.issue_date and db_license.issue_date < _date.today()
        else EvidenceChangeType.INITIAL
    )
    ea.register_license_version(
        db, db_license.id, ct,
        valid_from=db_license.issue_date,
        valid_until=db_license.valid_until,
        is_active=bool(db_license.is_valid),
    )
    # 补证可能影响治疗日在其生效区间内的历史记录：追加重新判定
    ea.rejudge_after_evidence_change(
        db, dimension="license", change_type=ct,
        institution_id=db_license.institution_id,
        valid_from=db_license.issue_date, valid_until=db_license.valid_until,
        trigger_remark="机构执业许可证建档/补证",
    )
    db.commit()
    db.refresh(db_license)
    return db_license


@router.get("/{institution_id}/licenses", response_model=List[schemas.InstitutionLicense])
def list_institution_licenses(institution_id: int, db: Session = Depends(get_db)):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    return institution.licenses


@router.post("/{institution_id}/authorized-procedures", response_model=schemas.InstitutionAuthorizedProcedure)
def authorize_procedure_for_institution(
    institution_id: int,
    auth_data: schemas.InstitutionAuthorizedProcedureCreate,
    db: Session = Depends(get_db)
):
    from ..models import InstitutionAuthorizedProcedure, Procedure
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    procedure = db.query(Procedure).filter(Procedure.id == auth_data.procedure_id).first()
    if not procedure:
        raise HTTPException(status_code=404, detail="项目不存在")
    existing = db.query(InstitutionAuthorizedProcedure).filter(
        InstitutionAuthorizedProcedure.institution_id == institution_id,
        InstitutionAuthorizedProcedure.procedure_id == auth_data.procedure_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该项目已授权")
    db_auth = InstitutionAuthorizedProcedure(**auth_data.model_dump())
    db.add(db_auth)
    db.flush()
    # 登记机构项目授权初始版本；授权日早于系统登记日的按事后补录处理
    from .. import evidence_audit as ea
    from ..models import EvidenceChangeType
    from datetime import date as _date
    ct = (
        EvidenceChangeType.BACKFILL
        if db_auth.authorized_date and db_auth.authorized_date < _date.today()
        else EvidenceChangeType.INITIAL
    )
    ea.register_institution_auth_version(
        db, db_auth.id, ct,
        valid_from=db_auth.authorized_date,
    )
    # 机构项目授权建档/补证：重判该机构该项目的历史记录
    ea.rejudge_after_evidence_change(
        db, dimension="institution_auth", change_type=ct,
        institution_id=db_auth.institution_id, procedure_id=db_auth.procedure_id,
        valid_from=db_auth.authorized_date,
        trigger_remark="机构项目授权建档/补证",
    )
    db.commit()
    db.refresh(db_auth)
    from ..schemas import Procedure as ProcedureSchema
    db_auth.procedure = procedure
    return db_auth


@router.get("/{institution_id}/authorized-procedures", response_model=List[schemas.InstitutionAuthorizedProcedure])
def list_institution_authorized_procedures(institution_id: int, db: Session = Depends(get_db)):
    from ..models import InstitutionAuthorizedProcedure
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")
    auths = db.query(InstitutionAuthorizedProcedure).filter(
        InstitutionAuthorizedProcedure.institution_id == institution_id
    ).all()
    return auths
