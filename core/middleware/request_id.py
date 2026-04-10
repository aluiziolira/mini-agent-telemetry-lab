"""Request ID middleware for correlation tracking."""

import uuid
from threading import local
from typing import Callable

from django.http import HttpRequest, HttpResponse

_thread_locals = local()


class RequestIdMiddleware:
    """Middleware to propagate X-Request-ID header for request correlation."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        _thread_locals.request_id = request_id

        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response


def get_current_request_id() -> str | None:
    """Get the current request ID from thread-local storage."""
    return getattr(_thread_locals, "request_id", None)
