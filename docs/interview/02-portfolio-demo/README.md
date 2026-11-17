# 作品集 Demo：手写 Agent Loop 的迷你 Coding Agent

## 这是什么

一个 **200 行左右、零框架**的 coding agent，证明三件事：

1. 我懂 Agent Loop 的原理（不依赖 LangChain 等框架手写）
2. 我懂工具工程（权限、路径安全、输出截断、错误自愈）
3. 我懂上下文工程（多级压缩策略）

## 文件结构

```
mini-agent/
├── main.py               CLI 入口（argparse + 交互确认 + 过程打印）
├── agent.py              Agent Loop 主体（system prompt + 工具分发）
├── tools.py              6 个工具实现 + JSON Schema + 安全防护
├── context_manager.py    上下文管理（token 估算 + 三级压缩）
├── test_agent_loop.py    mock LLM 的单元测试（展示 evals 思路）
└── sandbox/              演示用的小项目（Agent 的操作对象）
    ├── greet.py
    └── test_greet.py
```

## 运行

```bash
pip install openai

# 任何 OpenAI 兼容端点都行：DeepSeek / GLM / Qwen / 本地 vLLM
set OPENAI_API_KEY=sk-xxx
set OPENAI_BASE_URL=https://api.deepseek.com/v1

cd docs/interview/02-portfolio-demo/mini-agent

# 让 Agent 在 sandbox 里干活
python main.py --task "先读 sandbox/greet.py，然后给它加一个 say_goodbye(name) 函数，并运行 python -m pytest test_greet.py 验证" --workdir ./sandbox

# 单元测试（不需要 API key，mock LLM）
python -m pytest test_agent_loop.py -v
```

## 工具集（对齐 Claude Code 的极简正交思路）

| 工具 | 作用 | 安全措施 |
|---|---|---|
| `list_dir` | 浏览目录结构 | 路径越界校验 |
| `read_file` | 按行号范围读文件 | 二进制文件拦截、分页读取 |
| `write_file` | 创建/覆写文件 | 人工确认 + 路径校验 |
| `edit_file` | 精确旧文本替换（对齐 Claude Code Edit 设计） | 唯一性校验 + 人工确认 |
| `run_shell` | 执行命令/跑测试 | 人工确认 + 超时 + 输出截断 |
| `grep` | 代码搜索（Agent 检索主力） | 忽略 .git 等、命中数上限 |

## 演示要点（面试现场怎么讲）

1. **先跑任务**，指着过程输出讲 Agent 的每一步决策。
2. **现场演示一次确认拒绝**：写文件时输入 N，Agent 收到 "rejected" 后改用其他方式——展示权限设计真的在工作。
3. **指着上下文 token 计数**讲压缩时机：观察 `~xxxxx tok` 的变化。
4. **讲错误自愈**：故意让它读不存在的文件，看错误信息回传后模型的修正行为。

深入的设计理由全部在 [docs/design-decisions.md](docs/design-decisions.md)。
