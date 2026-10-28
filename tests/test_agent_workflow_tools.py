# -*- coding: utf-8 -*-
r"""
agent 端接入 workflow 工具组（v1）单元测试 — 除 live 条外不依赖运行中的微服务

覆盖：
1. setup_workflow_tools 的子集过滤：默认 6 个；tools=[...] 只注册列表内；
   未知名字打印 warning 后跳过
2. setup_agent_tools 的暴露面：agent 只有 workflow_list/run/status/stats，
   没有 workflow_create / workflow_cancel
3. RunWorkflowTool 的运行前预检：guard 返回拒绝文案 -> FORBIDDEN + read_only，
   且 runner 一次都不调用
4. guard 返回 None -> runner 调用 1 次且结果 success
5. live（可跳过）：workflow_service 的 /workflows 真跑 workflow_list

运行:
    & "D:\semester4\deploy\qmclaw\qmclaw\python.exe" -m pytest tests/test_agent_workflow_tools.py -q
"""

import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from services.agent_service.adapter import setup_agent_tools  # noqa: E402
from services.common.quantum_tools.errors import ErrorCode  # noqa: E402
from services.common.quantum_tools.registry import ToolRegistry  # noqa: E402
from services.common.quantum_tools.workflow_tools import (  # noqa: E402
    GetWorkflowStatsTool,
    GetWorkflowStatusTool,
    RunWorkflowTool,
    setup_workflow_tools,
)

WORKFLOW_SERVICE_URL = "http://localhost:3008"

ALL_WORKFLOW_TOOLS = {
    "workflow_list",
    "workflow_create",
    "workflow_run",
    "workflow_status",
    "workflow_cancel",
    "workflow_stats",
}

AGENT_WORKFLOW_TOOLS = {
    "workflow_list",
    "workflow_run",
    "workflow_status",
    "workflow_stats",
}

# 不联网的占位地址：setup_* 只构造工具实例，不发请求
_DUMMY_URL = "http://127.0.0.1:1"


def _tool_names(registry) -> set:
    return set(registry.list_tools())


# ── 1. setup_workflow_tools 的工具子集过滤 ────────────────────────────────────

def test_setup_workflow_tools_subset_filtering(capsys):
    """默认注册全部 6 个；给列表只注册列表内；未知名字警告后跳过。"""
    default_registry = ToolRegistry()
    setup_workflow_tools(default_registry, _DUMMY_URL)
    assert _tool_names(default_registry) == ALL_WORKFLOW_TOOLS

    subset_registry = ToolRegistry()
    setup_workflow_tools(
        subset_registry,
        _DUMMY_URL,
        tools=["workflow_list", "workflow_status"],
    )
    assert _tool_names(subset_registry) == {"workflow_list", "workflow_status"}

    mixed_registry = ToolRegistry()
    setup_workflow_tools(
        mixed_registry,
        _DUMMY_URL,
        tools=["workflow_list", "workflow_typo", "workflow_status"],
    )
    assert _tool_names(mixed_registry) == {"workflow_list", "workflow_status"}
    assert "workflow_typo" in capsys.readouterr().out


# ── 2. agent 端暴露面 ─────────────────────────────────────────────────────────

def test_agent_tools_expose_only_four_workflow_tools():
    """agent registry 只有 4 个 workflow 工具，绝不暴露 create/cancel。"""
    registry = setup_agent_tools(
        ToolRegistry(),
        quantum_service_url=_DUMMY_URL,
        workflow_service_url=_DUMMY_URL,
    )
    workflow_names = {n for n in _tool_names(registry) if n.startswith("workflow_")}

    assert workflow_names == AGENT_WORKFLOW_TOOLS
    assert "workflow_create" not in workflow_names
    assert "workflow_cancel" not in workflow_names


# ── 3. 运行前预检：拒绝 ───────────────────────────────────────────────────────

def test_run_workflow_guard_blocks_before_runner():
    """guard 返回拒绝文案 -> FORBIDDEN/read_only，runner 调用次数为 0。"""
    calls = []

    def runner(workflow_id, context):
        calls.append((workflow_id, context))
        return {"success": True, "workflow_id": workflow_id, "status": "running"}

    reason = "当前为只读联调（写闸门关闭），不能运行工作流"
    tool = RunWorkflowTool(runner, guard=lambda: reason)
    result = tool.execute("wf_demo")

    assert not result.success
    assert result.error == reason
    assert result.error_code == ErrorCode.FORBIDDEN.value
    assert result.metadata.get("status") == "read_only"
    assert result.metadata.get("actuation_allowed") is False
    assert calls == []


# ── 4. 运行前预检：放行 ───────────────────────────────────────────────────────

def test_run_workflow_guard_none_allows_runner():
    """guard 返回 None -> runner 调用 1 次且结果 success。"""
    calls = []

    def runner(workflow_id, context):
        calls.append((workflow_id, context))
        return {"success": True, "workflow_id": "wf_x", "status": "running"}

    tool = RunWorkflowTool(runner, guard=lambda: None)
    result = tool.execute("wf_x")

    assert result.success, f"预检放行后应成功: {result.to_dict()}"
    assert len(calls) == 1
    assert result.data.get("workflow_id") == "wf_x"
    assert result.data.get("status") == "running"


# ── 5. live（可选）：真实 workflow_service ────────────────────────────────────

def _service_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{WORKFLOW_SERVICE_URL}/health", timeout=5) as resp:
            resp.read()
        return True
    except Exception:
        return False


def test_live_workflow_list():
    """workflow_service 运行时，真跑一次 workflow_list。"""
    if not _service_reachable():
        pytest.skip("workflow_service not running")

    registry = ToolRegistry()
    setup_workflow_tools(registry, WORKFLOW_SERVICE_URL)
    result = registry.execute("workflow_list", {})

    assert result.success, f"workflow_list 失败: {result.to_dict()}"


# ── 6. 契约容错回归（服务端 /workflows/status 与 /runs/stats 不回 success 字段）──
# live 验收发现：这两个接口只返回业务字段（id/status/nodes… 或 totalRuns/…），
# 旧工具实现硬要求 success -> 永远 EXECUTION_ERROR。以下锁定兼容行为。


def test_status_tool_accepts_service_contract_without_success():
    """真实服务端返回 {id,name,status,nodes,...} 没有 success，工具应视为成功。"""
    payload = {"id": "wf_x", "name": "demo", "status": "completed", "nodes": []}
    tool = GetWorkflowStatusTool(lambda wid: payload)

    result = tool.execute("wf_x")

    assert result.success, f"应兼容无 success 字段: {result.to_dict()}"
    assert result.data.get("status") == "completed"


def test_status_tool_maps_service_error():
    tool = GetWorkflowStatusTool(lambda wid: {"error": "Workflow not found"})

    result = tool.execute("wf_missing")

    assert not result.success
    assert result.error == "Workflow not found"
    assert result.error_code == ErrorCode.EXECUTION_ERROR.value


def test_stats_tool_accepts_service_contract_without_success():
    payload = {"totalRuns": 2, "completedRuns": 2, "failedRuns": 0, "avgDuration": 1000.0}
    tool = GetWorkflowStatsTool(lambda wid: payload)

    result = tool.execute("wf_x")

    assert result.success, f"应兼容无 success 字段: {result.to_dict()}"
    assert result.data.get("totalRuns") == 2


# ── 7. 节点产物提升（v1.1：工作流的图/指标要能被 agent 通用收集器读到）──────────


def test_status_tool_surfaces_node_plots_and_metrics():
    """nodes[].result 里的 plotPath/metrics/dataset_id 应提升到顶层。"""
    payload = {
        "id": "wf_x",
        "name": "demo",
        "status": "completed",
        "nodes": [
            {
                "id": "n1",
                "type": "experiment",
                "status": "completed",
                "result": {
                    "plotPath": "/plots/offline_sim_t1_1.png",
                    "metrics": {"t1": 8e-06},
                    "dataset_id": "20251103_00398",
                },
            },
            {
                "id": "n2",
                "type": "analysis",
                "status": "completed",
                "result": {"plotPath": "/plots/analysis_2.png"},
            },
        ],
    }
    tool = GetWorkflowStatusTool(lambda wid: payload)

    result = tool.execute("wf_x")

    assert result.success
    assert result.data.get("plots") == [
        "/plots/offline_sim_t1_1.png",
        "/plots/analysis_2.png",
    ]
    assert result.data.get("results") == {"t1": 8e-06}
    assert result.data.get("dataset_id") == "20251103_00398"
    assert result.metadata.get("dataset_id") == "20251103_00398"


def test_status_tool_without_node_results_has_no_plots_key():
    """节点还没结果（running/pending）时不应凭空造 plots 字段。"""
    payload = {
        "id": "wf_x",
        "name": "demo",
        "status": "running",
        "nodes": [{"id": "n1", "type": "experiment", "status": "running"}],
    }
    tool = GetWorkflowStatusTool(lambda wid: payload)

    result = tool.execute("wf_x")

    assert result.success
    assert "plots" not in result.data
    assert "results" not in result.data
