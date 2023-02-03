import abc

import torch
from torch.nn import functional as F

from lfxai.utils.math import (
    log_density_gaussian,
    log_importance_weight_matrix,
    matrix_log_density_gaussian,
)

LOSSES = ["betaH", "btcvae"]
RECON_DIST = ["bernoulli", "laplace", "gaussian"]


def get_loss_f(loss_name, **kwargs_parse):
    """Return the correct loss function given the argparse arguments."""
    kwargs_all = dict(
        rec_dist=kwargs_parse["rec_dist"], steps_anneal=kwargs_parse["reg_anneal"]
    )
    if loss_name == "betaH":
        return BetaHLoss(beta=kwargs_parse["betaH_B"], **kwargs_all)

    elif loss_name == "btcvae":
        return BtcvaeLoss(
            kwargs_parse["n_data"],
            alpha=kwargs_parse["btcvae_A"],
            beta=kwargs_parse["btcvae_B"],
            gamma=kwargs_parse["btcvae_G"],
            **kwargs_all,
        )
    else:
        assert loss_name not in LOSSES
        raise ValueError(f"Uknown loss : {loss_name}")


class BaseVAELoss(abc.ABC):
    """
    Base class for losses.

    Parameters:
    -----------
    record_loss_every: int, optional
        Every how many steps to recorsd the loss.
    rec_dist: {"bernoulli", "gaussian", "laplace"}, optional
        Reconstruction distribution istribution of the likelihood on the each pixel.
        Implicitely defines the reconstruction loss. Bernoulli corresponds to a
        binary cross entropy (bse), Gaussian corresponds to MSE, Laplace
        corresponds to L1.
    steps_anneal: nool, optional
        Number of annealing steps where gradually adding the regularisation.
    """

    def __init__(self, record_loss_every=50, rec_dist="bernoulli", steps_anneal=0):
        self.n_train_steps = 0
        self.record_loss_every = record_loss_every
        self.rec_dist = rec_dist
        self.steps_anneal = steps_anneal

    @abc.abstractmethod
    def __str__(self) -> str:
        """
        Returns: Name of the loss
        """

    @abc.abstractmethod
    def __call__(self, data, recon_data, latent_dist, is_train, storer, **kwargs):
        """Calculates loss for a batch of data.

        Parameters:
        -----------
        data : torch.Tensor
            Input data (e.g. batch of images). Shape : (batch_size, n_chan,
            height, width).
        recon_data : torch.Tensor
            Reconstructed data. Shape : (batch_size, n_chan, height, width).
        latent_dist : tuple of torch.tensor
            sufficient statistics of the latent dimension. E.g. for gaussian
            (mean, log_var) each of shape : (batch_size, latent_dim).
        is_train : bool
            Whether currently in train mode.
        storer : dict
            Dictionary in which to store important variables for vizualisation.
        kwargs:
            Loss specific arguments
        """

    def _pre_call(self, is_train, storer):
        if is_train:
            self.n_train_steps += 1

        if not is_train or self.n_train_steps % self.record_loss_every == 1:
            storer = storer
        else:
            storer = None

        return storer


class BetaHLoss(BaseVAELoss):
    """
    Compute the Beta-VAE loss as in [1]

    Parameters:
    -----------
    beta : float, optional
        Weight of the kl divergence.
    kwargs:
        Additional arguments for `BaseLoss`, e.g. rec_dist`.

    References:
    -----------
        [1] Higgins, Irina, et al. "beta-vae: Learning basic visual concepts with
        a constrained variational framework." (2016).
    """

    def __init__(self, beta=4, **kwargs):
        super().__init__(**kwargs)
        self.beta = beta

    def __call__(self, data, recon_data, latent_dist, is_train, storer, **kwargs):
        storer = self._pre_call(is_train, storer)

        rec_loss = _reconstruction_loss(
            data, recon_data, storer=storer, distribution=self.rec_dist
        )
        kl_loss = _kl_normal_loss(*latent_dist, storer)
        anneal_reg = (
            linear_annealing(0, 1, self.n_train_steps, self.steps_anneal)
            if is_train
            else 1
        )
        loss = rec_loss + anneal_reg * (self.beta * kl_loss)

        if storer is not None:
            storer["loss"].append(loss.item())
        return loss

    def __str__(self):
        return "Beta"


class BtcvaeLoss(BaseVAELoss):
    """
    Compute the decomposed KL loss with either minibatch weighted sampling or
    minibatch stratified sampling according to [1]

    Parameters:
    -----------
    n_data: int
        Number of data in the training set
    alpha : float
        Weight of the mutual information term.
    beta : float
        Weight of the total correlation term.
    gamma : float
        Weight of the dimension-wise KL term.
    is_mss : bool
        Whether to use minibatch stratified sampling instead of minibatch
        weighted sampling.
    kwargs:
        Additional arguments for `BaseLoss`, e.g. rec_dist`.

    References:
    -----------
       [1] Chen, Tian Qi, et al. "Isolating sources of disentanglement in variational
       autoencoders." Advances in Neural Information Processing Systems. 2018.
    """

    def __init__(self, n_data, alpha=1.0, beta=6.0, gamma=1.0, is_mss=True, **kwargs):
        super().__init__(**kwargs)
        self.n_data = n_data
        self.beta = beta
        self.alpha = alpha
        self.gamma = gamma
        self.is_mss = is_mss  # minibatch stratified sampling

    def __call__(
        self, data, recon_batch, latent_dist, is_train, storer, latent_sample=None
    ):
        storer = self._pre_call(is_train, storer)
        batch_size, latent_dim = latent_sample.shape

        rec_loss = _reconstruction_loss(
            data, recon_batch, storer=storer, distribution=self.rec_dist
        )
        log_pz, log_qz, log_prod_qzi, log_q_zCx = _get_log_pz_qz_prodzi_qzCx(
            latent_sample, latent_dist, self.n_data, is_mss=self.is_mss
        )
        # I[z;x] = KL[q(z,x)||q(x)q(z)] = E_x[KL[q(z|x)||q(z)]]
        mi_loss = (log_q_zCx - log_qz).mean()
        # TC[z] = KL[q(z)||\prod_i z_i]
        tc_loss = (log_qz - log_prod_qzi).mean()
        # dw_kl_loss is KL[q(z)||p(z)] instead of usual KL[q(z|x)||p(z))]
        dw_kl_loss = (log_prod_qzi - log_pz).mean()

        anneal_reg = (
            linear_annealing(0, 1, self.n_train_steps, self.steps_anneal)
            if is_train
            else 1
        )

        # total loss
        loss = rec_loss + (
            self.alpha * mi_loss
            + self.beta * tc_loss
            + anneal_reg * self.gamma * dw_kl_loss
        )

        if storer is not None:
            storer["loss"].append(loss.item())
            storer["mi_loss"].append(mi_loss.item())
            storer["tc_loss"].append(tc_loss.item())
            storer["dw_kl_loss"].append(dw_kl_loss.item())
            # computing this for storing and comparaison purposes
            _ = _kl_normal_loss(*latent_dist, storer)

        return loss

    def __str__(self):
        return "TC"


def _reconstruction_loss(data, recon_data, distribution="bernoulli", storer=None):
    """
    Calculates the per image reconstruction loss for a batch of data. I.e. negative
    log likelihood.

    Parameters:
    -----------
    data : torch.Tensor
        Input data (e.g. batch of images). Shape : (batch_size, n_chan,
        height, width).
    recon_data : torch.Tensor
        Reconstructed data. Shape : (batch_size, n_chan, height, width).
    distribution : {"bernoulli", "gaussian", "laplace"}
        Distribution of the likelihood on the each pixel. Implicitely defines the
        loss Bernoulli corresponds to a binary cross entropy (bse) loss and is the
        most commonly used. It has the issue that it doesn't penalize the same
        way (0.1,0.2) and (0.4,0.5), which might not be optimal. Gaussian
        distribution corresponds to MSE, and is sometimes used, but hard to train
        ecause it ends up focusing only a few pixels that are very wrong. Laplace
        distribution corresponds to L1 solves partially the issue of MSE.
    storer : dict
        Dictionary in which to store important variables for vizualisation.

    Returns:
    --------
    loss : torch.Tensor
        Per image cross entropy (i.e. normalized per batch but not pixel and
        channel)
    """
    batch_size, n_chan, height, width = recon_data.size()
    n_chan == 3

    if distribution == "bernoulli":
        loss = F.binary_cross_entropy(recon_data, data, reduction="sum")
    elif distribution == "gaussian":
        # loss in [0,255] space but normalized by 255 to not be too big
        loss = F.mse_loss(recon_data * 255, data * 255, reduction="sum") / 255
    elif distribution == "laplace":
        # loss in [0,255] space but normalized by 255 to not be too big but
        # multiply by 255 and divide 255, is the same as not doing anything for L1
        loss = F.l1_loss(recon_data, data, reduction="sum")
        loss = (
            loss * 3
        )  # emperical value to give similar values than bernoulli => use same hyperparam
        loss = loss * (loss != 0)  # masking to avoid nan
    else:
        assert distribution not in RECON_DIST
        raise ValueError(f"Unkown distribution: {distribution}")

    loss = loss / batch_size

    if storer is not None:
        storer["recon_loss"].append(loss.item())

    return loss


def _kl_normal_loss(mean, logvar, storer=None):
    """Calculates the KL divergence between a normal distribution
    with diagonal covariance and a unit normal distribution.

    Parameters:
    -----------
    mean : torch.Tensor
        Mean of the normal distribution. Shape (batch_size, latent_dim) where
        D is dimension of distribution.
    logvar : torch.Tensor
        Diagonal log variance of the normal distribution. Shape (batch_size,
        latent_dim)
    storer : dict
        Dictionary in which to store important variables for vizualisation.
    """
    latent_dim = mean.size(1)
    # batch mean of kl for each latent dimension
    latent_kl = 0.5 * (-1 - logvar + mean.pow(2) + logvar.exp()).mean(dim=0)
    total_kl = latent_kl.sum()

    if storer is not None:
        storer["kl_loss"].append(total_kl.item())
        for i in range(latent_dim):
            storer["kl_loss_" + str(i)].append(latent_kl[i].item())

    return total_kl


def _permute_dims(latent_sample):
    """Implementation of Algorithm 1 in ref [1]. Randomly permutes the sample from
    q(z) (latent_dist) across the batch for each of the latent dimensions (mean
    and log_var).

    Parameters:
    -----------
    latent_sample: torch.Tensor
        sample from the latent dimension using the reparameterisation trick
        shape : (batch_size, latent_dim).

    References:
    -----------
        [1] Kim, Hyunjik, and Andriy Mnih. "Disentangling by factorising."
        arXiv preprint arXiv:1802.05983 (2018).
    """
    perm = torch.zeros_like(latent_sample)
    batch_size, dim_z = perm.size()

    for z in range(dim_z):
        pi = torch.randperm(batch_size).to(latent_sample.device)
        perm[:, z] = latent_sample[pi, z]

    return perm


def linear_annealing(init, fin, step, annealing_steps):
    """Linear annealing of a parameter."""
    if annealing_steps == 0:
        return fin
    assert fin > init
    delta = fin - init
    annealed = min(init + delta * step / annealing_steps, fin)
    return annealed


# Batch TC specific
# TO-DO: test if mss is better!
def _get_log_pz_qz_prodzi_qzCx(latent_sample, latent_dist, n_data, is_mss=True):
    batch_size, hidden_dim = latent_sample.shape

    # calculate log q(z|x)
    log_q_zCx = log_density_gaussian(latent_sample, *latent_dist).sum(dim=1)

    # calculate log p(z)
    # mean and log var is 0
    zeros = torch.zeros_like(latent_sample)
    log_pz = log_density_gaussian(latent_sample, zeros, zeros).sum(1)

    mat_log_qz = matrix_log_density_gaussian(latent_sample, *latent_dist)

    if is_mss:
        # use stratification
        log_iw_mat = log_importance_weight_matrix(batch_size, n_data).to(
            latent_sample.device
        )
        mat_log_qz = mat_log_qz + log_iw_mat.view(batch_size, batch_size, 1)

    log_qz = torch.logsumexp(mat_log_qz.sum(2), dim=1, keepdim=False)
    log_prod_qzi = torch.logsumexp(mat_log_qz, dim=1, keepdim=False).sum(1)

    return log_pz, log_qz, log_prod_qzi, log_q_zCx
