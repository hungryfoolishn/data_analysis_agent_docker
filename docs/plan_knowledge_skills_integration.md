# 业务知识 & 分析技能融入方案

> 状态：规划阶段（v2，参考 hermes-agent Skills 机制） | 优先级：P1 | 预计工期：5-7 天

## 1. 设计理念

参考 [hermes-agent](https://github.com/nicobailey/hermes) 的 Skills 架构，核心设计原则：

| 原则 | 说明 |
|------|------|
| **Markdown 即技能** | 每个技能是一个 SKILL.md 文件 + 可选的参考文档/模板，无需写 Python 类 |
| **渐进式披露** | 系统提示只列技能元数据（名称+描述），Agent 按需加载完整指令 |
| **目录即注册** | 技能放在固定目录结构下自动发现，无需手动注册 |
| **自进化** | Agent 执行复杂分析后可自动提取为新技能保存 |

---

## 2. 技能目录结构

```
langgraph_langchain/
├── skills/                              # 技能根目录
│   ├── data-analysis/                   # 类别：数据分析方法
│   │   ├── funnel-analysis/
│   │   │   ├── SKILL.md                 # 漏斗分析技能指令
│   │   │   └── references/
│   │   │       └── ecommerce_funnel.md  # 电商漏斗参考知识
│   │   ├── cohort-analysis/
│   │   │   └── SKILL.md
│   │   ├── rfm-segmentation/
│   │   │   └── SKILL.md
│   │   ├── attribution-analysis/
│   │   │   └── SKILL.md
│   │   ├── anomaly-diagnosis/
│   │   │   └── SKILL.md
│   │   └── trend-forecast/
│   │       └── SKILL.md
│   ├── domain/                          # 类别：行业领域知识
│   │   ├── rd-efficiency/
│   │   │   ├── SKILL.md                 # R&D 效率分析技能
│   │   │   └── references/
│   │   │       ├── metrics.md           # 指标定义与口径
│   │   │       └── validators.md        # 验证规则
│   │   ├── ecommerce/
│   │   │   ├── SKILL.md                 # 电商分析技能
│   │   │   └── references/
│   │   │       ├── gmv_metrics.md       # GMV/客单价/复购率定义
│   │   │       └── benchmarks.md        # 行业基准值
│   │   └── finance/
│   │       └── SKILL.md
│   └── reporting/                       # 类别：报告生成
│       ├── executive-summary/
│       │   └── SKILL.md
│       └── data-quality-report/
│           └── SKILL.md
├── skills_loader.py                     # 技能加载器（核心）
└── langgraph_agent.py                   # 集成入口
```

---

## 3. SKILL.md 格式规范

每个技能以 YAML frontmatter + Markdown 正文定义，兼容 [agentskills.io](https://agentskills.io) 标准：

```markdown
---
name: funnel-analysis
description: >
  用户行为漏斗分析：定义漏斗步骤、计算逐步转化率、定位流失环节、
  按维度拆分分析流失原因。适用于有用户ID+事件类型+时间的数据。
version: 1.0.0
metadata:
  tags: [漏斗, 转化率, 流失, funnel, conversion, drop-off]
  category: data-analysis
  trigger_keywords:
    - 转化
    - 漏斗
    - 流失
    - 每步转化
  data_patterns:            # 数据特征匹配（可选）
    has_user_id: true
    has_event_type: true
    has_timestamp: true
  related_skills:
    - cohort-analysis
    - rfm-segmentation
---

# 漏斗分析

## 适用场景

当用户问题涉及"转化流程"、"每步转化率"、"哪个环节流失最多"时，
使用此技能。

## 分析工作流

### Step 1: 定义漏斗步骤

根据数据中的事件/状态列，定义漏斗各步骤。
- 步骤必须按业务逻辑排列，不能跳步
- 常见电商漏斗：浏览 → 加购 → 下单 → 支付 → 完成
- 使用 python_repl 统计每个步骤的用户数

### Step 2: 计算转化率

计算以下指标并保存图表：
- 逐步转化率（step-to-step）
- 整体转化率（第一步到最后一步）
- 流失率 = 1 - 转化率

参考基准：
- 电商整体转化率：2-5%
- 加购→下单：10-30%
- 下单→支付：60-80%

### Step 3: 定位流失环节

找出流失率最高的步骤，按维度拆分分析：
- 设备维度（移动端 vs PC）
- 渠道维度（自然流量 vs 付费流量）
- 新老用户维度

### Step 4: 生成建议

用 record_finding 记录发现，包括：
- 最大流失环节及其转化率
- 各维度的差异
- 可执行的改进建议

## 注意事项

- 漏斗步骤必须有时间先后顺序
- 注意去重：同一用户在同一步骤只计算一次
- 大促/节假日期间数据需单独分析，不能与日常数据混合
```

---

## 4. 核心模块：skills_loader.py

```python
"""
技能加载器 — 渐进式披露架构

三层披露：
  Tier 1: skills_list()     → 元数据（名称+描述+标签），注入系统提示
  Tier 2: skill_view()      → 完整 SKILL.md 正文，Agent 按需加载
  Tier 3: references/       → 参考文档，深度场景按需加载
"""

from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel
import yaml


class SkillMeta(BaseModel):
    """技能元数据（Tier 1）"""
    name: str
    description: str
    version: str = "1.0.0"
    tags: List[str] = []
    category: str = ""
    trigger_keywords: List[str] = []
    data_patterns: Dict = {}
    related_skills: List[str] = []


class Skill(BaseModel):
    """完整技能（Tier 2）"""
    meta: SkillMeta
    content: str                      # SKILL.md 正文
    references: Dict[str, str] = {}   # 参考文档名 → 路径
    path: Path


class SkillsLoader:
    """技能加载与匹配引擎"""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self._cache: Dict[str, Skill] = {}
        self._load_all()

    def _load_all(self):
        """扫描 skills/ 下所有 SKILL.md，解析元数据"""
        for skill_file in self.skills_dir.rglob("SKILL.md"):
            meta, content = self._parse_skill(skill_file)
            if meta:
                self._cache[meta.name] = Skill(
                    meta=meta,
                    content=content,
                    references=self._find_references(skill_file),
                    path=skill_file,
                )

    def _parse_skill(self, path: Path) -> tuple:
        """解析 SKILL.md 的 YAML frontmatter + Markdown 正文"""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None, ""
        _, fm, body = text.split("---", 2)
        data = yaml.safe_load(fm)
        hermes_meta = data.get("metadata", {}).get("hermes", {})
        # 也支持扁平的 metadata
        flat_meta = data.get("metadata", {})
        meta = SkillMeta(
            name=data.get("name", path.parent.name),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            tags=hermes_meta.get("tags", flat_meta.get("tags", [])),
            category=hermes_meta.get("category", flat_meta.get("category", "")),
            trigger_keywords=flat_meta.get("trigger_keywords", []),
            data_patterns=flat_meta.get("data_patterns", {}),
            related_skills=hermes_meta.get("related_skills", flat_meta.get("related_skills", [])),
        )
        return meta, body.strip()

    # ── Tier 1: 列表（轻量，用于系统提示） ────────────────
    def skills_list(self, category: str = None) -> List[SkillMeta]:
        """返回所有技能的元数据列表"""
        skills = [s.meta for s in self._cache.values()]
        if category:
            skills = [s for s in skills if s.category == category]
        return skills

    def build_skills_prompt(self) -> str:
        """生成注入系统提示的技能索引（Token 高效）"""
        lines = ["## 可用分析技能 (Skills)", ""]
        for meta in self.skills_list():
            keywords = ", ".join(meta.trigger_keywords[:5])
            lines.append(f"- **{meta.name}**: {meta.description[:120]}")
            lines.append(f"  触发词: {keywords}")
        return "\n".join(lines)

    # ── Tier 2: 查看完整内容 ──────────────────────────────
    def skill_view(self, name: str) -> Optional[str]:
        """加载完整技能指令（Agent 按需调用）"""
        skill = self._cache.get(name)
        return skill.content if skill else None

    # ── Tier 3: 参考文档 ─────────────────────────────────
    def skill_reference(self, name: str, ref_name: str) -> Optional[str]:
        """加载技能的参考文档"""
        skill = self._cache.get(name)
        if not skill:
            return None
        ref_path = skill.references.get(ref_name)
        if ref_path and ref_path.exists():
            return ref_path.read_text(encoding="utf-8")
        return None

    # ── 技能匹配 ─────────────────────────────────────────
    def match(self, question: str, df=None) -> List[tuple]:
        """根据用户问题匹配最相关的技能

        返回: [(skill_name, confidence), ...] 按置信度排序
        """
        scored = []
        q_lower = question.lower()
        for skill in self._cache.values():
            score = 0.0
            # 关键词匹配
            for kw in skill.meta.trigger_keywords:
                if kw.lower() in q_lower:
                    score += 0.3
            # 标签匹配
            for tag in skill.meta.tags:
                if tag.lower() in q_lower:
                    score += 0.2
            # 数据特征匹配（如果有 DataFrame）
            if df is not None and skill.meta.data_patterns:
                score += self._score_data_match(df, skill.meta.data_patterns)
            if score > 0:
                scored.append((skill.meta.name, min(score, 1.0)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:3]  # 返回 Top-3

    def _score_data_match(self, df, patterns: Dict) -> float:
        """评估 DataFrame 与技能数据模式的匹配度"""
        score = 0.0
        cols_lower = [c.lower() for c in df.columns]
        dtypes = df.dtypes

        if patterns.get("has_user_id"):
            id_cols = [c for c in df.columns if any(
                k in c.lower() for k in ["user", "uid", "customer", "member"]
            )]
            if id_cols:
                score += 0.2

        if patterns.get("has_timestamp"):
            time_cols = [c for c in df.columns if any(
                k in c.lower() for k in ["time", "date", "datetime", "timestamp"]
            )]
            if time_cols:
                score += 0.2

        if patterns.get("has_event_type"):
            event_cols = [c for c in df.columns if any(
                k in c.lower() for k in ["event", "action", "type", "status"]
            )]
            if event_cols:
                score += 0.2

        return score
```

---

## 5. Agent 集成方式

### 5.1 系统提示注入（Tier 1）

在 `_SYSTEM_PROMPT` 中动态插入技能索引：

```python
# langgraph_agent.py 中
from langgraph_langchain.skills_loader import SkillsLoader

skills_loader = SkillsLoader(Path(__file__).parent / "skills")

# 在 _SYSTEM_PROMPT 末尾追加
SYSTEM_PROMPT_WITH_SKILLS = _SYSTEM_PROMPT + "\n\n" + skills_loader.build_skills_prompt()
```

### 5.2 新增 Agent 工具

```python
@tool
def skill_view(skill_name: str) -> str:
    """加载指定分析技能的完整指令。
    当你判断当前分析任务匹配某个技能时，调用此工具获取详细工作流。

    Args:
        skill_name: 技能名称，如 "funnel-analysis"
    """
    content = skills_loader.skill_view(skill_name)
    if content:
        return content
    return f"Skill '{skill_name}' not found. Use available skills from the skills list."

@tool
def skill_reference(skill_name: str, reference: str) -> str:
    """加载技能的参考文档（行业知识、指标定义等）。

    Args:
        skill_name: 技能名称
        reference: 参考文档名，如 "metrics.md"
    """
    content = skills_loader.skill_reference(skill_name, reference)
    if content:
        return content
    return f"Reference '{reference}' not found in skill '{skill_name}'."
```

### 5.3 自动匹配流程

```
用户提交分析请求
    ↓
Agent 调用 load_data → eda_profile
    ↓
EDA 结果 + 用户问题 → skills_loader.match()
    ↓
匹配到 skill（如 funnel-analysis）
    ↓
Agent 调用 skill_view("funnel-analysis")
    ↓
获取完整工作流步骤
    ↓
按步骤执行分析
```

---

## 6. 与现有 R&D 模块的关系

现有 `rd_*.py` 中的知识逐步迁移为 SKILL.md 格式：

| 现有文件 | 迁移目标 | 内容 |
|---------|---------|------|
| `rd_efficiency_domain.py` | `skills/domain/rd-efficiency/references/metrics.md` | 指标定义 |
| `rd_templates.py` | `skills/domain/rd-efficiency/SKILL.md` | 分析模板 |
| `rd_validators.py` | `skills/domain/rd-efficiency/references/validators.md` | 验证规则 |
| `rd_metric_library.py` | `skills/domain/rd-efficiency/references/calculations.md` | 计算方法 |

迁移策略：
1. 保留现有 `rd_*.py` 不动（向后兼容）
2. 新建 SKILL.md 版本，内容从 Python 提取为 Markdown
3. Agent 优先走 Skills 框架，旧代码作为 fallback

---

## 7. 实施路线

### Phase 1：基础设施（1.5 天）
- [ ] 创建 `skills/` 目录结构和 `skills_loader.py`
- [ ] 实现 YAML frontmatter 解析 + 渐进式披露（3 层）
- [ ] 实现 `skills_list` / `skill_view` / `skill_reference` 工具
- [ ] 集成到 `_SYSTEM_PROMPT`（动态注入技能索引）

### Phase 2：内置技能（2 天）
- [ ] 编写 3 个高优技能 SKILL.md：漏斗分析 / 同期群分析 / RFM 分群
- [ ] 编写 1 个领域知识技能：R&D 效率（迁移现有 rd_ 模块）
- [ ] 每个技能配套 references/ 参考文档
- [ ] 实现关键词 + 数据特征匹配的 `match()` 方法

### Phase 3：Agent 集成测试（1 天）
- [ ] 集成到 `langgraph_agent.py` 的工具链
- [ ] E2E 测试：用户问"帮我分析转化漏斗" → 自动匹配 → 按工作流执行
- [ ] 更新 CLAUDE.md

### Phase 4：自定义技能入口（1 天）
- [ ] API 端点：`POST /skills/upload` 上传自定义 SKILL.md
- [ ] API 端点：`GET /skills` 列出已安装技能
- [ ] 支持通过 workspace 目录放置技能文件

### Phase 5：技能自进化（1 天）
- [ ] 分析完成后，Agent 自动判断是否值得保存为新技能
- [ ] 将成功的工作流提取为 SKILL.md 格式
- [ ] 保存到 `workspace/<session_id>/skills/` 下

---

## 8. 相比 v1 方案的关键改进

| 维度 | v1 方案（Python 类） | v2 方案（Markdown 技能） |
|------|-------------------|----------------------|
| 技能定义方式 | Python Pydantic 类 | SKILL.md（YAML + Markdown） |
| 新增技能成本 | 写 Python 代码 | 写 Markdown 文件 |
| 业务人员可用 | ❌ 需要开发 | ✅ 只需写文档 |
| Token 效率 | 全量加载 | 渐进式披露（3 层） |
| 自进化能力 | 需代码生成 | 天然支持（Markdown 可编辑） |
| 社区复用 | ❌ | ✅ 兼容 agentskills.io |
| 参考文档 | 耦合在代码中 | 独立 references/ 目录 |
