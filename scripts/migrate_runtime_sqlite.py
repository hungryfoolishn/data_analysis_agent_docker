#!/usr/bin/env python
from pathlib import Path
import argparse
from langgraph_langchain.runtime.sqlite_store import SQLiteMetadataStore

parser=argparse.ArgumentParser()
parser.add_argument("--workspace",default="workspace")
parser.add_argument("--database",default="workspace/.analysis_metadata.sqlite")
args=parser.parse_args()
count=SQLiteMetadataStore(Path(args.database)).migrate_workspace(Path(args.workspace))
print(f"migrated {count} run snapshot(s)")
