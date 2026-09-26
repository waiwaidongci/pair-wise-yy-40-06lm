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
- `GET /api/audit`
- `GET /api/batches?state=active|closable|closed`
- `POST /api/batches`
- `GET /api/batches/{id}`
- `POST /api/batches/{id}/close`，必须提交`expected_version`

允许角色：assessor, structural_engineer, review_board, viewer。风险分值和人员密度共同影响排序；审核通过前必须完成评估、设计和施工证据登记。

## 鉴定批次

评估员（assessor）通过`POST /api/batches`把多个项目选入批次，提交`scope`、`planned_review_date`（YYYY-MM-DD）和`item_ids`。同一项目只能出现在一个未结束批次中：若所选项目已在进行中批次里，接口返回原批次（HTTP 200，`created=false`）而不新建。

批次优先级按批内最紧急项目、未关闭事项总数和最近剩余期限实时重算。批内项目全部到达终态（accepted/rejected）且无未关闭事项时，总工（structural_engineer）才能关闭批次；否则关闭返回409并列出阻塞项目及原因，批次详情的`blockers`字段同样可见。列表接口的`state`筛选：`active`进行中、`closable`待关闭、`closed`已关闭。批次规则在`src/rules.py`，持久化在`src/repository.py`，接口在`src/http_api.py`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
