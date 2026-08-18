"""Deterministic sampling protocol with explicit disclosure metadata."""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field


class SamplingSpec(BaseModel):
    method: Literal["head", "random", "stratified"] = "random"
    target_rows: int = Field(default=1000, gt=0)
    random_seed: int = 20260817
    stratification_fields: list[str] = Field(default_factory=list)


class SamplingMetadata(BaseModel):
    method: str
    random_seed: int | None = None
    target_rows: int
    original_row_count: int
    sampled_row_count: int
    stratification_fields: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)


def sample_dataframe(dataframe: pd.DataFrame, spec: SamplingSpec) -> tuple[pd.DataFrame, SamplingMetadata]:
    original = len(dataframe)
    target = min(spec.target_rows, original)
    if target == original:
        sampled = dataframe.copy()
    elif spec.method == "head":
        sampled = dataframe.head(target).copy()
    elif spec.method == "stratified":
        fields = [field for field in spec.stratification_fields if field in dataframe.columns]
        if not fields:
            raise ValueError("Stratified sampling requires at least one existing stratification field")
        groups = dataframe.groupby(fields, dropna=False, sort=True)
        sizes = groups.size()
        allocation = (sizes / sizes.sum() * target).astype(int).clip(lower=1)
        # A deterministic cap prevents small groups from making the sample exceed target.
        allocation = allocation.clip(upper=sizes)
        sampled_parts = []
        for key, group in groups:
            count = int(allocation.loc[key])
            sampled_parts.append(group.sample(n=count, random_state=spec.random_seed))
        sampled = pd.concat(sampled_parts, ignore_index=False).head(target).copy()
    else:
        sampled = dataframe.sample(n=target, random_state=spec.random_seed).copy()
    coverage: dict[str, Any] = {"sample_fraction": (len(sampled) / original) if original else 1.0}
    for field in spec.stratification_fields:
        if field in dataframe.columns:
            coverage[field] = {
                "source_distinct": int(dataframe[field].nunique(dropna=False)),
                "sample_distinct": int(sampled[field].nunique(dropna=False)),
            }
    return sampled, SamplingMetadata(
        method=spec.method,
        random_seed=None if spec.method == "head" else spec.random_seed,
        target_rows=spec.target_rows,
        original_row_count=original,
        sampled_row_count=len(sampled),
        stratification_fields=spec.stratification_fields,
        coverage=coverage,
    )
