# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Workflow 工具

提供工作流管理相关的 MCP 工具。
通过 HTTP 调用 Workflow Service 执行工作流操作。
"""

import json
import traceback
from typing import Any, Callable, Dict, List, Optional

from .base import ParameterSchema, QuantumTool, ToolResult, ToolSchema
from .errors import ErrorCode


def _http_call(url: str, data: dict = None, method: str = "GET") -> dict:
    """发送 HTTP 请求"""
    import urllib.request
    import urllib.error

    try:
        if method == "GET":
            req = urllib.request.Request(url)
        else:
            payload = json.dumps(data or {}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method=method
            )

        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            error_body = json.loads(e.read().decode("utf-8"))
            return {"error": error_body.get("error", f"HTTP {e.code}: {e.reason}")}
        except Exception:
            return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _attach_node_artifacts(data: Dict[str, Any]) -> Optional[str]:
    """把工作流节点的产物提升到顶层，供 agent/前端的通用收集器使用。

    workflow_service 的 /workflows/status 把每个节点的执行结果放在
    nodes[].result 里（plotPath / metrics / dataset_id / data…）。agent 侧的
    图表与数值收集器只读顶层 data["plots"] / data["results"]，因此这里做一次
    提升 —— 同一份工具实现给 Hermes 用时也一并受益。

    Returns:
        第一个节点的 dataset_id（没有则 None），供调用方塞进 ToolResult.metadata
    """
    plots: List[str] = []
    results: Dict[str, Any] = {}
    dataset_ids: List[str] = []

    for node in (data.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        res = node.get("result")
        if not isinstance(res, dict):
            continue
        path = res.get("plotPath") or res.get("plot_path")
        if path:
            plots.append(path)
        metrics = res.get("metrics")
        if isinstance(metrics, dict):
            results.update(metrics)
        ds = res.get("dataset_id")
        if ds:
            dataset_ids.append(ds)

    if plots:
        data["plots"] = plots
    if results:
        data["results"] = results
    if dataset_ids:
        data["dataset_ids"] = dataset_ids
        data.setdefault("dataset_id", dataset_ids[0])
    return dataset_ids[0] if dataset_ids else None


class ListWorkflowsTool(QuantumTool):
    """列出工作流工具"""

    def __init__(
        self,
        lister: Callable[[], Dict[str, Any]],
    ):
        """
        Args:
            lister: 工作流列表获取函数，签名为 () -> Dict
        """
        self._lister = lister

    @property
    def name(self) -> str:
        return "workflow_list"

    @property
    def description(self) -> str:
        return """List all available workflows.
Returns workflow IDs, names, status, and node counts."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={},
        )

    def execute(self, **kwargs) -> ToolResult[List[Dict[str, Any]]]:
        """列出工作流"""
        try:
            result = self._lister()
            # workflow service 返回格式可能没有 success 字段
            if result.get("success") or ("workflows" in result and "count" in result):
                return ToolResult.ok(data=result.get("workflows", []))
            else:
                return ToolResult.err(
                    result.get("error", "Failed to list workflows"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )


class CreateWorkflowTool(QuantumTool):
    """创建工作流工具"""

    def __init__(
        self,
        creator: Callable[[str, List[Dict]], Dict[str, Any]],
    ):
        """
        Args:
            creator: 工作流创建函数，签名为 (name: str, nodes: List[Dict]) -> Dict
        """
        self._creator = creator

    @property
    def name(self) -> str:
        return "workflow_create"

    @property
    def description(self) -> str:
        return """Create a new workflow with specified nodes.
Each node should have: id, type, config, and depends."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "name": ParameterSchema(
                    name="name",
                    type="string",
                    description="Workflow name",
                    required=True,
                ),
                "nodes": ParameterSchema(
                    name="nodes",
                    type="array",
                    description="List of workflow nodes",
                    required=True,
                ),
            },
        )

    def execute(
        self,
        name: str,
        nodes: List[Dict[str, Any]],
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """创建工作流"""
        if not name:
            return ToolResult.err(
                "name is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        if not nodes:
            return ToolResult.err(
                "nodes cannot be empty",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._creator(name, nodes)
            if result.get("success"):
                return ToolResult.ok(data={
                    "workflow_id": result.get("workflow_id"),
                    "name": result.get("name"),
                    "node_count": result.get("node_count"),
                })
            else:
                return ToolResult.err(
                    result.get("error", "Failed to create workflow"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )


class RunWorkflowTool(QuantumTool):
    """运行工作流工具"""

    def __init__(
        self,
        runner: Callable[[str, Dict], Dict[str, Any]],
        guard: Optional[Callable[[], Optional[str]]] = None,
    ):
        """
        Args:
            runner: 工作流运行函数，签名为 (workflow_id: str, context: Dict) -> Dict
            guard: 运行前预检。返回非空字符串表示拒绝（作为错误文案返回），
                返回 None 表示放行。默认 None（不做预检，保持既有行为）。
        """
        self._runner = runner
        self._guard = guard

    @property
    def name(self) -> str:
        return "workflow_run"

    @property
    def description(self) -> str:
        return """Run an existing workflow.
Provides context variables that will be available to workflow nodes."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "workflow_id": ParameterSchema(
                    name="workflow_id",
                    type="string",
                    description="Workflow ID to run",
                    required=True,
                ),
                "context": ParameterSchema(
                    name="context",
                    type="object",
                    description="Context variables for the workflow",
                    required=False,
                    default={},
                ),
            },
        )

    def execute(
        self,
        workflow_id: str,
        context: Dict[str, str] = None,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """运行工作流"""
        if not workflow_id:
            return ToolResult.err(
                "workflow_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        context = context or {}

        # 运行前预检：闸门只在服务端强制，这里只是提前解释。
        if self._guard is not None:
            reason = self._guard()
            if reason:
                return ToolResult.err(
                    reason,
                    error_code=ErrorCode.FORBIDDEN.value,
                    status="read_only",
                    actuation_allowed=False,
                )

        try:
            result = self._runner(workflow_id, context)
            if result.get("success"):
                return ToolResult.ok(data={
                    "workflow_id": result.get("workflow_id"),
                    "status": result.get("status"),
                    "run_id": result.get("run_id"),
                })
            else:
                return ToolResult.err(
                    result.get("error", "Failed to run workflow"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
                workflow_id=workflow_id,
            )


class GetWorkflowStatusTool(QuantumTool):
    """获取工作流状态工具"""

    def __init__(
        self,
        status_getter: Callable[[str], Dict[str, Any]],
    ):
        """
        Args:
            status_getter: 状态获取函数，签名为 (workflow_id: str) -> Dict
        """
        self._status_getter = status_getter

    @property
    def name(self) -> str:
        return "workflow_status"

    @property
    def description(self) -> str:
        return """Get the status of a workflow run.
Returns node statuses and any available results."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "workflow_id": ParameterSchema(
                    name="workflow_id",
                    type="string",
                    description="Workflow ID to check status",
                    required=True,
                ),
            },
        )

    def execute(
        self,
        workflow_id: str,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """获取工作流状态"""
        if not workflow_id:
            return ToolResult.err(
                "workflow_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._status_getter(workflow_id)
            # workflow_service 的 /workflows/status 不返回 success 字段，
            # 只返回 id/name/status/nodes/…；这里兼容两种契约（既有 Hermes 也受益）：
            #   有 success=True，或同时带 id + status → 视为成功；只有 error → 失败。
            if result.get("success") or ("id" in result and "status" in result):
                # v1.1：把 nodes[].result 里的图/指标提升到顶层，供 agent 的
                # 通用收集器（data["plots"] / data["results"]）直接消费。
                data = dict(result)
                dataset_id = _attach_node_artifacts(data)
                if dataset_id:
                    return ToolResult.ok(data=data, dataset_id=dataset_id)
                return ToolResult.ok(data=data)
            else:
                return ToolResult.err(
                    result.get("error", "Failed to get workflow status"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                    workflow_id=workflow_id,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
                workflow_id=workflow_id,
            )


class CancelWorkflowTool(QuantumTool):
    """取消工作流工具"""

    def __init__(
        self,
        canceller: Callable[[str], Dict[str, Any]],
    ):
        """
        Args:
            canceller: 取消函数，签名为 (workflow_id: str) -> Dict
        """
        self._canceller = canceller

    @property
    def name(self) -> str:
        return "workflow_cancel"

    @property
    def description(self) -> str:
        return """Cancel a running workflow."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "workflow_id": ParameterSchema(
                    name="workflow_id",
                    type="string",
                    description="Workflow ID to cancel",
                    required=True,
                ),
            },
        )

    def execute(
        self,
        workflow_id: str,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """取消工作流"""
        if not workflow_id:
            return ToolResult.err(
                "workflow_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._canceller(workflow_id)
            if result.get("success"):
                return ToolResult.ok(data={
                    "workflow_id": result.get("workflow_id"),
                    "status": "cancelled",
                })
            else:
                return ToolResult.err(
                    result.get("error", "Failed to cancel workflow"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
                workflow_id=workflow_id,
            )


class GetWorkflowStatsTool(QuantumTool):
    """获取工作流统计信息工具"""

    def __init__(
        self,
        stats_getter: Callable[[str], Dict[str, Any]],
    ):
        """
        Args:
            stats_getter: 统计获取函数，签名为 (workflow_id: str) -> Dict
        """
        self._stats_getter = stats_getter

    @property
    def name(self) -> str:
        return "workflow_stats"

    @property
    def description(self) -> str:
        return """Get execution statistics for a workflow.
Returns total runs, completed, failed counts, and average duration."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "workflow_id": ParameterSchema(
                    name="workflow_id",
                    type="string",
                    description="Workflow ID to get stats for",
                    required=True,
                ),
            },
        )

    def execute(
        self,
        workflow_id: str,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """获取工作流统计"""
        if not workflow_id:
            return ToolResult.err(
                "workflow_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._stats_getter(workflow_id)
            # workflow_service 的 /runs/stats 同样不返回 success 字段，
            # 只返回 totalRuns/completedRuns/failedRuns/avgDuration…；兼容两种契约。
            if result.get("success") or "totalRuns" in result:
                return ToolResult.ok(data=result)
            else:
                return ToolResult.err(
                    result.get("error", "Failed to get workflow stats"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
                workflow_id=workflow_id,
            )


def setup_workflow_tools(
    registry,
    workflow_service_url: str = "http://localhost:3008",
    tools: Optional[List[str]] = None,
    run_guard: Optional[Callable[[], Optional[str]]] = None,
) -> None:
    """设置 Workflow 工具到注册中心

    Args:
        registry: 工具注册中心实例
        workflow_service_url: Workflow Service 地址
        tools: 要注册的工具名子集。None（默认）表示注册全部 6 个，
            传入列表则只注册列表内的工具，未知名字打印警告后跳过。
        run_guard: 透传给 RunWorkflowTool 的运行前预检（返回拒绝文案或 None）
    """

    def list_workflows() -> Dict[str, Any]:
        """列出工作流"""
        result = _http_call(f"{workflow_service_url}/workflows")
        return result

    def create_workflow(name: str, nodes: List[Dict]) -> Dict[str, Any]:
        """创建工作流"""
        result = _http_call(
            f"{workflow_service_url}/workflows/create",
            {"name": name, "nodes": nodes},
            "POST"
        )
        return result

    def run_workflow(workflow_id: str, context: Dict) -> Dict[str, Any]:
        """运行工作流"""
        result = _http_call(
            f"{workflow_service_url}/workflows/run",
            {"workflow_id": workflow_id, "context": context},
            "POST"
        )
        return result

    def get_workflow_status(workflow_id: str) -> Dict[str, Any]:
        """获取工作流状态"""
        result = _http_call(
            f"{workflow_service_url}/workflows/status",
            {"workflowId": workflow_id},
            "POST"
        )
        return result

    def cancel_workflow(workflow_id: str) -> Dict[str, Any]:
        """取消工作流"""
        result = _http_call(
            f"{workflow_service_url}/workflows/cancel",
            {"workflowId": workflow_id},
            "POST"
        )
        return result

    def get_workflow_stats(workflow_id: str) -> Dict[str, Any]:
        """获取工作流统计"""
        result = _http_call(f"{workflow_service_url}/runs/stats/{workflow_id}")
        return result

    # 构造 6 个工具（保持既有实例与顺序），再按 tools 过滤注册
    tools_by_name: Dict[str, QuantumTool] = {
        "workflow_list": ListWorkflowsTool(list_workflows),
        "workflow_create": CreateWorkflowTool(create_workflow),
        "workflow_run": RunWorkflowTool(run_workflow, guard=run_guard),
        "workflow_status": GetWorkflowStatusTool(get_workflow_status),
        "workflow_cancel": CancelWorkflowTool(cancel_workflow),
        "workflow_stats": GetWorkflowStatsTool(get_workflow_stats),
    }

    if tools is None:
        wanted = list(tools_by_name.keys())
    else:
        wanted = list(tools)

    for name in wanted:
        tool = tools_by_name.get(name)
        if tool is None:
            print(
                f"WARNING: setup_workflow_tools: unknown tool '{name}' ignored "
                f"(available: {', '.join(tools_by_name)})"
            )
            continue
        registry.register_tool(tool)
