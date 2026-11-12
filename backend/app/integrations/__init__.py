"""External platform integrations (API clients only, no business logic).

Phase 0 intentionally contains no collectors. Each later phase adds one isolated
client module here (e.g. ``github.py``) that:

* talks only to official APIs or explicitly permitted data sources;
* returns typed Pydantic models carrying source URL and collection timestamp;
* distinguishes "unavailable" from zero values;
* handles timeouts, rate limits and API errors without raising into callers.

Which platforms are active is controlled by the ``ENABLED_PLATFORMS`` setting
(see :class:`app.core.config.Platform`).
"""
