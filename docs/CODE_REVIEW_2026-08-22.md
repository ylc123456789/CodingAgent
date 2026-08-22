# CodingAgent 代码审查与整理报告（2026-08-22）

分支：`codex/readability-cleanup`（基于 `main` @ `c304962`，未合并）
范围：四模块整理计划 `ResAgent/docs/active/FOUR_MODULE_CODE_REVIEW_AND_SIMPLIFICATION_PLAN.md` 中 CodingAgent 部分
结果：清理提交 8 个 + 正确性修复提交 3 个 + 本报告，全部测试通过。

## 1. 主流程与公共入口

公共入口（`src/coding_agent/__init__.py`，由 `tests/test_phase0_contract.py` 锁定 `__all__`）：

- `run_code_task(CodeTaskSpec) -> PatchReport` —— 通用代码任务
- `run_code_question(CodeQuestionSpec) -> CodeExplanation` —— 只读代码问答（内部包装为 read_only CodeTaskSpec，复用同一 loop）
- `resume_code_task(output_dir, instruction, **overrides) -> PatchReport` —— 会话续跑
- `list_sessions` / `read_session_card` / `session_status` —— 会话卡管理
- 跨模块引用面：ResAgent adapter 使用上述入口 + `resources.delete_environment`（cleanup）；reproagent 经 `run_code_task` + `env_policy/env_name` 委托。

主流程（唯一核心 loop，standalone / 委托 / QA / resume 共享）：

```text
run_code_task / run_code_question / resume_code_task
  -> _prepare_workspace (repo_url -> clone)  -> _prepare_environment (resource_root -> 建/绑环境)
  -> controller.loop.run_step_controller
       choose_next_action (LLM 单步决策) -> execute_action (11 动作) -> repair (失败修复)
  -> session.write_session_card -> PatchReport / CodeExplanation
```

## 2. 本轮删除内容及证据

| 内容 | 证据 | 提交 |
|---|---|---|
| 死 import ×9（`loop.py:4-7` json/subprocess/sys/Path、`actions.py:4,9` json/extract_patch_paths、`prompts.py:5` Path、`repair.py:9,13` normalize_patch_text/SafetyError） | 逐模块 AST 引用扫描（stdlib ast），每个符号零 Name/Attribute 引用；删除后全量测试通过 | `8eeda02` |
| `PatchRepairResponse.patch` 字段 | 全仓 grep：`.patch` 仅被动作层 `action.patch` 与日志文件名使用，`repaired.patch` 零读取；pydantic 对未声明字段默认忽略，删除无行为变化 | `df628fb` |
| `resources.sha256_hex_bytes` | 全仓 grep：唯一引用是它自己的测试断言；生产代码在 vendored 契约内联 `hashlib.sha256`。原测试改写为直接断言生产收集路径的原始字节哈希，守卫性质保留 | `82a4bb3` |
| `context/builder._git_diff` 重复实现 | 与 `runtime/apply.current_diff` 同为 `git diff --no-ext-diff` 双实现；`current_diff` 增加可选 `timeout` 参数后合并 | `e34f1ab` |
| `_conda_executable` 冗余校验循环 | 所有候选在追加前已 `is_file()` 校验，末尾循环恒真 | `b408022` |

未删除但记录的冗余（保留理由见第 5 节）：`mirror_profile`/`pip_index_profile` 双字段映射、`_git_head`/`_git_info` 双助手、`CodeQuestionSpec` 调优字段重复。

## 3. 正确性修复（C1/C2/C3）

### C1 收尾验证继承环境绑定

- 位置：`controller/actions.py::_run_missing_finish_verification`
- 根因：finish 前自动验证调用 `run_verify_commands` 时未传 `spec.env_name` / `spec.env_policy`，而同文件的 `run_command` 分支（:132）传了——委托 frozen 模式下验证会跑在宿主环境而非绑定 conda 环境。
- 修改：补传两个字段，与 `run_command` 分支完全一致；环境解析仍只在 `run_verify_commands` 一处，未复制逻辑。
- 测试：`tests/test_finish_verify_env.py` —— 断言自动验证收到 `env_name/env_policy`（含空绑定默认值回归）。
- 提交：`ddf17ec`

### C2 resume 保留初始差异

- 位置：`controller/loop.py`（loop 入口）
- 根因：`write_initial_diff` 无条件执行；resume 时 workspace 已含上次修改，会把任务真正基线 diff 覆盖为当前 diff，丢失证据。
- 修改：`resume_state is None` 时才写 `initial_diff.patch`，resume 保留首跑产物。
- 测试：`tests/test_resume_initial_diff.py` —— 首跑（编辑→ask_user 暂停）→ resume（finish），断言 `initial_diff.patch` 内容不变，且当时 current diff 非空（证明覆盖场景真实存在）。
- 提交：`cddabf2`

### C3 验证失败不得报告完成

- 位置：`controller/loop.py::_final_status` + finish 分支；`reviewer.py::review_outcome`
- 根因：两层都允许"LLM 请求 completed"或"预算耗尽路径"覆盖确定性验证证据——上层系统会把带失败验证证据的任务误判为成功。
- 修改（最小语义）：
  - `_final_status`：请求 `completed` 且存在"实际执行且失败"的验证命令 → 返回 `failed`；其余请求状态（failed/blocked/needs_user_input）原样透传；无验证结果时保持原行为。
  - finish 分支：降级时在 residual_risks 追加一条说明（"N verification command(s) failed; final status downgraded ..."）。
  - `review_outcome`：存在失败验证 → `status="failed"` 并调整 summary；无验证命令时保持原有 completed+note 行为（不扩大修改范围）。
- 测试：`tests/test_reviewer.py` 修正锁定旧行为的断言并补无验证保持 completed 的回归；`tests/test_finish_semantics.py` 四例（自动验证失败降级 / 显式验证失败降级 / 无验证保持 completed / 通过保持 completed）。
- 提交：`7e15c3d`（含 README 状态语义同步）

## 4. 测试结果

| 项目 | 结果 |
|---|---|
| 全量单元测试 | **183/183 通过**（整理前 172；新增 reviewer 4 + C1 2 + C2 1 + C3 4） |
| 契约锁 | `test_phase0_contract.py` 通过——`__all__`、QA_SYSTEM/ACTION_SCHEMA/QA_ACTION_SCHEMA 哈希、公共模型字段列表未变 |
| vendored 契约 | `test_vendor_contract.py` 通过（sha256 字节一致） |
| 公共入口导入 | 全部公共 + 内部入口导入正常；`typing.get_type_hints` 可解析 |
| 真实 API 冒烟 | `scripts/deepseek_smoke.py`：`status=completed`，验证 exit 0，diff 正确 |
| `git diff --check` | clean |
| 依赖 | `pyproject.toml` / `agent.yaml` 零变更 |

## 5. 未处理项及风险

| 项 | 严重度 | 状态 |
|---|---|---|
| `review_outcome` 状态语义 | — | 已由 C3 任务单裁决（失败验证 → failed），不再挂起 |
| `delete_environment` 未校验 `env_id` 目录名 | 低 | 记录不处理：本模块 writer 只产生合法值，调用方（ResAgent cleanup）取自查验过的 `inspect_environments` 结果 |
| `mirror_profile`/`pip_index_profile` 双字段 + `agent.py` 映射 | 契约 | 保留：跨模块冻结契约（ResAgent 传两字段、reproagent 用 mirror 命名），映射仅 6 行已注释 |
| `_git_head`(resources) / `_git_info`(session) 双助手 | 低 | 保留：字段需求不同，合并需新共享模块，违反"不为 2 个调用点建抽象" |
| `CodeQuestionSpec` 与 `CodeTaskSpec` 调优字段重复 | 低 | 保留：合并将改变公共模型构造，被 Phase-0 契约锁禁止 |
| `write_file` 修复路径与主 `write_file` 动作的分支相似性 | 低 | 保留：修复路径有截断保护等差异语义，合并会引入行为风险 |

## 6. 验收对照（任务单）

- 全量测试通过 ✅（183/183）
- 自动验证在任务指定环境执行 ✅（C1 + 测试断言绑定传递；包装逻辑原在 `run_verify_commands` 单一位置）
- resume 后 `initial_diff.patch` 内容不变 ✅（C2 + 端到端测试）
- 验证失败时最终状态不是 completed ✅（C3 + controller/reviewer 两层测试）
- 未修改公共 Prompt、模型契约和依赖 ✅（契约锁测试通过；pyproject/agent.yaml 零 diff）
- 工作区干净、按主题提交 ✅（12 个主题提交）
- 分支已推送、未合并 main ✅
