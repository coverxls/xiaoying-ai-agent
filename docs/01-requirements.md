# 01 · 需求

## 1. 用户

内容团队的运营/审核人员。他们手上有待发布素材，需要在已有排期中找一个不冲突的时段，并且清楚知道哪些素材有风险、哪些时间需要自己确认。

## 2. 目标

对单个素材完成一次“审核 + 排期草稿”：

- 找出素材信息缺口，不猜；
- 找出规则风险，阻断有硬风险的素材；
- 找出时段冲突，给出最近可用替代时段；
- 涉及硬性发布时间变更时，只保存待确认草稿，不擅自改要求；
- 相同请求重复提交只保留一份草稿；
- 保存失败必须明确失败；
- 全程不发布，只保存草稿。

## 3. 输入

### 3.1 用户输入

```json
{
  "material_id": "M01",
  "requested_publish_at": "2026-11-01T18:00:00+08:00",
  "notes": "可选说明"
}
```

- `material_id`：必填，对应 `data/materials.json`。
- `requested_publish_at`：可选。若提供，视为用户本次的发布时间要求；若省略，使用素材元数据里的 `required_publish_at`。
- `notes`：可选，仅作为上下文，不改变硬规则。

### 3.2 素材与规则

- `data/materials.json`：素材 ID、标题、类型、时长、平台、期望时间、是否硬性要求、版权状态、分辨率、描述、标签。
- `data/rules.json`：版权状态、敏感词/绝对化用语、平台时长上限、分辨率建议、必填字段。
- `data/schedule.json`：已有排期、时段粒度、每日可排范围、时区。

### 3.3 工具

| 工具 | 作用 |
|---|---|
| `get_material` | 读取素材，返回 `missing_fields` |
| `check_rules` | 返回 `blocked`、`risks`、`warnings` |
| `query_slots` | 返回目标时段是否可用、冲突、替代时段 |
| `save_draft` | 只保存草稿；幂等；可注入失败 |

## 4. 输出

每次运行输出一个 JSON：

```json
{
  "status": "completed | pending_confirmation | needs_input | blocked | save_failed | max_steps | invalid_model_output",
  "material_id": "M01",
  "audit": {"status": "passed | blocked | not_checked", "risks": [], "warnings": [], "missing_fields": []},
  "suggested_slot": {"date": "...", "start": "...", "end": "...", "requested_time": "...", "changed_from_request": true},
  "pending_items": ["..."],
  "draft": {"ok": true, "draft_id": "...", "duplicate": false, "draft_status": "ready | pending_confirmation", "published": false},
  "steps": 5,
  "trace_path": "examples/.../trace.json",
  "message": "给用户看的中文结论",
  "published": false
}
```

失败时 `draft` 为 `null` 或 `{"ok": false, "error": "...", "message": "..."}`，不得出现虚假的 `draft_id`。

## 5. 范围

### 5.1 本次实现

- 单素材或同一批次多素材；批量按输入顺序串行处理；
- CLI（`python -m src.main`）；
- 本地 JSON 数据与本地 JSON 草稿库；
- DeepSeek（OpenAI 兼容）真实模型 + 离线 Mock；
- 8 组合成素材、规则、排期；
- 结构化 Trace（JSON + Markdown）；
- pytest 单元/集成/CLI 测试。

### 5.2 非目标

- 不接真实抖音/小红书/B站发布接口；
- 不做 UI、登录、多租户、线上部署；
- 不做多 Agent 协作；
- 不做完整数据库与并发调度；
- 不做跨平台联合排期、优先级排序和跨批次占位。

这些不是题目必需项；在 3 小时约束下，优先把决策、确认边界、幂等和失败语义做扎实。

## 6. 成功标准

### 6.1 功能成功标准

1. 正常素材：规则通过、原时段可用、保存 `ready` 草稿，状态 `completed`。
2. 信息缺失：不查时段、不保存，状态 `needs_input`，列出缺失字段。
3. 规则阻断：不查时段、不保存，状态 `blocked`，风险带规则 ID。
4. 时段冲突：建议最近可用时段，保存 `pending_confirmation` 草稿，保留原要求，状态 `pending_confirmation`。
5. 重复提交：相同请求只保留一份草稿，第二次 `duplicate=true`。
6. 保存失败：状态 `save_failed`，`draft.ok=false`，不得声称成功。
7. 达到步数上限：状态 `max_steps`，明确“未完成”。
8. 批量排期：同一批次内先保存的草稿占用时段；后一个素材查询时必须看到该占位，自动改到下一个可用时段，不能产生两个同平台同时段的草稿。
9. 批量部分失败：某条 `needs_input`、`blocked` 或 `save_failed` 不应阻止后续素材继续处理；总结果标记 `partial` 并保留每条状态。
10. 批量重复素材：同一 `material_id` 第二次出现标记 `skipped_duplicate`，复用第 1 条结果，不重复创建草稿。
11. 任何路径都不得出现“已发布/发布成功”，输出 `published=false`。

### 6.2 工程成功标准

- `pytest -q` 全绿；
- CLI 可从 README 命令复现；
- 每条真实运行有输入、输出、Trace；
- 不提交真实密钥；
- 文档说明取舍、失败与限制。

## 7. 验收矩阵

| 验收点 | 用例 | 预期 | 证据 |
|---|---|---|---|
| 正常排期 | `examples/01_normal` | `completed`，`ready` 草稿 | `output.json` / `trace.md` |
| 信息缺失 | `examples/02_missing_info` | `needs_input`，无草稿 | `output.json` |
| 规则风险 | `examples/03_rule_risk` | `blocked`，R001 | `output.json` |
| 时段冲突 | `examples/04_slot_conflict` | `pending_confirmation`，19:00 | `output.json` |
| 重复提交 | `examples/05_duplicate` | 第二次 `duplicate=true`，1 份草稿 | `output.json`、`drafts.json` |
| 冲突后重复提交 | `examples/07_conflict_duplicate` | 复用同一待确认草稿 | `output.json` |
| 保存失败 | `examples/06_save_failure` | `save_failed`，`draft.ok=false` | `output.json` |
| 批量无冲突 | `examples/08_batch_normal` | 第一条 10:00，第二条 10:30，`pending_confirmation` | `output.json`、`traces/*_M08` |
| 批量部分失败 | `examples/09_batch_mixed` | `partial`，M06 仍完成，1 份草稿 | `output.json` |
| 批量重复素材 | `tests/test_batch.py` | 第二条 `skipped_duplicate`，1 份草稿 | pytest |
| 步数上限 | `tests/test_cli.py::test_cli_max_steps_exit_code` | `max_steps` | pytest |
| 两条动态路径 | 01 vs 04 | 工具结果不同 → 动作不同 | 两份 `trace.md` |

## 8. 假设

- 时区：`Asia/Shanghai`；输入时间带 `+08:00`。
- 时段粒度：30 分钟；素材时长按分钟向上取整。
- “硬性要求”指 `hard_requirement=true`；替代时段必须用户确认。
- 草稿不是发布；`published` 永远为 `false`。
- 规则风险分 `blocking` 与 `warning`；`blocking` 阻断，`warning` 仅提示。
- 批量按输入顺序处理；同一 `material_id` 只处理第一次，后续标记 `skipped_duplicate`。
- 批次内草稿占位只在当前进程有效，不跨批次持久化。

## 9. 关键边界

**“建议 19:00”和“改到 19:00”不是一回事。** 本实现保存的是 `pending_confirmation` 草稿：`requested_time` 仍是 18:00，`slot` 是 19:00，`changed_from_request=true`，`pending_items` 要求用户确认。没有用户确认，不会变成 `completed`，也不会发布。
