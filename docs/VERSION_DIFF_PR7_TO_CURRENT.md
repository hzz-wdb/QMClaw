# 版本对比报告：当前版本 vs 上一次 PR（PR #7）

> 生成时间：2026-10-31
> 对比基准：PR #7 的 head = `0a04184`（"上一次 PR 的版本"）
> 当前版本：`b4904cc`（分支 `feat/agent-image-tools`）
> 数据来源：`git log/diff/numstat`、`git ls-remote`、GitHub API（PR #7 状态）

---

## 0. 结论先行

| 项 | 结果 |
|---|---|
| **PR #7 状态** | ✅ 已被导师合并（merge commit `83ce3b9`，2026-10-14） |
| **对比基准** | PR #7 的 head = `0a04184` |
| **当前版本** | `b4904cc`（已推送 `hzz-wdb/QMClaw:feat/agent-image-tools`） |
| **新增内容** | **6 个提交 / 8 个文件 / +1363 −14** |
| **核心变化** | agent 工具数 **6 → 12**（工作流 4 + 图像 2）；测试 **82 → 107（+25，全绿）** |
| **前端/配置改动** | **0**（两个功能均为纯后端接线 + 系统提示） |

---

## 1. 版本树（谁基于谁）

```
e3cb8fa  ← 导师 main 最新（PR #8，来自 LZLDS）
83ce3b9  Merge pull request #7 from hzz-wdb/main   ← 本仓库 PR 已被合并
0a04184  ← PR #7 的 head = 上一次 PR 的版本（对比基准）
   │
   ├─ ef9428b  2026-10-22  feat(agent): 接入 workflow 工具组（list/run/status/stats）
   ├─ 571eced  2026-10-22  docs: 记录 agent workflow 工具接线与契约修复
   ├─ 03c90a8  2026-10-29  fix(agent): workflow_status 回传节点图与指标（v1.1）
   ├─ 0d4a4a7  2026-10-29  docs: 记录 workflow 结果带图修复
   ├─ b6d016f  2026-10-29  feat(agent): 接入 image 分析工具组（接口层）
   └─ b4904cc  2026-10-29  docs: 记录 image 工具接线与 VLM 前置条件   ← 当前 HEAD
```

远端分支现状：

| 分支 | SHA | 说明 |
|---|---|---|
| `hzz-wdb/QMClaw:main` | `0a04184` | = PR #7 版本（已被导师合并） |
| `hzz-wdb/QMClaw:feat/agent-workflow-tools` | `0d4a4a7` | 工作流功能（4 提交） |
| `hzz-wdb/QMClaw:feat/agent-image-tools` | `b4904cc` | 当前分支（含 image，2 提交） |

---

## 2. 提交清单（6 个，2 个功能块）

| # | 提交 | 日期 | 内容 |
|---|---|---|---|
| 1 | `ef9428b` | 2026-10-22 | **feat**：agent 接入 workflow 工具组（4 个工具，create/cancel 不暴露） |
| 2 | `571eced` | 2026-10-22 | docs：接线与契约修复记录 |
| 3 | `03c90a8` | 2026-10-29 | **fix(v1.1)**：`workflow_status` 回传节点图与指标 |
| 4 | `0d4a4a7` | 2026-10-29 | docs：结果带图修复记录 |
| 5 | `b6d016f` | 2026-10-29 | **feat**：agent 接入 image 分析工具组（接口层，2 个工具） |
| 6 | `b4904cc` | 2026-10-29 | docs：VLM 前置条件记录 |

---

## 3. 文件级差异（`git diff 0a04184..b4904cc`）

| 文件 | +/− | 性质 | 内容 |
|---|---|---|---|
| `services/common/quantum_tools/image_tools.py` | **+577** | 新增 | `image_analyze`（7 种 task、路径/自动最新图、原图回显、401 人话错误）+ `image_list_plots` |
| `tests/test_agent_image_tools.py` | **+298** | 新增 | 15 例（全 mock HTTP，不依赖 VLM） |
| `tests/test_agent_workflow_tools.py` | **+264** | 新增 | 10 例（子集过滤 / guard / 契约容错 / 产物提升） |
| `services/common/quantum_tools/workflow_tools.py` | **+100 / −11** | 修改 | ①`RunWorkflowTool` 加 `guard` 预检；②`setup_workflow_tools` 加 `tools` 子集过滤 + `run_guard`；③`_attach_node_artifacts`（节点 plotPath/metrics/dataset_id 提升）；④status/stats 契约容错 |
| `services/agent_service/adapter.py` | **+57 / −3** | 修改 | 新增 `WORKFLOW_SERVICE_URL`、`QUBITCLIENT_SERVICE_URL`、`workflow_run_guard`、两处注册（工具 6→12）、Adapter 参数透传 |
| `services/agent_service/server.py` | **+18** | 修改 | 系统提示追加「工作流能力 5–8」「图像分析能力 9–11」 |
| `.claude/progress.md` | +43 | 台账 | 两轮开发与验收记录 |
| `docs/BACKLOG.md` | +6 | 台账 | I8（workflow 契约）、I10–I13（VLM 前置 / 假绿 / 分类缺模型 / analyze_full 耗时） |

总计：**8 files changed, 1363 insertions(+), 14 deletions(-)**

---

## 4. 能力差异对比

| 能力 | **PR #7 版本**（`0a04184`） | **当前版本**（`b4904cc`） |
|---|---|---|
| agent 工具总数 | **6**（仅 quantum：get_qubits / get_qubit_params / list_experiments / run_code / run_experiment / list_history） | **12**（+ workflow 4 + image 2） |
| 对话里列出/运行工作流 | ❌ 不支持（工具不存在） | ✅ `workflow_list / workflow_run / workflow_status / workflow_stats` |
| 工作流写权限控制 | — | ✅ `run_guard`：离线/开闸放行；**在线 + 写闸门关闭 → 提前拒绝并解释** |
| 工作流创建/取消 | — | ❌ 刻意不暴露（缩小权限面） |
| `workflow_status/stats` 可用性 | ⚠️ 工具存在但**必然失败**（硬要求 `success` 字段，服务端不返回；Hermes 同病） | ✅ 契约容错修复（有 `success` 或带业务键即成功） |
| 工作流结果回显图/指标 | ❌ `charts=0`（图埋在 `nodes[].result.plotPath`） | ✅ 节点产物提升 → 对话带图（live 实测 `charts=1`）+ 数值进 `results` |
| 图像分析 | ❌ 无任何 image 工具 | ✅ `image_analyze`（describe/classify/reasoning/assess_fit/extract_params/evaluate/analyze_full；路径或自动最新图；原图回显；**401 时给可操作的人话错误、不编造**） |
| 列出最近实验图 | ❌ | ✅ `image_list_plots`（含实验族推断，live 可用） |
| 前端改动 | — | **0**（两功能都走现有 `charts`/`results` 契约） |
| 配置/依赖改动 | — | **0** |
| 测试数 | **82 passed** | **107 passed / 0 failed**（+25：workflow 10 + image 15） |

### 关键 live 验收证据（当前版本）

- `/api/agent/tools` → **12 个**，含 `workflow_*` 4 个与 `image_analyze` / `image_list_plots`
- 对话「运行工作流 wf_xxx」→ `workflow_run` 成功（离线 t1：T1≈8.00 µs）
- 对话「跑完了吗/节点状态+统计」→ `workflow_status` + `workflow_stats` 返回 completed，**charts=1**（`/plots/offline_sim_t1_*.png`）
- 对话「列出最近的实验图」→ `image_list_plots`：10 张图 + 族推断（t1）
- 对话「分析最新那张实验图」→ `image_analyze` 真实连上 `:3010` → 返回 **401 的人话错误**且拒绝编造（证明**接口链路通，缺的是上游 VLM 配置**）

---

## 5. 导师侧在这期间也前进了（需要并入）

PR #7 合并后，导师 main 又合了 **PR #8**（作者 `LZLDS`），共 4 个提交：

| 提交 | 内容 |
|---|---|
| `b6719fb fix(config)` | 离线数据路径改回相对路径（与我们已做的一致，**无差异**） |
| `1c42b91 fix(qubitclient)` | **删除两个非法实验族 id**：`s21peak`、`s21peakmulti`（vendor 合法 prompt 集合只有 33 个族；选中这俩会**静默回退到 rabi 模板**，给出看似合理的错误分析） |
| `c7ec2b9 chore(gitignore)` | 兜底忽略任意层级的 `qubitclient.json`（防内网地址/Key 误提交） |
| `e3cb8fa` | 上述 PR 的合并提交 |

**我们分支缺少的导师改动 = 2 个文件（+5/−2）**：
- `.gitignore`（兜底规则）
- `qubitclient_service/server.py`（删 2 个家族 id）

### ⚠️ 合并时的一个实质性冲突（必须处理）

`image_tools.py` 的 `FAMILY_HINTS` 里有这两条（L80–81）：

```python
"s21peakmulti": "s21peakmulti",
"s21peak": "s21peak",
```

导师已判定它们**不在合法 prompt 集合**里。若不删，将来 VLM key 配好后：
`文件名含 s21peak* → 推断出非法 family → vendor get_prompt 静默回退 rabi → 输出"看似合理但模板错误"的分析`

**→ 与导师 main 合并时，必须同步删掉这两条 hint。**

---

## 6. 后续提 PR / 合并建议

```bash
# ① 吸收导师最新 main（解决上面 2 处）
git fetch https://github.com/QMC-AI/QMClaw.git main:refs/remotes/mentor/main
git merge mentor/main
# 冲突预期：.gitignore（取导师）、server.py（取导师的删除）
# 另需：删掉 image_tools.py FAMILY_HINTS 的 s21peak / s21peakmulti 两条

# ② 复跑测试（应仍 107 passed / 0 failed）
python -m pytest tests/ -q --ignore=tests/test_labrad.py --ignore=tests/test_load_qubits.py -p no:cacheprovider

# ③ 提 PR（两种粒度）
#   A. 一个 PR：feat/agent-image-tools → mentor/main（含工作流 + 图像）
#   B. 两个 PR：先 feat/agent-workflow-tools，再 feat/agent-image-tools（审查更细）
```

---

## 7. 一句话总结

> 上一次 PR（#7）交付的是「真机安全 + 分级记忆 + Hermes 技能接线 + agent 图表修复」，agent 侧只有 **6 个量子工具**；
> 当前版本在此基础上新增了「**agent 会跑工作流**（含写闸门预检、结果带图）」与「**agent 能调用 VLM 图像分析**（接口层已连通，VLM 配置待补）」，
> 工具面 **6 → 12**、测试 **82 → 107**，且**未改前端、未改配置**。

---

## 附录：核对命令

```bash
git log --oneline 0a04184..HEAD
git diff --stat 0a04184..HEAD
git diff --numstat 0a04184..HEAD
git ls-remote --heads https://github.com/hzz-wdb/QMClaw.git
git ls-remote https://github.com/QMC-AI/QMClaw.git refs/pull/7/head refs/heads/main
# PR #7 状态：GET https://api.github.com/repos/QMC-AI/QMClaw/pulls/7
# 新增测试收集：pytest tests/test_agent_workflow_tools.py tests/test_agent_image_tools.py --collect-only -q
```
