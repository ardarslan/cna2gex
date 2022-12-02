import os
import logging
from typing import Dict, Any, List

import numpy as np
import pandas as pd
from sklearn.utils import shuffle
import torch


class Dataset(torch.utils.data.Dataset):
    def __init__(
        self,
        cfg: Dict[str, Any],
        input_data_types: List[str],
        output_data_type: str,
        logger: logging.Logger
    ):
        super().__init__()
        self.cfg = cfg
        self.input_data_types = input_data_types
        self.output_data_type = output_data_type
        self.logger = logger

        self.split_ratios = cfg["split_ratios"]

        if "train" not in self.split_ratios.keys() or "val" not in self.split_ratios.keys() or "test" not in self.split_ratios.keys():
            raise Exception("The dictionary 'split_ratios' should have the following keys: 'train', 'val' and 'test'.")

        if self.split_ratios["train"] + self.split_ratios["val"] + self.split_ratios["test"] != 1.0:
            raise Exception("The values of dictionary 'split_ratios' should sum up to 1.0.")

        self.processed_data_dir = cfg["processed_data_dir"]
        self.seed = cfg["seed"]
        self.logger = logger

        self._process_dataset()
        self.input_dimension = self.X.shape[1]
        self.output_dimension = self.y.shape[1]

        self.device = cfg["device"]
        self.len_dataset = self.X.shape[0]

        all_indices = shuffle(range(self.len_dataset), random_state=self.seed)
        train_size = int(self.len_dataset * self.split_ratios["train"])
        val_size = int(self.len_dataset * self.split_ratios["val"])

        self.train_idx = all_indices[:train_size]
        self.val_idx = all_indices[train_size:train_size+val_size]
        self.test_idx = all_indices[train_size+val_size:]

        X_train = self.X[self.train_idx, :]
        y_train = self.y[self.train_idx, :]

        self.X_train_mean = np.mean(X_train, axis=0)
        self.X_train_std = np.std(X_train, axis=0) + cfg["normalization_eps"]
        self.X_train_mean[self.one_hot_column_indices] = 0.0
        self.X_train_std[self.one_hot_column_indices] = 1.0
        self.X_train_mean = torch.as_tensor(self.X_train_mean, device=self.device, dtype=torch.float32)
        self.X_train_std = torch.as_tensor(self.X_train_std, device=self.device, dtype=torch.float32)

        self.y_train_mean = np.mean(y_train, axis=0)
        self.y_train_std = np.std(y_train, axis=0) + cfg["normalization_eps"]
        self.y_train_mean = torch.as_tensor(self.y_train_mean, device=self.device, dtype=torch.float32)
        self.y_train_std = torch.as_tensor(self.y_train_std, device=self.device, dtype=torch.float32)

    def _process_dataset(self) -> None:
        output_df = pd.read_csv(os.path.join(self.processed_data_dir, self.output_data_type + ".tsv"), sep="\t")

        mask_df = pd.read_csv(os.path.join(self.processed_data_dir, "thresholded_cna.tsv"), sep="\t")

        cancer_type_df = pd.read_csv(os.path.join(self.processed_data_dir, "cancer_type.tsv"), sep="\t")

        if self.cfg["cancer_type"] != "all":
            cancer_type_sample_ids = cancer_type_df[cancer_type_df["cancer_type"] == self.cfg["cancer_type"]]["sample_id"].tolist()

        input_df = None

        for current_input_data_type in self.input_data_types:
            current_input_data_type_df = pd.read_csv(os.path.join(self.processed_data_dir, current_input_data_type + ".tsv"), sep="\t")
            if current_input_data_type in ["cna", "cna_thresholded"]:
                assert current_input_data_type_df.columns.tolist() == output_df.columns.tolist(), f"Columns of {current_input_data_type} dataframe are not the same with columns of output dataframe."

            if self.cfg["cancer_type"] != "all":
                current_input_data_type_df = current_input_data_type_df[current_input_data_type_df["sample_id"].apply(lambda x: x in cancer_type_sample_ids)]

            if input_df is None:
                input_df = current_input_data_type_df
            else:
                input_df = pd.merge(left=input_df,
                                    right=current_input_data_type_df,
                                    on="sample_id",
                                    how="inner")

        if self.cfg["cancer_type"] == "all":
            cancer_type_one_hot_df = pd.read_csv(os.path.join(self.processed_data_dir, "cancer_type_one_hot.tsv"), sep="\t")
            input_df = pd.merge(left=input_df,
                                right=cancer_type_one_hot_df,
                                on="sample_id",
                                how="inner")

        assert output_df.columns.tolist() == mask_df.columns.tolist(), "Columns of output dataframe are not the same with columns of mask dataframe."

        # merge input, output and mask dataframes
        merged_df = pd.merge(left=input_df, right=output_df, how="inner", on="sample_id")
        merged_df = pd.merge(left=merged_df, right=mask_df, how="inner", on="sample_id")
        merged_df = pd.merge(left=merged_df, right=cancer_type_df, how="inner", on="sample_id")
        merged_df.drop(columns=["sample_id"], inplace=True)
        merged_df = shuffle(merged_df, random_state=self.seed)

        self.one_hot_column_indices = [index for index, column in enumerate(merged_df.columns) if str(column).startswith("cancer_type_")]

        self.input_dimension = input_df.shape[1] - 1
        self.output_dimension = output_df.shape[1] - 1
        self.mask_dimension = mask_df.shape[1] - 1

        self.X = merged_df.values[:, :self.input_dimension]
        self.y = merged_df.values[:, self.input_dimension:self.input_dimension + self.output_dimension]
        self.mask = merged_df.values[:, self.input_dimension + self.output_dimension:self.input_dimension + self.output_dimension + self.mask_dimension]
        self.cancer_types = merged_df.values[:, self.input_dimension + self.output_dimension + self.mask_dimension:]

        self.logger.log(level=logging.INFO, msg=f"X.shape: {self.X.shape}, y.shape: {self.y.shape}, mask.shape: {self.mask.shape}")

        self.entrezgene_ids = [column for column in output_df.columns if column != "sample_id"]

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        return {
                "X": torch.as_tensor(self.X[idx, :], device=self.device, dtype=torch.float32),
                "y": torch.as_tensor(self.y[idx, :], device=self.device, dtype=torch.float32),
                "mask": torch.as_tensor(self.mask[idx, :], device=self.device, dtype=torch.float32),
                "cancer_type": torch.as_tensor(self.cancer_types[idx, :], device=self.device, dtype=torch.StringType)
               }

    def __len__(self) -> int:
        return self.len_dataset


class UnthresholdedCNAPurity2GEXDataset(Dataset):
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        super().__init__(
            cfg=cfg,
            input_data_types=["unthresholded_cna", "tumor_purity"],
            output_data_type="gex",
            logger=logger
        )


class UnthresholdedCNA2GEXDataset(Dataset):
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        super().__init__(
            cfg=cfg,
            input_data_types=["unthresholded_cna"],
            output_data_type="gex",
            logger=logger
        )


class ThresholdedCNAPurity2GEXDataset(Dataset):
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        super().__init__(
            cfg=cfg,
            input_data_types=["thresholded_cna", "tumor_purity"],
            output_data_type="gex",
            logger=logger
        )


class ThresholdedCNA2GEXDataset(Dataset):
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        super().__init__(
            cfg=cfg,
            input_data_types=["thresholded_cna"],
            output_data_type="gex",
            logger=logger
        )


class RPPA2GEXDataset(Dataset):
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        super().__init__(
            cfg=cfg,
            input_data_types=["rppa"],
            output_data_type="gex",
            logger=logger
        )
