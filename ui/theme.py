"""
CSS 主题系统 — 暗色/亮色中医风格配色
"""
from __future__ import annotations

import streamlit as st

# ── 间距与字体常量 ─────────────────────────────────────────────────────────────
SPACING = {
    "xs": "0.25rem",
    "sm": "0.5rem",
    "md": "1rem",
    "lg": "1.5rem",
    "xl": "2rem",
}

FONT_STACK = (
    "'Noto Serif SC', 'Source Han Serif CN', 'STSong', "
    "'SimSun', 'Songti SC', serif"
)
FONT_STACK_BODY = (
    "'Noto Sans SC', 'Source Han Sans CN', 'Microsoft YaHei', "
    "'PingFang SC', 'Hiragino Sans GB', sans-serif"
)
FONT_SIZE_BASE = "0.92rem"
FONT_SIZE_SM = "0.8rem"
FONT_SIZE_LG = "1.15rem"
BORDER_RADIUS = "12px"
BORDER_RADIUS_SM = "8px"

DARK_PALETTE = {
    "bg": "#1a1a2e",
    "surface": "#16213e",
    "surface_hover": "#1c2a4a",
    "primary": "#c9a96e",
    "secondary": "#4a9e7d",
    "text": "#e8e8e8",
    "text_muted": "#a0a0b0",
    "disease": "#e07a5f",
    "formula": "#81b29a",
    "herb": "#f2cc8f",
    "symptom": "#e63946",
    "disease_accent": "#e07a5f",
    "formula_accent": "#81b29a",
    "herb_accent": "#f2cc8f",
    "symptom_node": "#e63946",
    "edge": "#3d5a80",
    "border": "#2a3a5e",
    "sidebar_bg": "linear-gradient(180deg, #16213e 0%, #1a1a2e 100%)",
    "card_shadow": "0 4px 16px rgba(0,0,0,0.3)",
    "card_shadow_hover": "0 8px 24px rgba(0,0,0,0.4)",
    "input_bg": "#1c2a4a",
    "input_border": "#3d5a80",
    "input_text": "#e8e8e8",
}

LIGHT_PALETTE = {
    "bg": "#faf7f2",
    "surface": "#ffffff",
    "surface_hover": "#f5f0e8",
    "primary": "#8b4513",
    "secondary": "#2d6a4f",
    "text": "#2d2d2d",
    "text_muted": "#6b6b6b",
    "disease": "#c44536",
    "formula": "#2d6a4f",
    "herb": "#b5651d",
    "symptom": "#d62828",
    "disease_accent": "#c44536",
    "formula_accent": "#2d6a4f",
    "herb_accent": "#b5651d",
    "symptom_node": "#d62828",
    "edge": "#457b9d",
    "border": "#e0d8cc",
    "sidebar_bg": "linear-gradient(180deg, #f5f0e8 0%, #faf7f2 100%)",
    "card_shadow": "0 2px 8px rgba(0,0,0,0.08)",
    "card_shadow_hover": "0 6px 16px rgba(0,0,0,0.12)",
    "input_bg": "#ffffff",
    "input_border": "#d4c9b8",
    "input_text": "#2d2d2d",
}


def get_palette(mode: str = "dark") -> dict:
    return DARK_PALETTE if mode == "dark" else LIGHT_PALETTE


def inject_theme(mode: str = "dark") -> None:
    """注入全局 CSS 主题样式"""
    p = get_palette(mode)

    css = f"""
    <style>
    /* ── 全局背景 ── */
    .stApp {{
        background-color: {p['bg']};
    }}

    /* ── 侧边栏 ── */
    section[data-testid="stSidebar"] {{
        background: {p['sidebar_bg']};
    }}
    section[data-testid="stSidebar"] .stMarkdown {{
        color: {p['text']};
    }}

    /* ── 标题 ── */
    .tcm-title {{
        color: {p['primary']};
        font-size: 2.2rem;
        font-weight: 700;
        text-align: center;
        padding: 0.5rem 0 1rem 0;
        border-bottom: 2px solid {p['primary']}40;
        margin-bottom: 1.5rem;
    }}

    /* ── 卡片基础 ── */
    .tcm-card {{
        background: {p['surface']};
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 1rem;
        box-shadow: {p['card_shadow']};
        transition: all 0.3s ease;
        border: 1px solid {p['border']};
    }}
    .tcm-card:hover {{
        transform: translateY(-2px);
        box-shadow: {p['card_shadow_hover']};
        background: {p['surface_hover']};
    }}

    /* ── 卡片变体 ── */
    .tcm-card-herb {{
        border-left: 4px solid {p['herb']};
    }}
    .tcm-card-formula {{
        border-left: 4px solid {p['formula']};
    }}
    .tcm-card-disease {{
        border-left: 4px solid {p['disease']};
    }}
    .tcm-card-symptom {{
        border-left: 4px solid {p['symptom']};
    }}

    /* ── 卡片内部标签/值 ── */
    .tcm-label {{
        display: inline-block;
        font-size: 0.78rem;
        color: {p['text_muted']};
        margin-bottom: 0.2rem;
        font-weight: 600;
        letter-spacing: 0.5px;
    }}
    .tcm-value {{
        display: block;
        color: {p['text']};
        font-size: 0.92rem;
        line-height: 1.5;
    }}

    /* ── 卡片标题 ── */
    .tcm-card-title {{
        margin: 0 0 0.6rem 0;
        font-size: 1.15rem;
        font-weight: 600;
    }}
    .tcm-card-subtitle {{
        font-size: 0.85rem;
        color: {p['text_muted']};
        margin: 0;
    }}

    /* ── 置信度徽章 ── */
    .tcm-badge {{
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        color: #fff;
    }}
    .tcm-badge-high {{
        background: {p['secondary']};
    }}
    .tcm-badge-mid {{
        background: {p['primary']};
    }}
    .tcm-badge-low {{
        background: {p['disease']};
    }}

    /* ── 网格布局 ── */
    .tcm-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.8rem;
        margin-top: 0.8rem;
    }}
    .tcm-grid-full {{
        grid-column: span 2;
    }}

    /* ── 时间线 ── */
    .tcm-timeline {{
        position: relative;
        padding-left: 2rem;
        margin: 1rem 0;
    }}
    .tcm-timeline::before {{
        content: '';
        position: absolute;
        left: 0.6rem;
        top: 0;
        bottom: 0;
        width: 2px;
        background: {p['primary']}60;
    }}
    .tcm-timeline-item {{
        position: relative;
        margin-bottom: 1.5rem;
        padding: 0.8rem 1rem;
        background: {p['surface']};
        border-radius: 8px;
        border: 1px solid {p['border']};
    }}
    .tcm-timeline-item::before {{
        content: '';
        position: absolute;
        left: -1.65rem;
        top: 1.2rem;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: {p['primary']};
        border: 2px solid {p['bg']};
    }}
    .tcm-timeline-step {{
        font-size: 0.75rem;
        color: {p['primary']};
        font-weight: 700;
        margin-bottom: 0.3rem;
    }}
    .tcm-timeline-db {{
        font-size: 0.9rem;
        color: {p['text']};
        font-weight: 600;
    }}
    .tcm-timeline-detail {{
        font-size: 0.82rem;
        color: {p['text_muted']};
        margin-top: 0.3rem;
        line-height: 1.5;
    }}

    /* ── 流程指示器 ── */
    .tcm-flow {{
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
        margin: 1.5rem 0;
        flex-wrap: wrap;
    }}
    .tcm-flow-node {{
        padding: 0.5rem 1rem;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.9rem;
        color: #fff;
    }}
    .tcm-flow-arrow {{
        color: {p['text_muted']};
        font-size: 1.2rem;
    }}

    /* ── 分隔线 ── */
    .tcm-divider {{
        border: none;
        height: 1px;
        background: {p['border']};
        margin: 1.5rem 0;
    }}

    /* ── Hero 标题区 ── */
    .tcm-hero {{
        background: linear-gradient(135deg, {p['primary']}26 0%, {p['secondary']}1f 100%);
        border: 1px solid {p['primary']}40;
        border-radius: 16px;
        padding: 1.5rem 2rem;
        margin: 0.5rem 0 1.2rem 0;
        position: relative;
        overflow: hidden;
    }}
    .tcm-hero::before {{
        content: '';
        position: absolute;
        top: -50%; right: -10%;
        width: 280px; height: 280px;
        background: radial-gradient(circle, {p['primary']}22 0%, transparent 70%);
        pointer-events: none;
    }}
    .tcm-hero-title {{
        color: {p['primary']};
        font-family: {FONT_STACK};
        font-size: 2rem;
        font-weight: 700;
        letter-spacing: 2px;
        margin: 0;
        position: relative;
    }}
    .tcm-hero-subtitle {{
        color: {p['text_muted']};
        font-size: 0.92rem;
        margin-top: 0.35rem;
        letter-spacing: 0.5px;
        position: relative;
    }}

    /* ── KPI 数据卡片 ── */
    .tcm-kpi-row {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 0.75rem;
        margin-bottom: 1.4rem;
    }}
    .tcm-kpi {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 12px;
        padding: 0.9rem 1rem;
        text-align: left;
        box-shadow: {p['card_shadow']};
        transition: all 0.25s ease;
    }}
    .tcm-kpi:hover {{
        transform: translateY(-2px);
        box-shadow: {p['card_shadow_hover']};
        border-color: {p['primary']}55;
    }}
    .tcm-kpi-value {{
        color: {p['primary']};
        font-size: 1.6rem;
        font-weight: 700;
        font-family: {FONT_STACK};
        line-height: 1.1;
    }}
    .tcm-kpi-label {{
        color: {p['text_muted']};
        font-size: 0.78rem;
        margin-top: 0.25rem;
        letter-spacing: 1px;
    }}

    /* ── 现代卡片 ── */
    .tcm-card-modern {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-left: 4px solid {p['primary']};
        border-radius: 12px;
        padding: 1.1rem 1.3rem;
        margin-bottom: 0.9rem;
        box-shadow: {p['card_shadow']};
        transition: all 0.25s ease;
    }}
    .tcm-card-modern:hover {{
        transform: translateY(-2px);
        box-shadow: {p['card_shadow_hover']};
    }}
    .tcm-card-modern-head {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin-bottom: 0.55rem;
    }}
    .tcm-card-modern-title {{
        font-size: 1.15rem;
        font-weight: 700;
        font-family: {FONT_STACK};
    }}
    .tcm-card-modern-sub {{
        color: {p['text_muted']};
        font-size: 0.82rem;
    }}
    .tcm-card-modern-meta {{
        display: flex;
        align-items: center;
        gap: 0.4rem;
    }}
    .tcm-card-modern-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.4rem 1.2rem;
    }}
    .tcm-card-modern-rows {{
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
    }}
    .tcm-k {{
        color: {p['text_muted']};
        font-size: 0.8rem;
        margin-right: 0.4rem;
    }}
    .tcm-v {{
        color: {p['text']};
        font-size: 0.9rem;
    }}

    /* ── 评分徽章 ── */
    .tcm-score-pill {{
        display: inline-block;
        color: #fff;
        padding: 2px 12px;
        border-radius: 14px;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.3px;
    }}

    /* ── 症状 chip ── */
    .tcm-symptom-row {{
        display: flex;
        flex-wrap: wrap;
        gap: 0.35rem;
        margin: 0.3rem 0 0.7rem 0;
    }}
    .tcm-symptom-chip {{
        background: {p['symptom_node']}22;
        color: {p['symptom_node']};
        border: 1px solid {p['symptom_node']}55;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.78rem;
        font-weight: 600;
    }}

    /* ── 置信度仪表 ── */
    .tcm-confidence {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin: 0.8rem 0 1rem 0;
    }}
    .tcm-confidence-row {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.5rem;
    }}
    .tcm-confidence-label {{
        color: {p['text_muted']};
        font-size: 0.85rem;
        letter-spacing: 0.5px;
    }}
    .tcm-confidence-value {{
        font-weight: 700;
        font-size: 0.95rem;
    }}
    .tcm-confidence-track {{
        background: {p['bg']};
        border-radius: 6px;
        height: 10px;
        overflow: hidden;
        border: 1px solid {p['border']};
    }}
    .tcm-confidence-fill {{
        height: 100%;
        border-radius: 6px;
        transition: width 0.6s ease;
    }}

    /* ── LLM 解读卡片 ── */
    .tcm-llm-card {{
        background: linear-gradient(135deg, {p['surface']} 0%, {p['surface_hover']} 100%);
        border: 1px solid {p['primary']}55;
        border-left: 4px solid {p['primary']};
        border-radius: 12px;
        padding: 1.3rem 1.5rem;
        margin: 0.6rem 0 1rem 0;
        line-height: 1.85;
        color: {p['text']};
        font-size: 0.95rem;
        box-shadow: {p['card_shadow']};
        max-height: 320px;
        overflow-y: auto;
    }}
    .tcm-llm-card-header {{
        display: flex;
        align-items: center;
        gap: 0.5rem;
        color: {p['primary']};
        font-weight: 700;
        margin-bottom: 0.6rem;
        font-size: 1rem;
        letter-spacing: 1px;
    }}

    /* ── Section 标题 ── */
    .tcm-section {{
        display: flex;
        align-items: center;
        gap: 0.5rem;
        color: {p['primary']};
        font-weight: 700;
        font-size: 1.05rem;
        margin: 1.2rem 0 0.6rem 0;
        letter-spacing: 1px;
        font-family: {FONT_STACK};
    }}
    .tcm-section::after {{
        content: '';
        flex: 1;
        height: 1px;
        background: linear-gradient(90deg, {p['primary']}66, transparent);
        margin-left: 0.5rem;
    }}

    /* ── Streamlit 控件美化 ── */
    div[data-testid="stRadio"] > label {{
        font-size: 0.85rem !important;
        color: {p['text_muted']} !important;
        letter-spacing: 1px;
    }}
    div[data-testid="stRadio"] > div {{
        gap: 0.4rem;
    }}
    div[data-testid="stRadio"] label[data-baseweb="radio"] {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 10px;
        padding: 0.45rem 0.9rem;
        transition: all 0.2s ease;
    }}
    div[data-testid="stRadio"] label[data-baseweb="radio"]:hover {{
        border-color: {p['primary']};
    }}
    div[data-testid="stTextInput"] input,
    div[data-testid="stTextArea"] textarea {{
        background: {p['input_bg']} !important;
        border: 1px solid {p['input_border']} !important;
        color: {p['input_text']} !important;
        border-radius: 10px !important;
    }}
    div[data-testid="stTextInput"] input:focus,
    div[data-testid="stTextArea"] textarea:focus {{
        border-color: {p['primary']} !important;
        box-shadow: 0 0 0 2px {p['primary']}33 !important;
    }}
    button[kind="primary"] {{
        background: {p['primary']} !important;
        color: #fff !important;
        border-radius: 10px !important;
        border: none !important;
        font-weight: 700 !important;
        letter-spacing: 2px !important;
        transition: all 0.2s ease !important;
    }}
    button[kind="primary"]:hover {{
        filter: brightness(1.1);
        transform: translateY(-1px);
        box-shadow: 0 4px 12px {p['primary']}55 !important;
    }}
    div[data-testid="stTabs"] button {{
        border-radius: 8px 8px 0 0 !important;
    }}

    /* ── 隐藏 Streamlit 默认元素（仅隐藏汉堡菜单和页脚，保留侧边栏切换按钮）── */
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def render_sidebar_theme_toggle() -> str:
    """在侧边栏渲染主题切换开关，返回当前主题模式"""
    if "theme" not in st.session_state:
        st.session_state.theme = "dark"

    with st.sidebar:
        theme_choice = st.radio(
            "主题",
            ["dark", "light"],
            format_func=lambda x: "暗色模式" if x == "dark" else "亮色模式",
            index=0 if st.session_state.theme == "dark" else 1,
            key="theme_radio",
            horizontal=True,
        )
        st.session_state.theme = theme_choice

    return st.session_state.theme
