"""
语义检索模块
- 使用 BAAI/bge-small-zh-v1.5 将文本转为向量
- 首次运行时构建索引并缓存到磁盘（data_csv/*.npy + *.pkl）
- 提供 semantic_search_herb / semantic_search_formula 接口
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_csv"

_HERB_CSV    = _DATA_DIR / "HERB_herb_info_v1.csv"
_FORMULA_CSV = _DATA_DIR / "HERB_formula_info_v2.csv"
_SYMPTOM_XLSX = _DATA_DIR / "Symptom_Properties.xlsx"
_SYMPTOM_AI_CSV = _DATA_DIR / "Symptom_Properties_ai_filled.csv"

_HERB_EMB_PATH    = _DATA_DIR / "herb_embeddings.npy"
_HERB_META_PATH   = _DATA_DIR / "herb_meta.pkl"
_FORMULA_EMB_PATH = _DATA_DIR / "formula_embeddings.npy"
_FORMULA_META_PATH= _DATA_DIR / "formula_meta.pkl"
_SYMPTOM_EMB_PATH = _DATA_DIR / "symptom_embeddings.npy"
_SYMPTOM_META_PATH= _DATA_DIR / "symptom_meta.pkl"
_SYMPTOM_AI_EMB_PATH = _DATA_DIR / "symptom_ai_embeddings.npy"
_SYMPTOM_AI_META_PATH = _DATA_DIR / "symptom_ai_meta.pkl"

MODEL_NAME = "BAAI/bge-small-zh-v1.5"

DEFAULT_MIN_SCORE = {
    "herb": 0.20,
    "formula": 0.20,
    "symptom": 0.20,
}

# ── 数据源开关（全局） ─────────────────────────────────────────────────────────
_use_ai_symptoms: bool = False


def set_symptom_source(use_ai: bool) -> None:
    """切换症状数据源：False=原始xlsx, True=AI增强csv"""
    global _use_ai_symptoms
    _use_ai_symptoms = use_ai

# ── 懒加载模型 ─────────────────────────────────────────────────────────────────
_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _encode(texts: list[str]) -> np.ndarray:
    """将文本列表编码为 L2 归一化向量（余弦相似度 = 点积）。"""
    model = _get_model()
    vecs = model.encode(texts, batch_size=256, show_progress_bar=True, normalize_embeddings=True)
    return vecs.astype(np.float32)


# ── 拼接字段为检索文本 ─────────────────────────────────────────────────────────
def _herb_text(row: pd.Series) -> str:
    parts = [
        str(row.get("Herb_cn_name", "")),
        str(row.get("Herb_pinyin_name", "")),
        str(row.get("Function", "")),
        str(row.get("Indication", "")),
        str(row.get("Therapeutic_cn_class", "")),
        str(row.get("Properties", "")),
        str(row.get("Meridians", "")),
    ]
    return " ".join(p for p in parts if p and p != "nan")


def _formula_text(row: pd.Series) -> str:
    parts = [
        str(row.get("Formula_cn_name", "")),
        str(row.get("Formula_alias_name", "")),
        str(row.get("Syndromes_in_Chinese", "")),
        str(row.get("Indications_in_Chinese", "")),
        str(row.get("Herbs_in_Chinese", "")),
        str(row.get("Category", "")),
    ]
    return " ".join(p for p in parts if p and p != "nan")


def _symptom_text(row: pd.Series) -> str:
    parts = [
        str(row.get("TCM_symptom_name", "")),
        str(row.get("Symptom_definition", "")),
        str(row.get("Symptom_property", "")),
        str(row.get("Symptom_locus", "")),
    ]
    return " ".join(p for p in parts if p and p != "nan")


# ── 构建 / 加载索引 ────────────────────────────────────────────────────────────
def _build_herb_index() -> tuple[np.ndarray, list[dict]]:
    df = pd.read_csv(_HERB_CSV, encoding="utf-8-sig", low_memory=False)
    texts = [_herb_text(row) for _, row in df.iterrows()]
    print(f"[semantic] 构建中药索引（{len(texts)} 条）...")
    embs = _encode(texts)
    meta = df.to_dict(orient="records")
    np.save(_HERB_EMB_PATH, embs)
    with open(_HERB_META_PATH, "wb") as f:
        pickle.dump(meta, f)
    _write_cache_manifest(_HERB_EMB_PATH, _HERB_CSV)
    print("[semantic] 中药索引已缓存到磁盘")
    return embs, meta


def _build_formula_index() -> tuple[np.ndarray, list[dict]]:
    df = pd.read_csv(_FORMULA_CSV, encoding="utf-8-sig", low_memory=False)
    texts = [_formula_text(row) for _, row in df.iterrows()]
    print(f"[semantic] 构建药方索引（{len(texts)} 条）...")
    embs = _encode(texts)
    meta = df.to_dict(orient="records")
    np.save(_FORMULA_EMB_PATH, embs)
    with open(_FORMULA_META_PATH, "wb") as f:
        pickle.dump(meta, f)
    _write_cache_manifest(_FORMULA_EMB_PATH, _FORMULA_CSV)
    print("[semantic] 药方索引已缓存到磁盘")
    return embs, meta


def _build_symptom_index(use_ai: bool = False) -> tuple[np.ndarray, list[dict]]:
    if use_ai:
        df = pd.read_csv(_SYMPTOM_AI_CSV, encoding="utf-8-sig", low_memory=False)
        emb_path, meta_path = _SYMPTOM_AI_EMB_PATH, _SYMPTOM_AI_META_PATH
        label = "症状（AI增强）"
    else:
        df = pd.read_excel(_SYMPTOM_XLSX)
        emb_path, meta_path = _SYMPTOM_EMB_PATH, _SYMPTOM_META_PATH
        label = "症状（原始）"
    texts = [_symptom_text(row) for _, row in df.iterrows()]
    print(f"[semantic] 构建{label}索引（{len(texts)} 条）...")
    embs = _encode(texts)
    meta = df.to_dict(orient="records")
    np.save(emb_path, embs)
    with open(meta_path, "wb") as f:
        pickle.dump(meta, f)
    _write_cache_manifest(emb_path, _SYMPTOM_AI_CSV if use_ai else _SYMPTOM_XLSX)
    print(f"[semantic] {label}索引已缓存到磁盘")
    return embs, meta


def _cache_manifest_path(emb_path: Path) -> Path:
    return emb_path.with_suffix(".manifest.json")


def _source_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _write_cache_manifest(emb_path: Path, source_path: Path) -> None:
    manifest = {
        "model": MODEL_NAME,
        "source": source_path.name,
        "source_mtime": _source_mtime(source_path),
    }
    try:
        with open(_cache_manifest_path(emb_path), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _cache_is_valid(emb_path: Path, meta_path: Path, source_path: Path) -> bool:
    if not (emb_path.exists() and meta_path.exists()):
        return False

    manifest_path = _cache_manifest_path(emb_path)
    if not manifest_path.exists():
        cache_mtime = min(_source_mtime(emb_path) or 0.0, _source_mtime(meta_path) or 0.0)
        source_mtime = _source_mtime(source_path) or 0.0
        return cache_mtime >= source_mtime

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    return (
        manifest.get("model") == MODEL_NAME
        and manifest.get("source") == source_path.name
        and manifest.get("source_mtime") == _source_mtime(source_path)
    )


# 运行时缓存
_herb_embs: np.ndarray | None = None
_herb_meta: list[dict] | None = None
_formula_embs: np.ndarray | None = None
_formula_meta: list[dict] | None = None
_symptom_embs: np.ndarray | None = None
_symptom_meta: list[dict] | None = None
_symptom_ai_embs: np.ndarray | None = None
_symptom_ai_meta: list[dict] | None = None


def _load_herb_index() -> tuple[np.ndarray, list[dict]]:
    global _herb_embs, _herb_meta
    if _herb_embs is None:
        if _cache_is_valid(_HERB_EMB_PATH, _HERB_META_PATH, _HERB_CSV):
            _herb_embs = np.load(_HERB_EMB_PATH)
            with open(_HERB_META_PATH, "rb") as f:
                _herb_meta = pickle.load(f)
        else:
            _herb_embs, _herb_meta = _build_herb_index()
    return _herb_embs, _herb_meta


def _load_formula_index() -> tuple[np.ndarray, list[dict]]:
    global _formula_embs, _formula_meta
    if _formula_embs is None:
        if _cache_is_valid(_FORMULA_EMB_PATH, _FORMULA_META_PATH, _FORMULA_CSV):
            _formula_embs = np.load(_FORMULA_EMB_PATH)
            with open(_FORMULA_META_PATH, "rb") as f:
                _formula_meta = pickle.load(f)
        else:
            _formula_embs, _formula_meta = _build_formula_index()
    return _formula_embs, _formula_meta


def _load_symptom_index() -> tuple[np.ndarray, list[dict]]:
    if _use_ai_symptoms:
        global _symptom_ai_embs, _symptom_ai_meta
        if _symptom_ai_embs is None:
            if _cache_is_valid(_SYMPTOM_AI_EMB_PATH, _SYMPTOM_AI_META_PATH, _SYMPTOM_AI_CSV):
                _symptom_ai_embs = np.load(_SYMPTOM_AI_EMB_PATH)
                with open(_SYMPTOM_AI_META_PATH, "rb") as f:
                    _symptom_ai_meta = pickle.load(f)
            else:
                _symptom_ai_embs, _symptom_ai_meta = _build_symptom_index(use_ai=True)
        return _symptom_ai_embs, _symptom_ai_meta
    else:
        global _symptom_embs, _symptom_meta
        if _symptom_embs is None:
            if _cache_is_valid(_SYMPTOM_EMB_PATH, _SYMPTOM_META_PATH, _SYMPTOM_XLSX):
                _symptom_embs = np.load(_SYMPTOM_EMB_PATH)
                with open(_SYMPTOM_META_PATH, "rb") as f:
                    _symptom_meta = pickle.load(f)
            else:
                _symptom_embs, _symptom_meta = _build_symptom_index(use_ai=False)
        return _symptom_embs, _symptom_meta


# ── 检索接口 ───────────────────────────────────────────────────────────────────
def _cosine_topk(query_vec: np.ndarray, embs: np.ndarray, k: int) -> list[tuple[int, float]]:
    """返回 [(idx, score), ...] 按相似度降序，score ∈ [0,1]。"""
    scores = embs @ query_vec  # 已 L2 归一化，点积 = 余弦相似度
    k = max(1, min(int(k), len(scores)))
    top_idx = np.argpartition(scores, -k)[-k:]
    top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]
    return [(int(i), float(scores[i])) for i in top_idx]


def _filter_min_score(
    results: list[tuple[int, float]],
    min_score: float | None,
) -> list[tuple[int, float]]:
    if min_score is None:
        return results
    return [(idx, score) for idx, score in results if score >= min_score]


def semantic_search_herb(
    query: str,
    top_k: int = 10,
    min_score: float | None = DEFAULT_MIN_SCORE["herb"],
) -> list[tuple[dict[str, Any], float]]:
    """
    语义检索中药，返回不低于 min_score 的 top_k 条。
    返回 [(row_dict, score), ...]，score 为余弦相似度仅供参考。
    """
    embs, meta = _load_herb_index()
    q_vec = _get_model().encode([query], normalize_embeddings=True)[0].astype(np.float32)
    results = _filter_min_score(_cosine_topk(q_vec, embs, top_k), min_score)
    return [(meta[i], score) for i, score in results]


def semantic_search_formula(
    query: str,
    top_k: int = 10,
    min_score: float | None = DEFAULT_MIN_SCORE["formula"],
) -> list[tuple[dict[str, Any], float]]:
    """
    语义检索药方，返回不低于 min_score 的 top_k 条。
    返回 [(row_dict, score), ...]，score 为余弦相似度仅供参考。
    """
    embs, meta = _load_formula_index()
    q_vec = _get_model().encode([query], normalize_embeddings=True)[0].astype(np.float32)
    results = _filter_min_score(_cosine_topk(q_vec, embs, top_k), min_score)
    return [(meta[i], score) for i, score in results]


def semantic_search_symptom(
    query: str,
    top_k: int = 10,
    min_score: float | None = DEFAULT_MIN_SCORE["symptom"],
) -> list[tuple[dict[str, Any], float]]:
    """
    语义检索症状/证候概念，返回不低于 min_score 的 top_k 条。
    返回 [(row_dict, score), ...]，score 为余弦相似度。
    """
    embs, meta = _load_symptom_index()
    q_vec = _get_model().encode([query], normalize_embeddings=True)[0].astype(np.float32)
    results = _filter_min_score(_cosine_topk(q_vec, embs, top_k), min_score)
    return [(meta[i], score) for i, score in results]


def encode_text(text: str) -> np.ndarray:
    """将文本编码为 L2 归一化向量（供 chain_reasoning 语义自洽性检查使用）。"""
    return _get_model().encode([text], normalize_embeddings=True)[0].astype(np.float32)


def build_all_indexes():
    """预构建所有索引（可在命令行运行：python -m backend.semantic）。"""
    _build_herb_index()
    _build_formula_index()
    _build_symptom_index()


if __name__ == "__main__":
    build_all_indexes()
