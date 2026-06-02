"""
可复用卡片组件 — 中药卡片、药方卡片、疾病卡片、置信度仪表、Hero、KPI、症状 chip。

⚠️ 关键：Streamlit 的 markdown 渲染对行首 4 个以上空格会判定为「代码块」，
   因此所有要走 unsafe_allow_html=True 的 HTML 必须扁平化（去除每行行首空白），
   否则会出现「源 HTML 被当文本展示」的问题。
"""
from __future__ import annotations

import re

import streamlit as st

from ui.theme import DARK_PALETTE, LIGHT_PALETTE


def _get_colors() -> dict:
    theme = st.session_state.get("theme", "dark")
    return DARK_PALETTE if theme == "dark" else LIGHT_PALETTE


def _flatten(html: str) -> str:
    """把多行 HTML 压平成一行（去行首空白 + 合并换行），避免被 Markdown 当代码块。"""
    # 去除每行前后空白
    lines = [ln.strip() for ln in html.splitlines()]
    # 过滤空行后再用空格连接
    return "".join(ln for ln in lines if ln)


def _safe(item: dict, *keys: str, default: str = "—") -> str:
    """从多个候选 key 中取第一个非空字符串。"""
    for k in keys:
        v = item.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ("nan", "none", "null"):
            return s
    return default


# ── Hero ────────────────────────────────────────────────────────────────────
def render_hero(title: str = "中医药知识查询系统", subtitle: str = "") -> None:
    html = f"""
    <div class="tcm-hero">
        <div class="tcm-hero-title">{title}</div>
        <div class="tcm-hero-subtitle">{subtitle}</div>
    </div>
    """
    st.markdown(_flatten(html), unsafe_allow_html=True)


# ── KPI 数据卡片行 ──────────────────────────────────────────────────────────
def render_kpi_row(items: list[tuple[str, str]]) -> None:
    """items: [(label, value), ...]"""
    if not items:
        return
    cards = "".join(
        f'<div class="tcm-kpi"><div class="tcm-kpi-value">{v}</div>'
        f'<div class="tcm-kpi-label">{l}</div></div>'
        for l, v in items
    )
    html = f'<div class="tcm-kpi-row">{cards}</div>'
    st.markdown(_flatten(html), unsafe_allow_html=True)


# ── Section 标题 ────────────────────────────────────────────────────────────
def render_section(title: str) -> None:
    st.markdown(
        _flatten(f'<div class="tcm-section">{title}</div>'),
        unsafe_allow_html=True,
    )


# ── 症状 chip 行 ────────────────────────────────────────────────────────────
def render_symptom_chips(symptoms: list[str]) -> None:
    if not symptoms:
        return
    chips = "".join(f'<span class="tcm-symptom-chip">{s}</span>' for s in symptoms)
    html = f'<div class="tcm-symptom-row">{chips}</div>'
    st.markdown(_flatten(html), unsafe_allow_html=True)


# ── 中药卡片 ────────────────────────────────────────────────────────────────
def _bilingual(en_text: str, cn_text: str, colors: dict) -> str:
    """生成中英双语展示 HTML，中文在前，英文以次要色展示。"""
    cn = cn_text if cn_text and cn_text != "—" else ""
    en = en_text if en_text and en_text != "—" else ""
    if cn and en:
        return f"{cn}　<span style='color:{colors['text_muted']};font-size:0.82rem;'>({en})</span>"
    if cn:
        return cn
    return en


def render_herb_card(item: dict) -> None:
    colors = _get_colors()
    accent = colors["herb_accent"]

    cn_name     = _safe(item, "cn_name", "Herb_cn_name", default="")
    pinyin      = _safe(item, "pinyin_name", "Herb_pinyin_name", default="")
    en_name     = _safe(item, "en_name", "Herb_en_name")
    properties  = _bilingual(_safe(item, "properties", "Properties"), _safe(item, "properties_cn", default=""), colors)
    meridians   = _bilingual(_safe(item, "meridians", "Meridians"), _safe(item, "meridians_cn", default=""), colors)
    use_part    = _bilingual(_safe(item, "use_part", "UsePart"), _safe(item, "use_part_cn", default=""), colors)
    function    = _bilingual(_safe(item, "function", "Function"), _safe(item, "function_cn", default=""), colors)
    indication  = _bilingual(_safe(item, "indication", "Indication"), _safe(item, "indication_cn", default=""), colors)
    toxicity    = _bilingual(_safe(item, "toxicity", "Toxicity"), _safe(item, "toxicity_cn", default=""), colors)

    sub = f'<span class="tcm-card-modern-sub">{pinyin}</span>' if pinyin else ""
    meta = ""
    if item.get("matched_field"):
        match_label = {
            "exact": "完全匹配",
            "prefix": "前缀匹配",
            "contains": "包含匹配",
            "semantic_supplement": "语义补充",
        }.get(str(item.get("match_type", "")), str(item.get("match_type", "")))
        meta = (
            f'<span class="tcm-score-pill" style="background:{accent};">'
            f'{match_label} · {_safe(item, "matched_field")}</span>'
        )
    elif "semantic_score" in item:
        meta = (
            f'<span class="tcm-score-pill" style="background:{colors["secondary"]};">'
            f'语义相似度 {float(item["semantic_score"]):.2f}</span>'
        )

    html = f"""
    <div class="tcm-card-modern" style="border-left-color:{accent};">
        <div class="tcm-card-modern-head">
            <span class="tcm-card-modern-title" style="color:{accent};">{cn_name}</span>
            <div class="tcm-card-modern-meta">{sub}{meta}</div>
        </div>
        <div class="tcm-card-modern-grid">
            <div><span class="tcm-k">英文名</span><span class="tcm-v">{en_name}</span></div>
            <div><span class="tcm-k">性味</span><span class="tcm-v">{properties}</span></div>
            <div><span class="tcm-k">归经</span><span class="tcm-v">{meridians}</span></div>
            <div><span class="tcm-k">使用部位</span><span class="tcm-v">{use_part}</span></div>
            <div style="grid-column:span 2;"><span class="tcm-k">功效</span><span class="tcm-v">{function}</span></div>
            <div style="grid-column:span 2;"><span class="tcm-k">主治</span><span class="tcm-v">{indication}</span></div>
            <div style="grid-column:span 2;"><span class="tcm-k">毒性</span><span class="tcm-v">{toxicity}</span></div>
        </div>
    </div>
    """
    st.markdown(_flatten(html), unsafe_allow_html=True)


# ── 药方卡片 ────────────────────────────────────────────────────────────────
def render_formula_card(item: dict, show_score: bool = False) -> None:
    colors = _get_colors()
    accent = colors["formula_accent"]

    cn_name = _safe(item, "cn_name", "name", default="")
    pinyin  = _safe(item, "pinyin_name", default="")
    alias   = _safe(item, "alias")
    dosage  = _safe(item, "dosage_form")
    admin   = _safe(item, "administration")
    cat     = _safe(item, "category", "type")
    herbs   = _safe(item, "herbs_cn", "herbs")
    syn     = _safe(item, "syndromes_cn")
    ind     = _safe(item, "indications_cn", "indication")
    ind_en  = _safe(item, "indications_en")
    source  = _safe(item, "source")

    if len(herbs) > 140:
        herbs = herbs[:140] + "…"
    if len(ind) > 160:
        ind = ind[:160] + "…"

    # 评分徽章
    score_badge = ""
    match_badge = ""
    if item.get("matched_field"):
        match_label = {
            "exact": "完全匹配",
            "prefix": "前缀匹配",
            "contains": "包含匹配",
            "semantic_supplement": "语义补充",
        }.get(str(item.get("match_type", "")), str(item.get("match_type", "")))
        match_badge = (
            f'<span class="tcm-score-pill" style="background:{accent};">'
            f'{match_label} · {_safe(item, "matched_field")}</span>'
        )
    if show_score and "normalized_score" in item:
        score = float(item["normalized_score"])
        badge_color = colors["primary"] if score > 0.15 else colors["text_muted"]
        score_badge = (
            f'<span class="tcm-score-pill" style="background:{badge_color};">'
            f'{score * 100:.1f}%</span>'
        )
    elif "semantic_score" in item:
        score = float(item["semantic_score"])
        score_badge = (
            f'<span class="tcm-score-pill" style="background:{colors["secondary"]};">'
            f'相似度 {score:.2f}</span>'
        )

    sub = f'<span class="tcm-card-modern-sub">{pinyin}</span>' if pinyin else ""

    # 匹配症状 chips
    matched = item.get("matched_symptoms") or []
    chips = ""
    if matched:
        chip_html = "".join(f'<span class="tcm-symptom-chip">{s}</span>' for s in matched)
        chips = f'<div class="tcm-symptom-row">{chip_html}</div>'

    rows = []
    if alias:
        rows.append(f'<div><span class="tcm-k">别名</span><span class="tcm-v">{alias}</span></div>')
    if dosage or admin:
        rows.append(
            f'<div><span class="tcm-k">剂型 / 用法</span>'
            f'<span class="tcm-v">{dosage}　·　{admin}</span></div>'
        )
    if cat:
        rows.append(f'<div><span class="tcm-k">类别</span><span class="tcm-v">{cat}</span></div>')
    rows.append(f'<div><span class="tcm-k">组成</span><span class="tcm-v">{herbs}</span></div>')
    if syn:
        rows.append(f'<div><span class="tcm-k">证型</span><span class="tcm-v">{syn}</span></div>')
    rows.append(f'<div><span class="tcm-k">主治</span><span class="tcm-v">{ind}</span></div>')
    if ind_en:
        ind_en_cn = _safe(item, "indications_en_cn", default="")
        ind_display = _bilingual(ind_en, ind_en_cn, colors) if ind_en_cn else ind_en
        rows.append(f'<div><span class="tcm-k">主治（英）</span><span class="tcm-v">{ind_display}</span></div>')
    if source:
        rows.append(f'<div><span class="tcm-k">来源</span><span class="tcm-v">{source}</span></div>')

    rows_html = "".join(rows)

    html = f"""
    <div class="tcm-card-modern" style="border-left-color:{accent};">
        <div class="tcm-card-modern-head">
            <span class="tcm-card-modern-title" style="color:{accent};">{cn_name}</span>
            <div class="tcm-card-modern-meta">{match_badge}{score_badge}{sub}</div>
        </div>
        {chips}
        <div class="tcm-card-modern-rows">{rows_html}</div>
    </div>
    """
    st.markdown(_flatten(html), unsafe_allow_html=True)


# ── 相关症状/证候概念卡片 ───────────────────────────────────────────────────
def render_concept_card(item: dict) -> None:
    colors = _get_colors()
    accent = colors["disease_accent"]

    name = _safe(item, "name", default="")
    sim = float(item.get("similarity", 0) or 0)
    definition = _safe(item, "definition", default="无定义")
    prop = _safe(item, "property", default="无属性")

    sim_pill = (
        f'<span class="tcm-score-pill" style="background:{accent};">'
        f'{sim * 100:.1f}%</span>'
    )

    html = f"""
    <div class="tcm-card-modern" style="border-left-color:{accent};">
        <div class="tcm-card-modern-head">
            <span class="tcm-card-modern-title" style="color:{accent};">{name}</span>
            <div class="tcm-card-modern-meta">{sim_pill}</div>
        </div>
        <div class="tcm-card-modern-rows">
            <div><span class="tcm-k">定义</span><span class="tcm-v">{definition}</span></div>
            <div><span class="tcm-k">属性</span><span class="tcm-v">{prop}</span></div>
        </div>
    </div>
    """
    st.markdown(_flatten(html), unsafe_allow_html=True)


def render_disease_card(item: dict) -> None:
    """Backward-compatible alias for older mode-3 code paths."""
    render_concept_card(item)


# ── 置信度仪表 ──────────────────────────────────────────────────────────────
def render_confidence_meter(score_sum: float, threshold: float = 0.7) -> None:
    colors = _get_colors()
    pct = max(0.0, min(score_sum * 100, 100.0))

    if score_sum >= 0.7:
        bar_color = colors["secondary"]
        label = "信息充分"
    elif score_sum >= threshold:
        bar_color = colors["primary"]
        label = "可参考"
    else:
        bar_color = colors["disease_accent"]
        label = "建议补充症状"

    html = f"""
    <div class="tcm-confidence">
        <div class="tcm-confidence-row">
            <span class="tcm-confidence-label">推 荐 置 信 度</span>
            <span class="tcm-confidence-value" style="color:{bar_color};">
                {score_sum * 100:.1f}%　·　{label}
            </span>
        </div>
        <div class="tcm-confidence-track">
            <div class="tcm-confidence-fill" style="background:{bar_color}; width:{pct}%;"></div>
        </div>
    </div>
    """
    st.markdown(_flatten(html), unsafe_allow_html=True)


# ── LLM 解读卡片 ────────────────────────────────────────────────────────────
def render_llm_card(text: str, header: str = "中医师解读") -> None:
    if not text:
        return
    # 把换行变成 <br>，但要先转义 HTML 特殊字符以防越界
    safe_text = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
    safe_text = re.sub(r"\n{2,}", "<br><br>", safe_text)
    safe_text = safe_text.replace("\n", "<br>")

    html = f"""
    <div class="tcm-llm-card">
        <div class="tcm-llm-card-header">🩺 {header}</div>
        <div>{safe_text}</div>
    </div>
    """
    st.markdown(_flatten(html), unsafe_allow_html=True)
