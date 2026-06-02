<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Streamlit-1.57+-red?logo=streamlit" alt="Streamlit">
  <img src="https://img.shields.io/badge/BGE--small--zh-v1.5-orange" alt="BGE">
  <img src="https://img.shields.io/badge/LLM-DeepSeek%20V4-green" alt="DeepSeek">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="License">
</p>

<h1 align="center">🌿 中医药知识查询系统</h1>

<p align="center">
  基于 <b>BGE 语义检索</b>与<b> DeepSeek LLM</b> 的多模式中医药智能查询平台
</p>

---

## 项目简介

中医药知识体系博大精深，但海量数据分散在不同文献与数据库中，普通用户难以高效检索。本系统利用知识工程中的**语义检索**、**知识推理**和**大语言模型**技术，构建了一套面向中医药领域的智能知识查询系统。

**核心能力：**
- 支持中文名/拼音名/英文名多语言中药检索
- 基于 BGE 向量相似度的模糊语义检索，输入不精确也能匹配
- 自动将用户的口语化症状（如"胃痛"）映射为标准中医术语（"胃脘痛"）
- 症状→相关概念→药方→中草药的完整推理链路
- DeepSeek LLM 生成 300–500 字中医师自然语言解读
- 英-中双语字段翻译（功效、主治、性味归经等）
- 暗/亮双主题自适应 UI

**数据来源：** [HERB 数据库](http://herb.ac.cn/v2/)（7,263 种中药 · 6,749 首药方 · 2,364 条症状/证候概念）

---

## 快速开始

### 环境要求

- Python 3.10+
- 磁盘空间 ≥ 1 GB（BGE 模型 + 向量索引缓存）

### 安装

```bash
git clone <仓库地址> && cd traditional-chinese-medicine-recommendation-system
pip install -r requirements.txt
```

### 配置 LLM（可选）

模式 3 的中医师解读和英-中翻译需要 DeepSeek API 密钥。未配置时系统正常运行，仅跳过 LLM 功能。

三种配置方式（按优先级）：环境变量 `DEEPSEEK_API_KEY` → `.streamlit/secrets.toml` → `.env` 文件。

### 启动

```bash
streamlit run app.py
```

首次运行自动下载 BGE 模型（~100 MB）并构建向量索引（3–5 分钟）。后续启动直接加载索引缓存。

打开 `http://localhost:8501` 即可使用。

---

## 四种查询模式

| 模式 | 功能 | 输入示例 | 核心技术 |
|------|------|----------|----------|
| **模式 0**<br>🌿 中药查询 | 按中文名/拼音/英文检索 | `人参` `renshen` `Ginseng` | 名称四级匹配 → 语义回退 |
| **模式 1**<br>💊 药方查询 | 按方名检索，展现组成/证型/主治 | `六味地黄丸` `桂枝汤` | 名称匹配 + 语义补充 |
| **模式 2**<br>🔍 症状推方 | 症状→推荐药方（支持多症状） | `头痛，失眠，心悸` | 术语标准化 + BGE 检索<br>+ 复合置信度模型<br>+ 症状补充建议 |
| **模式 3**<br>🌐 推理链路 | 症状→概念→药方→草药<br>含 LLM 解读 | `怕冷腰膝酸软夜尿多` | 四步推理 + 语义自洽性检查<br>+ DeepSeek 中医师解读 |

### 各模式效果概览

**模式 0/1 — 名称查询：** 返回完整信息卡片，包含性味、归经、功效、主治、毒性等字段（中英双语）。匹配类型标注为"完全匹配 / 前缀匹配 / 包含匹配 / 语义补充"。

**模式 2 — 症状推方：** 展示三部分可视化：
- 识别到的症状标签栏 + 术语标准化映射
- Sankey 图展示症状→药方的匹配流向（连线粗细 = 置信度权重）
- 药方卡片列表含评分徽章和匹配症状标签
- 置信度仪表（信息充分 / 可参考 / 建议补充症状）

**模式 3 — 推理链路：** 展示四层推理过程（交互式节点图可按节点点击查看详情）：
1. 输入症状
2. 匹配到的相关症状/证候概念（含相似度）
3. 推荐药方（含组成和适应症）
4. 组成中草药（含功效、主治）
5. DeepSeek LLM 自然语言解读（"辨证→选方→用药"逻辑）
6. 语义自洽性分数

<details>
<summary><b>📁 项目结构（点击展开）</b></summary>

```
traditional-chinese-medicine-recommendation-system/
├── app.py                      # Streamlit 主入口
├── requirements.txt            # Python 依赖清单
├── backend/                    # 后端业务逻辑
│   ├── query.py                # 统一查询入口（四种模式分发）
│   ├── semantic.py             # BGE 语义检索引擎 + 向量索引缓存
│   ├── chain_reasoning.py      # 推理链路（症状→概念→药方→草药）
│   └── llm.py                  # DeepSeek API（解释生成 + 批量翻译）
├── ui/                         # Streamlit UI 组件
│   ├── theme.py                # 暗/亮双主题 CSS 注入
│   ├── components.py           # 可复用卡片组件（中药/药方/置信度仪表等）
│   ├── graph_mode2.py          # Sankey 症状-药方匹配流图
│   ├── graph_mode3.py          # 推理链路交互式节点图
│   ├── log_timeline.py         # 检索日志垂直时间线
│   └── animations.py           # Lottie 加载动画
├── data_csv/                   # HERB 数据 + BGE 向量缓存（.npy / .pkl）
├── 数据挖掘/                   # 数据增强脚本（爬虫 + AI 填充）
├── scripts/                    # 评估与测试脚本
└── origin_data/                # HERB 原始文本数据
```
</details>

---

## 技术架构

```
表示层（Streamlit UI）
  ↓
业务逻辑层（query.py / chain_reasoning.py / llm.py）
  ↓
语义检索层（BGE Embedding + 余弦相似度 Top-K + 索引缓存）
  ↓
数据层（HERB CSV + BGE 向量索引 / 原始 + AI 增强双版本）
```

### 核心技术点

- **语义检索**：`BAAI/bge-small-zh-v1.5`，L2 归一化向量 + 余弦相似度。中药/药方/症状三类实体分别维护独立索引
- **名称匹配**：完全匹配(100+权重) > 前缀匹配(75+权重) > 包含匹配(45+权重) > 语义回退
- **术语标准化**：用户口语词 → BGE 检索症状库 → 映射到标准中医术语 + 同义词去重
- **置信度模型**：`confidence = semantic_quality × count_factor × coverage_safe`（三因子乘法）
- **推理链路**：症状 → 相关概念 → 药方 → 中草药四步推理 + 语义自洽性向量检查
- **LLM 增强**：DeepSeek-V4-Flash，解释生成（temp=0.7, 1024 tokens）+ 批量翻译（temp=0.2, 2048 tokens）
- **知识增强**：爬虫（中国药典/医药平台） + AI 批量填充 1,620 条症状定义（覆盖率 31.5% → 100%）

---

## 数据增强流水线

原始 HERB 数据库存在大量字段缺失（中药性味归经 ~88% 为空，症状定义 ~69% 为空）。本系统通过多源数据挖掘补充：

| 来源 | 数据类型 | 填充方法 | 效果 |
|------|----------|----------|------|
| 中国药典（CHP） | 中药性味/归经/功效 | 网络爬虫 | 填充率 ~4%（有限） |
| 中国医药信息查询平台 | 中药字段 + 症状定义 | 网络爬虫 | 中英混杂，覆盖面中等 |
| 中医百科（读古籍） | 症状定义 | 网络爬虫 | 覆盖率 42.3% |
| DeepSeek AI | 症状定义 | LLM 批量生成 | **覆盖率 100%**（1,620 条） |

详细流水线说明见 [`数据挖掘/README.md`](数据挖掘/README.md)。

---

## 贡献者

| 姓名 | GitHub | 主要贡献 |
|------|--------|----------|
| 杨子轩 | [@username]() | 项目架构、数据挖掘与增强、实验分析 |
| 易亮 | [@username]() | Streamlit 前端、UI 组件与可视化 |
| 杨睿捷 | [@username]() | BGE 语义检索、推理链路、后端逻辑 |
