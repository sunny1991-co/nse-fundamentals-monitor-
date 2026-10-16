"""Print local readiness without requiring an active broker account."""
import json
from pathlib import Path
from research_monitor.readiness import snapshot
from research_monitor.schedule import atomic_status


def main():
    root = Path(__file__).resolve().parent
    result = snapshot(root)
    atomic_status(root/'runtime'/'readiness.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
