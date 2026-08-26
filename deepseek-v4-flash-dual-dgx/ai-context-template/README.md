# 项目持久上下文（.ai/）模板

> 用途：为每个项目建立稳定的 AI 上下文文件，配合 prefix/radix cache 提升命中率。
> 使用：将 `.ai/` 目录复制到你的项目根目录，按项目实际情况填写。
> 注意：**内容顺序保持稳定**（system → 项目背景 → 规则 → 历史 → 任务），不要每轮随机改变顺序，否则会降低 cache 命中率。

## 文件清单

| 文件 | 内容 | 更新频率 |
|---|---|---|
| README.md | 项目是什么、模块、目录结构、入口、运行/测试方式 | 低 |
| architecture.md | 架构、依赖关系、数据流、关键接口 | 中 |
| constraints.md | 硬性约束（如 AST-driven、禁 hard-code、向后兼容） | 低 |
| coding-rules.md | 编码风格与规范 | 低 |
| decisions.md | ADR 决策记录（ADR-001, ADR-002...），防止 Agent 推翻既有决策 | 每次决策 |
| current-status.md | 已完成什么 / 当前在哪 / 下一步（每次较大工作后更新） | 高 |
| known-issues.md | 已知问题与 workaround | 中 |
| glossary.md | 项目术语表 | 低 |

## OpenCode 使用建议（结合本部署）

- 日常小任务：`#fast` variant（thinking off，低延迟）
- Debug/设计/代码 review：`#think`
- 架构级/重大重构：`#max`（少用）
- 并发默认 1~2，大型 review 才用 3~4
- 长上下文先 64K，验证稳定后再考虑 128K
  - ⚠️ 当前已知问题：>32K prompt 会触发 sparse_mla CUDA 崩溃（见 optimization-report），64K/128K 需等 Phase D 调参解决

## Agent 分工

- **Planner**：只读分析、方案、任务拆解，不写代码
- **Builder**：实现、测试、修复
- **Reviewer**：git diff 审查、架构约束检查、回归风险、测试覆盖
