"""Agent Loop 的单元测试：mock LLM，验证工程逻辑（不需要 API key）。

演示 evals 思路的第一层——循环层确定性测试：
把 LLM 客户端 mock 成"按剧本返回 tool_calls 的演员"，
验证 Agent 正确地执行工具、回传结果、处理错误与确认拒绝。
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from agent import AgentLoop


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeToolCall:
    def __init__(self, call_id, name, args: dict):
        self.id = call_id
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()

    def model_dump(self):
        return {"id": self.id}


class FakeResponse:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]


def _make_client(script):
    """构造一个"按剧本返回响应"的 mock 客户端，接口对齐 openai.OpenAI。"""
    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return FakeResponse(self._outer.script.pop(0))

    class _Chat:
        def __init__(self, outer):
            self.completions = _Completions(outer)

    class _Client:
        def __init__(self, script):
            self.script = list(script)
            self.calls = []
            self.chat = _Chat(self)

    return _Client(script)


class AutoConfirm:
    """测试用：自动放行所有危险操作。"""

    def __call__(self, description: str) -> bool:
        return True


@pytest.fixture
def workdir():
    with TemporaryDirectory() as d:
        root = Path(d)
        (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        yield root


def _sent_messages(agent):
    return agent.ctx.snapshot()


def test_loop_executes_tool_and_returns_final_answer(workdir):
    script = [
        FakeMessage(tool_calls=[FakeToolCall("c1", "read_file", {"path": "calc.py"})]),
        FakeMessage(content="任务完成，add 函数返回 a+b。"),
    ]
    agent = AgentLoop(_make_client(script), model="fake", workdir=workdir, confirm=AutoConfirm())
    final = agent.run("读 calc.py 并总结")

    assert final == "任务完成，add 函数返回 a+b。"
    tool_msgs = [m for m in _sent_messages(agent) if m.get("role") == "tool"]
    assert tool_msgs, "工具结果必须以 role=tool 回传"
    assert "def add" in tool_msgs[0]["content"]


def test_tool_error_is_returned_to_model_not_raised(workdir):
    script = [
        # 模型先犯了个错：读不存在的文件
        FakeMessage(tool_calls=[FakeToolCall("c1", "read_file", {"path": "nope.py"})]),
        FakeMessage(content="文件不存在，任务终止。"),
    ]
    agent = AgentLoop(_make_client(script), model="fake", workdir=workdir, confirm=AutoConfirm())
    final = agent.run("读 nope.py")

    tool_msgs = [m for m in _sent_messages(agent) if m.get("role") == "tool"]
    assert "文件不存在" in tool_msgs[0]["content"]
    assert final == "文件不存在，任务终止。"


def test_invalid_tool_name_returns_error(workdir):
    script = [
        FakeMessage(tool_calls=[FakeToolCall("c1", "delete_everything", {})]),
        FakeMessage(content="收到错误，停止。"),
    ]
    agent = AgentLoop(_make_client(script), model="fake", workdir=workdir, confirm=AutoConfirm())
    agent.run("乱调用工具")
    tool_msgs = [m for m in _sent_messages(agent) if m.get("role") == "tool"]
    assert "无效的工具调用" in tool_msgs[0]["content"]


def test_path_escape_is_blocked(workdir):
    script = [
        FakeMessage(tool_calls=[FakeToolCall("c1", "read_file", {"path": "../../etc/passwd"})]),
        FakeMessage(content="路径被拒。"),
    ]
    agent = AgentLoop(_make_client(script), model="fake", workdir=workdir, confirm=AutoConfirm())
    agent.run("越界读取")
    tool_msgs = [m for m in _sent_messages(agent) if m.get("role") == "tool"]
    assert "越界" in tool_msgs[0]["content"] or "error" in tool_msgs[0]["content"]


def test_write_requires_confirmation(workdir):
    confirm_log = []

    def strict_confirm(desc):
        confirm_log.append(desc)
        return False  # 一律拒绝

    script = [
        FakeMessage(tool_calls=[FakeToolCall("c1", "write_file", {"path": "x.py", "content": "1"})]),
        FakeMessage(content="写入被拒。"),
    ]
    agent = AgentLoop(_make_client(script), model="fake", workdir=workdir, confirm=strict_confirm)
    agent.run("写文件")
    tool_msgs = [m for m in _sent_messages(agent) if m.get("role") == "tool"]
    assert confirm_log, "写操作必须触发确认回调"
    assert "rejected" in tool_msgs[0]["content"]
    assert not (workdir / "x.py").exists(), "被拒绝的写入不得落盘"


def test_max_steps_guard(workdir):
    # 无限要求工具调用的剧本：验证 max_steps 兜底
    call = FakeMessage(tool_calls=[FakeToolCall("c1", "list_dir", {})])
    agent = AgentLoop(_make_client([call] * 100), model="fake", workdir=workdir,
                      confirm=AutoConfirm(), max_steps=3)
    final = agent.run("无限循环测试")
    assert "最大步数" in final
