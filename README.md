# 建筑抗震鉴定与加固排序

依据结构、用途、人员密度和历史缺陷生成鉴定与加固优先级。

## 模块结构

- `app.py`：参数解析、依赖组装和HTTP服务启动。
- `src/domain.py`：数据结构、错误、状态和基础校验。
- `src/rules.py`：状态机、角色矩阵、优先级、期限和关闭不变量。
- `src/repository.py`：SQLite建表、事务、版本控制和审计链。
- `src/service.py`：权限检查、用例编排、并发控制和审计。
- `src/http_api.py`：JSON路由和统一错误响应。
- `src/audit.py`：UTC时间和SHA-256审计事件。
- `static/index.html`：最小演示页。
- `tests/`：完整流程、规则和失败测试。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8317
```

默认端口为`8317`，首次启动自动建库。使用`X-Actor`和`X-Role`请求头传递身份。

## 主要接口

- `GET /health`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `POST /api/batches`，鉴定批次建批：`scope`、`planned_date`(YYYY-MM-DD)、`item_ids`
- `GET /api/batches?state=active|in_progress|closeable|closed`
- `GET /api/batches/{id}`，含每个项目的终态/未关闭事项和阻塞明细
- `POST /api/batches/{id}/close`，仅`chief_engineer`可调用
- `GET /api/audit`

允许角色：assessor, structural_engineer, review_board, viewer, chief_engineer。风险分值和人员密度共同影响排序；审核通过前必须完成评估、设计和施工证据登记。

### 鉴定批次规则

- 评估员（assessor）一次选入多个项目，登记复查范围与计划复查日。
- 同一项目在未结束（active）批次中只出现一次：重复建批不新建，直接返回原批次（响应中`reused=true`）；批次关闭后项目才能再次入选。
- 批次优先级实时重算：批内最紧急项目的优先级（0–5）＋未关闭事项（最多2）＋计划复查日剩余期限（当日/逾期最高，最多3），封顶10；列表按优先级、剩余期限排序。
- 总工（chief_engineer）关闭批次时，批内项目必须全部到达终态（accepted/rejected）且无未关闭事项，否则返回409；`GET /api/batches/{id}`的`blockers`逐条列出阻塞项目及原因。
- 列表筛选：`in_progress`（仍有阻塞）、`closeable`（待关闭，无阻塞）、`closed`（已结束）。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
