"""Financial period normalization for Runtime V9.3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import re


class PeriodType(str, Enum):
    FY = "FY"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    H1 = "H1"
    H2 = "H2"
    TTM = "TTM"


class PeriodNormalizationError(ValueError):
    """Raised when a financial period cannot be normalized safely."""


@dataclass(frozen=True, order=False)
class FinancialPeriod:
    raw_period: str
    year: int
    period_type: PeriodType
    start_date: date
    end_date: date

    @property
    def is_fiscal_year(self) -> bool:
        return self.period_type == PeriodType.FY

    @property
    def normalized_period(self) -> str:
        """Stable identifier while preserving compatibility with FY values."""
        if self.period_type == PeriodType.FY:
            return str(self.year)
        if self.period_type in {PeriodType.Q1, PeriodType.Q2, PeriodType.Q3, PeriodType.Q4}:
            return f"{self.year}{self.period_type.value}"
        return f"{self.year}{self.period_type.value}"

    @property
    def order_key(self) -> tuple[int, float]:
        """Order by period end date, with FY explicitly following H2/Q4."""
        rank = {
            PeriodType.Q1: 1.0,
            PeriodType.H1: 1.5,
            PeriodType.Q2: 2.0,
            PeriodType.Q3: 3.0,
            PeriodType.Q4: 4.0,
            PeriodType.H2: 4.5,
            PeriodType.FY: 5.0,
        }.get(self.period_type, 9.0)
        return self.year, self.end_date.toordinal(), rank

    def previous(self) -> "FinancialPeriod":
        if self.period_type == PeriodType.FY:
            return FinancialPeriod(
                raw_period=str(self.year - 1),
                year=self.year - 1,
                period_type=PeriodType.FY,
                start_date=date(self.year - 1, 1, 1),
                end_date=date(self.year - 1, 12, 31),
            )
        if self.period_type == PeriodType.Q1:
            return FinancialPeriod(
                raw_period=f"{self.year - 1}Q4",
                year=self.year - 1,
                period_type=PeriodType.Q4,
                start_date=date(self.year - 1, 7, 1),
                end_date=date(self.year - 1, 9, 30),
            )
        if self.period_type == PeriodType.Q2:
            return FinancialPeriod(
                raw_period=f"{self.year}Q1",
                year=self.year,
                period_type=PeriodType.Q1,
                start_date=date(self.year, 1, 1),
                end_date=date(self.year, 3, 31),
            )
        if self.period_type == PeriodType.Q3:
            return FinancialPeriod(
                raw_period=f"{self.year}Q2",
                year=self.year,
                period_type=PeriodType.Q2,
                start_date=date(self.year, 4, 1),
                end_date=date(self.year, 6, 30),
            )
        if self.period_type == PeriodType.Q4:
            return FinancialPeriod(
                raw_period=f"{self.year}Q3",
                year=self.year,
                period_type=PeriodType.Q3,
                start_date=date(self.year, 4, 1),
                end_date=date(self.year, 6, 30),
            )
        if self.period_type == PeriodType.H2:
            return FinancialPeriod(
                raw_period=f"{self.year}H1",
                year=self.year,
                period_type=PeriodType.H1,
                start_date=date(self.year, 1, 1),
                end_date=date(self.year, 6, 30),
            )
        if self.period_type == PeriodType.H1:
            return FinancialPeriod(
                raw_period=f"{self.year - 1}H2",
                year=self.year - 1,
                period_type=PeriodType.H2,
                start_date=date(self.year - 1, 7, 1),
                end_date=date(self.year - 1, 12, 31),
            )
        raise PeriodNormalizationError("TTM previous period is not supported in V9.3")

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, FinancialPeriod):
            return NotImplemented
        return self.order_key < other.order_key

    def __le__(self, other: object) -> bool:
        if not isinstance(other, FinancialPeriod):
            return NotImplemented
        return self.order_key <= other.order_key

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, FinancialPeriod):
            return NotImplemented
        return self.order_key > other.order_key

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, FinancialPeriod):
            return NotImplemented
        return self.order_key >= other.order_key


class PeriodNormalizer:
    """Normalize explicit V9.3-supported annual and quarterly periods."""

    _FY_PATTERNS = (
        re.compile(r"^(?P<year>\d{4})$"),
        re.compile(r"^(?P<year>\d{4})年度$"),
        re.compile(r"^FY(?P<year>\d{4})$", re.IGNORECASE),
    )
    _QUARTER_PATTERN = re.compile(
        r"^(?P<year>\d{4})-?(?P<quarter>Q[1-4])$",
        re.IGNORECASE,
    )
    _HALF_PATTERN = re.compile(
        r"^(?P<year>\d{4})-?(?P<half>H[12])$",
        re.IGNORECASE,
    )
    _QUARTER_RANGES = {
        PeriodType.Q1: (1, 1, 3, 31),
        PeriodType.Q2: (4, 1, 6, 30),
        PeriodType.Q3: (7, 1, 9, 30),
        PeriodType.Q4: (10, 1, 12, 31),
    }
    _HALF_RANGES = {
        PeriodType.H1: (1, 1, 6, 30),
        PeriodType.H2: (7, 1, 12, 31),
    }

    def parse(self, raw_period: str | int) -> FinancialPeriod:
        raw = str(raw_period).strip()
        if not raw:
            raise PeriodNormalizationError("Financial period is empty")

        upper = raw.upper()
        for pattern in self._FY_PATTERNS:
            match = pattern.match(raw if "FY" not in upper else upper)
            if not match:
                continue
            year = int(match.group("year"))
            return FinancialPeriod(
                raw_period=raw,
                year=year,
                period_type=PeriodType.FY,
                start_date=date(year, 1, 1),
                end_date=date(year, 12, 31),
            )

        match = self._QUARTER_PATTERN.match(upper)
        if match:
            year = int(match.group("year"))
            period_type = PeriodType(match.group("quarter").upper())
            start_month, start_day, end_month, end_day = self._QUARTER_RANGES[period_type]
            return FinancialPeriod(
                raw_period=raw,
                year=year,
                period_type=period_type,
                start_date=date(year, start_month, start_day),
                end_date=date(year, end_month, end_day),
            )

        match = self._HALF_PATTERN.match(upper)
        if match:
            year = int(match.group("year"))
            period_type = PeriodType(match.group("half").upper())
            start_month, start_day, end_month, end_day = self._HALF_RANGES[period_type]
            return FinancialPeriod(
                raw_period=raw,
                year=year,
                period_type=period_type,
                start_date=date(year, start_month, start_day),
                end_date=date(year, end_month, end_day),
            )

        if upper in {"TTM", "LTM"}:
            raise PeriodNormalizationError(
                f"TTM/LTM financial periods are not supported in V9.3: {raw}"
            )
        raise PeriodNormalizationError(f"Unsupported financial period: {raw}")

    def normalize(self, raw_period: str | int) -> str:
        return self.parse(raw_period).normalized_period

    def order_key(self, raw_period: str | int) -> tuple[int, int]:
        return self.parse(raw_period).order_key
