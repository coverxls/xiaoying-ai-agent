# 04 · 分工、关键设计与 AI 使用

## 1. 项目分工

本项目由候选人负责需求、范围、技术路线、验收和提交；AI 助手负责实现初稿、测试执行与文档整理。实现初稿由 AI 完成，选题、范围、技术路线和最终验收由候选人负责。

### 1.1 候选人

- 从四道题中确定第 1 题“素材审核与发布排期”；
- 确定交付形态：Python CLI + 真实模型 API + 离线 Mock；
- 确定验收范围：正常、信息缺失、规则风险、时段冲突、重复提交、保存失败；
- 提供本机 DeepSeek 凭据，使真实模型 Trace 可以运行；
- 创建公开仓库 `coverxls/xiaoying-ai-agent`，并授权提交；
- 要求解释关键设计，并指示 AI 执行提交前验证；
- 对最终 GitHub URL、commit SHA 和提交动作负责。

### 1.2 AI 助手

- 实现 `src/` 下的 ReAct 循环、四个工具、批次执行器、JSON 草稿库、Trace 和 CLI；
- 生成 `data/` 下的合成素材、规则与排期；
- 编写 `tests/` 下的 33 个测试；
- 执行 `pytest` 和真实 DeepSeek 调用，整理 `examples/`；
- 起草 README 与 `docs/01`–`05`；
- 按候选人指示执行提交前验证，并记录结果。

## 2. 关键设计决策

| 决策   | 选择                              | 理由                          |
| ---- | ------------------------------- | --------------------------- |
| 选题   | 第 1 题：素材审核与发布排期                 | 验收点最清晰，能同时覆盖冲突、确认、幂等和保存失败   |
| 交付形态 | Python CLI + 真实 API + Mock      | 3 小时内可运行、可测试，不依赖 UI 或 GPU   |
| 冲突建议 | 优先更晚的可用时段                       | 把发布提前可能意味着素材未准备好，风险高于往后挪    |
| 重复提交 | 存储层幂等键                          | 避免“先查再写”的并发 race，重复请求复用同一草稿 |
| 保存失败 | 明确 `save_failed`，不生成 `draft_id` | 防止上层拿着不存在的草稿继续排期            |
| 批量策略 | 输入顺序串行 + 批次内草稿占位 | 同一批次不产生同时段草稿；后一个素材看到占位后自动避让 |
| 发布边界 | 只有 `save_draft`，没有 publish      | 题目要求只保存草稿，工具层直接消除越权可能       |

## 3. 三个关键行为

### 3.1 18:00 冲突后为什么建议 19:00

`data/schedule.json` 中，2026-11-01 的 18:00–19:00 已被素材 M00 占用。Agent 调用：

```json
{"date":"2026-11-01","platform":"douyin","duration_sec":30,"preferred_time":"18:00"}
```

`query_slots` 对请求区间和已占用区间做重叠判断，返回：

- `requested.available=false`；
- `conflicts=[{"start":"18:00","end":"19:00","material_id":"M00"}]`；
- `alternatives=[{"start":"19:00",...}, ...]`；
- `nearest_alternative="19:00"`。

替代时段的排序规则是：**优先晚于原时段的空档，再按距离排序**。17:30 虽然距离也近，但属于把发布提前，可能意味着素材还没准备好；19:00 是原时段之后最近的空档，因此作为首选建议。这个排序写在工具里，模型不需要自己猜。

### 3.2 为什么草稿状态是 `pending_confirmation` 而不是 `completed`

18:00 是用户的硬性要求（`hard_requirement=true`），19:00 只是 Agent 的建议，尚未得到用户确认。因此本实现做了三层限制：

1. `save_draft` 对 `slot != requested_time` 且 `draft_status="ready"` 的请求直接返回 `confirmation_required`；
2. 改时段只能保存为 `draft_status="pending_confirmation"`，并且 `pending_items` 必须非空；
3. `finish` 校验发现 `requested_time != suggested_time` 时，只接受 `status="pending_confirmation"`，且要求草稿状态也是 `pending_confirmation`。

最终输出保留：

```json
{
  "requested_time": "18:00",
  "suggested_slot": {"start": "19:00", "changed_from_request": true},
  "draft": {"draft_status": "pending_confirmation", "published": false},
  "status": "pending_confirmation"
}
```

本实现没有“用户确认”接口，不会把 `pending_confirmation` 自动转成已确认排期；确认动作留给上层或用户。这样不会把建议当成已确认排期，也不会出现“未经同意改了硬性发布时间”的情况。

### 3.3 为什么保存失败必须返回 `save_failed` 且没有 `draft_id`

`save_draft` 在写入前检查草稿库。`JsonDraftStore` 写入失败时抛出 `StorageError`，工具返回：

```json
{"ok": false, "error": "storage_unavailable", "message": "草稿存储不可用（注入故障）"}
```

这个分支不会生成 `draft_id`，也不会写入半条记录；JSON 存储使用临时文件 + `os.replace`，失败时原文件保持不变。

`finish` 校验随后检查：如果 `draft.ok=false`，只允许 `status="save_failed"`；标记 `completed` 或 `pending_confirmation` 都会被拒绝。模型可以重试一次，但每次失败都返回同样的错误；最终输出 `save_failed`，message 明确“未保存、未发布”。

这样做的目的是：上层不会拿到一个不存在的 `draft_id` 继续排期，也不会出现“保存失败但界面显示成功”。

## 4. 验证记录

2026-10-28 实际执行：

```text
python -m pytest
33 passed in 7.50s
```

冲突路径 Mock：

```text
status=pending_confirmation
suggested_slot.start=19:00
requested_time=18:00
changed_from_request=true
draft.draft_status=pending_confirmation
published=false
```

9 组真实 DeepSeek 运行记录在 `examples/`（7 个单素材 + 2 个批量），覆盖：

| 场景          | 结果                             |
| ----------- | ------------------------------ |
| 正常排期 M06    | `completed`，`ready` 草稿         |
| 信息缺失 M02    | `needs_input`，无草稿              |
| 规则风险 M03    | `blocked`，R001                 |
| 时段冲突 M01    | `pending_confirmation`，19:00   |
| 重复提交 M06    | 第二次 `duplicate=true`，草稿数 1     |
| 冲突后重复提交 M01 | 复用同一待确认草稿                      |
| 保存失败 M06    | `save_failed`，`draft.ok=false` |
| 批量无冲突 M06+M08 | 10:00 / 10:30，批次 `pending_confirmation` |
| 批量部分失败 M02+M03+M06 | 批次 `partial`，M06 仍完成 |

完整 Trace 与断言见 [`docs/03-evaluation.md`](03-evaluation.md) 和 [`examples/04_slot_conflict/trace.md`](../examples/04_slot_conflict/trace.md)。

## 5. AI 使用的边界

- **不记录隐藏思维链**：Trace 只保存模型显式给出的 `decision_basis`、工具参数和 observation。
- **不伪造真实模型结果**：`examples/` 中的 9 组均为真实 DeepSeek 调用；Mock 只用于离线流程和测试。
- **不伪造保存成功**：`--fail-save` 注入失败时，输出 `save_failed`，没有 `draft_id`。
- **不越权发布**：代码中没有 publish 工具；输出 `published=false`。
- **不提交密钥**：`.env` 在 `.gitignore` 中；真实 key 只在本机运行时注入，未写入仓库。
- **不替代候选人判断**：选题、业务规则合理性、最终提交由候选人负责。

## 6. 可复现性

AI 生成的真实 Trace 包含 `run_id`、时间戳、provider、model、每步参数与 observation。评审可以：

1. 用 Mock 命令复现同样的状态机（不需要 key）；
2. 看 `examples/*/trace.md` 核对真实模型的动作；
3. 用自己的 key 重跑 `--provider deepseek`，对比结果是否一致；
4. 用 `tests/` 验证边界条件。

模型输出具有随机性，重跑不保证逐字一致；但状态、草稿状态、`duplicate`、`published` 等关键字段应保持一致。
