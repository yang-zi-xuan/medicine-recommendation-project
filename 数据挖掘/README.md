# 数据挖掘与增强

从 HERB 数据库原始数据出发，通过网络爬虫和大语言模型（DeepSeek-V4-Flash）填补缺失字段，产出增强版数据集。

## 数据流水线

```
HERB 原始数据
  ├── 中药字段增强 ──────────────────────────────────────
  │   HERB_herb_info_v1.csv
  │     ├── chp/  → 爬取中国药典在线版 (ydz.chp.org.cn)
  │     ├── dayi/ → 爬取中国医药信息查询平台 (dayi.org.cn)
  │     └── combined/ → 合并 chp + dayi（chp 优先，dayi 补充）
  │         → 产物：HERB_herb_info_v1_combined_filled.csv
  │
  └── 症状定义增强 ──────────────────────────────────────
      Symptom_Properties.xlsx (2,364 条，定义覆盖率 31.5%)
        ├── symptom_dayi/    → 爬取 dayi.org.cn 症状定义
        ├── symptom_duguji/  → 爬取中医百科 (baike.duguji.cn) 症状定义
        ├── symptom_combined/→ 合并 dayi + duguji
        └── symptom_ai/      → DeepSeek AI 批量生成剩余定义
            → 产物：Symptom_Properties_ai_filled.csv (覆盖率 100%)
```

## 子目录说明

| 目录 | 脚本 | 数据源 | 产物 |
|------|------|--------|------|
| `chp/` | `fill_herb_info_from_chp.py` | 中国药典 2020 在线版 | 中药性味/归经/功效填充 |
| `dayi/` | `fill_herb_info_from_dayi.py` | dayi.org.cn | 中药字段 + 症状定义填充 |
| `combined/` | `fill_herb_info_combined.py` | chp + dayi 综合 | chp 优先→dayi 补充的合并填充 |
| `symptom_dayi/` | `fill_symptom_definition_from_dayi.py` | dayi.org.cn | 症状定义（覆盖率 ~68%） |
| `symptom_duguji/` | `fill_symptom_definition_from_duguji.py` | baike.duguji.cn | 症状定义（覆盖率 ~58%） |
| `symptom_combined/` | `fill_symptom_definition_combined.py` | dayi + duguji 综合 | 最终覆盖率 ~71% |
| `symptom_ai/` | `fill_symptom_definition_ai.py` | DeepSeek API | 全量填充（覆盖率 100%，1,620 条） |

## 运行说明

各子目录脚本均支持独立运行，通常用法：

```bash
cd 数据挖掘/<subdir>
python <script>.py --help    # 查看参数
python <script>.py           # 使用默认路径运行
```

多数脚本接受以下通用参数：
- `--input`：输入 CSV 路径
- `--output`：输出 CSV 路径
- `--cache-dir`：HTTP 缓存目录（爬虫脚本）

`symptom_ai/` 需要配置 DeepSeek API 密钥（同主系统配置方式）。

## 注意事项

- 爬虫脚本内置延迟和缓存机制，避免对目标站造成压力。CHP 和 dayi 的爬取耗时可能较长（数千条记录 × 每条约 1–2 秒延迟）
- 中药字段的爬虫增强效果有限（填充率仅 ~4%，且中英混杂），主系统默认使用原始数据 + DeepSeek 实时翻译方案
- 症状定义的 AI 填充是最终被采纳到主系统的增强方案（`data_csv/Symptom_Properties_ai_filled.csv`）
