"""发生时判定（双时态版本链 + 可审计重新判定）测试。

覆盖监管要求的六类一致性场景：
1. 投诉主场景：调入新机构当天完成项目、先记录后补证、处置后证据变化
2. 同日生效边界
3. 授权暂停与恢复触发追加式重新判定
4. 追溯更正推翻原结论但不覆盖历史
5. 跨机构执业的一致判定规则
6. 多人员治疗事件的一致结论
"""
from datetime import date, datetime, timedelta, time

import pytest

from app.models import (
    Institution, InstitutionType, Practitioner, Procedure, ProcedureCategory,
    ComplianceDetermination, DeterminationResult, DispositionImpactNotice,
)

T = date.today() - timedelta(days=1)          # 治疗日（昨天）
T_MINUS_1 = T - timedelta(days=1)             # 治疗日前一天
TIMELY = datetime.combine(T_MINUS_1, time(9, 0))  # 治疗日前已录入（非补录）


def _make_institution(db, code, name):
    inst = Institution(
        name=name,
        unified_social_code=code,
        institution_type=InstitutionType.HOSPITAL,
    )
    db.add(inst)
    db.flush()
    return inst


def _make_procedure(db, code="TP-001", name="测试注射项目"):
    proc = db.query(Procedure).filter(Procedure.code == code).first()
    if proc:
        return proc
    proc = Procedure(name=name, code=code, category=ProcedureCategory.INJECTION)
    db.add(proc)
    db.flush()
    return proc


def _make_practitioner(db, id_card, name, institution_id):
    prac = Practitioner(name=name, id_card=id_card, institution_id=institution_id)
    db.add(prac)
    db.flush()
    return prac


def _register_license(client, inst_id, effective_from, effective_to=None, recorded_at=None):
    payload = {
        "institution_id": inst_id,
        "license_number": f"LIC-{inst_id}",
        "effective_from": effective_from.isoformat(),
        "change_kind": "初始登记",
    }
    if effective_to:
        payload["effective_to"] = effective_to.isoformat()
    if recorded_at:
        payload["recorded_at"] = recorded_at.isoformat()
    resp = client.post("/api/determinations/versions/institution-licenses", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _register_inst_auth(client, inst_id, proc_id, effective_from, **kw):
    payload = {
        "institution_id": inst_id,
        "procedure_id": proc_id,
        "effective_from": effective_from.isoformat(),
        "change_kind": kw.pop("change_kind", "初始登记"),
    }
    if kw.get("effective_to"):
        payload["effective_to"] = kw["effective_to"].isoformat()
    if kw.get("recorded_at"):
        payload["recorded_at"] = kw["recorded_at"].isoformat()
    resp = client.post("/api/determinations/versions/institution-authorizations", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _register_qual(client, prac_id, qtype, effective_from, **kw):
    payload = {
        "practitioner_id": prac_id,
        "qualification_type": qtype,
        "certificate_number": kw.pop("cert", f"CERT-{prac_id}-{qtype}"),
        "effective_from": effective_from.isoformat(),
        "change_kind": kw.pop("change_kind", "初始登记"),
    }
    if kw.get("effective_to"):
        payload["effective_to"] = kw["effective_to"].isoformat()
    if kw.get("recorded_at"):
        payload["recorded_at"] = kw["recorded_at"].isoformat()
    resp = client.post("/api/determinations/versions/practitioner-qualifications", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _register_prac_auth(client, prac_id, proc_id, effective_from, institution_id=None, **kw):
    payload = {
        "practitioner_id": prac_id,
        "procedure_id": proc_id,
        "effective_from": effective_from.isoformat(),
        "change_kind": kw.pop("change_kind", "初始登记"),
    }
    if institution_id is not None:
        payload["institution_id"] = institution_id
    if kw.get("effective_to"):
        payload["effective_to"] = kw["effective_to"].isoformat()
    if kw.get("recorded_at"):
        payload["recorded_at"] = kw["recorded_at"].isoformat()
    resp = client.post("/api/determinations/versions/practitioner-authorizations", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _setup_compliant_background(client, inst_id, prac_id, proc_id):
    """机构许可证、机构项目授权、人员双证、人员项目授权均在治疗日前及时登记。"""
    _register_license(client, inst_id, T_MINUS_1, recorded_at=TIMELY)
    _register_inst_auth(client, inst_id, proc_id, T_MINUS_1, recorded_at=TIMELY)
    _register_qual(client, prac_id, "医师资格证", T_MINUS_1, recorded_at=TIMELY)
    _register_qual(client, prac_id, "医师执业证", T_MINUS_1, recorded_at=TIMELY)
    _register_prac_auth(client, prac_id, proc_id, T_MINUS_1, recorded_at=TIMELY)


def _create_event(client, inst_id, proc_id, participants, treatment_date=T):
    resp = client.post("/api/determinations/events", json={
        "institution_id": inst_id,
        "procedure_id": proc_id,
        "treatment_date": treatment_date.isoformat(),
        "participants": participants,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def two_institutions(db_session):
    inst_a = _make_institution(db_session, "91310000XFER0001", "原执业机构A")
    inst_b = _make_institution(db_session, "91310000XFER0002", "新调入机构B")
    proc = _make_procedure(db_session)
    doctor = _make_practitioner(db_session, "310101199001010099", "王调入", inst_a.id)
    return {"a": inst_a, "b": inst_b, "proc": proc, "doctor": doctor}


class TestComplaintScenario:
    """投诉主场景：调入新机构当天完成项目，机构授权与个人执业范围均事后补录。

    初始判定因治疗时无已知有效证据而违规；补录后生成新的判定（不覆盖原结论），
    证据标记补录；已作出的处置被标记为受影响。
    """

    def test_record_first_certify_later_full_chain(self, client, db_session, two_institutions):
        b = two_institutions["b"]
        proc = two_institutions["proc"]
        doctor = two_institutions["doctor"]

        # 治疗日前已知的证据：B 的许可证、医生的双证（资质本身一直有效）
        _register_license(client, b.id, T_MINUS_1, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师资格证", T_MINUS_1, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师执业证", T_MINUS_1, recorded_at=TIMELY)

        # 调入当天完成项目并登记记录：机构项目授权与个人项目授权均尚未登记
        created = _create_event(client, b.id, proc.id, [
            {"practitioner_id": doctor.id, "role": "主诊医师", "is_primary": True}
        ])
        event_id = created["event"]["id"]
        assert created["current_result"] == "违规"
        assert any("机构侧" in i and "项目授权" in i for i in created["issues"])
        assert any("跨机构执业" in i for i in created["issues"])

        # 自动生成违规线索，监管核查后作“已核实违规”处置
        clues = client.get(f"/api/determinations/events/{event_id}/clues").json()
        assert len(clues) == 1
        conclude = client.post(f"/api/clues/{clues[0]['clue_id']}/conclude", json={
            "status": "已核实违规",
            "conclusion": "治疗发生时无法证明机构授权与个人执业范围同时有效",
        })
        assert conclude.status_code == 200

        # 事后补录：机构项目授权与个人项目授权，生效日=治疗日（同日生效边界）
        r1 = _register_inst_auth(client, b.id, proc.id, T)
        assert r1["redetermination"]["events_redetermined"] >= 1
        r2 = _register_prac_auth(client, doctor.id, proc.id, T, institution_id=b.id)
        assert r2["redetermination"]["results_flipped"] == 1

        # 监管解释查询：当前结论转为合规，但依据的是补录证据
        exp = client.get(f"/api/determinations/events/{event_id}/explanation").json()
        assert exp["current_determination"]["result"] == "合规"
        assert len(exp["retroactive_flags"]) == 2
        assert any("机构侧项目授权" in f and "补录" in f for f in exp["retroactive_flags"])
        assert any("人员侧" in f and "补录" in f for f in exp["retroactive_flags"])
        assert "机构侧" in exp["dual_subject_summary"]
        assert "人员侧" in exp["dual_subject_summary"]

        # 后续变化影响已作出的处置
        assert exp["later_changes_affect_disposition"] is True
        assert len(exp["disposition_impacts"]) == 1
        assert exp["disposition_impacts"][0]["clue_status"] == "已核实违规"
        assert "复核" in exp["disposition_impacts"][0]["impact_detail"]

        # 历史判定完整保留：事件级 3 条（违规→违规→合规），结论链可追溯
        history = [h for h in exp["history"] if h["participant_id"] is None]
        assert [h["result"] for h in history] == ["违规", "违规", "合规"]
        assert [h["seq"] for h in history] == [1, 2, 3]
        assert history[0]["is_current"] is False
        assert history[2]["is_current"] is True
        assert history[2]["supersedes_id"] == history[1]["id"]
        assert history[1]["supersedes_id"] == history[0]["id"]

        # 原结论未被覆盖：首条判定仍是违规，且当时证据缺失的记录保留
        first = db_session.get(ComplianceDetermination, history[0]["id"])
        assert first.result == DeterminationResult.VIOLATION
        assert "项目授权" in first.issues

        # 处置影响通知已落库可审计
        notices = db_session.query(DispositionImpactNotice).filter_by(event_id=event_id).all()
        assert len(notices) == 1


class TestSameDayBoundary:
    """同日生效边界：生效日=治疗日有效，失效日=治疗日仍有效。"""

    def test_effective_from_same_day_is_valid(self, client, db_session):
        inst = _make_institution(db_session, "91310000SAME0001", "同日生效机构")
        proc = _make_procedure(db_session, "TP-101", "同日生效项目")
        doctor = _make_practitioner(db_session, "310101199001010101", "李同日", inst.id)

        _register_license(client, inst.id, T, recorded_at=TIMELY)
        _register_inst_auth(client, inst.id, proc.id, T, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师资格证", T, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师执业证", T, recorded_at=TIMELY)
        _register_prac_auth(client, doctor.id, proc.id, T, recorded_at=TIMELY)

        created = _create_event(client, inst.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        assert created["current_result"] == "合规"

    def test_effective_to_same_day_is_valid(self, client, db_session):
        inst = _make_institution(db_session, "91310000SAME0002", "末日有效机构")
        proc = _make_procedure(db_session, "TP-102", "末日有效项目")
        doctor = _make_practitioner(db_session, "310101199001010102", "赵末日", inst.id)

        _register_license(client, inst.id, T_MINUS_1, effective_to=T, recorded_at=TIMELY)
        _register_inst_auth(client, inst.id, proc.id, T_MINUS_1, effective_to=T, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师资格证", T_MINUS_1, effective_to=T, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师执业证", T_MINUS_1, effective_to=T, recorded_at=TIMELY)
        _register_prac_auth(client, doctor.id, proc.id, T_MINUS_1, effective_to=T, recorded_at=TIMELY)

        created = _create_event(client, inst.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        assert created["current_result"] == "合规"


class TestSuspensionAndReinstate:
    """任一侧的暂停/恢复触发追加式重新判定，历史结论保留。"""

    def test_suspend_then_reinstate_inst_auth(self, client, db_session):
        inst = _make_institution(db_session, "91310000SUSP0001", "暂停测试机构")
        proc = _make_procedure(db_session, "TP-201", "暂停测试项目")
        doctor = _make_practitioner(db_session, "310101199001010201", "陈暂停", inst.id)
        _setup_compliant_background(client, inst.id, doctor.id, proc.id)

        created = _create_event(client, inst.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        event_id = created["event"]["id"]
        assert created["current_result"] == "合规"

        # 机构项目授权在治疗日被暂停 → 重新判定为违规（追加，不覆盖）
        r = _register_inst_auth(
            client, inst.id, proc.id, T,
            change_kind="暂停", effective_to=T,
        )
        assert r["redetermination"]["results_flipped"] == 1
        exp = client.get(f"/api/determinations/events/{event_id}/explanation").json()
        assert exp["current_determination"]["result"] == "违规"
        assert any("暂停" in i for i in exp["current_determination"]["issues"])

        # 恢复 → 再次追加判定为合规
        r = _register_inst_auth(client, inst.id, proc.id, T, change_kind="恢复")
        assert r["redetermination"]["results_flipped"] == 1
        exp = client.get(f"/api/determinations/events/{event_id}/explanation").json()
        assert exp["current_determination"]["result"] == "合规"

        history = [h for h in exp["history"] if h["participant_id"] is None]
        assert [h["result"] for h in history] == ["合规", "违规", "合规"]
        assert sum(1 for h in history if h["is_current"]) == 1


class TestRetroactiveCorrection:
    """追溯更正推翻原结论：原判定保留，新判定说明更正来源。"""

    def test_correction_shrinks_qualification_window(self, client, db_session):
        inst = _make_institution(db_session, "91310000CORR0001", "更正测试机构")
        proc = _make_procedure(db_session, "TP-301", "更正测试项目")
        doctor = _make_practitioner(db_session, "310101199001010301", "周更正", inst.id)
        _setup_compliant_background(client, inst.id, doctor.id, proc.id)

        created = _create_event(client, inst.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        event_id = created["event"]["id"]
        assert created["current_result"] == "合规"

        # 追溯更正：医师执业证有效期更正为治疗日前一日止 → 治疗日无有效执业证
        r = _register_qual(
            client, doctor.id, "医师执业证", T_MINUS_1,
            change_kind="追溯更正", effective_to=T_MINUS_1,
        )
        assert r["redetermination"]["results_flipped"] == 1

        exp = client.get(f"/api/determinations/events/{event_id}/explanation").json()
        assert exp["current_determination"]["result"] == "违规"
        assert any("医师执业证" in i for i in exp["current_determination"]["issues"])
        assert exp["current_determination"]["trigger"] == "人员资质版本变化"

        history = [h for h in exp["history"] if h["participant_id"] is None]
        assert [h["result"] for h in history] == ["合规", "违规"]
        first = db_session.get(ComplianceDetermination, history[0]["id"])
        assert first.result == DeterminationResult.COMPLIANT
        assert first.is_current is False


class TestCrossInstitutionPractice:
    """跨机构执业：未限定机构的授权仅覆盖登记机构；跨机构需限定到治疗机构。"""

    def test_unscoped_auth_insufficient_cross_institution(self, client, db_session, two_institutions):
        b = two_institutions["b"]
        proc = two_institutions["proc"]
        doctor = two_institutions["doctor"]  # 登记在机构A

        _register_license(client, b.id, T_MINUS_1, recorded_at=TIMELY)
        _register_inst_auth(client, b.id, proc.id, T_MINUS_1, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师资格证", T_MINUS_1, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师执业证", T_MINUS_1, recorded_at=TIMELY)
        # 仅有未限定机构的项目授权（覆盖登记机构A，不覆盖B）
        _register_prac_auth(client, doctor.id, proc.id, T_MINUS_1, recorded_at=TIMELY)

        created = _create_event(client, b.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        assert created["current_result"] == "违规"
        assert any("跨机构执业" in i for i in created["issues"])

        # 补登限定到治疗机构B的授权（治疗日前已生效）→ 合规
        _register_prac_auth(
            client, doctor.id, proc.id, T_MINUS_1,
            institution_id=b.id, recorded_at=TIMELY,
        )
        exp = client.get(
            f"/api/determinations/events/{created['event']['id']}/explanation"
        ).json()
        assert exp["current_determination"]["result"] == "合规"

    def test_home_institution_unscoped_auth_sufficient(self, client, db_session):
        inst = _make_institution(db_session, "91310000HOME0001", "本机构执业机构")
        proc = _make_procedure(db_session, "TP-401", "本机构项目")
        doctor = _make_practitioner(db_session, "310101199001010401", "吴本机构", inst.id)
        _setup_compliant_background(client, inst.id, doctor.id, proc.id)

        created = _create_event(client, inst.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        assert created["current_result"] == "合规"
        side = created["event"]
        assert side["participants"][0]["registered_institution_id"] == inst.id


class TestMultiParticipantEvent:
    """多人员治疗事件：每人独立判定，事件级结论为全体之“与”。"""

    def test_event_result_is_consistent_and_per_participant(self, client, db_session):
        inst = _make_institution(db_session, "91310000TEAM0001", "多人员机构")
        proc = _make_procedure(db_session, "TP-501", "多人员项目")
        lead = _make_practitioner(db_session, "310101199001010501", "钱主诊", inst.id)
        assistant = _make_practitioner(db_session, "310101199001010502", "孙助手", inst.id)

        _register_license(client, inst.id, T_MINUS_1, recorded_at=TIMELY)
        _register_inst_auth(client, inst.id, proc.id, T_MINUS_1, recorded_at=TIMELY)
        # 主诊资质齐全；助手无任何资质与授权
        _register_qual(client, lead.id, "医师资格证", T_MINUS_1, recorded_at=TIMELY)
        _register_qual(client, lead.id, "医师执业证", T_MINUS_1, recorded_at=TIMELY)
        _register_prac_auth(client, lead.id, proc.id, T_MINUS_1, recorded_at=TIMELY)

        created = _create_event(client, inst.id, proc.id, [
            {"practitioner_id": lead.id, "role": "主诊医师", "is_primary": True},
            {"practitioner_id": assistant.id, "role": "助手"},
        ])
        event_id = created["event"]["id"]
        assert created["current_result"] == "违规"
        assert any("孙助手" in i for i in created["issues"])

        exp = client.get(f"/api/determinations/events/{event_id}/explanation").json()
        by_name = {
            p["evidence"]["practitioner_side"]["practitioner_name"]: p
            for p in exp["participant_determinations"]
        }
        assert by_name["钱主诊"]["result"] == "合规"
        assert by_name["孙助手"]["result"] == "违规"
        # 事件级结论与参与人员结论一致（任一违规则事件违规）
        assert exp["current_determination"]["result"] == "违规"
        assert "钱主诊" in exp["dual_subject_summary"]
        assert "孙助手" in exp["dual_subject_summary"]


class TestDeterminationIntegrity:
    """判定链的完整性与幂等性。"""

    def test_manual_redetermine_without_changes_is_noop(self, client, db_session):
        inst = _make_institution(db_session, "91310000IDEM0001", "幂等测试机构")
        proc = _make_procedure(db_session, "TP-601", "幂等项目")
        doctor = _make_practitioner(db_session, "310101199001010601", "郑幂等", inst.id)
        _setup_compliant_background(client, inst.id, doctor.id, proc.id)
        created = _create_event(client, inst.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        event_id = created["event"]["id"]

        resp = client.post(f"/api/determinations/events/{event_id}/redetermine", json={})
        assert resp.status_code == 200
        assert resp.json()["appended"] == 0

        history = client.get(f"/api/determinations/events/{event_id}/history").json()
        assert len(history) == 2  # 初始的参与人员级 + 事件级，未新增

    def test_missing_license_violation(self, client, db_session):
        inst = _make_institution(db_session, "91310000NOLIC001", "无许可证机构")
        proc = _make_procedure(db_session, "TP-701", "无证机构项目")
        doctor = _make_practitioner(db_session, "310101199001010701", "冯无证", inst.id)
        _register_inst_auth(client, inst.id, proc.id, T_MINUS_1, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师资格证", T_MINUS_1, recorded_at=TIMELY)
        _register_qual(client, doctor.id, "医师执业证", T_MINUS_1, recorded_at=TIMELY)
        _register_prac_auth(client, doctor.id, proc.id, T_MINUS_1, recorded_at=TIMELY)

        created = _create_event(client, inst.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        assert created["current_result"] == "违规"
        assert any("执业许可证" in i for i in created["issues"])

    def test_runs_are_auditable(self, client, db_session):
        inst = _make_institution(db_session, "91310000RUNS0001", "审计运行机构")
        proc = _make_procedure(db_session, "TP-801", "审计运行项目")
        doctor = _make_practitioner(db_session, "310101199001010801", "蒋审计", inst.id)
        _setup_compliant_background(client, inst.id, doctor.id, proc.id)
        _create_event(client, inst.id, proc.id, [
            {"practitioner_id": doctor.id, "is_primary": True}
        ])
        _register_inst_auth(client, inst.id, proc.id, T, change_kind="暂停", effective_to=T)

        runs = client.get("/api/determinations/runs").json()
        assert len(runs) >= 1
        latest = runs[0]
        assert latest["trigger"] == "机构项目授权版本变化"
        assert latest["events_scanned"] >= 1
        assert latest["source_version_id"] is not None
