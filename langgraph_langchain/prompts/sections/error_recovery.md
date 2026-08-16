## Error recovery
- If `python_repl` output starts with `[ERROR]`, read the traceback carefully, fix the code, and retry.
- Do NOT call `finish_report` while any error is unresolved.
- If a chart fails, simplify the visualization and try again.
- If a timeout occurs, break the code into smaller steps.
- If you get a **SyntaxError: invalid decimal literal**, it usually means a dictionary key or variable name contains an operator like `>` or `<` (e.g. `产出>0记录数`). Fix by quoting the key as a string: `{"产出>0记录数": ("产出总数", lambda x: (x>0).sum())}` instead of `产出>0记录数=...`.
- Never use operators (`>`, `<`, `>=`, `<=`, `==`, `!=`) inside a bare identifier or dictionary key — always wrap such keys in quotes.
