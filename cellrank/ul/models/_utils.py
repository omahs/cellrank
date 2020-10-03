# -*- coding: utf-8 -*-
from typing import List, Union, TypeVar, Optional

import numpy as np
from scipy.stats import rankdata
from scipy.sparse import issparse, spmatrix
from sklearn.utils.sparsefuncs import csc_median_axis_0

import cellrank.logging as logg
from cellrank.ul._utils import valuedispatch
from cellrank.tl._constants import ModeEnum
from cellrank.ul._parallelize import parallelize

# sources:
# edgeR: https://github.com/jianjinxu/edgeR/blob/master/R/calcNormFactors.R

AnnData = TypeVar("AnnData")


class NormMode(ModeEnum):  # noqa
    TMM = "tmm"
    RLE = "rle"
    UPPER_QUANT = "upper_quant"
    NONE = "none"


def _extract_data(
    adata, layer: str, use_raw: bool = True
) -> Union[np.ndarray, spmatrix]:
    from anndata import AnnData as _AnnData

    if isinstance(adata, _AnnData):
        if use_raw:
            if not hasattr(adata, "raw"):
                raise AttributeError()
            elif adata.raw is None:
                raise ValueError()
            x = adata.raw.X
        elif layer is not None:
            if layer not in adata.layers:
                raise KeyError()
            x = adata.layers[layer]
        else:
            x = adata.X
    elif not isinstance(adata, (np.ndarray, spmatrix)):
        raise TypeError()
    else:
        x = adata

    return x


def _calculate_norm_factors(
    data: Union[AnnData, np.ndarray, spmatrix],
    method: str = NormMode.TMM.s,
    layer: Optional[str] = None,
    use_raw: bool = True,
    library_size: Optional[np.ndarray] = None,
    ref_ix: Optional[int] = None,
    logratio_trim: float = 0.3,
    sum_trim: float = 0.05,
    weight: bool = True,
    a_cutoff: float = -1e10,
    p: float = 0.75,
) -> np.ndarray:
    method = NormMode(method)

    x = _extract_data(data, layer, use_raw)

    if library_size is None:
        library_size = np.array(x.sum(1)).squeeze()
    elif library_size.shape != (x.shape[0],):
        raise ValueError()

    f = _dispatch_computation(
        method,
        x=x,
        library_size=library_size,
        ref_ix=ref_ix,
        logratio_trim=logratio_trim,
        sum_trim=sum_trim,
        weight=weight,
        a_cutoff=a_cutoff,
        p=p,
    )

    # return f / np.exp(np.mean(np.log(f)))
    return f / np.expm1(np.mean(np.log1p(f)))


@valuedispatch
def _dispatch_computation(mode, *_args, **_kwargs):
    raise NotImplementedError(mode)


@_dispatch_computation.register(NormMode.TMM)
def _(
    x: Union[np.ndarray, spmatrix], library_size, ref_ix=None, **kwargs
) -> np.ndarray:
    if ref_ix is None:
        assert library_size is not None
        f75 = _calc_factor_quant(x, library_size=library_size, p=0.75)
        ref_ix = np.argmin(np.abs(f75 - np.mean(f75)))

    return parallelize(
        _calc_factor_weighted,
        collection=np.arange(x.shape[0]),
        show_progress_bar=False,
        as_array=False,
        extractor=lambda res: np.array([r for rs in res for r in rs]),
        backend="threading",  # TODO: expose?
        n_jobs=4,
    )(
        obs_=x,
        obs_lib_size_=library_size,
        ref=x[ref_ix],
        ref_lib_size=library_size[ref_ix],
        **kwargs,
    )


@_dispatch_computation.register(NormMode.UPPER_QUANT)
def _calc_factor_quant(
    x: Union[np.ndarray, spmatrix], library_size: np.ndarray, p: float, **_
) -> np.ndarray:
    library_size = np.array(library_size).reshape((-1, 1))
    if not issparse(x):
        # this is same as below, which is R's default
        # return mquantiles(x / library_size, prob=q, alphap=1, betap=1, axis=1).data.squeeze()
        return np.quantile(x / library_size, p, axis=1)

    # not very efficient
    y = x.multiply(1.0 / library_size).tocsr()
    return np.array([np.quantile(y[i].A[0], p) for i in range(y.shape[0])])


@_dispatch_computation.register(NormMode.RLE)
def _(x: Union[np.ndarray, spmatrix], **_) -> np.ndarray:
    gm = np.array(np.exp(np.sum(np.log1p(x), axis=0))).squeeze()
    mask = (gm > 0) & np.isfinite(gm)
    if not issparse(x):
        return np.median(x[:, mask] / gm[mask], axis=1)

    return csc_median_axis_0(x.tocsr()[:, mask].multiply(1.0 / gm[mask]).tocsr().T)


@_dispatch_computation.register(NormMode.NONE)
def _(x: Union[np.ndarray, spmatrix], **_) -> np.ndarray:
    return np.ones((x.shape[0],), dtype=x.dtype)


def _calc_factor_weighted(
    ixs: np.ndarray,
    obs_: Union[np.ndarray, spmatrix],
    ref: Union[np.ndarray, spmatrix],
    obs_lib_size_: np.ndarray,
    ref_lib_size: Optional[float] = None,
    logratio_trim: float = 0.3,
    sum_trim: float = 0.05,
    weight: bool = True,
    a_cutoff: float = -1e10,
    queue=None,
    **_,
) -> List[float]:
    # TODO: vectorize, not parallelize
    if issparse(ref):
        ref = ref.A.squeeze(0)  # 1 x genes
    if ref_lib_size is None:
        ref_lib_size = np.sum(ref)

    r_scaled = ref / ref_lib_size
    log_ref = np.log2(r_scaled)

    res = []

    for ix in ixs:
        if queue is not None:
            queue.put(1)

        obs = obs_[ix]

        if issparse(obs):
            obs = obs.A.squeeze(0)  # 1 x genes

        obs_lib_size = obs_lib_size_[ix]

        if obs_lib_size is None:
            obs_lib_size = np.sum(obs)

        o_scaled = obs / obs_lib_size
        log_obs = np.log2(o_scaled)

        log_r = log_obs - log_ref
        abs_e = (log_obs + log_ref) / 2.0
        v = (obs_lib_size - obs) / o_scaled + (ref_lib_size - ref) / r_scaled

        mask = np.isfinite(log_r) & np.isfinite(abs_e) & (abs_e > a_cutoff)

        log_r = log_r[mask]
        abs_e = abs_e[mask]
        v = v[mask]

        if not len(log_r) or np.max(np.abs(log_r)) < 1e-6:
            res.append(1.0)
            continue

        n = len(log_r)
        lol = np.floor(n * logratio_trim) + 1.0
        hil = n + 1 - lol

        los = np.floor(n * sum_trim) + 1.0
        his = n + 1 - los

        rank_log_r, rank_abs_e = rankdata(log_r), rankdata(abs_e)
        mask = (
            (rank_log_r >= lol)
            & (rank_log_r <= hil)
            & (rank_abs_e >= los)
            & (rank_abs_e <= his)
        )

        f = (
            (np.nansum(log_r[mask] / v[mask]) / np.nansum(1.0 / v[mask]))
            if weight
            else np.nanmean(log_r[mask])
        )

        res.append(1.0 if not np.isfinite(f) else 2 ** f)

    if queue is not None:
        queue.put(None)

    return res


def _find_knots(
    pseudotime: np.ndarray,
    n_knots: int,
) -> np.ndarray:
    if n_knots <= 0:
        raise ValueError()

    if np.any(~np.isfinite(pseudotime)):
        raise ValueError()

    x = np.quantile(
        pseudotime, q=np.arange(n_knots, dtype=np.float64) / max(n_knots - 1, 1)
    )
    u, ix, c = np.unique(x, return_index=True, return_counts=True)

    if len(u) != len(x):
        locs = []
        for start, end, size in zip(x[ix], x[ix[1:]], c):
            locs.extend(np.linspace(start, end, size, endpoint=False))
        locs.extend(np.linspace(locs[-1], x[ix[-1]], c[-1] + 1, endpoint=True)[1:])
        locs = np.array(locs)
    else:
        locs = x

    locs[0] = np.min(pseudotime)
    locs[-1] = np.max(pseudotime)

    logg.debug(f"Setting knot locations to `{list(locs)}`.")

    return locs


def _get_offset(
    adata: AnnData, layer: Optional[str] = None, use_raw: bool = True, **kwargs
) -> np.ndarray:
    data = _extract_data(adata, layer=layer, use_raw=use_raw)
    try:
        nf = _calculate_norm_factors(adata, layer=layer, use_raw=use_raw, **kwargs)
    except Exception as e:
        # TODO: logg
        print(e)
        nf = np.ones(adata.n_obs, dtype=np.floay64)
    return np.log1p(nf * np.array(data.sum(1)).squeeze())
