# 技术资料 3：代码智能（职责 2 落档 + 职责 3 重构的基础）

## 1. 代码解析：tree-sitter（必须实际用过）

### 是什么

tree-sitter 是增量式语法解析器生成器，一次解析即可得到**带精确位置信息的语法树**，支持 100+ 语言（Python、Java、Go、SQL……）。VS Code 的语法高亮、GitHub 的代码导航都在用它。

### 为什么 Coding Agent 工具栈离不开它

1. **跨语言**：银行存量代码以 Java 为主，你的工具是 Python 写的——tree-sitter 让 Python 工具能解析 Java 代码。
2. **结构化切块**：RAG 里按函数/类切块（AST 切块）远好于按行数滑动窗口——函数是完整的语义单元，不会把一个方法切成两半。
3. **精确重构**：AST 节点带起止字节位置，定位替换可以做到"只动这一个方法"。
4. **增量解析**：文件改动后只重新解析变化部分，百万行级索引实时更新的基础。

### 最小上手（Python 端）

```bash
pip install tree-sitter tree-sitter-python tree-sitter-java
```

```python
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANG = Language(tspython.language())
parser = Parser(PY_LANG)

tree = parser.parse(source_bytes)

# 遍历所有函数定义节点
def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)

for node in walk(tree.root_node):
    if node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        body_node = node.child_by_field_name("body")
        print(name_node.text.decode(), node.start_point, node.end_point)
```

**练习到什么程度**：能用 tree-sitter 写一个"提取文件里所有函数及其 docstring、调用关系"的脚本（demo 里已包含简化版，读 `mini-agent/` 的实现）。

### 对比选项（面试聊选型用）

| 工具 | 适用 | 局限 |
|---|---|---|
| tree-sitter | 跨语言通用解析、检索、切块 | 语法层，不做类型/作用域完整分析 |
| Python `ast` 模块 | Python 原生精确分析、AST 变换 | 只支持 Python；重构变换 API 强大 |
| JavaParser（Java） | Java 深度分析、符号解析 | Java 专用，JVM 生态 |
| LSP（语言服务器） | 精确的引用查找、跳转、重命名 | 每语言一个 server，集成成本高，但**精度天花板最高** |
| ctags/universal-ctags | 轻量符号索引 | 只有定义位置，无引用关系 |

**分层观点**：tree-sitter 做快而糙的第一层（切块、粗检索），LSP/类型分析做慢而准的第二层（精确引用、重构验证）。

## 2. 代码检索：RAG 在代码场景的特殊性

### Chunking 策略（核心考点）

| 策略 | 做法 | 评价 |
|---|---|---|
| 按行/字符滑动窗口 | 每 50 行一切 | 最差。函数被腰斩，检索命中也读不通 |
| **AST 切块（推荐）** | tree-sitter 按函数/类/方法切块，超长函数再内部细分 | 语义完整；每个 chunk 记录 `file:class.method` 路径和行号 |
| 定义+引用扩展 | 命中某函数后，把它的调用方/被调方也捞出来 | 检索质量飞跃，靠符号索引支持 |

### Embedding 与混合检索

- **通用 embedding（BGE-M3、text-embedding-3）对代码够用但不完美**；代码专用如 Voyage code 系列、jina-code 更好。银行内网可用 BGE 本地部署。
- **代码检索必须混合三路，只用向量是明显短板**：
  1. **符号匹配**（精确/正则）——代码里标识符是强信号，grep 永远不可替代；
  2. **向量检索**——"哪里处理了对账逻辑"这类语义查询；
  3. **结构检索**——依赖图/调用图，回答"改这个函数影响谁"。
- 实践配比：先 grep + 符号索引，零结果或结果发散时再走向量。Claude Code 就是以 grep 为主、向量为辅（其 EM 检索靠 ripgrep + glob），**这个事实本身就说明纯向量 RAG 不是编码工具的银弹**。

### 向量库

- **pgvector**：银行已有 PG 基础设施，权限体系可复用，首选推荐。
- Milvus / Qdrant：大规模、独立部署时考虑。
- demo 级别：sqlite-vec 或直接 numpy 暴力检索都行，讲清"规模到多大再换库"。

## 3. 代码知识库：落档的地基（职责 2 的核心）

存量资产落档 ≠ 生成注释文档，而是构建一个**活的代码知识库**，让 AI 和人都能查：

1. **符号表**：所有类/函数/接口的定义位置、签名、docstring（缺失的用 LLM 补生成，标注"AI 生成待确认"）。
2. **依赖图/调用图**：模块间 import 关系、函数调用关系（静态分析可得），支撑变更影响分析。
3. **数据流文档**：银行系统核心是数据——关键表、字段含义、接口报文、上下游系统对接点，这些信息往往只在老员工脑子里。
4. **业务规则提取**：从代码+存储过程里提取"计息规则""额度控制"等业务规则，落成结构化文档。这是最难也最有价值的部分——**让 LLM 读代码归纳规则，人审核确认**。
5. **更新机制**：代码变更触发增量更新（git hook / CI 监听），否则知识库三个月就腐烂。**面试里必须主动讲"知识库的生命周期管理"，只讲生成不讲维护是新手信号。**

## 4. 大规模代码库的工程问题

- **索引性能**：百万行级首次索引要分批、并行 embedding；文件监听（watchdog）做增量更新。
- **检索延迟**：符号索引放内存/SQLite，向量检索走本地服务，P99 要控制在秒级。
- **权限隔离**：银行项目间代码隔离，检索层必须带项目/模块权限过滤——**这一点 banking 场景几乎必问**。

## 5. 学习资源

- tree-sitter 官方文档: https://tree-sitter.github.io/tree-sitter/
- tree-sitter Python binding: https://github.com/tree-sitter/py-tree-sitter
- Voyage AI code embeddings: https://docs.voyageai.com/docs/embeddings
- pgvector: https://github.com/pgvector/pgvector
- 论文《RepoFusion / Repository-level code completion》类文章了解 repo 级检索思路；Magentic-One / SWE-bench 相关文章看 Agent 怎么检索仓库

## 6. 自查问题

1. 用 tree-sitter 写脚本提取一个 Java 文件所有 public 方法签名。
2. 为什么代码 RAG 按函数切块而不是固定行数？超长函数怎么办？（递归细分 + 保留路径上下文）
3. "改 A 函数影响哪些调用方"——纯向量检索能回答吗？不能，靠什么？（调用图/静态分析）
4. 设计一个增量索引方案：开发者提交 git commit 后，索引层发生什么？（diff 文件列表 → 解析变更函数 → 增量 embedding → 更新符号表）
5. 知识库怎么防止内容过时？怎么让开发者信任 AI 生成的文档？

---

## 自查问题参考答案

### 1. 用 tree-sitter 写脚本提取一个 Java 文件所有 public 方法签名

```python
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

parser = Parser(Language(tsjava.language()))
tree = parser.parse(open("Foo.java", "rb").read())

def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)

for node in walk(tree.root_node):
    if node.type == "method_declaration":
        # method_declaration 的 modifiers 子节点包含 public/private
        modifiers = [c.text.decode() for c in node.children if c.type == "modifiers"]
        if "public" not in " ".join(modifiers):
            continue
        name = node.child_by_field_name("name").text.decode()
        params = node.child_by_field_name("parameters").text.decode()
        ret = node.child_by_field_name("type").text.decode()
        print(f"{ret} {name}{params}  (line {node.start_point[0]+1})")
```

注意两个细节：① 节点的 `text` 是 bytes，要 decode；② `start_point` 是 0 基行号，展示给人看要 +1。能独立跑通这个脚本，tree-sitter 的基本操作就过关了。

### 2. 为什么代码 RAG 按函数切块而不是固定行数？超长函数怎么办？

按函数切块的理由：
- **函数是完整的语义单元**：签名+docstring+实现是一个可独立理解的闭环；固定行数切割会把方法腰斩，检索命中的半个函数读不通，embedding 也学不到完整语义；
- **元数据天然可用**：每个 chunk 自带 `file:class.method` 路径和行号范围，检索结果可以直接定位跳转；
- **边界稳定**：代码变更只影响所在函数的 chunk，增量更新粒度最小。

超长函数（如几百行的大方法，银行老代码很常见）的处理：
- 递归细分：函数体内部按语句块/分支切分，每个子块保留"路径上下文"（chunk 文本前缀带上函数签名和文件路径，否则 embedding 不知道这段代码属于谁）；
- 或粗细两级索引：函数级摘要 chunk + 语句块级明细 chunk，先命中摘要再下钻。

### 3. "改 A 函数影响哪些调用方"——纯向量检索能回答吗？

**不能，这是向量检索的能力边界题。** 原因：调用关系是**图结构**问题，不是语义相似度问题——调用方代码可能和 A 函数毫无文本相似性（不同模块、不同命名风格），embedding 根本不会把"调用 A 的代码块"和 A 本身排在邻近；反过来，语义相似但不调用 A 的代码会被错误召回。

正确工具是**静态分析得到的调用图**：
- tree-sitter 够做粗版：解析所有函数定义 + 收集"名字被调用"的位置（但会被同名函数/动态调用干扰）；
- 精确版用 LSP 的 find references（Java 用 Eclipse JDT LS），能处理重载、继承、动态分派；
- 检索架构上这是"第三路"：符号检索（名字）→ 向量检索（语义）→ 结构检索（关系）。变更影响分析必须走第三路。

面试加分句："这个例子正好说明代码 RAG 和文档 RAG 的本质区别——代码有严格的图结构，结构检索不是锦上添花而是刚需。"

### 4. 设计一个增量索引方案：开发者提交 git commit 后，索引层发生什么？

```
git push → hook 触发
  ① git diff --name-only HEAD~1 HEAD 拿到变更文件列表
  ② 对每个变更文件：
     - tree-sitter 重新解析（毫秒级）
     - 对比新旧 AST：按函数节点 diff，找出新增/删除/修改的函数
  ③ 只对变更的函数 chunk 重新 embedding（而非整文件、更非全库）
  ④ 更新符号表（定义/签名/行号）和调用图的受影响边
  ⑤ 向量库里 upsert 受影响的 chunk（按 chunk_id = file:path#symbol 覆盖旧向量）
```

关键设计点：
- **chunk_id 稳定**：以"文件+符号路径"为 ID，函数没改就不用重算；
- **延迟预算**：单 commit 的增量更新应控制在秒级，这样索引永远"准实时"，不需要夜间全量重建兜底（但每周全量重建一次用于纠偏）；
- **失败恢复**：处理失败的文件进重试队列，不阻塞后续 commit 的索引。

### 5. 知识库怎么防止内容过时？怎么让开发者信任 AI 生成的文档？

防过时的核心认知：**知识库不是一次生成的资产，是跟着代码走的流水线**。
- 生成流程挂进 CI/CD：代码合并即触发相关文档的增量重生成，落档脚本把"文档对应代码版本"写进元数据；
- 文档带"新鲜度标记"：代码变更后文档未重生成 → 标记 stale，检索时降权或提示"此文档落后于代码 N 个 commit"；
- 无人维护的文档宁可下线，错误文档比没文档危害大。

建立信任的机制：
- **标注来源**：每段 AI 生成的文档标注"AI 生成，基于 commit abc123 的代码"，并附生成依据（哪些文件/函数），可点击溯源——能验证的 AI 输出才敢信；
- **人审关口**：AI 生成 → 定期 review → 人工确认后升级为"已审核"状态；审核成本用抽样控制（高危模块全审，普通模块抽审）；
- **反馈闭环**：开发者能一键标记"这条不对"，标记直接进生成器的改进 backlog；
- **用使用数据说话**：展示知识库的检索命中率、被 Agent 引用后任务成功率，让大家看到它真的在省时间——信任最终来自有用，而不是来自文案。
