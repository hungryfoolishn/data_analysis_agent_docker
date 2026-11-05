"""External public-data adapter infrastructure."""

from .base import (
    AdapterCache,
    AdapterDescriptor,
    AdapterQualityError,
    AdapterRequestError,
    AdapterTemporaryError,
    DataSourceAdapter,
    DatasetSpec,
    FetchRequest,
    FetchResult,
    HttpJsonAdapter,
    QualityReport,
)
from .registry import AdapterRegistry, CatalogEntry, DatasetCatalog

__all__ = [
    "AdapterCache",
    "AdapterDescriptor",
    "AdapterQualityError",
    "AdapterRequestError",
    "AdapterTemporaryError",
    "AdapterRegistry",
    "CatalogEntry",
    "DataSourceAdapter",
    "DatasetCatalog",
    "DatasetSpec",
    "FetchRequest",
    "FetchResult",
    "HttpJsonAdapter",
    "QualityReport",
]
