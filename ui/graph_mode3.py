"""
Mode 3: 推理链路交互式节点图 (streamlit-agraph)
症状 → 相关症状/证候概念 → 药方 → 中草药
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from ui.theme import DARK_PALETTE, LIGHT_PALETTE


def _get_colors() -> dict:
    theme = st.session_state.get("theme", "dark")
    return DARK_PALETTE if theme == "dark" else LIGHT_PALETTE


def render_chain_graph(data: dict[str, Any]) -> None:
    """渲染推理链路的交互式节点图"""
    try:
        from streamlit_agraph import agraph, Node, Edge, Config
    except ImportError:
        st.warning("streamlit-agraph 未安装，无法渲染节点图")
        _render_fallback(data)
        return

    colors = _get_colors()
    nodes = []
    edges = []

    symptom_text = data.get("symptom", "症状")
    concepts = (data.get("concepts") or data.get("diseases") or [])[:5]
    formulas = data.get("formulas", [])[:5]
    herbs = data.get("herbs", [])[:10]

    # 症状节点（中心）
    nodes.append(Node(
        id="symptom_0",
        label=symptom_text,
        size=35,
        color=colors["symptom_node"],
        shape="diamond",
        font={"size": 14, "color": colors["text"]},
        title=f"输入症状：{symptom_text}",
    ))

    # 相关概念节点
    for i, d in enumerate(concepts):
        nid = f"disease_{i}"
        nodes.append(Node(
            id=nid,
            label=d["name"],
            size=28,
            color=colors["disease_accent"],
            shape="dot",
            font={"size": 12, "color": colors["text"]},
            title=f"定义: {d.get('definition', '无')}\n属性: {d.get('property', '无')}",
        ))
        edges.append(Edge(
            source="symptom_0",
            target=nid,
            label=f"{d['similarity']:.0%}",
            color=colors["edge"],
            width=max(1.5, d["similarity"] * 5),
        ))

    # 药方节点
    for i, f in enumerate(formulas):
        nid = f"formula_{i}"
        herbs_text = f.get("herbs", "")
        if len(herbs_text) > 60:
            herbs_text = herbs_text[:60] + "…"
        nodes.append(Node(
            id=nid,
            label=f["name"],
            size=26,
            color=colors["formula_accent"],
            shape="dot",
            font={"size": 12, "color": colors["text"]},
            title=f"组成: {herbs_text}\n适应症: {f.get('indication', '无')}",
        ))
        # 连接到前2个相关概念
        for j in range(min(2, len(concepts))):
            edges.append(Edge(
                source=f"disease_{j}",
                target=nid,
                label=f"{f['similarity']:.0%}",
                color=colors["edge"],
                width=max(1, f["similarity"] * 4),
            ))

    # 中草药节点
    for i, h in enumerate(herbs):
        nid = f"herb_{i}"
        func = h.get("function_cn") or h.get("function", "无")
        indi = h.get("indication_cn") or h.get("indication", "无")
        nodes.append(Node(
            id=nid,
            label=h["name"],
            size=20,
            color=colors["herb_accent"],
            shape="dot",
            font={"size": 11, "color": colors["text"]},
            title=f"功效: {func}\n主治: {indi}",
        ))
        # 连接到包含该草药的药方
        connected = False
        for j, f in enumerate(formulas):
            if h["name"] in f.get("herbs", ""):
                edges.append(Edge(
                    source=f"formula_{j}",
                    target=nid,
                    color=colors["herb_accent"] + "88",
                    width=1.5,
                ))
                connected = True
        # 如果没有匹配到任何药方，连接到第一个药方
        if not connected and formulas:
            edges.append(Edge(
                source="formula_0",
                target=nid,
                color=colors["herb_accent"] + "44",
                width=1,
            ))

    # 图配置
    config = Config(
        width=900,
        height=500,
        directed=True,
        physics=True,
        hierarchical=True,
        nodeHighlightBehavior=True,
        highlightColor=colors["primary"],
        collapsible=False,
        node={"highlightStrokeColor": colors["primary"]},
        link={"highlightColor": colors["primary"]},
    )

    # 渲染图
    st.markdown(f"""
    <div style="
        background: {colors['surface']};
        border-radius: 16px;
        padding: 1rem;
        margin: 1rem 0;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    ">
        <div style="display: flex; gap: 1.5rem; justify-content: center; margin-bottom: 0.5rem; font-size: 0.8rem;">
            <span style="color: {colors['symptom_node']};">◆ 症状</span>
            <span style="color: {colors['disease_accent']};">● 相关概念</span>
            <span style="color: {colors['formula_accent']};">● 药方</span>
            <span style="color: {colors['herb_accent']};">● 中草药</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    selected = agraph(nodes=nodes, edges=edges, config=config)

    # 点击节点显示详情
    if selected:
        _render_node_detail(selected, data, colors)


def _render_node_detail(node_id: str, data: dict, colors: dict) -> None:
    """根据点击的节点 ID 显示详情卡片"""
    if node_id.startswith("disease_"):
        idx = int(node_id.split("_")[1])
        concepts = data.get("concepts") or data.get("diseases", [])
        if idx < len(concepts):
            d = concepts[idx]
            st.markdown(f"""
            <div style="background: {colors['surface']}; border-left: 4px solid {colors['disease_accent']};
                        border-radius: 8px; padding: 1rem; margin-top: 0.5rem;">
                <strong style="color: {colors['disease_accent']};">{d['name']}</strong>
                <span style="color: {colors['text_muted']}; margin-left: 1rem;">相似度 {d['similarity']:.1%}</span>
                <div style="color: {colors['text']}; margin-top: 0.5rem;">
                    定义：{d.get('definition', '无')}<br>属性：{d.get('property', '无')}
                </div>
            </div>
            """, unsafe_allow_html=True)

    elif node_id.startswith("formula_"):
        idx = int(node_id.split("_")[1])
        formulas = data.get("formulas", [])
        if idx < len(formulas):
            f = formulas[idx]
            st.markdown(f"""
            <div style="background: {colors['surface']}; border-left: 4px solid {colors['formula_accent']};
                        border-radius: 8px; padding: 1rem; margin-top: 0.5rem;">
                <strong style="color: {colors['formula_accent']};">{f['name']}</strong>
                <span style="color: {colors['text_muted']}; margin-left: 1rem;">相似度 {f['similarity']:.1%}</span>
                <div style="color: {colors['text']}; margin-top: 0.5rem;">
                    组成：{f.get('herbs', '无')}<br>适应症：{f.get('indication', '无')}
                </div>
            </div>
            """, unsafe_allow_html=True)

    elif node_id.startswith("herb_"):
        idx = int(node_id.split("_")[1])
        herbs = data.get("herbs", [])
        if idx < len(herbs):
            h = herbs[idx]
            st.markdown(f"""
            <div style="background: {colors['surface']}; border-left: 4px solid {colors['herb_accent']};
                        border-radius: 8px; padding: 1rem; margin-top: 0.5rem;">
                <strong style="color: {colors['herb_accent']};">{h['name']}</strong>
                <span style="color: {colors['text_muted']}; margin-left: 1rem;">相似度 {h['similarity']:.1%}</span>
                <div style="color: {colors['text']}; margin-top: 0.5rem;">
                    功效：{h.get('function_cn') or h.get('function', '无')}<br>主治：{h.get('indication_cn') or h.get('indication', '无')}<br>分类：{h.get('category_cn') or h.get('category', '无')}
                </div>
            </div>
            """, unsafe_allow_html=True)


def _render_fallback(data: dict) -> None:
    """节点图不可用时的文本回退"""
    symptom = data.get("symptom", "")
    concepts = data.get("concepts") or data.get("diseases", [])
    formulas = data.get("formulas", [])
    herbs = data.get("herbs", [])

    lines = [f"**{symptom}**"]
    if concepts:
        lines.append("  ↓  辨证")
        lines.append(f"**{' / '.join(d['name'] for d in concepts[:3])}**")
    if formulas:
        lines.append("  ↓  选方")
        lines.append(f"**{' / '.join(f['name'] for f in formulas[:3])}**")
    if herbs:
        lines.append("  ↓  用药")
        lines.append(f"**{' / '.join(h['name'] for h in herbs[:5])}**")

    st.markdown("\n".join(lines))
