"""Generate sample data files for benchmark testing."""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta


def generate_sales_simple():
    """Generate simple sales data."""
    data = {
        "product": ["Product A", "Product B", "Product C", "Product D", "Product E"] * 20,
        "quantity": np.random.randint(1, 50, 100),
        "price": np.random.uniform(10, 100, 100),
    }
    df = pd.DataFrame(data)
    df["revenue"] = df["quantity"] * df["price"]
    return df


def generate_sales_timeseries():
    """Generate time series sales data."""
    dates = pd.date_range(start="2023-01-01", end="2023-12-31", freq="D")
    data = {
        "date": dates,
        "sales": np.random.normal(1000, 200, len(dates)) + np.linspace(0, 500, len(dates)),  # Upward trend
        "orders": np.random.poisson(50, len(dates)),
    }
    df = pd.DataFrame(data)
    return df


def generate_customers():
    """Generate customer data for segmentation."""
    n = 500
    data = {
        "customer_id": [f"C{i:04d}" for i in range(n)],
        "total_purchases": np.random.poisson(10, n),
        "total_spent": np.random.lognormal(6, 1, n),
        "days_since_last_purchase": np.random.exponential(30, n),
        "avg_order_value": np.random.normal(100, 30, n),
    }
    df = pd.DataFrame(data)
    df["total_spent"] = df["total_spent"].clip(lower=0)
    df["avg_order_value"] = df["avg_order_value"].clip(lower=10)
    return df


def generate_sales_missing():
    """Generate sales data with missing values."""
    data = {
        "product": ["Product A", "Product B", "Product C", "Product D"] * 25,
        "quantity": np.random.randint(1, 50, 100),
        "price": np.random.uniform(10, 100, 100),
        "region": np.random.choice(["North", "South", "East", "West"], 100),
    }
    df = pd.DataFrame(data)
    df["revenue"] = df["quantity"] * df["price"]

    # Introduce missing values
    df.loc[np.random.choice(df.index, 15, replace=False), "quantity"] = np.nan
    df.loc[np.random.choice(df.index, 10, replace=False), "price"] = np.nan
    df.loc[np.random.choice(df.index, 20, replace=False), "region"] = np.nan

    return df


def generate_marketing():
    """Generate marketing data for correlation analysis."""
    n = 200
    data = {
        "campaign_id": [f"CMP{i:03d}" for i in range(n)],
        "ad_spend": np.random.uniform(100, 5000, n),
        "impressions": np.random.randint(1000, 100000, n),
        "clicks": np.random.randint(10, 5000, n),
        "email_sent": np.random.randint(100, 10000, n),
    }
    df = pd.DataFrame(data)

    # Create correlation: conversion rate depends on clicks and email
    df["conversions"] = (
        df["clicks"] * 0.05 +
        df["email_sent"] * 0.01 +
        np.random.normal(0, 50, n)
    ).clip(lower=0).astype(int)

    df["conversion_rate"] = df["conversions"] / df["impressions"]

    return df


def generate_transactions():
    """Generate transaction data with outliers."""
    n = 1000
    data = {
        "transaction_id": [f"TXN{i:05d}" for i in range(n)],
        "amount": np.random.lognormal(4, 1, n),
        "customer_id": [f"C{i:04d}" for i in np.random.randint(0, 200, n)],
        "timestamp": pd.date_range(start="2024-01-01", periods=n, freq="H"),
    }
    df = pd.DataFrame(data)

    # Add some outliers
    outlier_indices = np.random.choice(df.index, 10, replace=False)
    df.loc[outlier_indices, "amount"] = np.random.uniform(10000, 50000, len(outlier_indices))

    return df


def generate_ecommerce():
    """Generate e-commerce data for multi-metric analysis."""
    n = 500
    data = {
        "customer_id": [f"C{i:04d}" for i in range(n)],
        "orders": np.random.poisson(5, n),
        "total_revenue": np.random.lognormal(6, 1, n),
        "first_purchase_date": pd.date_range(start="2023-01-01", periods=n, freq="D"),
        "last_purchase_date": pd.date_range(start="2024-01-01", periods=n, freq="D"),
    }
    df = pd.DataFrame(data)
    df["avg_order_value"] = df["total_revenue"] / df["orders"].replace(0, 1)
    df["days_active"] = (df["last_purchase_date"] - df["first_purchase_date"]).dt.days

    return df


def generate_user_activity():
    """Generate user activity data for cohort analysis."""
    cohorts = pd.date_range(start="2023-01-01", end="2023-12-01", freq="MS")
    data = []

    for cohort_date in cohorts:
        n_users = np.random.randint(50, 150)
        for user_id in range(n_users):
            # Simulate retention decay
            for month in range(12):
                if np.random.random() < 0.7 ** month:  # Retention decay
                    activity_date = cohort_date + pd.DateOffset(months=month)
                    data.append({
                        "user_id": f"U{cohort_date.strftime('%Y%m')}_{user_id:04d}",
                        "cohort_date": cohort_date,
                        "activity_date": activity_date,
                        "actions": np.random.poisson(10),
                    })

    df = pd.DataFrame(data)
    return df


def generate_ab_test():
    """Generate A/B test data."""
    n = 1000
    data = {
        "user_id": [f"U{i:05d}" for i in range(n)],
        "group": np.random.choice(["control", "treatment"], n),
        "converted": np.random.binomial(1, 0.1, n),  # Base conversion rate
    }
    df = pd.DataFrame(data)

    # Treatment group has higher conversion rate
    treatment_mask = df["group"] == "treatment"
    df.loc[treatment_mask, "converted"] = np.random.binomial(1, 0.15, treatment_mask.sum())

    return df


def generate_unclear_schema():
    """Generate data with unclear column names."""
    n = 200
    data = {
        "dt": pd.date_range(start="2024-01-01", periods=n, freq="D"),
        "amt": np.random.uniform(100, 1000, n),
        "qty": np.random.randint(1, 20, n),
        "cat": np.random.choice(["A", "B", "C", "D"], n),
        "rgn": np.random.choice(["N", "S", "E", "W"], n),
        "sts": np.random.choice(["OK", "PND", "CNC"], n),
    }
    df = pd.DataFrame(data)
    return df


def main():
    """Generate all benchmark data files."""
    output_dir = Path("./benchmark_data")
    output_dir.mkdir(exist_ok=True)

    generators = {
        "sales_simple.csv": generate_sales_simple,
        "sales_timeseries.csv": generate_sales_timeseries,
        "customers.csv": generate_customers,
        "sales_missing.csv": generate_sales_missing,
        "marketing.csv": generate_marketing,
        "transactions.csv": generate_transactions,
        "ecommerce.csv": generate_ecommerce,
        "user_activity.csv": generate_user_activity,
        "ab_test.csv": generate_ab_test,
        "unclear_schema.csv": generate_unclear_schema,
    }

    for filename, generator in generators.items():
        print(f"Generating {filename}...")
        df = generator()
        output_path = output_dir / filename
        df.to_csv(output_path, index=False)
        print(f"  Saved to {output_path} ({len(df)} rows)")

    print("\nAll benchmark data files generated successfully!")


if __name__ == "__main__":
    main()
