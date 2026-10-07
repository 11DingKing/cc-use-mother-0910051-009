# 医疗美容执业与项目合规服务

本项目是使用 Python、FastAPI 与 SQLite 实现的服务端应用，覆盖机构、人员资质、项目分级、执业范围、合规线索和监管处置。它可在单个 Linux 应用容器内完成安装、测试、编译和接口验收，不依赖浏览器、外部数据库、缓存、消息队列或额外运行服务。

## 安装

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
```

## 测试

```bash
python3 seed_data.py && python3 -m pytest -q
```

## 编译

```bash
python3 -m compileall -q .
```

## 接口验收

```bash
python3 -c "from app.main import app; assert len(app.routes) > 5; print(len(app.routes))"
```

## 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 发生时判定与审计（/api/determinations）

针对投诉调查中“事后补录显示有效、却无法证明治疗发生时满足条件”的问题，系统提供
双时态（业务有效期 + 系统录入时间）的证据版本链与追加式判定，保证每次项目记录
绑定**发生时**的四侧证据版本，任何一侧的暂停、变更或追溯更正都生成可审计的
重新判定，而不是覆盖原结论。

### 证据版本链

四类证据各自成链、只增不改：

- 机构执业许可证版本 `institution_license_versions`
- 机构项目授权版本 `institution_procedure_auth_versions`
- 人员资质版本 `practitioner_qualification_versions`（医师资格证、医师执业证）
- 人员项目授权版本 `practitioner_procedure_auth_versions`（可限定执业机构）

每行携带：业务有效期 `[effective_from, effective_to]`（两端含当日，空为长期）、
录入时间 `recorded_at`、状态（有效/暂停/注销）、变更类型（初始登记/续期/变更/
追溯更正/暂停/恢复/注销）。更正类登记会关闭旧行（`superseded_at`）；暂停、注销、
恢复为叠加行，由“录入最晚者覆盖”规则解析，历史行永不删除。

### 一致性规则

1. **同日生效边界**：生效日当日即可用于治疗日，失效日当日仍有效。
2. **知识时点**：每次判定只使用判定时刻已知（`recorded_at <= 判定时刻`）且未被
   更正关闭的版本。
3. **先记录后补证**：录入日期晚于治疗日的证据在判定中标记 `retroactive_entry`；
   晚于诊疗记录登记时间的标记 `entered_after_record`，初始判定不受影响地保留。
4. **跨机构执业**：参与人员登记机构与治疗机构不一致时，人员项目授权必须显式
   限定到治疗机构；未限定机构的授权仅覆盖其登记机构。人员的登记机构在事件
   登记时快照保存（`registered_institution_id`）。
5. **多名人员**：一个诊疗事件可含多名参与人员，每人独立判定（机构侧证据共享），
   事件级结论为全体结论的“与”，同一事件结论一致。
6. **追加式重判**：任一侧版本变化触发受影响事件的重新判定；绑定或结论有变化时
   追加新判定行（`seq` 递增、`supersedes_id` 链接、旧行 `is_current=false`），
   无变化时不产生噪音行，扫描过程记录于 `redetermination_runs`。

### 处置影响

事件级判定为违规时自动生成违规线索并与事件关联；线索办结（已核实违规/已排除）
后若重新判定与处置冲突，生成 `disposition_impact_notices`，解释查询中
`later_changes_affect_disposition` 为真并列出明细。

### 主要接口

- `POST /api/determinations/versions/institution-licenses`
- `POST /api/determinations/versions/institution-authorizations`
- `POST /api/determinations/versions/practitioner-qualifications`
- `POST /api/determinations/versions/practitioner-authorizations`
  （登记即触发重新判定，响应含 `redetermination` 扫描结果）
- `GET /api/determinations/versions/{kind}` 查看版本链全貌
- `POST /api/determinations/events` 登记诊疗事件（含多名参与人员）并即时判定
- `GET /api/determinations/events/{id}/history` 判定历史（审计链）
- `GET /api/determinations/events/{id}/explanation` 监管解释查询：当时通过/违规
  的双主体证据、补录标记、判定历史、后续变化对处置的影响
- `POST /api/determinations/events/{id}/redetermine` 人工重新判定
- `GET /api/determinations/runs` 重新判定运行审计

> 说明：旧接口 `/api/compliance/recalculate` 的就地重算保留用于兼容既有数据，
> 新发生的诊疗记录应使用本小节的判定链以获得可审计的历史。
