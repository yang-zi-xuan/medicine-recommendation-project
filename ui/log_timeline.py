"""
Mode 2: 查询日志可视化时间线
替换纯文本日志为带动画的垂直时间线
"""
from __future__ import annotations

import streamlit as st

from ui.theme import DARK_PALETTE, LIGHT_PALETTE


def _get_colors() -> dict:
    theme = st.session_state.get("theme", "dark")
    return DARK_PALETTE if theme == "dark" else LIGHT_PALETTE


def render_log_timeline(log_data: dict) -> None:
    """渲染查询日志为可视化时间线"""
    if not log_data:
        return

    colors = _get_colors()
    entries = log_data.get("entries", [])
    total = log_data.get("total_label", 0)

    if not entries:
        return

    # 时间线容器
    timeline_html = f"""
    <div style="
        background: {colors['surface']};
        border-radius: 12px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    ">
        <div style="color: {colors['primary']}; font-weight: 600; margin-bottom: 1rem; font-size: 1rem;">
            查询链路（共 {total} 步）
        </div>
        <div style="position: relative; padding-left: 2rem;">
    """

    step_colors = [colors["symptom_node"], colors["formula_accent"], colors["primary"]]

    for i, entry in enumerate(entries):
        dot_color = step_colors[i % len(step_colors)]
        is_last = i == len(entries) - 1
        line_style = "" if is_last else f"border-left: 2px solid {colors['edge']};"

        outputs = entry.get("output_with_score", [])[:5]
        outputs_html = ", ".join(f"<code>{o}</code>" for o in outputs)
        if len(entry.get("output_with_score", [])) > 5:
            outputs_html += " …"

        chosen = entry.get("chosen_output", [])
        chosen_html = ", ".join(f"<strong>{c}</strong>" for c in chosen)

        timeline_html += f"""
        <div style="position: relative; padding-bottom: 1.2rem; {line_style} padding-left: 1.5rem; margin-left: 0;">
            <div style="
                position: absolute; left: -0.5rem; top: 0.2rem;
                width: 12px; height: 12px;
                background: {dot_color};
                border-radius: 50%;
                border: 2px solid {colors['surface']};
                box-shadow: 0 0 0 3px {dot_color}44;
            "></div>
            <div style="font-size: 0.9rem;">
                <div style="color: {colors['text']}; font-weight: 600; margin-bottom: 0.3rem;">
                    步骤 {entry['time_label']}：{entry['database_name']}
                </div>
                <div style="color: {colors['text_muted']}; font-size: 0.82rem; margin-bottom: 0.2rem;">
                    输入：<code>{entry['input']}</code>（来源：{entry['input_fromwhere']}）
                </div>
                <div style="color: {colors['text_muted']}; font-size: 0.82rem; margin-bottom: 0.2rem;">
                    输出：{outputs_html}
                </div>
                <div style="color: {colors['text']}; font-size: 0.82rem;">
                    选定：{chosen_html}
                </div>
            </div>
        </div>
        """

    timeline_html += "</div></div>"
    st.markdown(timeline_html, unsafe_allow_html=True)
