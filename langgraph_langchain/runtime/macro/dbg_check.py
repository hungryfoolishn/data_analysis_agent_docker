from langgraph_langchain.runtime.macro.templates import get_template, check_data_sufficiency
from langgraph_langchain.runtime.macro.models import MacroIndicator

t = get_template("employment")
print("required:", t.required_concepts)
print("any_of:", t.any_of_concepts)
print("optional:", t.optional_concepts)

# Simulate the test
from tests.test_macro_templates import _resolved_map
resolved = _resolved_map([
    ("nonfarm_payrolls", "us", "bls_employment", "bls", "monthly", 3),
])
print("resolved keys:", list(resolved.keys()))
print("resolved concepts:", {item.indicator.concept for item in resolved.values()})
suff, missing = check_data_sufficiency(t, resolved)
print("sufficiency:", suff, "missing:", missing)
