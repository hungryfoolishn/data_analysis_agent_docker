# Development workflow

- Treat `docker-host` as the primary development and execution environment.
- Modify project code on the server under `/python/pragrams/data_analysis_agent`.
- Run project commands and verification on the server, using the Conda environment `analysis` (`conda activate analysis`) unless a task explicitly requires another environment.
- After server-side changes and verification, synchronize the changed source files back to the local workspace at `D:\all\pythoncode\data_analysis_agent` so the local repository contains the final code for Git operations.
- Preserve machine-specific and ignored data such as `.env`, `workspace/`, and `temp_uploads/` during synchronization unless the user explicitly requests otherwise.
- Review the local Git diff after synchronization. Do not commit or push unless the user explicitly asks.
