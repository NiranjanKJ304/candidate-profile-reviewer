"""Request-scoped context variables shared by middleware and logging."""

from contextvars import ContextVar

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the current request ID, or ``None`` outside a request."""
    return request_id_ctx.get()
