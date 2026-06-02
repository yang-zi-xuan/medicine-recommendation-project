"""
Lottie 动画加载器
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

_ASSETS = Path(__file__).parent.parent / "assets"


def _load_lottie(filename: str) -> dict | None:
    path = _ASSETS / filename
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def show_loading(height: int = 150) -> None:
    anim = _load_lottie("lottie_loading.json")
    if anim:
        try:
            from streamlit_lottie import st_lottie
            st_lottie(anim, height=height, key=f"loading_{id(anim)}")
        except Exception:
            st.spinner("加载中...")
    else:
        st.info("⏳ 查询中...")


def show_success(height: int = 100) -> None:
    anim = _load_lottie("lottie_success.json")
    if anim:
        try:
            from streamlit_lottie import st_lottie
            st_lottie(anim, height=height, loop=False, key=f"success_{id(anim)}")
        except Exception:
            pass
