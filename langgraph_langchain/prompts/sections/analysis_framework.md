## Analysis framework you must follow
After `eda_profile`, your custom analysis should usually progress through these stages when relevant:
1. Data quality check
   - verify missing values, duplicates, suspicious outliers, impossible values, inconsistent categories
2. Metric and dimension definition
   - identify target metrics, additive metrics, ratios, and key grouping dimensions
3. Segment comparison
   - if categorical dimensions exist, compare groups with groupby / pivot / ranking
4. Trend analysis
   - if a time field exists, analyze trend, recent changes, volatility, and unusual periods
5. Anomaly explanation
   - when anomalies appear, discuss whether they are more likely due to data problems, business events, or small-sample instability
6. Driver / contribution analysis
   - when a metric changes materially, assess which groups, products, regions, channels, or periods contribute most
   - prefer using `decompose_metric_change` before making driver claims
   - label driver confidence with `assess_evidence_level` and separate supported observations from hypotheses
7. Final synthesis
   - summarize what is known, what is only suggestive, and what needs further validation
   - recommendations must be scaled to evidence strength; weak evidence should lead to validation / observe suggestions instead of strong actions
