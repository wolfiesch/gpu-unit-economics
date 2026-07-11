"""One-shot durable alert-delivery queue worker."""

from __future__ import annotations

import json

from .intelligence_store import IntelligenceStore
from .notifications import deliver_pending


def run_once(store: IntelligenceStore | None = None) -> dict:
    return deliver_pending(store or IntelligenceStore())


def main() -> int:
    print(json.dumps(run_once(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

