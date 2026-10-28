# 02 · 方案与取舍

## 1. 总体结构

```text
CLI (src/main.py)
  ├─ 单素材：AgentLoop (src/agent/loop.py)
  │    ├─ LLMClient: DeepSeek / Mock (src/agent/llm.py)
  │    ├─ ToolRegistry (src/agent/tools.py)
  │    │    ├─ get_material
  │    │    ├─ check_rules
  │    │    ├─ query_slots
  │    │    └─ save_draft
  │    ├─ JsonDraftStore (src/agent/store.py)
  │    └─ TraceWriter (src/agent/trace.py)
  └─ 批量：BatchRunner (src/agent/batch.py)
       └─ 按输入顺序，对每个素材跑一次 AgentLoop
            └─ 共享 ToolRegistry / JsonDraftStore
```

单素材一次运行：

```text
读取 input
  → 建立 state
  → 循环：
       模型输出 JSON 动作
       → 若是工具：执行工具、更新 state、把 observation 回给模型
       → 若是 finish：校验状态是否自洽
           → 通过：写 output + trace
           → 不通过：把拒绝原因作为 observation 回给模型，继续
  → 达到 max_steps：返回 max_steps
```

批量运行：

```text
读取数组或 {"materials":[...]}
  → 清空批次内占位
  → 按输入顺序：
       同一 material_id 第二次出现 → skipped_duplicate，复用第 1 条结果
       否则 → 跑一次 AgentLoop
            → save_draft 成功后，把 slot 写入批次内占位
            → 下一个素材 query_slots 时看到该占位，自动避让
  → 汇总 counts / status / 每条 result 与 trace
```

**批次内占位**只在当前进程有效，保存在 `ToolRegistry.reservations`，不写入 `data/schedule.json`，也不跨批次持久化。这样批量运行时不会产生同平台同时段的两份草稿，同时不会改动原始排期数据。

## 2. 为什么是原生循环

题目只要求“模型选动作 → 执行工具 → 观察 → 调整或结束”，没有要求多 Agent、持久化 Runtime 或复杂编排。原生实现的优势：

- 循环、状态、校验都在一个文件里，评审可以直接读到决策边界；
- 不依赖框架版本，3 小时内可运行、可测试；
- 方便针对“确认边界、幂等、保存失败”写精确测试。

代价：

- 没有框架自带的 checkpoint、human-in-the-loop、可视化；
- 需要自己维护状态和 Trace；
- 单机 JSON 存储不适合生产并发。

这些代价在当前题目范围内可接受。若要做生产级排期，下一步会换数据库、加锁、接消息队列，并保留同样的工具契约。

## 3. 模型输出协议

模型每轮只输出一个 JSON：

```json
{
  "action": "query_slots",
  "args": {
    "date": "2026-11-01",
    "platform": "douyin",
    "duration_sec": 30,
    "preferred_time": "18:00"
  },
  "decision_basis": "规则已通过，先确认用户要求的 18:00 是否可用。"
}
```

没有使用 OpenAI function calling，而是“JSON 动作 + 提示词工具说明”。原因：

- DeepSeek/多数兼容端点对 JSON object 输出支持稳定；
- Mock 可以直接返回同样的 JSON，测试不需要模拟 tool_call 协议；
- 解析失败时可以统一重试，不让不同 provider 的 tool-call 格式污染循环。

代价：模型可能输出多余文字或参数格式错误。处理方式：

- `parse_action` 容忍 Markdown 围栏和前后说明；
- 参数错误由工具返回 `invalid_arguments`，模型看到后修正；
- 连续两次非法 JSON 才终止为 `invalid_model_output`。

真实 Trace 中记录了模型把 `slot` 写成 `10:00-10:01`、工具报错、模型改成 `10:00` 重试的过程（`examples/05_duplicate/first_run_trace.md`）。

## 4. 状态机

状态不是“模型说了算”，而是工具观察累积出来的：

```text
START
  └─ get_material
       ├─ missing_fields 非空 → needs_input
       └─ 完整 → check_rules
            ├─ blocked=true → blocked
            └─ 通过 → query_slots
                 ├─ requested.available=true → save_draft(ready) → completed
                 └─ 冲突 → save_draft(pending_confirmation) → pending_confirmation
                          └─ save_draft 失败 → save_failed
```

`state` 保存：

- 素材、缺失字段、规则风险、时段查询结果、建议时段；
- 草稿结果、是否重复、保存错误；
- 原始要求时间与硬性标记。

每轮把 `state_summary` 和 observation 一起给模型，避免它只靠上下文记忆。

## 5. 四个工具

### 5.1 `get_material`

返回素材和 `missing_fields`。必填字段：`title`、`content_type`、`duration_sec`、`platform`、`rights_status`。

### 5.2 `check_rules`

逐条执行 `data/rules.json`：

- R001 版权必须 `cleared`（blocking）；
- R002 标题/描述/标签命中敏感词（blocking）；
- R003 平台时长上限（blocking）；
- R004 分辨率建议（warning）；
- R005 必填字段（缺失时返回 `missing_fields`，不进入规则检查）。

### 5.3 `query_slots`

- 校验日期/时间/时长；
- 检查目标时段与已有排期的重叠；
- 冲突时按“优先更晚、再按距离”排序替代时段；
- 返回 `requested.available`、`conflicts`、`alternatives`、`nearest_alternative`。

为什么优先更晚：把发布往前挪可能意味着素材还没准备好，风险更高；往后挪是更自然的 fallback。这个偏好写在工具里，模型不需要自己猜排序规则。

### 5.4 `save_draft`

- 只接受 `ready` / `pending_confirmation`；
- `ready` 且 `slot != requested_time` 直接拒绝，要求走 `pending_confirmation`；
- 保存前再次检查时段是否被占用；
- 幂等键：`material_id|date|slot|requested_time|draft_status` 的 SHA-256 前 20 位；
- 已存在相同键 → `duplicate=true`，复用 `draft_id`；
- `fail_save=true` → `storage_unavailable`，不写任何记录；
- 没有 publish 方法。

## 6. finish 校验：防止“模型说了算”

模型可以选动作，但不能绕过业务边界。`_validate_finish` 检查：

| 情况 | 拒绝条件 |
|---|---|
| 没读素材 | `material_loaded=false` |
| 信息缺失 | `status != needs_input` |
| 规则阻断 | `status != blocked` |
| 没查时段 | `slot_checked=false` |
| 没保存 | `draft_attempted=false` |
| 保存失败 | `status != save_failed` |
| 改了时段 | `status != pending_confirmation` 或草稿不是 `pending_confirmation` |
| 越权发布 | message 含“已发布/发布成功” |
| 原时段完成 | 草稿不是 `ready` |

拒绝不是抛异常，而是把 `finish_rejected` 作为 observation 还给模型，让它自己修正。这保留了 ReAct 的“观察—调整”，同时把不可接受的结果挡在输出之外。

## 7. 幂等设计

```text
key = sha256("M06|2026-11-02|10:00|10:00|ready")[:20]
```

相同请求第二次保存：

```json
{"ok": true, "duplicate": true, "draft_id": "D-897a51928e"}
```

草稿库中仍只有一条记录。冲突场景同理：M01 的 19:00 待确认草稿重复提交，仍复用 `D-58451013b9`。

没有用“先查再写”的两步逻辑，因为那在并发下会 race；幂等键由存储层原子判断，当前单进程 JSON 写入用临时文件 + `os.replace`。

## 8. 保存失败语义

`--fail-save` 或 `JsonDraftStore(fail_save=True)` 注入 `StorageError`。工具返回：

```json
{"ok": false, "error": "storage_unavailable", "message": "草稿存储不可用（注入故障）"}
```

模型可以重试一次；仍失败则 `finish(save_failed)`。校验层会确认：

- `draft.ok` 不能为 true；
- 不能有 `draft_id`；
- message 不能出现“已保存/成功”。

真实运行中模型确实重试了一次，然后在第 6 步返回 `save_failed`。

## 9. 确认边界

这是本题最重要的业务点。

当 `requested_time=18:00`、目标时段被占用、替代为 19:00 时：

```json
{
  "requested_time": "18:00",
  "slot": "19:00",
  "draft_status": "pending_confirmation",
  "pending_items": ["是否接受 19:00 替代 18:00"]
}
```

- `suggested_slot.changed_from_request=true`；
- `status=pending_confirmation`，不是 `completed`；
- 不修改素材的 `required_publish_at`；
- 不调用任何发布接口；
- 输出 `published=false`。

只有用户明确确认后，才应由上层把草稿转为确认排期；本实现不提供这个“确认”入口，避免越权。

## 10. Trace 设计

每次运行写两份：

- `trace.json`：run_id、provider、model、max_steps、input、steps、final；
- `trace.md`：可直接阅读的表格。

每一步记录：

- step；
- action；
- args；
- observation（工具返回摘要/完整 JSON）；
- decision_basis（模型给的一句话，不是隐藏 CoT）；
- timestamp。

Trace 不记录模型内部思维链，只记录可审计的动作与依据。

## 11. 主要取舍

| 取舍 | 选择 | 理由 |
|---|---|---|
| 框架 vs 原生 | 原生 | 单 Agent、四工具，原生更透明，3 小时可验证 |
| function calling vs JSON | JSON | Mock/真实模型共用协议，解析可控 |
| 固定流程 vs 自由 ReAct | 提示词给主流程，模型选动作，校验层兜底 | 既稳定又能展示动态决策 |
| 冲突建议 | 优先更晚 | 往前挪发布风险更高 |
| 批量策略 | 输入顺序串行 + 批次内占位 | 同一批次不产生同时段草稿；不做并发和优先级 |
| 存储 | JSON 文件 | 单机、可读、易复现；生产应换 DB |
| 发布 | 不实现 | 题目明确“仅保存草稿” |

## 12. 已知技术债

- JSON 存储没有进程锁，不适合并发；
- 批次内占位只在当前进程有效，不跨批次；批量顺序固定为输入顺序，没有优先级/截止时间排序；
- 单批最多 20 条，避免一次请求过长；
- 时段模型没有审核缓冲、跨平台冲突、节假日；
- 模型输出没有 JSON Schema 级校验，靠解析 + 工具参数校验 + finish 校验；
- Trace 没有采样/脱敏策略，生产需处理。
