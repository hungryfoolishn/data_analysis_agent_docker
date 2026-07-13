## R&D Efficiency Analysis Guidelines
When analyzing R&D management data, apply these domain-specific practices:

**Metric validation**:
- Check if metrics match standard R&D definitions (use `rd_efficiency_domain.get_metric_definition()`)
- Validate calculation methods (use `rd_metric_library.validate_*` functions)
- Apply appropriate aggregation (median for cycle time, sum for velocity)
- Consider typical ranges and flag outliers

**Common anti-patterns to avoid**:
- Comparing velocity across teams (velocity is team-specific)
- Using test coverage alone as quality metric (must correlate with defect rate)
- Blaming individuals (focus on process and system issues)
- Ignoring work item type/size when analyzing cycle time

**Template matching**:
- Check if the analysis matches a standard template (use `rd_templates.suggest_template()`)
- Follow template analysis steps if applicable
- Include template-recommended metrics and dimensions

**Data quality checks**:
- Sprint data: check for zero/negative story points, date consistency
- PR data: check for very large PRs (>1000 lines), negative review times
- Deployment data: check for high change failure rate (>30%)

**Domain-specific validators**:
- Use `rd_validators.validate_rd_metric_definition()` for metric definitions
- Use `rd_validators.validate_rd_finding()` for findings
- Use `rd_validators.validate_rd_analysis_completeness()` for overall analysis

**Interpretation helpers**:
- Use `rd_metric_library.interpret_*` functions for human-readable interpretations
- Reference metric caveats from domain knowledge
- Suggest related metrics that should be analyzed together
