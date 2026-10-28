# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Image 工具

把 :3010 QubitClient 的 VLM 图像分析能力接成 agent 工具组：

- image_analyze      分析一张已存在的实验图（或自动取最新实验图）
- image_list_plots   列出最近实验图，便于选择要分析的那张

约束：agent 不做聊天上传 —— 只能分析服务器磁盘上已存在的 PNG
（qmclaw-web/public/plots，可用环境变量 PLOTS_DIR 覆盖）。
"""

import base64
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import ParameterSchema, QuantumTool, ToolResult, ToolSchema
from .errors import ErrorCode


# qubitclient_service 地址（VLM 图像分析）
QUBITCLIENT_SERVICE_URL = "http://localhost:3010"


def _default_plots_dir() -> Path:
    """默认图目录：<Agentic Workflow>/qmclaw-web/public/plots

    本文件在 <qmclaw-server>/services/common/quantum_tools/ 下：
    parents[0]=quantum_tools, [1]=common, [2]=services, [3]=qmclaw-server。
    """
    return (
        Path(__file__).resolve().parents[3].parent
        / "qmclaw-web"
        / "public"
        / "plots"
    )


# 图目录：优先环境变量，否则仓库内约定位置（模块导入时定型）
PLOTS_DIR: Path = Path(os.environ["PLOTS_DIR"]) if os.environ.get("PLOTS_DIR") else _default_plots_dir()


# 可用的 VLM 任务（键即 :3010 的端点名）
ANALYZE_TASKS: Dict[str, str] = {
    "describe": "describe",
    "classify": "classify",
    "reasoning": "reasoning",
    "assess_fit": "assess_fit",
    "extract_params": "extract_params",
    "evaluate": "evaluate",
    "analyze_full": "analyze_full",
}

# 默认任务：一次拿全（describe/classify/reasoning/assess_fit/extract_params/evaluate）
DEFAULT_TASK = "analyze_full"

# 推不出实验族时的兜底（rabi 图最常见，且 analyze_full 对它容错较好）
DEFAULT_FAMILY = "rabi"

# 文件名关键词 -> 实验族 id（顺序即优先级；更具体的写在前面）
# 匹配口径见 _infer_family：大小写不敏感，按 "_" 分词后做整词匹配，
# 所以 "t1" 不会误配 "t1_fluctuations"（后者有自己的、更靠前的条目），
# 也不会把 "s21peak" 误配成 "s21"。
FAMILY_HINTS: Dict[str, str] = {
    "t1_fluctuations": "t1_fluctuations",
    "t1_fluct": "t1_fluctuations",
    "singleshot": "singleshot",
    "single_shot": "singleshot",
    "optpipulse": "optpipulse",
    "opt_pi_pulse": "optpipulse",
    "s21peakmulti": "s21peakmulti",
    "s21peak": "s21peak",
    "s21vflux": "s21vflux",
    "s21": "s21",
    "spectrum_2d": "spectrum_2d",
    "spectrum": "spectrum",
    "rabi_hw": "rabi_hw",
    "rabi": "rabi",
    "ramsey_t2star": "ramsey_t2star",
    "ramsey": "ramsey",
    "drag": "drag",
    "t1": "t1",
    "t2": "t2",
    "rb": "rb",
}


def _plots_dir() -> Path:
    """当前图目录（运行时读模块全局，便于测试 monkeypatch PLOTS_DIR）"""
    return Path(PLOTS_DIR)


def _http_post(
    path: str,
    payload: dict,
    timeout: float = 120,
    service_url: Optional[str] = None,
) -> dict:
    """POST 到 qubitclient_service（VLM 分析接口）

    Args:
        path: 端点路径，如 "/analyze_full"
        payload: 请求体
        timeout: 超时秒数（analyze_full 约 6 次模型调用，需放宽）
        service_url: 覆盖服务地址（默认 QUBITCLIENT_SERVICE_URL）

    Returns:
        解析后的 dict；任何异常都转成 {"success": False, "error": ...}
    """
    base = (service_url or QUBITCLIENT_SERVICE_URL).rstrip("/")
    url = f"{base}{path}"
    try:
        data = json.dumps(payload).encode("utf-8")
    except Exception as e:  # payload 里出现不可序列化对象
        return {"success": False, "error": f"请求体序列化失败: {e}"}

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except Exception:
            # 401 文本/HTML 之类不是 JSON，原样带回去让上层判错
            return {"success": False, "error": body[:500]}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        detail = f"HTTP {e.code}: {e.reason}"
        if body:
            detail += f" | body={body[:800]}"
        return {"success": False, "error": detail}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"URLError: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _latest_plot() -> Path:
    """PLOTS_DIR 下最新（按 mtime）的 *.png"""
    root = _plots_dir()
    if not root.is_dir():
        raise ValueError("没有可分析的图：PLOTS_DIR 为空")
    pngs = [p for p in root.glob("*.png") if p.is_file()]
    if not pngs:
        raise ValueError("没有可分析的图：PLOTS_DIR 为空")
    return max(pngs, key=lambda p: p.stat().st_mtime)


def _repo_relative_candidates(image_path: str) -> List[Path]:
    """"仓库相对路径"的候选位置（按优先级）

    约定写法是相对仓库根，例如：
      "Agentic Workflow/qmclaw-web/public/plots/offline_sim_t1_1.png"

    注意 depth 取值与 PLOTS_DIR 层级有关：PLOTS_DIR 默认是
    <repo>/Agentic Workflow/qmclaw-web/public/plots，
    此时 parents[3] 才是仓库根、parents[4] 已是 repo 的上一级。
    两档都试并按存在性取第一个命中，避免层数假设失效时直接报"不存在"。
    """
    candidates: List[Path] = []
    for depth in (4, 3):
        try:
            candidates.append(_plots_dir().parents[depth] / image_path)
        except IndexError:
            continue
    return candidates


def _resolve_image_path(image_path: Optional[str]) -> Path:
    """把工具入参解析成磁盘上真实存在的图片

    支持：
      ① 省略 -> PLOTS_DIR 下最新 *.png
      ② "/plots/xxx.png" -> PLOTS_DIR/xxx.png
      ③ 绝对路径
      ④ 仓库相对路径

    Raises:
        ValueError: 解析不到 / 文件不存在
    """
    raw = (image_path or "").strip()
    if not raw:
        return _latest_plot()

    normalized = raw.replace("\\", "/")

    # ② 前端同款 URL 形式
    if normalized.startswith("/plots/"):
        candidate = _plots_dir() / normalized[len("/plots/"):]
        if candidate.is_file():
            return candidate
        raise ValueError(f"图片不存在: {image_path}")

    # ③ 绝对路径
    abs_path = Path(raw)
    if abs_path.is_absolute():
        if abs_path.is_file():
            return abs_path
        raise ValueError(f"图片不存在: {image_path}")

    # ④ 仓库相对（不用 Path.cwd()：服务进程的 cwd 不确定）
    for candidate in _repo_relative_candidates(raw):
        if candidate.is_file():
            return candidate

    # ⑤ 兜底再试一次：相对 PLOTS_DIR 本身（"xxx.png"）
    candidate = _plots_dir() / normalized
    if candidate.is_file():
        return candidate

    raise ValueError(f"图片不存在: {image_path}")


def _infer_family(path: Path) -> Tuple[str, bool]:
    """从文件名推断实验族

    按 "_" 分词后做整词匹配（大小写不敏感），因此：
      offline_sim_t1_1.png -> ("t1", True)
      rabi_plot.png        -> ("rabi", True)
      unknown.png          -> ("rabi", False)   # 兜底，inferred=False

    Returns:
        (family_id, inferred)
    """
    stem = path.stem.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", stem) if t]
    if not tokens:
        return DEFAULT_FAMILY, False
    padded = "_" + "_".join(tokens) + "_"
    for keyword, family in FAMILY_HINTS.items():
        if f"_{keyword}_" in padded:
            return family, True
    return DEFAULT_FAMILY, False


def _image_to_data_url(path: Path) -> str:
    """读图片 -> data URL（VLM 接口要的就是这个）"""
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif suffix == ".webp":
        mime = "image/webp"
    else:
        mime = "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _list_plot_items(limit: int = 5) -> Dict[str, Any]:
    """列出 PLOTS_DIR 下最近的实验图（不联网）

    返回的 data 里**没有** "plots" 键 —— agent 的收集器会把 plots 当图表收，
    列表工具不该污染对话里的图表。
    """
    try:
        wanted = int(limit)
    except (TypeError, ValueError):
        wanted = 5
    if wanted <= 0:
        wanted = 0

    root = _plots_dir()
    items: List[Dict[str, Any]] = []
    if root.is_dir():
        pngs = [p for p in root.glob("*.png") if p.is_file()]
        pngs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in pngs[:wanted]:
            try:
                st = p.stat()
            except OSError:
                continue
            family, _ = _infer_family(p)
            items.append({
                "name": p.name,
                "url": f"/plots/{p.name}",
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "family": family,
                "size_kb": round(st.st_size / 1024.0, 1),
            })

    return {"items": items, "plots_dir": str(root)}


def _looks_like_auth_failure(error_text: str) -> bool:
    """401 / Authentication / invalid 之类 -> VLM 密钥不可用"""
    low = (error_text or "").lower()
    return ("401" in low) or ("authentication" in low) or ("invalid" in low)


class ImageAnalyzeTool(QuantumTool):
    """VLM 分析一张已存在的实验图"""

    def __init__(
        self,
        analyzer: Optional[Callable[..., Dict[str, Any]]] = None,
    ):
        """
        Args:
            analyzer: 可选的 HTTP 注入点，签名 (path, payload, timeout) -> dict；
                      None 时直接用模块级 _http_post（便于测试 monkeypatch）
        """
        self._analyzer = analyzer

    @property
    def name(self) -> str:
        return "image_analyze"

    @property
    def description(self) -> str:
        return """分析一张量子实验图（视觉模型 VLM）。

给图片路径或省略以自动取最新实验图，返回文字报告并回显被分析的图。

PARAMETERS:
- image_path (optional): /plots/xxx.png、绝对路径或仓库相对路径；省略 = 最新实验图
- experiment_family (optional): 实验族 id（如 t1/rabi/ramsey/s21）；省略 = 从文件名推断
- task (optional): describe/classify/reasoning/assess_fit/extract_params/evaluate/analyze_full
- language (optional): 报告语言，默认 zh

NOTE: 只能分析服务器上已存在的图片文件；不能接收用户上传的图片。
analyze_full 会串行调用 6 个模型任务，较慢（约 300 秒超时）。"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "image_path": ParameterSchema(
                    name="image_path",
                    type="string",
                    description="图片路径：/plots/xxx.png、绝对路径或仓库相对路径；省略=最新实验图",
                    required=False,
                ),
                "experiment_family": ParameterSchema(
                    name="experiment_family",
                    type="string",
                    description="实验族 id，如 t1/rabi/ramsey/s21；省略=从文件名推断",
                    required=False,
                ),
                "task": ParameterSchema(
                    name="task",
                    type="string",
                    description=(
                        "分析任务：describe/classify/reasoning/assess_fit/"
                        "extract_params/evaluate/analyze_full"
                    ),
                    required=False,
                    default=DEFAULT_TASK,
                ),
                "language": ParameterSchema(
                    name="language",
                    type="string",
                    description="报告语言，如 zh/en",
                    required=False,
                    default="zh",
                ),
            },
        )

    def _post(self, path: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """调 :3010（注入点优先，其次模块级 _http_post）"""
        if self._analyzer is not None:
            return self._analyzer(path=path, payload=payload, timeout=timeout)
        return _http_post(path, payload, timeout)

    def execute(
        self,
        image_path: Optional[str] = None,
        experiment_family: Optional[str] = None,
        task: str = DEFAULT_TASK,
        language: str = "zh",
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """分析图片"""
        # 1) 定位图片
        try:
            p = _resolve_image_path(image_path)
        except ValueError as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        # 2) 实验族：显式参数优先，否则从文件名推断
        family_param = (experiment_family or "").strip()
        if family_param:
            family, inferred = family_param, False
        else:
            family, inferred = _infer_family(p)

        # 3) 任务合法性
        task_name = (task or DEFAULT_TASK).strip().lower()
        if task_name not in ANALYZE_TASKS:
            return ToolResult.err(
                f"task 不合法: {task}；可选值: {', '.join(ANALYZE_TASKS.keys())}",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        # 4) 组请求
        try:
            data_url = _image_to_data_url(p)
        except Exception as e:
            return ToolResult.err(
                f"读取图片失败: {e}",
                error_code=ErrorCode.EXECUTION_ERROR.value,
            )

        payload = {
            "image": data_url,
            "experiment_family": family,
            "language": language or "zh",
        }

        # 5) 调 VLM 服务
        timeout = 300 if task_name == DEFAULT_TASK else 120
        resp = self._post("/" + task_name, payload, timeout)
        if not isinstance(resp, dict):
            resp = {"success": False, "error": f"响应格式异常: {type(resp).__name__}"}

        # 6) 失败映射
        raw_error = resp.get("error")
        if resp.get("success") is False or raw_error is not None:
            err_text = (
                str(raw_error) if raw_error is not None
                else json.dumps(resp, ensure_ascii=False)[:300]
            )
            if _looks_like_auth_failure(err_text):
                return ToolResult.err(
                    "VLM 未配置可用密钥（3010 认证失败）：请在 "
                    "qmclaw-server/config/qubitclient.json 的 llm 段配置视觉模型与有效 key。"
                    f"原始错误: {err_text[:200]}",
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
            return ToolResult.err(
                f"图像分析失败: {err_text[:300]}",
                error_code=ErrorCode.EXECUTION_ERROR.value,
            )

        # 7) 成功回填
        report = resp.get("result") if task_name != DEFAULT_TASK else resp.get("results")

        chart_url = data_url
        inline = True
        try:
            rel = p.resolve().relative_to(_plots_dir().resolve())
            chart_url = "/plots/" + rel.as_posix()
            inline = False
        except ValueError:
            # 图不在 plots 目录下：直接把 data URL 交给前端（不落盘、不写日志）
            pass

        data: Dict[str, Any] = {
            "report": report,
            "task": task_name,
            "experiment_family": family,
            "family_inferred": inferred,
            "image_path": str(p),
            "plots": [chart_url],
        }
        if inline:
            data["inline_image"] = True

        # extract_params 的数值字段提升为 results，供前端数值卡
        if task_name == "extract_params" and isinstance(report, dict):
            data["results"] = {
                k: v
                for k, v in report.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }

        return ToolResult.ok(data=data)


class ImageListPlotsTool(QuantumTool):
    """列出最近的实验图"""

    def __init__(
        self,
        lister: Optional[Callable[[int], Dict[str, Any]]] = None,
    ):
        """
        Args:
            lister: 可选的图列表提供者，签名 (limit) -> dict；
                    None 时直接读模块级 PLOTS_DIR
        """
        self._lister = lister

    @property
    def name(self) -> str:
        return "image_list_plots"

    @property
    def description(self) -> str:
        return """列出最近的实验图（含推断的实验族），便于选择要分析的图。

只读服务器上已存在的 PNG 文件；返回 name/url/mtime/family/size_kb 列表。"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "limit": ParameterSchema(
                    name="limit",
                    type="integer",
                    description="最多返回多少张（默认 5）",
                    required=False,
                    default=5,
                    minimum=1,
                    maximum=50,
                ),
            },
        )

    def execute(self, limit: int = 5, **kwargs) -> ToolResult[Dict[str, Any]]:
        """列出最近的图"""
        try:
            data = self._lister(limit) if self._lister is not None else _list_plot_items(limit)
        except Exception as e:
            return ToolResult.err(
                f"列出实验图失败: {e}",
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )

        # 防御：列表工具绝不能带出 plots 键（agent 会当成图表收）
        if isinstance(data, dict):
            data.pop("plots", None)
        return ToolResult.ok(data=data)


def setup_image_tools(
    registry,
    qubitclient_service_url: str = QUBITCLIENT_SERVICE_URL,
) -> None:
    """注册图像分析工具组（2 个）

    Args:
        registry: 工具注册中心实例
        qubitclient_service_url: QubitClient Service（:3010，VLM）地址
    """

    def analyze(**kwargs: Any) -> Dict[str, Any]:
        """调 :3010 的分析端点（闭包注入，替换真实 HTTP 只需 monkeypatch _http_post）"""
        path = str(kwargs.get("path") or "")
        payload = kwargs.get("payload") or {}
        timeout = kwargs.get("timeout")
        timeout = float(timeout) if timeout is not None else 120.0
        if qubitclient_service_url == QUBITCLIENT_SERVICE_URL:
            # 默认地址时按 3 参调用，便于测试直接 monkeypatch _http_post
            return _http_post(path, payload, timeout)
        return _http_post(path, payload, timeout, service_url=qubitclient_service_url)

    def list_plots(limit: int = 5) -> Dict[str, Any]:
        """读 PLOTS_DIR（不联网）"""
        return _list_plot_items(limit)

    registry.register_tool(ImageAnalyzeTool(analyze))
    registry.register_tool(ImageListPlotsTool(list_plots))
