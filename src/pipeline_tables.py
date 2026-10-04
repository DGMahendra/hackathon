"""src/pipeline_tables.py — the single definition of which tables are pipeline data.

PipelineState (Bronze/Silver/Gold) tables are the only tables an agent-proposed action
may ever target (INV-S8). Harness metadata tables are written only by the State Manager.
Must match the pipeline tables created by src/schema.sql.
"""

PIPELINE_TABLES = ("pipeline_bronze", "pipeline_silver", "pipeline_gold")
HARNESS_TABLES = ("ScenarioRun", "Attempt", "TraceEvent")
