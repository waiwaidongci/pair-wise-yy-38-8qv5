# 水库防汛调度与操作确认

根据库位、入库流量、下游警戒和施工限制生成复核授权的泄洪指令。

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
python3 app.py --db ./data.db --port 8315
```

默认端口为`8315`，首次启动自动建库。使用`X-Actor`和`X-Role`请求头传递身份。

## 主要接口

- `GET /health`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/items/{id}/tickets`
- `POST /api/items/{id}/tickets`，调度员在指令授权后登记闸门操作票据
- `POST /api/items/{id}/tickets/{tid}/confirm`，指令执行后由复核人确认
- `POST /api/items/{id}/tickets/{tid}/revise`，调度员修订未确认票据（如复核人换班）
- `GET /api/audit`

允许角色：duty_officer, chief_engineer, dispatcher, viewer。库位超过汛限或入库流量上升时提升紧迫度；授权前必须有复核记录，执行后仍要闭环现场反馈。

## 闸门操作票据

汛期开闸在总工授权之外按闸门逐项签认：

- 调度员登记闸门顺序、计划开度、执行人、复核人、库位下限和过流上限；同一账号不能同时担任执行与复核，同一指令内闸门顺序不能重复。
- 指令执行后，复核人带入现场库位和实际过流逐票确认；读数越界（库位低于下限或过流超过上限）或复核人已换班（确认账号与登记复核人不一致）时，票据转`pending_redo`并保留原读数，可由调度员修订后重新确认。
- 全部票据确认后指令才能归档；票据的登记、修订、确认均写入原有SHA-256审计链。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
