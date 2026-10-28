# 03 · 验证与结果

## 1. 验证环境

| 项 | 值 |
|---|---|
| 日期 | 2026-10-28 |
| OS | Windows |
| Python | 3.13.16 |
| pytest | 9.2.0 |
| 真实模型 | DeepSeek OpenAI-compatible endpoint；请求 `deepseek-chat`，响应 `deepseek-flash` |
| 时区 | Asia/Shanghai |

运行命令：

```powershell
python -m pytest -q
```

结果：

```text
33 passed
```

测试分布：

- `tests/test_tools.py`：素材缺失、规则阻断、时段冲突/可用、幂等、保存失败、非法参数。
- `tests/test_loop.py`：正常、缺失、风险、冲突、重复、保存失败、步数上限、提前 finish 被拒、替代时段被误标 completed、发布措辞被拒、素材不存在。
- `tests/test_batch.py`：批次内时段避让、部分失败继续、重复素材跳过、保存失败、批量输入上限。
- `tests/test_cli.py`：CLI 正常/失败退出码、重复提交、批量 Mock、Trace 落盘。

## 2. 验收点与证据

| 必须验收点 | 用例 | 实际结果 | 证据 |
|---|---|---|---|
| 正常排期 | `01_normal` | `completed`，`ready` 草稿 `D-897a51928e` | `examples/01_normal/output.json` |
| 素材信息缺失 | `02_missing_info` | `needs_input`，`pending_items` 两项，无草稿 | `examples/02_missing_info/output.json` |
| 规则风险 | `03_rule_risk` | `blocked`，R001 版权 `pending` | `examples/03_rule_risk/output.json` |
| 时段冲突 | `04_slot_conflict` | `pending_confirmation`，18:00→19:00，原要求保留 | `examples/04_slot_conflict/output.json` |
| 重复提交 | `05_duplicate` | 第二次 `duplicate=true`，草稿数 1 | `examples/05_duplicate/output.json`、`drafts.json` |
| 冲突后重复提交 | `07_conflict_duplicate` | 复用同一 `pending_confirmation` 草稿 | `examples/07_conflict_duplicate/output.json` |
| 保存失败 | `06_save_failure` | `save_failed`，`draft.ok=false`，`storage_unavailable` | `examples/06_save_failure/output.json` |
| 批量无冲突 | `08_batch_normal` | M06 10:00，M08 10:30，批次 `pending_confirmation` | `examples/08_batch_normal/output.json` |
| 批量部分失败 | `09_batch_mixed` | M02 `needs_input`、M03 `blocked`、M06 `completed`，批次 `partial` | `examples/09_batch_mixed/output.json` |
| 批量重复素材 | `tests/test_batch.py` | 第二条 `skipped_duplicate`，草稿数 1 | pytest |
| 硬性要求不被擅改 | `04` / `07` | `requested_time=18:00`，`slot=19:00`，`changed_from_request=true` | `output.json` |
| 不越权发布 | 全部 | `published=false`，无 publish 工具 | 全部 `output.json` |
| 步数上限 | `tests/test_cli.py` | `max_steps`，退出码 3 | pytest |
| 两条决策路径 | `01` vs `04` | 正常：`completed`；冲突：`pending_confirmation` | 两份 `trace.md` |

## 3. 真实模型运行记录

以下均为真实 DeepSeek 调用，不是 Mock。

### 3.1 正常路径：M06，10:00 可用

`run-20261028-041314-28f214`，5 步：

1. `get_material(M06)` → 字段完整；
2. `check_rules(M06)` → `blocked=false`；
3. `query_slots(2026-11-02, douyin, 30, 10:00)` → `available=true`；
4. `save_draft(..., slot=10:00, draft_status=ready)` → `D-897a51928e`；
5. `finish(completed)`。

结果：`completed`，`draft.ok=true`，`published=false`。  
证据：[`examples/01_normal/trace.md`](../examples/01_normal/trace.md)

### 3.2 冲突路径：M01，要求 18:00，18:00-19:00 占用

`run-20261028-041230-348e12`，5 步：

1. `get_material(M01)` → 完整；
2. `check_rules(M01)` → 通过；
3. `query_slots(2026-11-01, douyin, 30, 18:00)` → `available=false`，冲突 `M00 18:00-19:00`，最近可用 `19:00`；
4. `save_draft(..., slot=19:00, requested_time=18:00, draft_status=pending_confirmation)` → `D-58451013b9`；
5. `finish(pending_confirmation)`，`pending_items=["是否接受 19:00 替代 18:00"]`。

结果：`suggested_slot.start=19:00`，`requested_time=18:00`，`changed_from_request=true`，`draft.draft_status=pending_confirmation`，`published=false`。  
证据：[`examples/04_slot_conflict/trace.md`](../examples/04_slot_conflict/trace.md)

**与正常路径的差异**：同样是 5 步，但第 3 步观察不同，导致第 4 步草稿状态和第 5 步 finish 状态不同。这是题目要求的“模型根据工具结果调整动作”，不是固定分支。

### 3.3 信息缺失：M02，缺时长与版权

`run-20261028-041238-b29300`，2 步：

1. `get_material(M02)` → `missing_fields=["duration_sec","rights_status"]`；
2. `finish(needs_input)`，要求补充视频时长（秒）和版权状态。

结果：`needs_input`，无 `query_slots`、无 `save_draft`。  
证据：[`examples/02_missing_info/trace.md`](../examples/02_missing_info/trace.md)

### 3.4 规则风险：M03，版权 pending

`run-20261028-041241-e83245`，3 步：

1. `get_material(M03)` → 完整；
2. `check_rules(M03)` → `blocked=true`，R001；
3. `finish(blocked)`，要求先把版权从 `pending` 改为 `cleared`。

结果：`blocked`，无草稿。  
证据：[`examples/03_rule_risk/trace.md`](../examples/03_rule_risk/trace.md)

### 3.5 重复提交：M06 两次

第一次 `run-20261028-041253-406763`，6 步；第二次 `run-20261028-041259-9e2707`，5 步。

第一次有一个值得保留的失败：

- 第 4 步模型把 `slot` 写成 `"10:00-10:01"`；
- 工具返回 `invalid_arguments`（时间必须是 `HH:MM`）；
- 第 5 步模型改成 `"10:00"` 重试，成功保存 `D-897a51928e`；
- 第 6 步 `finish(completed)`。

第二次：

- 第 4 步 `save_draft` 返回 `duplicate=true`，同一个 `D-897a51928e`；
- 第 5 步 `finish(completed)`，message 明确“已复用已有草稿，未重复创建”。

草稿库 `examples/05_duplicate/drafts.json` 只有 1 条记录。  
证据：[`first_run_trace.md`](../examples/05_duplicate/first_run_trace.md)、[`trace.md`](../examples/05_duplicate/trace.md)

这个失败不是“演示事故”，而是真实模型参数错误 → 工具观察 → 模型修正的完整证据。

### 3.6 保存失败：M06，注入 storage_unavailable

`run-20261028-041248-ea9f94`，6 步：

1. `get_material`；
2. `check_rules`；
3. `query_slots`；
4. `save_draft` → `storage_unavailable`；
5. 模型重试 `save_draft` → 仍失败；
6. `finish(save_failed)`，message 明确“重试一次仍失败，草稿未保存，也未发布”。

结果：`save_failed`，`draft.ok=false`，`draft.error=storage_unavailable`，无 `draft_id`。  
证据：[`examples/06_save_failure/trace.md`](../examples/06_save_failure/trace.md)

### 3.7 冲突后重复提交：M01 的 19:00 待确认草稿两次

第一次 `run-20261028-041230-348e12`（`duplicate=false`）；第二次 `run-20261028-041346-e65029`（`duplicate=true`，5 步）。

第二次第 4 步 `save_draft` 返回同一个 `D-58451013b9`，`duplicate=true`；第 5 步仍以 `pending_confirmation` 结束，待确认项不变。

证据：[`examples/07_conflict_duplicate/trace.md`](../examples/07_conflict_duplicate/trace.md)

### 3.8 批量无冲突：M06 + M08 都要求 10:00

`batch-20261028-050038-87e611`，2 条，结果：

| 顺序 | 素材 | 状态 | 步数 | 建议时段 | 草稿 |
|---|---|---|---|---|---|
| 1 | M06 | `completed` | 5 | 10:00 | `D-897a51928e`，`ready` |
| 2 | M08 | `pending_confirmation` | 5 | 10:30 | `D-f226446a53`，`pending_confirmation` |

M08 第 3 步 `query_slots` 的 observation 明确包含：

```json
{
  "requested": {"start": "10:00", "available": false},
  "conflicts": [{"start": "10:00", "end": "10:01", "material_id": "M06", "title": "本批次草稿占位"}],
  "alternatives": [{"start": "10:30", "end": "10:31"}]
}
```

模型据此把 M08 改到 10:30，并因为原要求是 10:00 而标记 `pending_confirmation`。批次最终 `pending_confirmation`，两份草稿分别在 10:00、10:30，没有重叠。  
证据：[`examples/08_batch_normal/output.json`](../examples/08_batch_normal/output.json)、[`traces/*_M08*.md`](../examples/08_batch_normal/traces/)

### 3.9 批量部分失败：M02 + M03 + M06

`batch-20261028-050052-e0a855`，3 条：

| 顺序 | 素材 | 状态 | 步数 | 结果 |
|---|---|---|---|---|
| 1 | M02 | `needs_input` | 2 | 缺时长/版权，不保存 |
| 2 | M03 | `blocked` | 3 | 版权 pending，R001 |
| 3 | M06 | `completed` | 5 | 10:00 草稿 `D-897a51928e` |

批次 `status=partial`，但 M02/M03 没有阻断 M06；草稿库只有 1 条。  
证据：[`examples/09_batch_mixed/output.json`](../examples/09_batch_mixed/output.json)

## 4. 测试覆盖的边界

`tests/test_loop.py` 还覆盖了真实模型这次没有触发的防御路径：

| 测试 | 验证点 |
|---|---|
| `test_finish_before_material_is_rejected_then_recovers` | 没读素材就 finish 被拒，模型改动作后完成 |
| `test_alternative_cannot_be_completed_without_confirmation` | 替代时段不能标 `completed`，必须 `pending_confirmation` |
| `test_publish_claim_is_rejected` | message 含“已发布”被拒，模型改措辞后通过 |
| `test_max_steps_returns_explicit_incomplete` | 达到上限返回 `max_steps`，不假装完成 |
| `test_material_not_found_can_request_correct_id` | 素材不存在时返回 `needs_input`，要求正确 ID |
| `test_duplicate_conflict_proposal_reuses_one_pending_draft` | 冲突待确认草稿重复提交仍只有一份 |
| `test_batch_avoids_intra_batch_slot_conflict` | 批次内第一条占 10:00 后，第二条改到 10:30 |
| `test_batch_continues_after_missing_and_blocked` | 缺失/阻断不阻止后续正常素材 |
| `test_batch_skips_duplicate_material_without_second_draft` | 重复 material_id 只处理一次，不产生第二份草稿 |
| `test_batch_save_failure_is_not_success` | 批量保存失败返回 `save_failed`，草稿库为空 |

## 5. 失败与限制

### 5.1 真实模型观察到的问题

1. **模型名路由**：请求 `deepseek-chat`，响应 `model=deepseek-flash`。这由服务端决定，不影响调用；Trace 记录的是响应模型名。
2. **参数格式错误**：模型曾把 `slot` 写成 `10:00-10:01`。工具没有静默接受，而是返回 `invalid_arguments`；模型修正后成功。这是有意保留的可观测失败。
3. **自然语言差异**：不同运行的 `pending_items` 措辞不同（“是否接受 19:00 替代 18:00” vs 更长的解释），但语义一致；校验只要求非空和状态一致，不要求固定文案。

### 5.2 已知限制

- 批量已支持，单批最多 20 条，按输入顺序串行处理；批次内草稿占位不跨批次，没有优先级/截止时间排序；
- 同一批次内重复 `material_id` 只处理第一次，后续标记 `skipped_duplicate`；
- 30 分钟粒度，无审核缓冲、跨平台冲突、节假日；
- JSON 文件存储，无数据库事务/并发锁；
- 不接真实发布平台（题目要求只保存草稿）；
- Mock 只验证流程，不证明真实模型效果；
- 真实模型调用依赖网络和 key，评审可用 Mock 复现流程，用 `examples/` 查看真实结果。

### 5.3 没有做的事

- 没有为了“看起来像 Agent”而堆多 Agent；
- 没有把固定分支包装成 ReAct；真实 Trace 中能看到工具结果改变下一步动作；
- 没有在保存失败时返回假 `draft_id`；
- 没有把 19:00 写成“已发布”或“已完成”。

## 6. 复现命令

Mock：

```powershell
python -m src.main `
  --input examples/04_slot_conflict/input.json `
  --provider mock --scenario conflict `
  --store runtime/repro.json --reset-store `
  --trace runtime/repro_trace.json
```

真实模型：

```powershell
$env:LLM_API_KEY="sk-..."
python -m src.main `
  --input examples/04_slot_conflict/input.json `
  --provider deepseek --model deepseek-chat `
  --store runtime/repro_real.json --reset-store `
  --trace runtime/repro_real_trace.json
```

测试：

```powershell
python -m pytest -q
```
