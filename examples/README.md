# Examples

这些不是手写快照，而是真实 DeepSeek 调用的输出与 Trace。单素材用例包含：

- `input.json`：用户输入；
- `output.json`：Agent 最终输出；
- `trace.json`：机器可读的逐步记录；
- `trace.md`：可直接阅读的表格；
- 部分用例带 `drafts.json`，用于证明幂等后的草稿数量。

批量用例（`08_batch_normal`、`09_batch_mixed`）包含 `batch_trace.json` / `batch_trace.md`，以及 `traces/` 下每个素材的独立 Trace。

| 目录 | 场景 | 状态 | 步数 | 关键证据 |
|---|---|---|---|---|
| `01_normal` | M06 正常排期 | `completed` | 5 | 10:00 可用，`ready` 草稿 |
| `02_missing_info` | M02 缺时长/版权 | `needs_input` | 2 | 直接停下，无草稿 |
| `03_rule_risk` | M03 版权 `pending` | `blocked` | 3 | R001 阻断，无草稿 |
| `04_slot_conflict` | M01 要求 18:00，18:00-19:00 占用 | `pending_confirmation` | 5 | 建议 19:00，原要求保留 |
| `05_duplicate` | M06 同一请求保存两次 | `completed` | 5（第二次） | `duplicate=true`，`drafts.json` 仅 1 条 |
| `06_save_failure` | M06 注入保存失败 | `save_failed` | 6 | `draft.ok=false`，重试后仍失败 |
| `07_conflict_duplicate` | M01 19:00 待确认草稿再次提交 | `pending_confirmation` | 5（第二次） | 复用同一 `D-58451013b9` |
| `08_batch_normal` | M06 + M08 都要求 10:00 | `pending_confirmation` | 5 + 5 | M06 占 10:00，M08 自动后移 10:30 |
| `09_batch_mixed` | M02 缺失 + M03 阻断 + M06 正常 | `partial` | 2 + 3 + 5 | M06 仍完成，草稿数 1 |

## 建议阅读顺序

1. `04_slot_conflict/trace.md`：看冲突如何改变下一步，以及为什么不是 `completed`。
2. `05_duplicate/first_run_trace.md`：看模型把 `slot` 写成区间、工具报错、模型修正的真实过程。
3. `05_duplicate/trace.md`：看第二次提交如何复用草稿。
4. `06_save_failure/trace.md`：看保存失败和重试，确认没有虚报成功。
5. `08_batch_normal/traces/*_M08*.md`：看第二个素材如何发现“本批次草稿占位”，从 10:00 改到 10:30。

## 运行 ID

| 目录 | run_id |
|---|---|
| `01_normal` | `run-20261028-041314-28f214` |
| `02_missing_info` | `run-20261028-041238-b29300` |
| `03_rule_risk` | `run-20261028-041241-e83245` |
| `04_slot_conflict` | `run-20261028-041230-348e12` |
| `05_duplicate`（first） | `run-20261028-041253-406763` |
| `05_duplicate`（second） | `run-20261028-041259-9e2707` |
| `06_save_failure` | `run-20261028-041248-ea9f94` |
| `07_conflict_duplicate`（first） | `run-20261028-041230-348e12` |
| `07_conflict_duplicate`（second） | `run-20261028-041346-e65029` |
| `08_batch_normal` | `batch-20261028-050038-87e611` |
| `09_batch_mixed` | `batch-20261028-050052-e0a855` |

## 测试结果

[`test-results.txt`](test-results.txt) 是 2026-10-28 的实际 pytest 输出：`33 passed`。

## 说明

- Trace 中的 `decision_basis` 是模型显式给出的简短理由，不是隐藏思维链。
- `trace.json` 的 `final.trace_path` 已改成对应示例路径，便于评审直接定位。
- `05_duplicate/first_run_trace.md` 里的 `invalid_arguments` 是真实模型参数错误；保留它是为了证明“观察 → 修正”，不是失败掩盖。
- 所有 `output.json` 的 `published` 都是 `false`。
- 批量用例的 `batch_trace.md` 是批次汇总；每个素材的逐步决策在 `traces/` 下。
