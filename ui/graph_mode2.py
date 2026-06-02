"""
Mode 2: Sankey 评分流可视化 (streamlit-echarts)
症状关键词 → 推荐药方（基于 symptom_alignment 数据）
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from ui.theme import DARK_PALETTE, LIGHT_PALETTE


def _get_colors() -> dict:
    theme = st.session_state.get("theme", "dark")
    return DARK_PALETTE if theme == "dark" else LIGHT_PALETTE


def render_sankey(data: dict[str, Any], all_symptoms: list[str]) -> None:
    """渲染症状→药方的 Sankey 评分流图"""
    try:
        from streamlit_echarts import st_echarts
    except ImportError:
        st.info("streamlit-echarts 未安装，无法渲染 Sankey 图")
        return

    colors = _get_colors()
    top5 = data.get("top5_formulas", [])
    alignment = data.get("symptom_alignment", {})

    if not top5 or not all_symptoms:
        return

    # 构建节点
    nodes = []
    for s in all_symptoms:
        nodes.append({"name": s, "itemStyle": {"color": colors["symptom_node"]}})
    for f in top5:
        nodes.append({"name": f["cn_name"], "itemStyle": {"color": colors["formula_accent"]}})

    # 构建连线
    links = []
    linked_symptoms: set[str] = set()
    for f in top5:
        formula_name = f["cn_name"]
        matched = alignment.get(formula_name, [])
        score = f.get("normalized_score", 0.05)
        has_link = False
        for symptom in matched:
            if symptom in all_symptoms:
                links.append({
                    "source": symptom,
                    "target": formula_name,
                    "value": max(round(score * 100, 1), 5),
                })
                has_link = True
                linked_symptoms.add(symptom)
        # 无对齐的药方：用第一个用户症状建一条默认连线
        if not has_link and all_symptoms:
            links.append({
                "source": all_symptoms[0],
                "target": formula_name,
                "value": max(round(score * 100, 1), 5),
            })
            linked_symptoms.add(all_symptoms[0])

    # 无连线的症状（术语差异导致子串匹配不到）：挂到 top1 药方
    for s in all_symptoms:
        if s not in linked_symptoms and top5:
            links.append({
                "source": s,
                "target": top5[0]["cn_name"],
                "value": max(round(top5[0].get("normalized_score", 0.05) * 100, 1), 5),
            })

    if not links:
        return

    option = {
        "tooltip": {"trigger": "item", "triggerOn": "mousemove"},
        "series": [{
            "type": "sankey",
            "layout": "none",
            "emphasis": {"focus": "adjacency"},
            "data": nodes,
            "links": links,
            "lineStyle": {
                "color": "gradient",
                "curveness": 0.5,
                "opacity": 0.6,
            },
            "itemStyle": {"borderWidth": 1, "borderColor": colors["surface"]},
            "label": {
                "color": colors["text"],
                "fontSize": 12,
            },
        }],
        "backgroundColor": "transparent",
    }

    st.markdown(f"""
    <div style="
        background: {colors['surface']};
        border-radius: 12px;
        padding: 0.5rem;
        margin: 1rem 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    ">
        <p style="color: {colors['text_muted']}; font-size: 0.85rem; text-align: center; margin: 0.5rem 0 0 0;">
            症状 → 药方 匹配流向图
        </p>
    </div>
    """, unsafe_allow_html=True)

    st_echarts(options=option, height="350px")
