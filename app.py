"""
中医药知识查询系统 - Streamlit 前端（成熟版 UI）

四种查询模式：
  模式 0：按中药名查询
  模式 1：按药方名查询
  模式 2：按症状推荐药方（Sankey 评分流 + 查询时间线 + 置信度仪表）
  模式 3：症状 → 相关症状/证候概念 → 药方 → 中草药 推理链路（交互节点图 + LLM 解读）

UI 风格：
  - Hero 标题区
  - 模式自适应的单输入字段（不再每个模式都显示两个输入框）
  - KPI 数据卡 + Section 标题 + 现代卡片
  - Tab 分区结果展示
  - 暗/亮主题切换
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# ── 加载后端模块 ───────────────────────────────────────────────────────────────
_BACKEND_PARENT = Path(__file__).parent
if str(_BACKEND_PARENT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PARENT))

from backend.query import query  # noqa: E402

# ── UI 组件/主题/动画（可选加载，保证向后兼容）────────────────────────────────
try:
    from ui.theme import inject_theme, render_sidebar_theme_toggle
except Exception:
    inject_theme = None
    render_sidebar_theme_toggle = None

try:
    from ui.components import (
        render_herb_card,
        render_formula_card,
        render_disease_card,
        render_confidence_meter,
        render_hero,
        render_kpi_row,
        render_section,
        render_symptom_chips,
        render_llm_card,
    )
except Exception:
    render_herb_card = None
    render_formula_card = None
    render_disease_card = None
    render_confidence_meter = None
    render_hero = None
    render_kpi_row = None
    render_section = None
    render_symptom_chips = None
    render_llm_card = None

try:
    from ui.animations import show_loading, show_success
except Exception:
    show_loading = None
    show_success = None

try:
    from ui.graph_mode2 import render_sankey
except Exception:
    render_sankey = None

try:
    from ui.graph_mode3 import render_chain_graph
except Exception:
    render_chain_graph = None

try:
    from ui.log_timeline import render_log_timeline
except Exception:
    render_log_timeline = None


# ── 页面配置 ───────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="中医药知识查询系统",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 初始化症状数据源
if "use_ai_symptoms" not in st.session_state:
    st.session_state["use_ai_symptoms"] = False
from backend.semantic import set_symptom_source
set_symptom_source(st.session_state["use_ai_symptoms"])

# ── 主题侧边栏与注入 ──────────────────────────────────────────────────────────
current_theme = "dark"
if render_sidebar_theme_toggle is not None:
    try:
        current_theme = render_sidebar_theme_toggle() or "dark"
    except Exception:
        current_theme = "dark"
if inject_theme is not None:
    try:
        inject_theme(current_theme)
    except Exception:
        pass

# ── 侧边栏：模式说明 ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("---")
    st.markdown("### 📖 使用说明")
    st.markdown(
        """
- **模式 0**：输入中药名（中文 / 拼音 / 英文），返回详细药材信息
- **模式 1**：输入药方名，返回组成、证型、主治等
- **模式 2**：输入症状描述（多个症状用逗号分隔），AI 智能推荐药方
- **模式 3**：症状 → 相关症状/证候概念 → 药方 → 中草药 推理链路，含中医师 LLM 解读
        """
    )
    st.markdown("---")
    st.caption("Powered by BGE 语义检索 + DeepSeek LLM")

    st.markdown("---")
    st.markdown("### 🧪 实验设置")
    if "use_ai_symptoms" not in st.session_state:
        st.session_state["use_ai_symptoms"] = False

    use_ai = st.checkbox(
        "AI 增强症状数据",
        value=st.session_state["use_ai_symptoms"],
        help="启用后将使用 AI 填充了全部症状定义的增强版数据库（默认使用原始数据）",
    )
    if use_ai != st.session_state["use_ai_symptoms"]:
        st.session_state["use_ai_symptoms"] = use_ai
        # 切换时清空缓存结果，触发重新查询
        st.session_state["query_data"] = None
        # 通知 semantic 模块切换数据源
        from backend.semantic import set_symptom_source
        set_symptom_source(use_ai)
        if "symptom_source_notified" not in st.session_state:
            st.session_state["symptom_source_notified"] = True
        st.rerun()


# ── Hero 标题区 ───────────────────────────────────────────────────────────────
if render_hero is not None:
    try:
        render_hero(
            title="🌿 中医药知识查询系统",
            subtitle="基于 HERB 数据库的语义检索 · 多模态推理 · 中医师 LLM 解读",
        )
    except Exception:
        st.title("中医药知识查询系统")
else:
    st.title("中医药知识查询系统")


# ── 模式定义 ──────────────────────────────────────────────────────────────────
MODE_LABELS = {
    0: "🌿 按中药名查询",
    1: "💊 按药方名查询",
    2: "🔍 按症状推荐药方",
    3: "🌐 推理链路（症状→相关概念→药方→中草药）",
}

MODE_PLACEHOLDERS = {
    0: "请输入中药名（如：人参、当归、Ginseng）",
    1: "请输入药方名（如：六味地黄丸、桂枝汤）",
    2: "请输入症状（如：头痛, 失眠, 心悸，多个症状用逗号分隔）",
    3: "请输入症状描述（如：怕冷腰膝酸软夜尿多）",
}

MODE_FIELD_LABELS = {
    0: "中药名",
    1: "药方名",
    2: "症状关键词",
    3: "症状描述",
}


# ── 模式选择行 ────────────────────────────────────────────────────────────────
mode = st.radio(
    "查询模式",
    list(MODE_LABELS.keys()),
    format_func=lambda x: MODE_LABELS[x],
    horizontal=True,
    label_visibility="collapsed",
)

# ── 模式自适应的单输入字段 ────────────────────────────────────────────────────
def _trigger_query():
    st.session_state["enter_pressed"] = True

# 处理症状芯片传入的 pending 合并值（必须在 text_input widget 渲染前）
if st.session_state.pop("apply_pending_input", False):
    pending = st.session_state.pop("pending_input_value", "")
    if pending:
        st.session_state[f"input_mode_{mode}"] = pending

col_input, col_btn = st.columns([5, 1])
with col_input:
    user_input = st.text_input(
        MODE_FIELD_LABELS[mode],
        placeholder=MODE_PLACEHOLDERS[mode],
        key=f"input_mode_{mode}",
        on_change=_trigger_query,
        label_visibility="visible",
    )
with col_btn:
    st.markdown("<div style='height:1.85rem;'></div>", unsafe_allow_html=True)
    do_query = st.button("查 询", type="primary", use_container_width=True)
    do_query = do_query or st.session_state.pop("enter_pressed", False)
    do_query = do_query or st.session_state.pop("pending_rerun_query", False)

# 把 user_input 映射到后端字段
if mode == 1:
    herb_arg, formula_arg = "", user_input
else:
    herb_arg, formula_arg = user_input, ""


# ── 辅助渲染器（带回退） ──────────────────────────────────────────────────────
def _show_formula_card(item: dict, show_score: bool = False) -> None:
    if render_formula_card is not None:
        try:
            render_formula_card(item, show_score=show_score)
            return
        except Exception:
            pass
    st.markdown(f"**{item.get('cn_name', '')}**（{item.get('pinyin_name', '')}）")
    st.markdown(f"- 组成：{item.get('herbs_cn','')}")
    st.markdown(f"- 证型：{item.get('syndromes_cn','')}")
    st.markdown(f"- 主治：{item.get('indications_cn','')}")


def _show_herb_card(item: dict) -> None:
    if render_herb_card is not None:
        try:
            render_herb_card(item)
            return
        except Exception:
            pass

    def _b(item, key, cn_key=""):
        en = item.get(key, "—") or "—"
        cn = item.get(cn_key, "") if cn_key else ""
        if cn and cn != "—":
            return f"{cn} ({en})"
        return en

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**英文名**：{item.get('en_name','—')}")
        st.markdown(f"**性味**：{_b(item, 'properties', 'properties_cn')}")
        st.markdown(f"**归经**：{_b(item, 'meridians', 'meridians_cn')}")
        st.markdown(f"**使用部位**：{_b(item, 'use_part', 'use_part_cn')}")
    with c2:
        st.markdown(f"**功效**：{_b(item, 'function', 'function_cn')}")
        st.markdown(f"**主治**：{_b(item, 'indication', 'indication_cn')}")
        st.markdown(f"**毒性**：{_b(item, 'toxicity', 'toxicity_cn')}")


def _show_disease_card(item: dict) -> None:
    if render_disease_card is not None:
        try:
            render_disease_card(item)
            return
        except Exception:
            pass
    st.markdown(
        f"- **{item.get('name','')}** "
        f"(相似度: {item.get('similarity',0):.1%})  \n"
        f"  定义：{item.get('definition','无')}  \n"
        f"  属性：{item.get('property','无')}"
    )


def _section(title: str) -> None:
    if render_section is not None:
        try:
            render_section(title)
            return
        except Exception:
            pass
    st.markdown(f"### {title}")


# ── 查询执行 ──────────────────────────────────────────────────────────────────
if "query_data" not in st.session_state:
    st.session_state["query_data"] = None
if "query_mode" not in st.session_state:
    st.session_state["query_mode"] = None

if do_query:
    if not user_input or not user_input.strip():
        st.warning(f"请输入{MODE_FIELD_LABELS[mode]}")
        st.session_state["query_data"] = None
    else:
        # 加载动画
        loading_slot = st.empty()
        if show_loading is not None:
            with loading_slot.container():
                try:
                    show_loading()
                except Exception:
                    pass

        with st.spinner("查询中..."):
            result = query(model=mode, herb=herb_arg, formula=formula_arg)

        loading_slot.empty()

        # ── 保存到 session state ──────────────────────────────────────────
        if result.get("code") != 200:
            st.error(f"查询失败：{result.get('data', {}).get('detail', result.get('message',''))}")
            st.session_state["query_data"] = None
        else:
            st.session_state["query_data"] = result.get("data") or {}
            st.session_state["query_mode"] = mode
            if show_success is not None:
                try:
                    show_success()
                except Exception:
                    pass

# ── 结果展示（从 session state 读取） ────────────────────────────────────────
_query_data = st.session_state.get("query_data")
if _query_data is not None and st.session_state.get("query_mode") == mode:
    data = _query_data

    # ── 模式 3：推理链路 ─────────────────────────────────────────────
    if mode == 3:
        chain = data.get("chain") or {}
        concepts = chain.get("concepts") or chain.get("diseases", []) or []
        formulas = chain.get("formulas", []) or []
        herbs_list = chain.get("herbs", []) or []

        # KPI 行
        if render_kpi_row is not None:
            try:
                render_kpi_row([
                    ("相关概念", str(len(concepts))),
                    ("推荐药方", str(len(formulas))),
                    ("组成中草药", str(len(herbs_list))),
                    ("推理深度", "4 层"),
                ])
            except Exception:
                pass

        # 术语标准化展示
        norm_map = chain.get("norm_map") or {}
        if norm_map:
            parts = []
            for raw, stds in norm_map.items():
                if isinstance(stds, list):
                    parts.append(f"{raw} → {'、'.join(stds)}")
                else:
                    parts.append(f"{raw} → {stds}")
            map_text = "　·　".join(parts)
            st.caption(f"🔄 术语标准化：{map_text}")
        dedup_merged = chain.get("dedup_merged") or {}
        if dedup_merged:
            dedup_text = "　·　".join(
                f"{'、'.join(raws)} → {std}" for std, raws in dedup_merged.items()
            )
            st.caption(f"🔀 已合并：{dedup_text}")

        # LLM 中医师解读
        llm_text = chain.get("llm_explanation")
        if llm_text:
            _section("🩺 中医师解读")
            if render_llm_card is not None:
                try:
                    render_llm_card(llm_text)
                except Exception:
                    st.info(llm_text)
            else:
                st.info(llm_text)

        # Tab 分区
        tab_graph, tab_concept, tab_formula, tab_herb, tab_raw = st.tabs(
            ["🌐 链路图", "📌 相关概念", "💊 推荐药方", "🌿 组成中草药", "📋 详细文本"]
        )

        with tab_graph:
            if render_chain_graph is not None:
                try:
                    render_chain_graph(chain)
                except Exception as e:
                    st.caption(f"（节点图渲染失败：{e}）")
            else:
                st.info("streamlit-agraph 未安装，无法渲染节点图")

        with tab_concept:
            if concepts:
                for d in concepts:
                    _show_disease_card(d)
            else:
                st.info("未找到相关症状/证候概念")

        with tab_formula:
            if formulas:
                for f in formulas:
                    card = {
                        "cn_name":          f.get("name"),
                        "pinyin_name":      "",
                        "herbs_cn":         f.get("herbs"),
                        "indications_cn":   f.get("indication"),
                        "normalized_score": f.get("similarity", 0.0),
                    }
                    _show_formula_card(card, show_score=True)
            else:
                st.info("未找到推荐药方")

        with tab_herb:
            if herbs_list:
                for h in herbs_list:
                    herb_card = {
                        "cn_name":        h.get("name"),
                        "function":       h.get("function"),
                        "function_cn":    h.get("function_cn", ""),
                        "indication":     h.get("indication"),
                        "indication_cn":  h.get("indication_cn", ""),
                        "properties":     h.get("category"),
                        "properties_cn":  h.get("category_cn", ""),
                    }
                    _show_herb_card(herb_card)
            else:
                st.info("未匹配到中草药")

        with tab_raw:
            cv = chain.get("closed_loop_validation")
            if cv:
                st.markdown("**🔁 语义自洽性检查**")
                st.code(cv, language="text")
            consistency = chain.get("semantic_consistency") or {}
            if consistency:
                st.caption(consistency.get("claim_scope", "该分数仅表示文本语义一致性，不代表医学诊断或用药建议。"))
                st.json({
                    "overall_score": consistency.get("overall_score"),
                    "level": consistency.get("level_text"),
                    "component_scores": consistency.get("component_scores"),
                })
            chain_text = chain.get("chain_result")
            if chain_text:
                st.markdown("**完整推理链路**")
                st.code(chain_text, language="text")
            if not (cv or chain_text):
                st.info("无详细文本")

    # ── 模式 0/1/2 ──────────────────────────────────────────────────
    else:
        items = data.get("items", []) or []
        total = data.get("total", len(items))
        match_type = data.get("match_type", "")

        # KPI
        if render_kpi_row is not None:
            kpis = [
                ("命中结果", str(total)),
                ("显示条数", str(min(total, 10))),
                ("匹配方式", "语义检索" if match_type == "semantic" else
                               "名称+语义补充" if match_type == "mixed" else
                               "完全匹配" if match_type == "exact" else
                               "前缀匹配" if match_type == "prefix" else
                               "包含匹配" if match_type == "contains" else
                               "关键词回退" if match_type == "fallback" else "—"),
            ]
            if mode == 2:
                norm_symptoms_kpi = data.get("normalized_symptoms") or data.get("all_symptoms", []) or []
                kpis.append(("识别症状", str(len(norm_symptoms_kpi))))
            else:
                kpis.append(("查询模式", MODE_LABELS[mode].split(" ", 1)[-1]))
            try:
                render_kpi_row(kpis)
            except Exception:
                pass

        # ── 模式 0：中药 ────────────────────────────────────────────
        if mode == 0:
            _section("🌿 中药信息")
            for item in items:
                _show_herb_card(item)

        # ── 模式 1：药方 ────────────────────────────────────────────
        elif mode == 1:
            _section("💊 药方信息")
            for item in items:
                _show_formula_card(item, show_score=False)

        # ── 模式 2：症状推荐 ───────────────────────────────────────
        elif mode == 2:
            symptoms = data.get("all_symptoms", []) or []
            # 术语标准化后的症状（用于 Sankey / 对齐）
            norm_symptoms = data.get("normalized_symptoms") or symptoms
            # 术语映射（非空的才展示）
            norm_map = data.get("norm_map") or {}

            if norm_symptoms and render_symptom_chips is not None:
                _section("🏷️ 识别到的症状")
                try:
                    render_symptom_chips(norm_symptoms)
                except Exception:
                    pass
            # 展示术语标准化映射
            if norm_map:
                parts = []
                for raw, stds in norm_map.items():
                    if isinstance(stds, list):
                        parts.append(f"{raw} → {'、'.join(stds)}")
                    else:
                        parts.append(f"{raw} → {stds}")
                map_text = "　·　".join(parts)
                st.caption(f"🔄 术语标准化：{map_text}")
            # 展示去重合并信息
            dedup_merged = data.get("dedup_merged") or {}
            if dedup_merged:
                dedup_text = "　·　".join(
                    f"{'、'.join(raws)} → {std}" for std, raws in dedup_merged.items()
                )
                st.caption(f"🔀 已合并：{dedup_text}")

            # 置信度
            confidence = data.get("confidence")
            if confidence is not None and render_confidence_meter is not None:
                try:
                    render_confidence_meter(float(confidence))
                except Exception:
                    pass

            # 症状补充建议：置信度不足时从 top10 药方推荐候选症状
            suggested = data.get("suggested_symptoms") or []
            if suggested and confidence is not None and confidence < 0.7:
                st.warning("⚠️ 症状不足，您是否还有以下症状？")
                cols = st.columns(min(len(suggested), 5))
                for i, s in enumerate(suggested):
                    with cols[i]:
                        label = f"➕ {s['symptom']}"
                        tip = f"在 {s['overlap_count']}/{s['total_formulas']} 个药方中出现"
                        if st.button(label, key=f"suggest_{s['symptom']}_{i}", help=tip, use_container_width=True):
                            st.session_state["pending_input_value"] = user_input + "，" + s["symptom"]
                            st.session_state["apply_pending_input"] = True
                            st.session_state["pending_rerun_query"] = True
                            st.rerun()

            # Tab：Sankey / Timeline / 药方
            tab_flow, tab_log, tab_cards = st.tabs(
                ["🌊 症状-药方匹配流", "🕒 检索日志", "💊 推荐药方"]
            )

            with tab_flow:
                if render_sankey is not None:
                    sankey_data = {
                        "top5_formulas":     data.get("top5_formulas", []),
                        "symptom_alignment": data.get("symptom_alignment", {}),
                    }
                    try:
                        render_sankey(sankey_data, norm_symptoms)
                    except Exception as e:
                        st.caption(f"（Sankey 渲染失败：{e}）")
                else:
                    st.info("streamlit-echarts 未安装")

            with tab_log:
                log_data = data.get("log_data")
                if log_data and render_log_timeline is not None:
                    try:
                        render_log_timeline(log_data)
                    except Exception as e:
                        st.caption(f"（时间线渲染失败：{e}）")
                else:
                    st.info("无检索日志数据")

            with tab_cards:
                if not items:
                    st.info("未找到推荐药方")
                for item in items:
                    _show_formula_card(item, show_score=True)
