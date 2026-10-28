# 素材审核与发布排期 Agent

> 小影科技 2027 校招 AI Agent 笔试题 · 第 1 题  
> 只保存草稿，不接真实发布。模型可以建议替代时段，但不能擅自改硬性发布时间。

## 1. 项目做什么

内容团队给一个素材 ID 和期望发布时间，Agent 依次完成：

1. 读取素材元数据，发现缺失字段就停下来要用户补充；
2. 检查版权、敏感词、平台时长、分辨率等规则；
3. 查询目标时段是否被占用；
4. 冲突时选择最近可用时段，保存为 `pending_confirmation` 草稿；
5. 重复提交时复用已有草稿，不重复创建；
6. 保存失败时明确返回 `save_failed`，不虚报成功。

最终输出：审核状态、风险原因、建议时段、待确认项、草稿保存状态。所有输出 `published=false`。

## 2. 快速运行

### 2.1 环境

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q
```

当前验证环境：Python 3.13.16、pytest 9.2.0。

### 2.2 离线 Mock（零密钥，先验证流程）

```powershell
python -m src.main `
  --input examples/01_normal/input.json `
  --provider mock --scenario normal `
  --store runtime/demo.json --reset-store `
  --trace runtime/demo_trace.json
```

`mock` 只验证循环、工具和状态机，**不证明真实模型决策效果**。真实模型记录见 `examples/`。

批量输入可以是 JSON 数组，也可以是 `{"materials":[...]}`：

```powershell
python -m src.main `
  --input examples/08_batch_normal/input.json `
  --provider mock `
  --store runtime/batch.json --reset-store `
  --trace runtime/batch_trace.json
```

同一批次内，先保存的草稿会占用时段，后一个素材查询时会看到“本批次草稿占位”，自动改到 10:30，并标记 `pending_confirmation`。

### 2.3 真实 DeepSeek

复制 `.env.example` 为 `.env`，填入自己的 key：

```env
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-...
```

```powershell
python -m src.main `
  --input examples/04_slot_conflict/input.json `
  --provider deepseek --model deepseek-chat `
  --store runtime/real.json --reset-store `
  --trace runtime/real_trace.json
```

也可以不写 `.env`，直接设置环境变量 `LLM_API_KEY`。不要把真实 key 提交到仓库。

## 3. 评审入口

先看这几组，能覆盖最关键的决策差异：

| 入口                                                       | 看什么                                                 |
| -------------------------------------------------------- | --------------------------------------------------- |
| [`examples/01_normal`](examples/01_normal)               | 正常排期：10:00 可用，保存 `ready` 草稿                         |
| [`examples/04_slot_conflict`](examples/04_slot_conflict) | 18:00 被占用，改建议 19:00，保存 `pending_confirmation`，不称已发布 |
| [`examples/05_duplicate`](examples/05_duplicate)         | 相同请求第二次保存，`duplicate=true`，只保留一份草稿                  |
| [`examples/06_save_failure`](examples/06_save_failure)   | 保存失败，返回 `save_failed`，`draft.ok=false`              |
| [`examples/08_batch_normal`](examples/08_batch_normal)   | 批量两条争用 10:00：第一条占 10:00，第二条自动后移 10:30               |
| [`examples/09_batch_mixed`](examples/09_batch_mixed)     | 批量中一条缺失、一条阻断，后续正常素材仍继续完成                            |

完整测试与失败分析见 [`docs/03-evaluation.md`](docs/03-evaluation.md)。运行记录同时提供 `trace.json`（机器可读）和 `trace.md`（表格可读）。

## 4. 验收点对照

| 必须验收点   | 用例                          | 实际结果                                       | 证据                                                                 |
| ------- | --------------------------- | ------------------------------------------ | ------------------------------------------------------------------ |
| 正常排期    | M06 / 2026-11-02 10:00      | `completed`，草稿 `ready`                     | `examples/01_normal/output.json`                                   |
| 素材信息缺失  | M02 缺时长、版权                  | `needs_input`，不保存草稿                        | `examples/02_missing_info/output.json`                             |
| 规则风险    | M03 版权 `pending`            | `blocked`，引用 R001                          | `examples/03_rule_risk/output.json`                                |
| 时段冲突    | M01 要求 18:00，18:00-19:00 占用 | `pending_confirmation`，建议 19:00，原要求保留      | `examples/04_slot_conflict/output.json`                            |
| 重复提交    | 同一 M06 请求保存两次               | 第二次 `duplicate=true`，草稿数仍为 1               | `examples/05_duplicate/`                                           |
| 冲突后重复提交 | M01 的 19:00 待确认草稿再提交        | 复用同一待确认草稿                                  | `examples/07_conflict_duplicate/`                                  |
| 保存失败    | 注入 `storage_unavailable`    | `save_failed`，不虚报成功                        | `examples/06_save_failure/output.json`                             |
| 批量无冲突   | M06 + M08 都要求 10:00         | 第一条 10:00，第二条 10:30，`pending_confirmation` | `examples/08_batch_normal/output.json`                             |
| 批量部分失败  | M02 缺失 + M03 阻断 + M06 正常    | 批次 `partial`，M06 仍完成，只有 1 份草稿              | `examples/09_batch_mixed/output.json`                              |
| 批量重复素材  | 同一 M06 出现两次                 | 第二条 `skipped_duplicate`，只保留 1 份草稿          | `tests/test_batch.py`                                              |
| 不得越权发布  | 所有路径                        | 工具层没有 publish；输出 `published=false`         | 全部 `output.json`                                                   |
| 步数上限    | 强制 `--max-steps 1`          | `max_steps`，明确“未完成”                        | `tests/test_cli.py`                                                |
| 两条动态路径  | 正常 vs 冲突                    | 工具结果不同 → 下一步动作不同                           | `examples/01_normal/trace.md`、`examples/04_slot_conflict/trace.md` |

## 5. 设计要点

### 5.1 真 ReAct，不是固定分支

每一轮模型只输出一个 JSON 动作：

```json
{"action":"query_slots","args":{...},"decision_basis":"为什么查这个时段"}
```

循环执行“模型选动作 → 执行工具 → 观察结果 → 更新状态 → 模型再决策”。`decision_basis` 是模型给出的简短决策依据，不记录隐藏思维过程。

### 5.2 三层防越权

1. **工具层**：只有 `save_draft`，没有 publish；`ready` 草稿不允许落在非原要求时段。
2. **状态层**：保存后写 `published=false`；冲突时草稿状态必须是 `pending_confirmation`。
3. **收尾校验层**：`finish` 会被检查。提前结束、把替代时段标成 `completed`、把保存失败说成成功、声称“已发布”，都会被拒绝并让模型重试。

### 5.3 幂等与确认边界

- 幂等键：`material_id + date + slot + requested_time + draft_status` 的 SHA-256 前缀。
- 相同请求第二次保存返回 `duplicate=true`，复用同一个 `draft_id`。
- 冲突建议 19:00 时，草稿保留 `requested_time=18:00`，`draft_status=pending_confirmation`，输出 `changed_from_request=true`；不会把 18:00 改成 19:00 后称已完成。

### 5.4 为什么不用 LangGraph / ADK

每个素材仍是一个独立 Agent 循环、四个工具、一个明确状态机；批量由 `BatchRunner` 串行调度，不是多 Agent 协作。原生实现让循环、状态和校验都可见，便于在 3 小时内解释和测试。框架会带来额外抽象和版本成本，但不会自动解决“确认边界”“幂等”“批次内占位”这几个真正的难点。代价是持久化、并发和可观测性需要自己做；当前用 JSON 文件 + Trace 满足题目范围。

## 6. 项目结构

```text
src/
  main.py                 # CLI（单素材 / 批量）
  agent/
    loop.py               # 单素材 ReAct 循环 + finish 校验
    batch.py              # 批量执行器 + 批次内草稿占位
    tools.py              # 素材/规则/时段/草稿工具
    store.py              # JSON 草稿库 + 幂等键
    llm.py                # DeepSeek 客户端 + 解析
    prompt.py             # 系统提示词与状态摘要
    trace.py              # JSON/Markdown Trace
    mock_scripts.py       # 离线 Mock 脚本 + 批量 PolicyMock
data/
  materials.json          # 8 个合成素材
  rules.json              # 规则
  schedule.json           # 已有排期
tests/                    # 33 个 pytest
examples/                 # 9 组输入、真实输出、Trace（含 2 组批量）
docs/01-requirements.md
docs/02-solution.md
docs/03-evaluation.md
docs/04-ai-usage.md
docs/05-retrospective.md
```

## 7. 已知限制

- 批量已支持，单批最多 20 条。
- 时段模型是 30 分钟粒度，不处理跨平台并发、审核缓冲、节假日等真实约束。
- 草稿存储是单机 JSON 文件，没有数据库事务和并发锁。
- 未接真实发布平台；这是题目要求，不是缺失。
- 真实模型可能把 `slot` 写成 `10:00-10:01` 这样的区间。工具会返回 `invalid_arguments`，模型在 Trace 中修正后重试；这类“模型犯错—观察—修正”恰好是 ReAct 的证据，不应被隐藏。
- Mock 只证明流程，不证明真实模型效果；真实模型效果以 `examples/` 中 DeepSeek 的记录为准。

## 8. 分工与 AI 使用

本项目由候选人确定选题、范围、技术路线和验收标准，并提供运行环境与 GitHub 仓库；AI 助手完成代码、测试、合成数据、文档初稿与命令执行。候选人审阅关键设计并对最终提交负责。

具体分工、关键设计与验证记录见 [`docs/04-ai-usage.md`](docs/04-ai-usage.md)。仓库不包含真实密钥；真实运行通过本机环境变量或本地凭据文件注入。
