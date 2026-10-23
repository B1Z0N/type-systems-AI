"""Paired, assumption-light statistics for comparing two conditions.

Tasks differ far more from each other than conditions do, so every comparison is paired by
task: each task contributes one number (its mean under condition A minus its mean under B,
averaged over repetitions). From those per-task numbers we report the mean, a bootstrap 95%
interval and a two-sided sign-flip permutation p-value, which assumes only that tasks are
exchangeable. Cost-like metrics (time, tokens, files read, ...) are compared on the log scale
and reported as a ratio, e.g. 0.80 = "A needs 20% less than B".
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def per_task_difference(frame: pd.DataFrame, metric: str, a: str, b: str, log: bool) -> pd.Series:
    """Per task: mean(metric | a) - mean(metric | b), on the log scale if `log` (tasks with both only)."""
    data = frame[frame["condition"].isin([a, b])][["task", "condition", metric]].dropna()
    if log:
        data = data[data[metric] > 0].assign(**{metric: lambda d: np.log(d[metric].astype(float))})
    means = data.groupby(["task", "condition"])[metric].mean().unstack()
    if a not in means or b not in means:
        return pd.Series(dtype=float)
    return (means[a] - means[b]).dropna()


def sign_flip_p(diffs: np.ndarray, n: int = 20000, seed: int = 0) -> float:
    diffs = np.asarray(diffs, dtype=float)
    if len(diffs) == 0 or np.allclose(diffs, 0):
        return 1.0
    observed = abs(diffs.mean())
    if len(diffs) <= 16:  # exact: all 2^n sign patterns
        signs = np.array(np.meshgrid(*[[-1, 1]] * len(diffs))).reshape(len(diffs), -1).T
    else:
        signs = np.random.default_rng(seed).choice([-1, 1], size=(n, len(diffs)))
    null = np.abs((signs * diffs).mean(axis=1))
    return float((np.sum(null >= observed - 1e-12) + 1) / (len(null) + 1))


def bootstrap_ci(diffs: np.ndarray, n: int = 10000, seed: int = 0) -> tuple[float, float]:
    diffs = np.asarray(diffs, dtype=float)
    if len(diffs) < 2:
        return float("nan"), float("nan")
    means = np.random.default_rng(seed).choice(diffs, size=(n, len(diffs)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def compare(frame: pd.DataFrame, metric: str, a: str, b: str, log: bool) -> dict[str, float]:
    diffs = per_task_difference(frame, metric, a, b, log).to_numpy()
    low, high = bootstrap_ci(diffs)
    estimate = float(diffs.mean()) if len(diffs) else float("nan")
    if log:  # back to a ratio
        estimate, low, high = np.exp(estimate), np.exp(low), np.exp(high)
    return {"tasks": len(diffs), "estimate": estimate, "ci_low": low, "ci_high": high, "p": sign_flip_p(diffs)}


def holm(pvalues: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values (NaN entries are left out and stay NaN)."""
    indexed = sorted((p, i) for i, p in enumerate(pvalues) if p == p)
    adjusted = [float("nan")] * len(pvalues)
    running = 0.0
    for rank, (p, i) in enumerate(indexed):
        running = max(running, min(1.0, (len(indexed) - rank) * p))
        adjusted[i] = running
    return adjusted
