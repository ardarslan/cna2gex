import os
import logging
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from utils import get_experiment_dir
from scipy.stats import pearsonr
from sklearn.metrics import mean_squared_error


def get_mse_corr_p_value(ground_truths: np.ndarray, predictions: np.ndarray) -> Tuple[float]:
    mse = mean_squared_error(y_true=ground_truths, y_pred=predictions)
    corr, p_value = pearsonr(x=ground_truths, y=predictions)
    return mse, corr, p_value


def get_evaluation_metrics(cancer_type: str, all_ground_truths: pd.DataFrame, all_predictions: pd.DataFrame, current_sample_ids: List[str]) -> Dict[str, float]:
    current_ground_truths = all_ground_truths[all_ground_truths["sample_id"].isin(current_sample_ids)].drop(columns=["sample_id"])
    current_ground_truths = current_ground_truths.values.ravel()

    current_predictions = all_predictions[all_predictions["sample_id"].isin(current_sample_ids)].drop(columns=["sample_id"])
    current_predictions = current_predictions.values.ravel()

    all_mse, all_corr, all_p_value = get_mse_corr_p_value(ground_truths=current_ground_truths, predictions=current_predictions)

    return (cancer_type, all_mse, all_corr, all_p_value)


def save_results_split(cfg: Dict[str, Any], data_loaders: Dict[str, DataLoader], split_name: str, model: nn.Module, loss_function, dataset: Dataset, logger: logging.Logger) -> None:
    model.eval()

    all_sample_ids = []
    all_ground_truths = []
    all_predictions = []

    total_loss = 0.0
    total_count = 0.0

    with torch.no_grad():
        for batch in data_loaders[split_name]:
            sample_id_indices = batch["sample_id_indices"]
            X = batch["X"]
            y = batch["y"]

            yhat = model(X)

            total_count += float(y.shape[0] * y.shape[1])
            total_loss += float(loss_function(yhat, y))

            all_sample_ids.append(np.array([dataset.sample_ids[int(sample_id_index)] for sample_id_index in sample_id_indices.numpy()]))
            all_ground_truths.append(y.cpu().numpy())
            all_predictions.append(yhat.cpu().numpy())

    all_sample_ids = np.hstack(all_sample_ids)
    all_ground_truths = np.vstack(all_ground_truths)
    all_predictions = np.vstack(all_predictions)
    all_loss = total_loss / total_count

    logger.log(level=logging.INFO, msg=f"{split_name.capitalize()} {cfg['loss_function']} loss is {np.round(all_loss, 2)}.")
    logger.log(level=logging.INFO, msg=f"Saving results for {split_name} split.")

    experiment_dir = get_experiment_dir(cfg=cfg)
    os.makedirs(os.path.join(experiment_dir, f"{split_name}_results"), exist_ok=True)

    all_ground_truths = pd.DataFrame(data=all_ground_truths, columns=cfg["entrezgene_ids"], index=all_sample_ids).reset_index(drop=False).rename(columns={"index": "sample_id"})
    all_ground_truths = all_ground_truths.sort_values(by=["sample_id"])
    all_ground_truths = all_ground_truths[["sample_id"] + sorted([column for column in all_ground_truths.drop(columns=["sample_id"]).columns])]
    all_ground_truths.to_csv(os.path.join(experiment_dir, f"{split_name}_results", "ground_truths.tsv"), sep="\t", index=False)

    all_predictions = pd.DataFrame(data=all_predictions, columns=cfg["entrezgene_ids"], index=all_sample_ids).reset_index(drop=False).rename(columns={"index": "sample_id"})
    all_predictions = all_predictions.sort_values(by=["sample_id"])
    all_predictions = all_predictions[["sample_id"] + sorted([column for column in all_predictions.drop(columns=["sample_id"]).columns])]
    all_predictions.to_csv(os.path.join(experiment_dir, f"{split_name}_results", "predictions.tsv"), sep="\t", index=False)

    all_cancer_types = pd.read_csv(os.path.join(cfg["processed_data_dir"], "cancer_type.tsv"), sep="\t")
    all_cancer_types = all_cancer_types[all_cancer_types["sample_id"].isin(all_sample_ids)]
    all_cancer_types = all_cancer_types.sort_values(by=["sample_id"])
    all_cancer_types.to_csv(os.path.join(experiment_dir, f"{split_name}_results", "cancer_types.tsv"), sep="\t", index=False)

    all_cancer_types = dict(all_cancer_types.groupby("cancer_type")["sample_id"].apply(list).reset_index(name="sample_ids").values)
    all_cancer_types["all"] = all_sample_ids.tolist()

    evaluation_metrics = []

    for current_cancer_type, current_sample_ids in all_cancer_types.items():
        evaluation_metrics.append(get_evaluation_metrics(cancer_type=current_cancer_type, all_ground_truths=all_ground_truths, all_predictions=all_predictions, current_sample_ids=current_sample_ids))

    evaluation_metrics = pd.DataFrame(data=evaluation_metrics, columns=["cancer_type", "all_mse", "all_corr", "all_p_value"])
    evaluation_metrics.to_csv(os.path.join(experiment_dir, f"{split_name}_results", "evaluation_metrics_all.tsv"), sep="\t", index=False)

    logger.log(level=logging.INFO, msg=f"Saved results for {split_name} split.")


def save_results(cfg: Dict[str, Any], data_loaders: Dict[str, DataLoader], model: nn.Module, loss_function, dataset: Dataset, logger: logging.Logger) -> None:
    for split_name in ["val", "test"]:
        save_results_split(cfg=cfg, data_loaders=data_loaders, split_name=split_name, model=model, loss_function=loss_function, dataset=dataset, logger=logger)
