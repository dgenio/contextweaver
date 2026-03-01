"""A2A adapter demo (placeholder).

This example will demonstrate converting A2A agent cards and task results
into contextweaver-native types once the A2A adapter is fully implemented.
"""

from __future__ import annotations

A2A_AGENT_CARD = {
    "name": "DataAgent",
    "description": "Retrieves and processes structured data from databases.",
    "skills": [
        {"id": "db_query", "name": "Database Query"},
        {"id": "data_transform", "name": "Data Transform"},
    ],
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text", "data"],
}

A2A_TASK_RESULT = {
    "status": {"state": "completed"},
    "artifacts": [
        {"name": "result", "parts": [{"type": "text", "text": "Query returned 42 rows."}]}
    ],
}


def main() -> None:
    print("A2A adapter demo — implementation pending.")
    print(f"Example agent: {A2A_AGENT_CARD['name']!r}")
    print(f"Example result: {A2A_TASK_RESULT['status']['state']!r}")


if __name__ == "__main__":
    main()
