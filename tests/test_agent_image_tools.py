# -*- coding: utf-8 -*-
r"""
agent 端图像分析工具组单元测试 — 全程 mock HTTP，不联网、不依赖 VLM

覆盖：
1. _resolve_image_path：/plots/ 形式、绝对路径、缺省取最新、不存在报错
2. _infer_family：t1/rabi/s21peakmulti/t1_fluctuations 命中；未知文件名兜底 rabi + inferred=False
3. task 路由：describe -> /describe；analyze_full -> /analyze_full（超时 120/300）
4. 成功回填：plots 回显 /plots/xxx.png、report 存在
5. 失败映射：401/认证失败 -> "VLM 未配置可用密钥"
6. image_list_plots：items 列表且 data 里没有 plots 键
7. extract_params：数值字段提升为 results（bool 不算）
8. setup_agent_tools 注册断言：12 个工具，含 image_analyze/image_list_plots

运行:
    & "D:\semester4\deploy\qmclaw\qmclaw\python.exe" -m pytest tests/test_agent_image_tools.py -q -p no:cacheprovider
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from services.agent_service.adapter import setup_agent_tools  # noqa: E402
from services.common.quantum_tools import image_tools  # noqa: E402
from services.common.quantum_tools.errors import ErrorCode  # noqa: E402
from services.common.quantum_tools.registry import ToolRegistry  # noqa: E402

# 不联网的占位地址：setup_* 只构造工具实例，不发请求
_DUMMY_URL = "http://127.0.0.1:1"

_ITEM_KEYS = {"name", "url", "mtime", "family", "size_kb"}


@pytest.fixture()
def plots_dir(tmp_path, monkeypatch):
    """把 PLOTS_DIR 指到临时目录（工具运行时读模块全局，monkeypatch 生效）"""
    d = tmp_path / "plots"
    d.mkdir()
    monkeypatch.setattr(image_tools, "PLOTS_DIR", d)
    return d


def _png(path: Path, mtime: float = None) -> Path:
    """造一张最小 png（内容不重要，纯字节）"""
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _fake_post(recorder, response):
    """替换 image_tools._http_post：只记录调用，不发请求"""
    def fake(path, payload, timeout=120):
        recorder.append({"path": path, "payload": payload, "timeout": timeout})
        return response
    return fake


# ── 1. 路径解析 ───────────────────────────────────────────────────────────────

def test_resolve_plots_url_form(plots_dir):
    """/plots/x.png -> PLOTS_DIR/x.png"""
    target = _png(plots_dir / "x.png")

    assert image_tools._resolve_image_path("/plots/x.png") == target


def test_resolve_absolute_and_missing(plots_dir, tmp_path):
    """绝对路径照原样；不存在 -> ValueError，工具侧是 VALIDATION_ERROR"""
    outside = _png(tmp_path / "abs.png")
    assert image_tools._resolve_image_path(str(outside)) == outside

    with pytest.raises(ValueError):
        image_tools._resolve_image_path("/plots/nope.png")


def test_missing_image_maps_to_validation_error(plots_dir, tmp_path, monkeypatch):
    """图片不存在时工具返回 VALIDATION_ERROR 且一次 HTTP 都不发"""
    recorder = []
    monkeypatch.setattr(image_tools, "_http_post", _fake_post(recorder, {"success": True}))

    result = image_tools.ImageAnalyzeTool().execute(image_path=str(tmp_path / "nope.png"))

    assert result.success is False
    assert result.error_code == ErrorCode.VALIDATION_ERROR.value
    assert recorder == []


def test_empty_path_picks_latest_png(plots_dir, monkeypatch):
    """省略 image_path -> 取 mtime 最新的那张"""
    _png(plots_dir / "old.png", mtime=1_600_000_000)
    newest = _png(plots_dir / "new.png", mtime=1_700_000_000)
    recorder = []
    monkeypatch.setattr(
        image_tools, "_http_post",
        _fake_post(recorder, {"success": True, "result": {"plot_type": "decay"}}),
    )

    result = image_tools.ImageAnalyzeTool().execute(task="describe")

    assert result.success, result.to_dict()
    assert result.data["image_path"] == str(newest)
    assert len(recorder) == 1


# ── 2. 实验族推断 ─────────────────────────────────────────────────────────────

def test_infer_family():
    """文件名 -> 实验族；未知名字兜底 rabi 且 inferred=False"""
    infer = image_tools._infer_family

    assert infer(Path("offline_sim_t1_1.png")) == ("t1", True)
    assert infer(Path("rabi_plot.png")) == ("rabi", True)
    assert infer(Path("unknown.png")) == (image_tools.DEFAULT_FAMILY, False)
    # 更具体的条目优先（不能把 t1_fluctuations 误判成 t1、s21peak 误判成 s21）
    assert infer(Path("offline_sim_t1_fluctuations_3.png")) == ("t1_fluctuations", True)
    assert infer(Path("s21peakmulti_2.png")) == ("s21peakmulti", True)
    assert infer(Path("T1_Plot.PNG")) == ("t1", True)


# ── 3. task 路由与超时 ────────────────────────────────────────────────────────

def test_task_routing_and_timeout(plots_dir, monkeypatch):
    """describe -> /describe(120s)；analyze_full -> /analyze_full(300s)"""
    _png(plots_dir / "rabi_1.png")
    calls = []
    monkeypatch.setattr(
        image_tools, "_http_post",
        _fake_post(calls, {"success": True, "result": {"a": 1}}),
    )
    tool = image_tools.ImageAnalyzeTool()

    tool.execute(image_path="/plots/rabi_1.png", task="describe")
    assert calls[-1]["path"] == "/describe"
    assert calls[-1]["timeout"] == 120
    assert calls[-1]["payload"]["experiment_family"] == "rabi"
    assert calls[-1]["payload"]["language"] == "zh"

    tool.execute(image_path="/plots/rabi_1.png", task="analyze_full")
    assert calls[-1]["path"] == "/analyze_full"
    assert calls[-1]["timeout"] == 300


def test_invalid_task_rejected(plots_dir, monkeypatch):
    """task 不在白名单 -> VALIDATION_ERROR，且不发请求"""
    _png(plots_dir / "x.png")
    recorder = []
    monkeypatch.setattr(image_tools, "_http_post", _fake_post(recorder, {"success": True}))

    result = image_tools.ImageAnalyzeTool().execute(
        image_path="/plots/x.png", task="nonsense",
    )

    assert result.success is False
    assert result.error_code == ErrorCode.VALIDATION_ERROR.value
    assert "analyze_full" in result.error
    assert recorder == []


# ── 4. 成功回填 ───────────────────────────────────────────────────────────────

def test_success_echoes_plot_and_report(plots_dir, monkeypatch):
    """成功时回显 /plots/xxx.png 并带上 report / family / image_path"""
    _png(plots_dir / "offline_sim_t1_9.png")
    monkeypatch.setattr(
        image_tools, "_http_post",
        _fake_post([], {"success": True, "result": {"plot_type": "decay", "main_features": "exp decay"}}),
    )

    result = image_tools.ImageAnalyzeTool().execute(
        image_path="/plots/offline_sim_t1_9.png", task="describe",
    )

    assert result.success, result.to_dict()
    assert result.data["plots"] == ["/plots/offline_sim_t1_9.png"]
    assert result.data["report"]["plot_type"] == "decay"
    assert result.data["task"] == "describe"
    assert result.data["experiment_family"] == "t1"
    assert result.data["family_inferred"] is True
    assert "inline_image" not in result.data


def test_explicit_family_wins(plots_dir, monkeypatch):
    """显式 experiment_family 优先，family_inferred=False"""
    _png(plots_dir / "offline_sim_t1_9.png")
    monkeypatch.setattr(
        image_tools, "_http_post",
        _fake_post([], {"success": True, "result": {"ok": 1}}),
    )

    result = image_tools.ImageAnalyzeTool().execute(
        image_path="/plots/offline_sim_t1_9.png", experiment_family="ramsey", task="describe",
    )

    assert result.data["experiment_family"] == "ramsey"
    assert result.data["family_inferred"] is False


def test_extract_params_numeric_results(plots_dir, monkeypatch):
    """extract_params 的数值字段进 results（bool 不算）"""
    _png(plots_dir / "t1_fit.png")
    report = {"T1_us": 12.5, "decay_visible": True, "n": 3, "fit_model": "exp"}
    monkeypatch.setattr(
        image_tools, "_http_post",
        _fake_post([], {"success": True, "result": report}),
    )

    result = image_tools.ImageAnalyzeTool().execute(
        image_path="/plots/t1_fit.png", task="extract_params",
    )

    assert result.success
    assert result.data["results"] == {"T1_us": 12.5, "n": 3}


# ── 5. 失败映射 ───────────────────────────────────────────────────────────────

def test_auth_failure_maps_to_clear_error(plots_dir, monkeypatch):
    """401 / Authentication / invalid -> 明确的 VLM 未配置文案"""
    _png(plots_dir / "x.png")
    resp = {
        "success": False,
        "error": "HTTP 401: Unauthorized | body=Authentication failed: invalid api key",
    }
    monkeypatch.setattr(image_tools, "_http_post", _fake_post([], resp))

    result = image_tools.ImageAnalyzeTool().execute(image_path="/plots/x.png", task="describe")

    assert result.success is False
    assert result.error_code == ErrorCode.EXECUTION_ERROR.value
    assert "VLM 未配置可用密钥" in result.error


def test_other_failure_wrapped(plots_dir, monkeypatch):
    """非认证失败 -> "图像分析失败: ..."，原文截断进文案"""
    _png(plots_dir / "x.png")
    monkeypatch.setattr(
        image_tools, "_http_post",
        _fake_post([], {"success": False, "error": "HTTP 500: boom"}),
    )

    result = image_tools.ImageAnalyzeTool().execute(image_path="/plots/x.png", task="describe")

    assert result.success is False
    assert "图像分析失败" in result.error
    assert "HTTP 500" in result.error


# ── 6. image_list_plots ───────────────────────────────────────────────────────

def test_list_plots_items_without_plots_key(plots_dir):
    """items 按 mtime 降序；data 里绝不能有 plots 键"""
    _png(plots_dir / "a_t1.png", mtime=1_600_000_000)
    newest = _png(plots_dir / "b_rabi.png", mtime=1_700_000_000)

    result = image_tools.ImageListPlotsTool().execute(limit=1)

    assert result.success, result.to_dict()
    assert "plots" not in result.data
    assert result.data["plots_dir"] == str(plots_dir)
    items = result.data["items"]
    assert len(items) == 1
    assert items[0]["name"] == newest.name
    assert items[0]["url"] == "/plots/b_rabi.png"
    assert items[0]["family"] == "rabi"
    assert set(items[0]) == _ITEM_KEYS


def test_list_plots_empty_dir(plots_dir):
    """空目录 -> items=[]（不报错、不造 plots）"""
    result = image_tools.ImageListPlotsTool().execute()

    assert result.success
    assert result.data["items"] == []
    assert "plots" not in result.data


# ── 7. agent 注册面：10 + 2 = 12 ─────────────────────────────────────────────

def test_setup_agent_tools_registers_image_tools():
    """agent 工具总数 12，含 image_analyze / image_list_plots"""
    registry = setup_agent_tools(
        ToolRegistry(),
        quantum_service_url=_DUMMY_URL,
        workflow_service_url=_DUMMY_URL,
    )
    names = set(registry.list_tools())

    assert {"image_analyze", "image_list_plots"} <= names
    assert len(names) == 12, sorted(names)
    assert "workflow_list" in names  # 原有工具组未受影响
