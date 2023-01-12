# Label-Free XAI
[![Tests](https://github.com/vanderschaarlab/Label-Free-XAI/actions/workflows/test.yml/badge.svg)](https://github.com/vanderschaarlab/Label-Free-XAI/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.png)](https://opensource.org/licenses/MIT)
[![Documentation Status](https://readthedocs.org/projects/lfxai/badge/?version=latest)](https://lfxai.readthedocs.io/en/latest/?badge=latest)

![image](https://github.com/vanderschaarlab/Label-Free-XAI/raw/main/docs/illustration.png "Label-Free Explainability")

Code Author: Jonathan Crabbé ([jc2133@cam.ac.uk](mailto:jc2133@cam.ac.uk))

This repository contains the implementation of LFXAI, a framework to explain the latent
representations of unsupervised black-box models with the help of usual feature importance and example-based methods.
For more details, please read our [ICML 2022 paper](https://arxiv.org/abs/2203.01928): 'Label-Free Explainability for Unsupervised Models'.

## 1. Installation
From PyPI
```bash
pip install lfxai
```

From repository:
1. Clone the repository
2. Create a new virtual environment with Python 3.8
3. Run the following command from the repository folder:

```shell
pip install .
```

When the packages are installed, you are ready to explain unsupervised models.

## 2. Toy example

Bellow, you can find a toy demonstration where we compute label-free feature and example importance
with a MNIST autoencoder. The relevant code can be found in the folder
[explanations](explanations).

```python
import torch
from pathlib import Path
from torchvision.datasets import MNIST
from torch.utils.data import DataLoader, Subset
from torchvision import transforms
from torch.nn import MSELoss
from captum.attr import IntegratedGradients

from lfxai.models.images import AutoEncoderMnist, EncoderMnist, DecoderMnist
from lfxai.models.pretext import Identity
from lfxai.explanations.features import attribute_auxiliary
from lfxai.explanations.examples import SimplEx

# Select torch device
device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")

# Load data
data_dir = Path.cwd() / "data/mnist"
train_dataset = MNIST(data_dir, train=True, download=True)
test_dataset = MNIST(data_dir, train=False, download=True)
train_dataset.transform = transforms.Compose([transforms.ToTensor()])
test_dataset.transform = transforms.Compose([transforms.ToTensor()])
train_loader = DataLoader(train_dataset, batch_size=100)
test_loader = DataLoader(test_dataset, batch_size=100, shuffle=False)

# Get a model
encoder = EncoderMnist(encoded_space_dim=10)
decoder = DecoderMnist(encoded_space_dim=10)
model = AutoEncoderMnist(encoder, decoder, latent_dim=10, input_pert=Identity())
model.to(device)

# Get label-free feature importance
baseline = torch.zeros((1, 1, 28, 28)).to(device) # black image as baseline
attr_method = IntegratedGradients(model)
feature_importance = attribute_auxiliary(encoder, test_loader,
                                         device, attr_method, baseline)

# Get label-free example importance
train_subset = Subset(train_dataset, indices=list(range(500))) # Limit the number of training examples
train_subloader = DataLoader(train_subset, batch_size=500)
attr_method = SimplEx(model, loss_f=MSELoss())
example_importance = attr_method.attribute_loader(device, train_subloader, test_loader)
```



## 3. Reproducing the paper results

### MNIST experiments
In the `experiments` folder, run the following script
```shell
python -m mnist --name experiment_name
```
where experiment_name can take the following values:

| experiment_name      | description                                                                  |
|----------------------|------------------------------------------------------------------------------|
| consistency_features | Consistency check for label-free<br/> feature importance (paper Section 4.1) |
| consistency_examples | Consistency check for label-free<br/> example importance (paper Section 4.1) |
| roar_test            | ROAR test for label-free<br/> feature importance (paper Appendix C)          |
| pretext              | Pretext task sensitivity<br/> use case (paper Section 4.2)                   |
| disvae               | Challenging assumptions with <br/> disentangled VAEs (paper Section 4.3)     |


The resulting plots and data are saved [here](results/mnist).

### ECG5000 experiments
Run the following script
```shell
python -m ecg5000 --name experiment_name
```
where experiment_name can take the following values:

| experiment_name      | description                                                                  |
|----------------------|------------------------------------------------------------------------------|
| consistency_features | Consistency check for label-free<br/> feature importance (paper Section 4.1) |
| consistency_examples | Consistency check for label-free<br/> example importance (paper Section 4.1) |



The resulting plots and data are saved [here](results/ecg5000).

### CIFAR10 experiments
Run the following script
```shell
python -m cifar10
```
The experiment can be selected by changing the experiment_name
parameter in [this file](simclr_config.yaml).
The parameter can take the following values:

| experiment_name      | description                                                                  |
|----------------------|------------------------------------------------------------------------------|
| consistency_features | Consistency check for label-free<br/> feature importance (paper Section 4.1) |
| consistency_examples | Consistency check for label-free<br/> example importance (paper Section 4.1) |



The resulting plots and data are saved [here](results/cifar10).
### dSprites experiment
Run the following script
```shell
python -m dsprites
```
The experiment needs several hours to run since several VAEs are trained.
The resulting plots and data are saved [here](results/dsprites).
## 4. Citing

If you use this code, please cite the associated paper:

```

@InProceedings{pmlr-v162-crabbe22a,
  title = 	 {Label-Free Explainability for Unsupervised Models},
  author =       {Crabb{\'e}, Jonathan and van der Schaar, Mihaela},
  booktitle = 	 {Proceedings of the 39th International Conference on Machine Learning},
  pages = 	 {4391--4420},
  year = 	 {2022},
  editor = 	 {Chaudhuri, Kamalika and Jegelka, Stefanie and Song, Le and Szepesvari, Csaba and Niu, Gang and Sabato, Sivan},
  volume = 	 {162},
  series = 	 {Proceedings of Machine Learning Research},
  month = 	 {17--23 Jul},
  publisher =    {PMLR},
  pdf = 	 {https://proceedings.mlr.press/v162/crabbe22a/crabbe22a.pdf},
  url = 	 {https://proceedings.mlr.press/v162/crabbe22a.html},
  abstract = 	 {Unsupervised black-box models are challenging to interpret. Indeed, most existing explainability methods require labels to select which component(s) of the black-box’s output to interpret. In the absence of labels, black-box outputs often are representation vectors whose components do not correspond to any meaningful quantity. Hence, choosing which component(s) to interpret in a label-free unsupervised/self-supervised setting is an important, yet unsolved problem. To bridge this gap in the literature, we introduce two crucial extensions of post-hoc explanation techniques: (1) label-free feature importance and (2) label-free example importance that respectively highlight influential features and training examples for a black-box to construct representations at inference time. We demonstrate that our extensions can be successfully implemented as simple wrappers around many existing feature and example importance methods. We illustrate the utility of our label-free explainability paradigm through a qualitative and quantitative comparison of representation spaces learned by various autoencoders trained on distinct unsupervised tasks.}
}
```
