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
- `POST /api/items/{id}/tickets`
- `POST /api/items/{id}/tickets/{tid}/confirm`
- `GET /api/audit`

允许角色：duty_officer, chief_engineer, dispatcher, viewer。库位超过汛限或入库流量上升时提升紧迫度；授权前必须有复核记录，执行后仍要闭环现场反馈。

## 闸门操作票据

汛期开闸除总工授权外，还须按闸门逐项签认：

- 调度员（dispatcher）登记票据：闸门顺序`gate_seq`（同一指令内唯一）、计划开度、执行人、复核人、库位下限`level_min`和过流上限`flow_max`；同一账号不能同时担任执行与复核。
- 指令进入`executed`后，由登记复核人带入现场库位`actual_level`和实际过流`actual_flow`确认；读数越界（库位低于下限或过流超过上限）或复核人已换班（确认人与登记复核人不一致）时，票据转`redo`待重做并保留原计划值，可重新签认。
- 全部票据确认后指令才能归档`closed`；登记、签认、重做均写入审计链。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
