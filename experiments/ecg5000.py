# This code does not necessarily need to be part of the file, 
# but it is useful to guarantee that this module will find all the required modules to
# execute properly
######### MY CODE ADDITION TO ADD THE CODE PATH #########
import sys
import os
# Add the lfxai package to the path
sys.path.append(os.path.abspath('../'))
sys.path.append(os.path.abspath('../src/lfxai/'))
sys.path.append(os.path.abspath('../src/lfxai/explanations'))
sys.path.append(os.path.abspath('../src/lfxai/models'))
sys.path.append(os.path.abspath('../src/lfxai/utils'))
#################################

import argparse
import logging
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import torch
from captum.attr import GradientShap, IntegratedGradients, Saliency
from torch.utils.data import DataLoader, RandomSampler, Subset, random_split

from lfxai.explanations.examples import (
    InfluenceFunctions,
    NearestNeighbours,
    SimplEx,
    TracIn,
)
from lfxai.explanations.features import attribute_auxiliary
from lfxai.models.time_series import RecurrentAutoencoder
from lfxai.utils.datasets import ECG5000
from lfxai.utils.feature_attribution import generate_tseries_masks
from lfxai.utils.metrics import similarity_rates


def consistency_feature_importance(
    random_seed: int = 1,
    batch_size: int = 50,
    dim_latent: int = 64,
    n_epochs: int = 150,
    val_proportion: float = 0.2,
    load_models: bool = True,
    load_metrics: bool = False,
) -> None:

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.random.manual_seed(random_seed)
    data_dir = Path.cwd() / "data/ecg5000"
    save_dir = Path.cwd() / "results/ecg5000/consistency_features"
    if not save_dir.exists():
        os.makedirs(save_dir)

    # Create training, validation and test data
    train_dataset = ECG5000(data_dir, True, random_seed)
    baseline_sequence = torch.mean(
        torch.stack(train_dataset.sequences), dim=0, keepdim=True
    ).to(
        device
    )  # Attribution Baseline
    val_length = int(len(train_dataset) * val_proportion)  # Size of validation set
    train_dataset, val_dataset = random_split(
        train_dataset, (len(train_dataset) - val_length, val_length)
    )
    train_loader = DataLoader(train_dataset, batch_size, True)
    val_loader = DataLoader(val_dataset, batch_size, True)
    test_dataset = ECG5000(data_dir, False, random_seed)
    test_loader = DataLoader(test_dataset, batch_size, False)
    time_steps = 140
    n_features = 1

    # Fit an autoencoder
    autoencoder = RecurrentAutoencoder(time_steps, n_features, dim_latent)
    
    load_metrics = load_metrics and (save_dir / "metrics.csv").is_file()
    
    if not load_metrics:
        name = autoencoder.name
        model_loaded = False
        if load_models:
            if (save_dir / (name + ".pt")).is_file():
                logging.info('Loading the pretrained model from: {}'.format((save_dir / (name + ".pt"))))
                model_loaded = True
            else:
                logging.info('Cannot load a model from: {}'.format((save_dir / (name + ".pt"))))

        if not model_loaded:
            # Train the denoising autoencoder
            logging.info('Training the model from scratch.')
            logging.info(f"Now fitting {name}")
            autoencoder.fit(device, train_loader, val_loader, save_dir, n_epochs, patience=10)
            autoencoder.train()
   
        encoder = autoencoder.encoder
        autoencoder.load_state_dict(
            torch.load(save_dir / (name + ".pt")), strict=False
        )
        autoencoder.to(device)

        attr_methods = {
            "Gradient Shap": GradientShap,
            "Integrated Gradients": IntegratedGradients,
            "Saliency": Saliency,
            "Random": None,
        }
        results_data = []
        pert_percentages = [5, 10, 20, 50, 80, 100]

        for method_name in attr_methods:
            logging.info(f"Computing feature importance with {method_name}")
            results_data.append([method_name, 0, 0])
            attr_method = attr_methods[method_name]
            if attr_method is not None:
                attr = attribute_auxiliary(
                    encoder, test_loader, device, attr_method(encoder), baseline_sequence
                )
            else:
                np.random.seed(random_seed)
                attr = np.random.randn(len(test_dataset), time_steps, 1)

            for pert_percentage in pert_percentages:
                logging.info(
                    f"Perturbing {pert_percentage}% of the features with {method_name}"
                )
                mask_size = int(pert_percentage * time_steps / 100)
                masks = generate_tseries_masks(attr, mask_size)
                for batch_id, (tseries, _) in enumerate(test_loader):
                    mask = masks[
                        batch_id * batch_size : batch_id * batch_size + len(tseries)
                    ].to(device)
                    tseries = tseries.to(device)
                    original_reps = encoder(tseries)
                    tseries_pert = mask * tseries + (1 - mask) * baseline_sequence
                    pert_reps = encoder(tseries_pert)
                    rep_shift = torch.mean(
                        torch.sum((original_reps - pert_reps) ** 2, dim=-1)
                    ).item()
                    results_data.append([method_name, pert_percentage, rep_shift])

        logging.info(f"Saving results in {save_dir}")
        results_df = pd.DataFrame(
            results_data,
            columns=["Method", "% Perturbed Time Steps", "Representation Shift"],
        )
        logging.info(f"Saving results in {save_dir}")
        results_df.to_csv(save_dir / "metrics.csv")

    if (save_dir / "metrics.csv").is_file():
        logging.info('Loading the metrics from: {}'.format((save_dir / "metrics.csv")))
        results_df = pd.read_csv(save_dir / "metrics.csv")
    else:
        logging.info('Cannot load a metrics from: {}'.format((save_dir / "metrics.csv")))

    sns.set(font_scale=1.3)
    sns.set_style("white")
    sns.set_palette("colorblind")
    sns.lineplot(
        data=results_df,
        x="% Perturbed Time Steps",
        y="Representation Shift",
        hue="Method",
    )
    plt.tight_layout()
    plt.savefig(save_dir / "ecg5000_consistency_features.pdf")
    # plt.close()
    plt.show()


def consistency_example_importance(
    random_seed: int = 1,
    batch_size: int = 50,
    dim_latent: int = 16,
    n_epochs: int = 150,
    subtrain_size: int = 200,
    load_models: bool = True,
    load_metrics: bool = False,
    checkpoint_interval: int = 10,
) -> None:
    # Initialize seed and device
    torch.random.manual_seed(random_seed)
    device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
    data_dir = Path.cwd() / "data/ecg5000"
    save_dir = Path.cwd() / "results/ecg5000/consistency_examples"
    if not save_dir.exists():
        os.makedirs(save_dir)

    # Load dataset
    train_dataset = ECG5000(data_dir, experiment="examples")
    train_dataset, test_dataset = random_split(train_dataset, (4000, 1000))
    train_loader = DataLoader(train_dataset, batch_size, True)
    test_loader = DataLoader(test_dataset, batch_size, False)
    # X_train = torch.stack([train_dataset[k][0] for k in range(len(train_dataset))])
    # X_test = torch.stack([test_dataset[k][0] for k in range(len(test_dataset))])
    time_steps = 140
    n_features = 1

    if load_metrics is not True:
        autoencoder = RecurrentAutoencoder(time_steps, n_features, dim_latent)
        
        name = autoencoder.name
        model_loaded = False
        if load_models == True:
            if (save_dir / (name + ".pt")).is_file():
                logging.info('Loading the pretrained model from: {}'.format((save_dir / (name + ".pt"))))
                model_loaded = True
            else:
                logging.info('Cannot load a model from: {}'.format((save_dir / (name + ".pt"))))

        if model_loaded == False:
            # Train the denoising autoencoder
            logging.info('Training the model from scratch.')
            logging.info(f"Now fitting {name}")
            autoencoder.fit(
            device,
            train_loader,
            test_loader,
            save_dir,
            n_epochs,
            checkpoint_interval=checkpoint_interval,
            )
        autoencoder.load_state_dict(
            torch.load(save_dir / (autoencoder.name + ".pt")), strict=False
        )

        # Prepare subset loaders for example-based explanation methods
        y_train = torch.tensor([train_dataset[k][1] for k in range(len(train_dataset))])
        idx_subtrain = [
            torch.nonzero(y_train == (n % 2))[n // 2].item() for n in range(subtrain_size)
        ]
        idx_subtest = torch.randperm(len(test_dataset))[:subtrain_size]
        train_subset = Subset(train_dataset, idx_subtrain)
        test_subset = Subset(test_dataset, idx_subtest)
        subtrain_loader = DataLoader(train_subset)
        subtest_loader = DataLoader(test_subset)
        labels_subtrain = torch.cat([label for _, label in subtrain_loader])
        labels_subtest = torch.cat([label for _, label in subtest_loader])
        recursion_depth = 100
        train_sampler = RandomSampler(
            train_dataset, replacement=True, num_samples=recursion_depth * batch_size
        )
        train_loader_replacement = DataLoader(
            train_dataset, batch_size, sampler=train_sampler
        )

        # Fitting explainers, computing the metric and saving everything
        autoencoder.train().to(device)
        l1_loss = torch.nn.L1Loss()
        explainer_list = [
            InfluenceFunctions(autoencoder, l1_loss, save_dir / "if_grads"),
            TracIn(autoencoder, l1_loss, save_dir / "tracin_grads"),
            SimplEx(autoencoder, l1_loss),
            NearestNeighbours(autoencoder, l1_loss),
        ]
        results_list = []
        # n_top_list = [1, 2, 5, 10, 20, 30, 40, 50, 100]
        frac_list = [0.05, 0.1, 0.2, 0.5, 0.7, 1.0]
        n_top_list = [int(frac * len(idx_subtrain)) for frac in frac_list]
        for explainer in explainer_list:
            logging.info(f"Now fitting {explainer} explainer")
            if isinstance(explainer, InfluenceFunctions):
                with torch.backends.cudnn.flags(enabled=False):
                    attribution = explainer.attribute_loader(
                        device,
                        subtrain_loader,
                        subtest_loader,
                        train_loader_replacement=train_loader_replacement,
                        recursion_depth=recursion_depth,
                    )
            else:
                attribution = explainer.attribute_loader(
                    device, subtrain_loader, subtest_loader
                )
            autoencoder.load_state_dict(
                torch.load(save_dir / (autoencoder.name + ".pt")), strict=False
            )
            sim_most, sim_least = similarity_rates(
                attribution, labels_subtrain, labels_subtest, n_top_list
            )
            results_list += [
                [str(explainer), "Most Important", 100 * frac, sim]
                for frac, sim in zip(frac_list, sim_most)
            ]
            results_list += [
                [str(explainer), "Least Important", 100 * frac, sim]
                for frac, sim in zip(frac_list, sim_least)
            ]
        results_df = pd.DataFrame(
            results_list,
            columns=[
                "Explainer",
                "Type of Examples",
                "% Examples Selected",
                "Similarity Rate",
            ],
        )
        logging.info(f"Saving results in {save_dir}")
        results_df.to_csv(save_dir / "metrics.csv")

    if (save_dir / "metrics.csv").is_file():
        logging.info('Loading the metrics from: {}'.format((save_dir / "metrics.csv")))
        results_df = pd.read_csv(save_dir / "metrics.csv")
    else:
        logging.info('Cannot load a metrics from: {}'.format((save_dir / "metrics.csv")))

    sns.lineplot(
        data=results_df,
        x="% Examples Selected",
        y="Similarity Rate",
        hue="Explainer",
        style="Type of Examples",
        palette="colorblind",
    )
    plt.savefig(save_dir / "ecg5000_similarity_rates.pdf")
    plt.show()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", type=str, default="consistency_features")
    parser.add_argument("--batch_size", type=int, default=20)
    parser.add_argument("--random_seed", type=int, default=42)
    parser.add_argument("--dim_latent", type=int, default=64)
    parser.add_argument("--checkpoint_interval", type=int, default=10)
    parser.add_argument("--subset_size", type=int, default=1000)
    args = parser.parse_args()
    if args.name == "consistency_features":
        consistency_feature_importance(
            batch_size=args.batch_size,
            random_seed=args.random_seed,
            dim_latent=args.dim_latent,
        )
    elif args.name == "consistency_examples":
        consistency_example_importance(
            batch_size=args.batch_size,
            random_seed=args.random_seed,
            dim_latent=args.dim_latent,
            subtrain_size=args.subset_size,
            checkpoint_interval=args.checkpoint_interval,
        )
    else:
        raise ValueError("Invalid experiment name.")
