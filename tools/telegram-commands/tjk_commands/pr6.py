"""Read an already obtained PR #6 analysis; does not fetch or start anything."""
from .models import Candidate


class PR6RaceProvider:
    """Attach an existing application's reader, never a second source collector.

    PR #6 has no verified race quote clock. An OK flag, download timestamp,
    scheduled race time, checksum or AGF must not override that limitation.
    """

    def __init__(self, snapshot_reader):
        self._reader = snapshot_reader

    def __call__(self, request):
        snapshot = self._reader(request)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("analysis"), dict):
            return Candidate(reason_codes=("SOURCE_UNAVAILABLE",))
        analysis = snapshot["analysis"]
        reasons = ["SOURCE_FRESHNESS_UNVERIFIED"]
        if analysis.get("modelVersion") != "market-no-agf-v2":
            reasons.append("MODEL_UNVERIFIED")
        if analysis.get("status") != "OK":
            reasons.append("UPSTREAM_PAS")
        # Deliberately do not copy raw runner/AGF fields, picks or free-form text.
        return Candidate(reason_codes=tuple(reasons))
