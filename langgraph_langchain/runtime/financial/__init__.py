"""Financial analysis domain layer for Runtime V9."""

from .models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    FinancialAnalysisResult,
    FinancialCalculation,
    FinancialDataSource,
    FinancialObservation,
    FinancialIndicator,
    FinancialQuery,
    FinancialAnomalySignal,
    FinancialComparison,
    FinancialComparisonEntity,
    FinancialEvidence,
    FinancialFinding,
    FinancialRiskSignal,
    FinancialVerification,
    IncomeStatement,
)
from .metrics import (
    CALCULATED,
    INVALID,
    UNAVAILABLE,
    FinancialMetricDefinition,
    FinancialMetricRegistry,
    MetricComputation,
    calculate_metric,
    compute_metric,
    financial_metric_registry,
)
from .period import FinancialPeriod, PeriodNormalizer, PeriodNormalizationError, PeriodType
from .unit import UnitConversion, UnitNormalizer
from .fact import FinancialFact, FinancialFactBuilder
from .golden import (
    FormulaGoldenCase,
    FormulaGoldenLoader,
    FormulaGoldenResult,
    FormulaGoldenRunner,
    FormulaGoldenSummary,
)
from .data_service import FinancialDataService
from .classifier import FinancialTaskClassifier, FinancialTaskType
from .workflow import FinancialAnalysisWorkflow
from .verification_engine import FinancialVerificationEngine
from .anomaly_engine import FinancialAnomalyEngine
from .finding_engine import FinancialFindingEngine
from .evaluation import FinancialEvaluationAdapter
from .risk_detector import FinancialRiskDetector

__all__ = [
    "BalanceSheetStatement",
    "CashFlowStatement",
    "Company",
    "FinancialAnalysisResult",
    "FinancialAnalysisWorkflow",
    "FinancialAnomalyEngine",
    "FinancialAnomalySignal",
    "CALCULATED",
    "FinancialCalculation",
    "FinancialComparison",
    "FinancialComparisonEntity",
    "FinancialDataService",
    "FinancialDataSource",
    "FinancialVerificationEngine",
    "FormulaGoldenCase",
    "FormulaGoldenLoader",
    "FormulaGoldenResult",
    "FormulaGoldenRunner",
    "FormulaGoldenSummary",
    "INVALID",
    "MetricComputation",
    "FinancialEvidence",
    "FinancialFact",
    "FinancialFactBuilder",
    "FinancialFinding",
    "FinancialPeriod",
    "FinancialEvaluationAdapter",
    "FinancialIndicator",
    "FinancialMetricDefinition",
    "FinancialMetricRegistry",
    "UNAVAILABLE",
    "calculate_metric",
    "compute_metric",
    "FinancialObservation",
    "FinancialQuery",
    "FinancialRiskDetector",
    "FinancialRiskSignal",
    "FinancialFindingEngine",
    "FinancialTaskClassifier",
    "FinancialTaskType",
    "FinancialVerification",
    "IncomeStatement",
    "PeriodNormalizationError",
    "PeriodNormalizer",
    "PeriodType",
    "UnitConversion",
    "UnitNormalizer",
    "financial_metric_registry",
]
