from __future__ import annotations

from typing import Any


def json_error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload = {"ok": False, "error": {"code": code, "message": message}}
    payload.update(extra)
    return payload


class StructuredPayloadError(Exception):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return json_error_payload(self.code, self.message, **self.extra)


class StructuredRuntimeError(StructuredPayloadError):
    pass


__all__ = ["StructuredPayloadError", "StructuredRuntimeError", "json_error_payload"]
