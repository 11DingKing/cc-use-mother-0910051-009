"""
时点证据与追加式重新判定测试
============================

覆盖投诉调查提出的五个关键场景：
1. 先记录、后补证：治疗时证据缺失判违规，倒签补录后改判“无法判定”，
   原违规判定保留，处置影响明确标注；
2. 同日生效边界：生效日含当日，前一日判“尚未生效”；
3. 跨机构执业：按治疗发生机构的双主体证据判定，他机构记录不受牵连；
4. 暂停 / 恢复 / 追溯更正：追加版本并对历史记录追加重新判定，不覆盖；
5. 一项治疗多名人员：逐人双主体证据，补齐后一致改判。
"""

from datetime import date

import pytest

from app.models import (
    InstitutionType, Institution, InstitutionLicense,
    Practitioner, PractitionerQualification, QualificationType,
    InstitutionAuthorizedProcedure, PractitionerAuthorizedProcedure,
    ActualProcedureRecord, ViolationClue, ClueType,
    EvidenceChangeType,
)
from app import evidence_audit as ea
from app.models import SnapshotConclusion


def _make_institution(db_session, name, code, with_license=False, proc_id=None):
    inst = Institution(
        name=name,
        unified_social_code=code,
        institution_type=InstitutionType.CLINIC,
        registration_date=date(2019, 1, 1),
    )
    db_session.add(inst)
    db_session.flush()
    if with_license:
        lic = InstitutionLicense(
            institution_id=inst.id,
            license_number=f"PDY-{code}",
            issue_date=date(2019, 1, 10),
            valid_until=date(2035, 1, 9),
            is_valid=True,
        )
        db_session.add(lic)
        db_session.flush()
        ea.register_license_version(
            db_session, lic.id, EvidenceChangeType.INITIAL,
            valid_from=lic.issue_date, valid_until=lic.valid_until,
        )
    if proc_id is not None:
        auth = InstitutionAuthorizedProcedure(
            institution_id=inst.id, procedure_id=proc_id,
            authorized_date=date(2019, 2, 1),
        )
        db_session.add(auth)
        db_session.flush()
        ea.register_institution_auth_version(
            db_session, auth.id, EvidenceChangeType.INITIAL,
            valid_from=auth.authorized_date,
        )
    db_session.flush()
    return inst


def _make_qualified_doctor(db_session, name, idcard, institution_id, proc_id=None):
    prac = Practitioner(
        name=name, id_card=idcard, institution_id=institution_id, position="医师"
    )
    db_session.add(prac)
    db_session.flush()
    doc = PractitionerQualification(
        practitioner_id=prac.id,
        qualification_type=QualificationType.DOCTOR,
        certificate_number=f"DOC-{idcard}",
        issue_date=date(2005, 1, 1),
        valid_until=None,
        practice_scope="外科专业",
        is_valid=True,
    )
    prac_q = PractitionerQualification(
        practitioner_id=prac.id,
        qualification_type=QualificationType.PRACTICE,
        certificate_number=f"PRAC-{idcard}",
        issue_date=date(2006, 1, 1),
        valid_until=date(2035, 12, 31),
        practice_scope="外科专业;医疗美容科",
        is_valid=True,
    )
    db_session.add_all([doc, prac_q])
    db_session.flush()
    ea.register_qualification_version(
        db_session, doc.id, EvidenceChangeType.INITIAL, valid_from=doc.issue_date
    )
    ea.register_qualification_version(
        db_session, prac_q.id, EvidenceChangeType.INITIAL,
        valid_from=prac_q.issue_date, valid_until=prac_q.valid_until,
    )
    if proc_id is not None:
        auth = PractitionerAuthorizedProcedure(
            practitioner_id=prac.id, procedure_id=proc_id,
            authorized_date=date(2019, 2, 1),
        )
        db_session.add(auth)
        db_session.flush()
        ea.register_practitioner_auth_version(
            db_session, auth.id, EvidenceChangeType.INITIAL,
            valid_from=auth.authorized_date,
        )
    db_session.flush()
    return prac


class TestRecordFirstBackfillLater:
    """场景一：调入新机构当天完成项目，机构授权和个人范围事后补录。"""

    def test_initial_violation_then_inconclusive_after_backfill(
        self, client, db_session, test_procedures
    ):
        proc = test_procedures["TEST-001"]
        treatment_day = "2024-03-01"

        # 治疗发生时：新机构无许可证/无项目授权，医生无资质/无项目授权
        inst = _make_institution(db_session, "调入新机构A", "91310000NEWINST01")
        prac = Practitioner(
            name="调入医生陈", id_card="310101198803010001",
            institution_id=inst.id, position="医师",
        )
        db_session.add(prac)
        db_session.flush()

        # 先记录：四类证据治疗时全部缺失
        resp = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst.id,
            "practitioner_id": prac.id,
            "procedure_id": proc.id,
            "procedure_date": treatment_day,
            "patient_count": 1,
        })
        assert resp.status_code == 200, resp.text
        record_id = resp.json()["id"]
        assert resp.json()["is_over_range"] is True
        assert resp.json()["judgment_conclusion"] == "当时违规"

        audit0 = client.get(
            f"/api/compliance/actual-procedures/{record_id}/audit"
        ).json()
        assert audit0["judgment_count"] == 1
        j0 = audit0["judgments"][0]
        assert j0["conclusion"] == "当时违规"
        # 双主体证据齐全：机构侧 2 项 + 人员侧 3 项
        assert len(j0["institution_evidence"]) == 2
        assert len(j0["participants"][0]["evidence"]) == 3
        assert all(
            e["status"] == "治疗时缺失"
            for e in j0["institution_evidence"]
        )
        # 初始违规自动生成线索
        clues = db_session.query(ViolationClue).filter(
            ViolationClue.clue_type == ClueType.OVER_RANGE_PRACTICE,
            ViolationClue.institution_id == inst.id,
        ).count()
        assert clues == 1

        # 事后倒签补录机构许可证（发证日早于系统登记日）
        r = client.post(f"/api/institutions/{inst.id}/licenses", json={
            "institution_id": inst.id,
            "license_number": "PDY-NEWINST01",
            "issue_date": "2024-01-01",
            "valid_until": "2035-01-01",
            "is_valid": True,
        })
        assert r.status_code == 200, r.text

        # 补录机构项目授权
        r = client.post(
            f"/api/institutions/{inst.id}/authorized-procedures",
            json={
                "institution_id": inst.id,
                "procedure_id": proc.id,
                "authorized_date": "2024-02-15",
            },
        )
        assert r.status_code == 200, r.text

        # 补录医师资格证、执业证
        r = client.post(f"/api/practitioners/{prac.id}/qualifications", json={
            "practitioner_id": prac.id,
            "qualification_type": "医师资格证",
            "certificate_number": "DOC-NEW-0001",
            "issue_date": "2010-01-01",
            "is_valid": True,
        })
        assert r.status_code == 200, r.text
        r = client.post(f"/api/practitioners/{prac.id}/qualifications", json={
            "practitioner_id": prac.id,
            "qualification_type": "医师执业证",
            "certificate_number": "PRAC-NEW-0001",
            "issue_date": "2024-02-20",
            "valid_until": "2030-12-31",
            "practice_scope": "外科专业;医疗美容科",
            "is_valid": True,
        })
        assert r.status_code == 200, r.text

        # 最后补录人员项目授权：四类证据齐备但全部系事后补录
        r = client.post(
            f"/api/practitioners/{prac.id}/authorized-procedures",
            json={
                "practitioner_id": prac.id,
                "procedure_id": proc.id,
                "authorized_date": "2024-02-25",
            },
        )
        assert r.status_code == 200, r.text

        audit = client.get(
            f"/api/compliance/actual-procedures/{record_id}/audit"
        ).json()

        # 原判定未被覆盖：判定序列追加
        assert audit["judgment_count"] >= 2
        assert audit["judgments"][0]["conclusion"] == "当时违规"

        latest = audit["judgments"][-1]
        # 事后补证不能自证治疗时有效 → 无法判定，而不是直接翻成合规
        assert latest["conclusion"] == "无法判定"
        assert latest["changed_from_previous"] is True
        assert latest["disposition_impact"] is not None
        assert "无法判定" in latest["disposition_impact"]
        assert "进一步核查" in latest["disposition_impact"]

        # 双主体证据均标注为“事后补录（治疗时无法证明）”
        statuses = [e["status"] for e in latest["institution_evidence"]]
        statuses += [
            e["status"]
            for p in latest["participants"]
            for e in p["evidence"]
        ]
        assert set(statuses) == {"事后补录（治疗时无法证明）"}
        assert all(
            e["backfilled_after_event"] is True
            for e in latest["institution_evidence"]
        )

        # 记录当前结论同步为无法判定，且不被算作“超范围违规”
        assert audit["current_is_over_range"] is False
        assert audit["latest_conclusion"] == "无法判定"

    def test_backfill_with_one_hard_gap_still_violation(
        self, client, db_session, test_procedures
    ):
        """补录了许可证但人员项目授权始终缺失：仍应判违规，硬缺陷优先。"""
        proc = test_procedures["TEST-003"]
        inst = _make_institution(db_session, "调入新机构B", "91310000NEWINST02")
        prac = _make_qualified_doctor(
            db_session, "调入医生林", "310101198803010002", inst.id
        )

        resp = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst.id,
            "practitioner_id": prac.id,
            "procedure_id": proc.id,
            "procedure_date": "2024-03-01",
        })
        record_id = resp.json()["id"]
        assert resp.json()["judgment_conclusion"] == "当时违规"

        # 只补机构侧两证，人员项目授权不补
        client.post(f"/api/institutions/{inst.id}/licenses", json={
            "institution_id": inst.id,
            "license_number": "PDY-NEWINST02",
            "issue_date": "2024-01-01",
            "valid_until": "2035-01-01",
        })
        client.post(
            f"/api/institutions/{inst.id}/authorized-procedures",
            json={
                "institution_id": inst.id,
                "procedure_id": proc.id,
                "authorized_date": "2024-02-15",
            },
        )

        audit = client.get(
            f"/api/compliance/actual-procedures/{record_id}/audit"
        ).json()
        latest = audit["judgments"][-1]
        assert latest["conclusion"] == "当时违规"
        person_statuses = [
            e["item_key"] + ":" + e["status"]
            for p in latest["participants"] for e in p["evidence"]
        ]
        assert any("项目#" in s and s.endswith("治疗时缺失") for s in person_statuses)


class TestEffectiveDayBoundary:
    """场景二：同日生效边界，生效日含当日。"""

    def test_boundary_day_is_effective(
        self, client, db_session,
        test_institution_valid, test_practitioner_fully_qualified, test_procedures
    ):
        proc = test_procedures["TEST-001"]
        inst_auth = InstitutionAuthorizedProcedure(
            institution_id=test_institution_valid.id, procedure_id=proc.id,
            authorized_date=date(2024, 3, 1),
        )
        prac_auth = PractitionerAuthorizedProcedure(
            practitioner_id=test_practitioner_fully_qualified.id,
            procedure_id=proc.id, authorized_date=date(2024, 3, 1),
        )
        db_session.add_all([inst_auth, prac_auth])
        db_session.flush()

        # 前一天：授权尚未生效
        r = client.get("/api/compliance/check/single", params={
            "institution_id": test_institution_valid.id,
            "practitioner_id": test_practitioner_fully_qualified.id,
            "procedure_id": proc.id,
            "procedure_date": "2024-02-29",
        }).json()
        assert r["conclusion"] == "当时违规"
        assert any("尚未生效" in i for i in r["issues"])
        auth_statuses = [
            e["status"] for e in r["institution_evidence"]
            if e["dimension"] == "机构项目授权"
        ]
        assert auth_statuses == ["治疗时尚未生效"]

        # 生效当天：有效（含当日）
        r = client.get("/api/compliance/check/single", params={
            "institution_id": test_institution_valid.id,
            "practitioner_id": test_practitioner_fully_qualified.id,
            "procedure_id": proc.id,
            "procedure_date": "2024-03-01",
        }).json()
        assert r["conclusion"] == "当时合规"
        assert all(
            e["status"] == "当时有效"
            for e in r["institution_evidence"]
        )


class TestCrossInstitution:
    """场景三：跨机构执业——按治疗机构取证，他机构记录不受牵连。"""

    def test_practice_at_new_institution_uses_new_institution_evidence(
        self, client, db_session, test_procedures
    ):
        proc = test_procedures["TEST-005"]
        # 机构 A：医生证据完整
        inst_a = _make_institution(
            db_session, "原机构甲", "91310000CROSSA01",
            with_license=True, proc_id=proc.id,
        )
        doc = _make_qualified_doctor(
            db_session, "多点执业医生吴", "310101198001010CROSS",
            inst_a.id, proc_id=proc.id,
        )
        # 机构 B：只有许可证，没有该项目授权
        inst_b = _make_institution(
            db_session, "调入机构乙", "91310000CROSSB01",
            with_license=True,
        )

        move_day = "2024-04-10"
        # 在 A 的历史合规记录
        r_a = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst_a.id, "practitioner_id": doc.id,
            "procedure_id": proc.id, "procedure_date": "2024-04-01",
        })
        assert r_a.json()["judgment_conclusion"] == "当时合规"
        id_a = r_a.json()["id"]

        # 调入 B 当天在 B 治疗：B 无项目授权 → 违规
        r_b = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst_b.id, "practitioner_id": doc.id,
            "procedure_id": proc.id, "procedure_date": move_day,
        })
        id_b = r_b.json()["id"]
        assert r_b.json()["judgment_conclusion"] == "当时违规"

        audit_b = client.get(
            f"/api/compliance/actual-procedures/{id_b}/audit"
        ).json()
        dims = {
            e["dimension"]: e["status"]
            for e in audit_b["judgments"][0]["institution_evidence"]
        }
        assert dims["机构执业许可证"] == "当时有效"
        assert dims["机构项目授权"] == "治疗时缺失"

        # 给 B 补项目授权（倒签至调入前）
        client.post(
            f"/api/institutions/{inst_b.id}/authorized-procedures",
            json={
                "institution_id": inst_b.id,
                "procedure_id": proc.id,
                "authorized_date": "2024-04-01",
            },
        )

        audit_b2 = client.get(
            f"/api/compliance/actual-procedures/{id_b}/audit"
        ).json()
        assert audit_b2["judgments"][-1]["conclusion"] == "无法判定"

        # A 的记录仍只有 1 条判定、结论合规，未被 B 的补证牵连
        audit_a = client.get(
            f"/api/compliance/actual-procedures/{id_a}/audit"
        ).json()
        assert audit_a["judgment_count"] == 1
        assert audit_a["judgments"][0]["conclusion"] == "当时合规"


class TestSuspendRestoreCorrect:
    """场景四：暂停、恢复、追溯更正生成追加式重新判定。"""

    def test_suspend_and_restore_append_judgments(
        self, client, db_session, test_procedures
    ):
        proc = test_procedures["TEST-001"]
        inst = _make_institution(
            db_session, "暂停测试机构", "91310000SUSP0001",
            with_license=True, proc_id=proc.id,
        )
        doc = _make_qualified_doctor(
            db_session, "暂停测试医生", "310101198001010SUSP0",
            inst.id, proc_id=proc.id,
        )
        lic = db_session.query(InstitutionLicense).filter(
            InstitutionLicense.institution_id == inst.id
        ).first()

        # 治疗发生在 3 月（当时许可证有效，判合规）
        r = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst.id, "practitioner_id": doc.id,
            "procedure_id": proc.id, "procedure_date": "2024-03-15",
        })
        rec_inside = r.json()["id"]
        assert r.json()["judgment_conclusion"] == "当时合规"

        # 许可证自 2024-02-01 起暂停、2024-05-01 恢复：
        # 3 月的治疗落入暂停区间 → 追加一次违规重新判定
        r = client.post(
            f"/api/compliance/evidence/licenses/{lic.id}/change",
            json={"change_type": "暂停", "effective_date": "2024-02-01"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["changed_conclusions"] == 1
        assert rec_inside in r.json()["affected_record_ids"]

        audit = client.get(
            f"/api/compliance/actual-procedures/{rec_inside}/audit"
        ).json()
        assert audit["judgment_count"] == 2
        j1, j2 = audit["judgments"]
        # 原判定保留且仍引用 v1，状态为当时有效
        assert j1["conclusion"] == "当时合规"
        lic_ev_1 = next(
            e for e in j1["institution_evidence"]
            if e["dimension"] == "机构执业许可证"
        )
        assert lic_ev_1["version_no"] == 1
        assert lic_ev_1["status"] == "当时有效"
        # 新判定引用 v2，状态为暂停
        assert j2["conclusion"] == "当时违规"
        lic_ev_2 = next(
            e for e in j2["institution_evidence"]
            if e["dimension"] == "机构执业许可证"
        )
        assert lic_ev_2["version_no"] == 2
        assert lic_ev_2["status"] == "治疗时处于暂停/失效状态"
        assert j2["reason"] == "机构许可证暂停"
        assert j2["disposition_impact"] is not None
        assert "新增违规线索" in j2["disposition_impact"]

        # 合规→违规翻转自动生成超范围线索，且去重（再次恢复/暂停不重复）
        auto_clues = db_session.query(ViolationClue).filter(
            ViolationClue.clue_type == ClueType.OVER_RANGE_PRACTICE,
            ViolationClue.institution_id == inst.id,
            ViolationClue.source == "证据变更自动重新判定",
        ).all()
        assert len(auto_clues) == 1

        # 2024-05-01 恢复：3 月的治疗仍处于暂停区间，结论不变不追加
        r = client.post(
            f"/api/compliance/evidence/licenses/{lic.id}/change",
            json={"change_type": "恢复", "effective_date": "2024-05-01"},
        )
        assert r.json()["rejudged_records"] == 0
        audit2 = client.get(
            f"/api/compliance/actual-procedures/{rec_inside}/audit"
        ).json()
        assert audit2["judgment_count"] == 2

        # 暂停期外的治疗：1 月（暂停前）、6 月（恢复后）均合规
        r = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst.id, "practitioner_id": doc.id,
            "procedure_id": proc.id, "procedure_date": "2024-01-10",
        })
        assert r.json()["judgment_conclusion"] == "当时合规"
        r = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst.id, "practitioner_id": doc.id,
            "procedure_id": proc.id, "procedure_date": "2024-06-01",
        })
        assert r.json()["judgment_conclusion"] == "当时合规"

    def test_retroactive_correction_changes_historical_judgment(
        self, client, db_session,
        test_institution_valid, test_practitioner_fully_qualified, test_procedures
    ):
        proc = test_procedures["TEST-001"]
        # 授权自 2024-03-10 起
        inst_auth = InstitutionAuthorizedProcedure(
            institution_id=test_institution_valid.id, procedure_id=proc.id,
            authorized_date=date(2024, 3, 10),
        )
        prac_auth = PractitionerAuthorizedProcedure(
            practitioner_id=test_practitioner_fully_qualified.id,
            procedure_id=proc.id, authorized_date=date(2024, 3, 10),
        )
        db_session.add_all([inst_auth, prac_auth])
        db_session.flush()

        # 2024-03-05 治疗：授权尚未生效 → 违规
        r = client.post("/api/compliance/actual-procedure", json={
            "institution_id": test_institution_valid.id,
            "practitioner_id": test_practitioner_fully_qualified.id,
            "procedure_id": proc.id,
            "procedure_date": "2024-03-05",
        })
        rec_id = r.json()["id"]
        assert r.json()["judgment_conclusion"] == "当时违规"

        # 追溯更正：机构项目授权实际自 2024-01-01 起生效
        r = client.post(
            f"/api/compliance/evidence/institution-authorizations/{inst_auth.id}/change",
            json={
                "change_type": "追溯更正",
                "valid_from": "2024-01-01",
                "valid_until": "2035-12-31",
            },
        )
        assert r.status_code == 200, r.text

        audit = client.get(
            f"/api/compliance/actual-procedures/{rec_id}/audit"
        ).json()
        latest = audit["judgments"][-1]
        # 机构侧已更正，但人员侧授权未同步更正 → 仍违规；机构证据状态变更
        inst_ev = next(
            e for e in latest["institution_evidence"]
            if e["dimension"] == "机构项目授权"
        )
        assert inst_ev["change_type"] == "追溯更正"
        assert inst_ev["status"] == "事后补录（治疗时无法证明）"

        # 人员授权同步追溯更正后：全部无法证明 → 无法判定
        client.post(
            f"/api/compliance/evidence/practitioner-authorizations/{prac_auth.id}/change",
            json={
                "change_type": "追溯更正",
                "valid_from": "2024-01-01",
                "valid_until": "2035-12-31",
            },
        )
        audit = client.get(
            f"/api/compliance/actual-procedures/{rec_id}/audit"
        ).json()
        assert audit["judgments"][-1]["conclusion"] == "无法判定"
        # 最初的违规判定仍在
        assert audit["judgments"][0]["conclusion"] == "当时违规"
        assert audit["judgment_count"] >= 3


class TestMultipleParticipants:
    """场景五：一项治疗多名人员，逐人双主体证据，补齐后一致改判。"""

    def test_multi_participant_per_person_evidence(
        self, client, db_session, test_procedures
    ):
        proc = test_procedures["TEST-002"]
        inst = _make_institution(
            db_session, "多人手术机构", "91310000MULTI001",
            with_license=True, proc_id=proc.id,
        )
        surgeon = _make_qualified_doctor(
            db_session, "主刀医生郑", "310101198001010MULT1",
            inst.id, proc_id=proc.id,
        )
        # 麻醉医生：无任何资质
        anesthetist = Practitioner(
            name="麻醉人员冯", id_card="310101199001010MULT2",
            institution_id=inst.id, position="麻醉",
        )
        db_session.add(anesthetist)
        db_session.flush()

        r = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst.id,
            "practitioner_id": surgeon.id,
            "procedure_id": proc.id,
            "procedure_date": "2024-05-20",
            "patient_count": 1,
            "participants": [
                {"practitioner_id": anesthetist.id, "role": "麻醉医师"}
            ],
        })
        assert r.status_code == 200, r.text
        rec_id = r.json()["id"]
        # 一人缺证 → 整条治疗违规，但主刀本人证据全部有效
        assert r.json()["judgment_conclusion"] == "当时违规"

        audit = client.get(
            f"/api/compliance/actual-procedures/{rec_id}/audit"
        ).json()
        j = audit["judgments"][0]
        by_person = {p["practitioner_name"]: p for p in j["participants"]}
        assert set(by_person) == {"主刀医生郑", "麻醉人员冯"}

        surgeon_p = by_person["主刀医生郑"]
        assert surgeon_p["is_primary"] is True
        assert all(e["status"] == "当时有效" for e in surgeon_p["evidence"])

        anes_p = by_person["麻醉人员冯"]
        assert anes_p["is_primary"] is False
        assert anes_p["role"] == "麻醉医师"
        assert all(e["status"] == "治疗时缺失" for e in anes_p["evidence"])

        # 事后为麻醉人员补齐资质与项目授权（存量登记，自动补登为 INITIAL）
        for qtype, cert_no in [
            (QualificationType.DOCTOR, "DOC-MULT-ANES"),
            (QualificationType.PRACTICE, "PRAC-MULT-ANES"),
        ]:
            q = PractitionerQualification(
                practitioner_id=anesthetist.id,
                qualification_type=qtype,
                certificate_number=cert_no,
                issue_date=date(2015, 1, 1),
                valid_until=date(2035, 12, 31),
                practice_scope="麻醉专业",
                is_valid=True,
            )
            db_session.add(q)
            db_session.flush()
            ea.register_qualification_version(
                db_session, q.id, EvidenceChangeType.INITIAL,
                valid_from=q.issue_date, valid_until=q.valid_until,
            )
        auth = PractitionerAuthorizedProcedure(
            practitioner_id=anesthetist.id, procedure_id=proc.id,
            authorized_date=date(2020, 1, 1),
        )
        db_session.add(auth)
        db_session.flush()
        ea.register_practitioner_auth_version(
            db_session, auth.id, EvidenceChangeType.INITIAL,
            valid_from=auth.authorized_date,
        )
        db_session.flush()

        # 批量重新判定：证据补齐 → 追加合规判定，原违规判定保留
        r = client.post("/api/compliance/recalculate")
        assert r.status_code == 200

        audit2 = client.get(
            f"/api/compliance/actual-procedures/{rec_id}/audit"
        ).json()
        assert audit2["judgment_count"] == 2
        assert audit2["judgments"][0]["conclusion"] == "当时违规"
        latest = audit2["judgments"][-1]
        assert latest["conclusion"] == "当时合规"
        assert latest["changed_from_previous"] is True
        assert latest["disposition_impact"] is not None
        # 两名参与人的证据在新判定中均为有效
        assert all(
            e["status"] == "当时有效"
            for p in latest["participants"] for e in p["evidence"]
        )

    def test_add_participant_triggers_rejudgment(
        self, client, db_session, test_procedures
    ):
        proc = test_procedures["TEST-001"]
        inst = _make_institution(
            db_session, "追加参与人机构", "91310000MULTI002",
            with_license=True, proc_id=proc.id,
        )
        surgeon = _make_qualified_doctor(
            db_session, "主刀王", "310101198001010MULT3",
            inst.id, proc_id=proc.id,
        )
        nurse = Practitioner(
            name="无证护士卫", id_card="310101199501010MULT4",
            institution_id=inst.id, position="护士",
        )
        db_session.add(nurse)
        db_session.flush()

        r = client.post("/api/compliance/actual-procedure", json={
            "institution_id": inst.id, "practitioner_id": surgeon.id,
            "procedure_id": proc.id, "procedure_date": "2024-05-20",
        })
        rec_id = r.json()["id"]
        assert r.json()["judgment_conclusion"] == "当时合规"

        r = client.post(
            f"/api/compliance/actual-procedures/{rec_id}/participants",
            json={"practitioner_id": nurse.id, "role": "巡回护士"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["judgment_count"] == 2
        assert data["judgments"][-1]["conclusion"] == "当时违规"
        assert data["judgments"][0]["conclusion"] == "当时合规"
