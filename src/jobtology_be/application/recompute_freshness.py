"""Stable manual-analysis context identity, separate from HTTP idempotency."""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime

from jobtology_be.infrastructure.persistence.contracts import JsonValue


def analysis_context_fingerprint(context: Mapping[str, JsonValue]) -> str:
    # Flexible calendars are regenerated from the current hour. Within a reference
    # day, identical inputs and weekly capacities are one semantic request; exact
    # microseconds must not turn repeated button clicks into independent CPU work.
    stable = dict(context)
    reference = stable.pop('reference_at', None)
    stable.pop('planning_started_at', None)
    try:
        stable['reference_day'] = (datetime.fromisoformat(reference).astimezone(UTC).date().isoformat()
                                   if isinstance(reference, str) else None)
    except ValueError:
        stable['reference_day'] = reference
    if stable.get('calendar_policy') == 'FLEXIBLE_WEEKLY_V1':
        calendar = stable.pop('calendar', ())
        capacities = Counter(slot['capacity_week_key'] for slot in calendar
                             if isinstance(slot, Mapping) and isinstance(slot.get('capacity_week_key'), str)) if isinstance(calendar, (tuple, list)) else Counter()
        stable['calendar_week_capacities'] = dict(sorted(capacities.items()))
    # Database row order and set iteration order do not change competency inputs.
    capabilities = stable.get('capabilities')
    if isinstance(capabilities, (list, tuple)):
        stable['capabilities'] = sorted(capabilities, key=lambda item: json.dumps(item, sort_keys=True))
    completeness = stable.get('completeness')
    if isinstance(completeness, Mapping):
        stable['completeness'] = {**completeness, 'experience_complete_entity_ids': sorted(
            completeness.get('experience_complete_entity_ids', ()),
        )}
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()
