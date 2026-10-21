# GreenPath — Partial Fine-tuning Experiments

**AC215 · Milestone 2 | Notebook 08**

This experiment asks whether partially updating pretrained visual encoders improves prediction of cycling-environment attributes from overhead imagery. It extends the frozen-feature experiments in Notebooks 06–07 and uses **the same dataset (`v1`) and fixed spatial train/validation/test split**. All ten targets are generated from street-view photographs by Gemini; they are **pseudo-labels, not independently validated cyclist ratings**.

> **Repository integration note:** This supplied Notebook 08 experiment uses ten targets
> and a 67/39/54 split. Its local dataset name `v1` is not the same data/schema as the
> existing eight-target, 1,012-cell historical experiment documented in
> [EXPERIMENTS.md](../../EXPERIMENTS.md). Its comparisons are within that separate
> ten-target experiment. These fine-tuned checkpoints are not integrated into the five
> ML containers or the current app's eight-target serving bundle. Earlier notebooks
> must be configured for the matching ten-target inputs; the archived eight-target
> inputs are not interchangeable. Raw CSVs/checkpoints referenced below were not
> included in this supplied package. The notebook and four figures are included here.

## 1. Data and evaluation protocol

The pipeline produced 1,127 geographic cells, of which 175 received labels; 160 remained after spatial filtering. The fixed dataset has **67 training, 39 validation, and 54 held-out test cells**. The targets are `tree_canopy`, `greenery`, `water_visibility`, `open_landscape`, `urban_density`, `historic_character`, `commercial_activity`, `bike_infrastructure`, `road_environment`, and `scenic_appeal` (each normalized to [0, 1]).

Models are selected by **validation MAE**, with early stopping based on that metric. The held-out test split is used for final evaluation. We report the **mean across the ten dimensions** of MAE and RMSE (lower is better) and Spearman rank correlation (higher is better). Missing targets are masked in the loss and evaluation. With only 67 training cells, these comparisons are preliminary.

## 2. Model configurations

| Component | M2a: ResNet50 Partial FT | M2b: DINOv2-S Partial FT |
|---|---|---|
| Pretrained encoder (`timm`) | `resnet50.a1_in1k` | `vit_small_patch14_dinov2.lvd142m` |
| Frozen encoder layers | All except final residual stage | All except last two Transformer blocks and final norm |
| Trainable encoder layers | `layer4` | `blocks[-2:]` and `norm` |
| Regression head | New linear head with 10 outputs + sigmoid | New linear head with 10 outputs + sigmoid |
| Trainable parameters (from Notebook 08 output) | 14,985,226 / 23,528,522 (**63.7%**) | 3,555,082 / 21,632,650 (**16.4%**) |
| Optimizer | AdamW | AdamW |
| Learning rate | Encoder `1e-5`; head `3e-4` | Encoder `1e-5`; head `3e-4` |
| Weight decay | `1e-4` | `1e-4` |
| Loss | Masked SmoothL1 / Huber (`beta=0.1`) | Same |
| Image processing | 224×224 center crop; ImageNet normalization; random flips and 90° rotations | Same |
| Batch size / maximum epochs | 8 / 25 | 8 / 25 |
| Early stopping | Patience 6; select minimum validation MAE | Same |

Training also uses CUDA mixed precision where available and gradient clipping (`max_norm=1.0`). The completed Google Drive notebook records `DRY_RUN=False`; its outputs show 25 ResNet50 epochs and 15 DINOv2 epochs (the latter stopped early). The saved checkpoints are reloaded for evaluation.

**Important comparison caveat:** Notebook 07's *frozen* models use a **Ridge regression probe on cached encoder embeddings**, whereas Notebook 08's partially fine-tuned models use a **new neural regression head**. The performance differences cannot be attributed exclusively to unfreezing the encoder.

## 3. Held-out test comparison (54 cells)

| Model | MAE ↓ | RMSE ↓ | Spearman ↑ |
|---|---:|---:|---:|
| B0: Mean baseline | 0.174 | 0.208 | 0.000 |
| B1: OSM + LightGBM | 0.132 | 0.163 | **0.553** |
| ResNet50 — Frozen + Ridge | 0.132 | 0.170 | 0.403 |
| ResNet50 — Partial FT | 0.174 | 0.209 | 0.215 |
| DINOv2-S — Frozen + Ridge | 0.132 | 0.164 | 0.468 |
| DINOv2-S — Partial FT | **0.125** | 0.161 | 0.441 |
| CLIP — Frozen + Ridge | 0.126 | **0.158** | 0.519 |
| M3: Image + OSM | 0.135 | 0.170 | 0.475 |

![Overall model comparison: MAE, RMSE, and Spearman](Figures/model_comparison.png)

*Figure 1. Overall model comparison. Connected points compare frozen and partially fine-tuned versions of ResNet50 and DINOv2. CLIP has the lowest RMSE among image-only models, while OSM + LightGBM has the highest Spearman overall.*

### Frozen vs partial fine-tuning: direct comparison

| Encoder | Frozen MAE | FT MAE | Δ MAE ↓ | Frozen RMSE | FT RMSE | Δ RMSE ↓ | Frozen Spearman | FT Spearman | Δ Spearman ↑ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ResNet50 | 0.132 | 0.174 | **+0.042** | 0.170 | 0.209 | **+0.039** | 0.403 | 0.215 | **−0.188** |
| DINOv2-S | 0.132 | 0.125 | **−0.007** | 0.164 | 0.161 | **−0.003** | 0.468 | 0.441 | **−0.027** |

DINOv2-S partial FT slightly reduces absolute prediction error, but does not improve ranking agreement. ResNet50 partial FT is worse than its frozen Ridge baseline on all three metrics. These are **descriptive test-set differences**, not claims of statistical significance.

![Frozen vs partial fine-tuning on the held-out test set](Figures/heldout_test_set.png)

*Figure 2. Focused comparison of ResNet50 and DINOv2-S before and after fine-tuning across all three metrics.*

## 4. Detailed comparison by target dimension

Aggregate results can hide differences across the ten target attributes. The following figures compare the same four image-only configurations at the **per-dimension** level. The underlying results were calculated by the notebook from the saved test predictions and Notebook 07's `per_dimension_metrics.csv`.

### Change in MAE

![Per-dimension change in MAE after fine-tuning](Figures/Finetuning_effect.png)

*Figure 3. Change in MAE (partial FT minus frozen) for each target. Negative values indicate lower prediction error after fine-tuning; positive values indicate deterioration.*

### Ranking agreement

![Per-dimension Spearman ranking agreement](Figures/Ranking_agreement.png)

*Figure 4. Per-dimension Spearman correlation for frozen and partially fine-tuned ResNet50 and DINOv2-S. Higher values indicate closer agreement with the Gemini-generated label ranking.*

The notebook also exports `ft_per_dimension_comparison.csv` (ten dimensions × four models, with MAE, RMSE, Spearman, and sample counts) and `ft_four_model_summary.csv`. These provide exact numeric values behind Figures 2–4 without crowding this page with a 40-row table.

## 5. Interpretation and limitations

- **DINOv2-S**: Partial fine-tuning gives the lowest overall MAE (0.125) among the compared models, but its Spearman decreases from 0.468 to 0.441.
- **ResNet50**: Partial fine-tuning increases MAE from 0.132 to 0.174 and reduces Spearman from 0.403 to 0.215. Notably, the configured `layer4` fine-tuning still makes **63.7% of this implementation's parameters trainable**.
- **Frozen CLIP**: Remains competitive without encoder updates (MAE 0.126, RMSE 0.158, Spearman 0.519).
- **OSM baseline**: OSM + LightGBM gives the highest Spearman (0.553), suggesting map-derived features are a strong comparator for route ranking.

The main limitations are the small number of labeled training cells, pseudo-label uncertainty, the overhead-vs-street-view domain gap, and differences between Ridge and neural regression heads. The next step is to **label more collected street-view images and expand the geographic coverage beyond Boston**, then rerun all models with a consistent spatial split. A frozen-encoder + neural-head control would better isolate the effect of unfreezing pretrained layers.

## 6. Reproducibility

Run `05_build_dataset.ipynb` → `06_embeddings.ipynb` → `07_baselines.ipynb` → **`08_finetuning.ipynb`**. Notebook 08 is designed for Google Colab with a T4 GPU and reads data from the configured Google Drive project directory. It saves checkpoints, `training_history.csv`, `validation_per_dimension.csv`, `test_per_dimension_finetuning.csv`, `test_model_comparison_with_finetuning.csv`, `config.json`, and plots under `data/results/finetuning-*/`.

**Repository layout:** This Markdown file, `08_finetuning.ipynb`, and the `Figures/` directory must remain siblings. All four image references above use relative paths and will render directly on GitHub.
