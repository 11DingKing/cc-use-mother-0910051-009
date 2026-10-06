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

## 时点证据与追加式重新判定

针对“同一医生调入新机构当天完成项目、事后补录授权，治疗时双主体条件无法
证明”的问题，每条项目记录的每次判定都会绑定到**治疗发生时**的四类证据版本：

- 机构执业许可证版本
- 机构项目授权版本
- 人员资质版本（逐证书）
- 人员项目授权版本

证据链只追加、不覆盖：暂停、恢复、变更、追溯更正、事后补录都追加新版本，
并对受影响的历史项目记录追加一次带编号的重新判定，原判定及其逐项证据永久
保留。判定结论为三态：`当时合规` / `当时违规` / `无法判定`（证据均系治疗
后倒签补录、治疗时是否满足无法证明时）。结论翻转时新判定带处置影响说明
（新增违规线索 / 建议撤销线索）。

时点语义：版本按主张生效区间 `[valid_from, valid_until]` 选版，生效日含当日；
版本登记时刻 `recorded_at` 晚于治疗日且为“事后补录/追溯更正”的，证据状态标
为“事后补录（治疗时无法证明）”。

一项治疗涉及多名人员时，逐人给出机构侧 + 人员侧证据，任一参与人缺证即判违规。

主要接口：

| 接口 | 说明 |
| --- | --- |
| `POST /api/compliance/actual-procedure` | 登记项目记录并生成不可变初次判定，可带 `participants` |
| `GET  /api/compliance/actual-procedures/{id}/audit` | 查询完整判定链与每次的双主体证据、处置影响 |
| `POST /api/compliance/actual-procedures/{id}/participants` | 追加参与人并触发重新判定 |
| `POST /api/compliance/evidence/licenses/{id}/change` | 许可证暂停/恢复/变更/追溯更正/事后补录 |
| `POST /api/compliance/evidence/institution-authorizations/{id}/change` | 机构项目授权变更 |
| `POST /api/compliance/evidence/qualifications/{id}/change` | 人员资质变更 |
| `POST /api/compliance/evidence/practitioner-authorizations/{id}/change` | 人员项目授权变更 |
| `GET  /api/compliance/check/single` | 时点试判定（不落库），返回双主体证据 |

