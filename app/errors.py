"""One mapping from a conversion failure to the HTTP answer of `POST /api/forms/convert`."""

import logging

import httpx
from fastapi import HTTPException, status

from app.llm.client import MissingKeyError

log = logging.getLogger(__name__)


def conversion_error(error: Exception) -> HTTPException:
    """The HTTPException for an exception raised by `pipeline.convert` (unknown ones: 500, logged)."""
    match error:
        case HTTPException():
            return error
        case httpx.HTTPStatusError():
            return HTTPException(status.HTTP_502_BAD_GATEWAY, "Model językowy zwrócił błąd.")
        # before TransportError: a timeout is one, but the model is up, just too slow
        case httpx.TimeoutException() | TimeoutError():
            return HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "Model nie odpowiedział na czas. Spróbuj ponownie.")
        case httpx.TransportError() | ConnectionError():
            log.warning("model unreachable: %s", type(error).__name__)  # the access log alone shows only the 503
            return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model językowy jest niedostępny.")
        # before ValueError: a missing key is the server's setup, not a bad form
        case MissingKeyError():
            return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                 "Brak klucza EDENAI_API_KEY: serwer nie ma dostępu do modeli.")
        case ValueError():
            return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Nie udało się odczytać formularza: {error}")
    log.exception("conversion failed", exc_info=error)
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Błąd wewnętrzny serwera.")
