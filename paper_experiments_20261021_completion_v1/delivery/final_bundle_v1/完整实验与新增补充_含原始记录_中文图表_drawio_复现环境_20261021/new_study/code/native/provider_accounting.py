"""Per-attempt safe HTTP/SDK accounting. No network, SDK import, secrets or file writes.

Attach request_hook/response_hook to the existing synchronous httpx client. begin()
must precede exactly one no-retry SDK call. Persist finish_success()/finish_error()
before continuing; begin() refuses all later calls after missing/invalid usage or error.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import re

CORE = ("prompt_tokens", "completion_tokens", "total_tokens")
ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,512}\Z")
SECRET_PATTERN = re.compile(r"(?:sk-|nap_)[A-Za-z0-9_-]{16,}")
ERROR_ID_PATTERN = re.compile(r'''(?i)(?<![A-Za-z0-9_])request(?:[_ -]?id)(?![A-Za-z0-9_])\s*["']?\s*[:=]\s*["']?([A-Za-z0-9][A-Za-z0-9_-]{7,127})(?![A-Za-z0-9_-])''')


class AccountingStop(RuntimeError):
    """Safe reason code only; never include exception/header/body text."""


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_id(value):
    if value is None:
        return None
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value) or SECRET_PATTERN.search(value):
        raise AccountingStop("UNSAFE_ID_VALUE_NOT_RETAINED")
    return value


def safe_id_headers(headers):
    result = {}
    # Read names first, then values of approved request/trace-ID headers only.
    for name in headers.keys():
        lower = name.lower()
        compact = re.sub(r"[^a-z]", "", lower)
        if not ("requestid" in compact or "traceid" in compact):
            continue
        if any(word in compact for word in ("authorization", "cookie", "key", "token", "secret", "credential", "session")):
            continue
        values = headers.get_list(name) if hasattr(headers, "get_list") else [headers[name]]
        result[lower] = [safe_id(value) for value in values]
    return result


def usage_valid(value):
    return (isinstance(value, dict)
            and all(type(value.get(key)) is int and value[key] >= 0 for key in CORE)
            and value["total_tokens"] == value["prompt_tokens"] + value["completion_tokens"])


def json_copy(value):
    # Preserve null/key presence; reject non-JSON/nonfinite values without coercion.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def error_projection(body):
    result = {"body_is_object": isinstance(body, dict), "explicit_ID_fields": [], "message_explicit_request_IDs": []}
    if not isinstance(body, dict):
        return result
    for root, obj in (("", body), ("/error", body.get("error"))):
        if not isinstance(obj, dict):
            continue
        for key in ("code", "type"):
            value = obj.get(key)
            result[root + "/" + key + "_present"] = key in obj
            # Codes/types only, never a free-form error message.
            if value is None or type(value) is int or (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) and not SECRET_PATTERN.search(value)):
                result[root + "/" + key] = value
        for key in ("id", "request_id", "requestId", "requestID"):
            if key in obj and obj[key] is not None:
                result["explicit_ID_fields"].append({"path": root + "/" + key, "value": safe_id(obj[key])})
        if isinstance(obj.get("message"), str):
            result["message_explicit_request_IDs"].extend(safe_id(value) for value in ERROR_ID_PATTERN.findall(obj["message"]))
    result["message_explicit_request_IDs"] = list(dict.fromkeys(result["message_explicit_request_IDs"]))
    return result


class ProviderAccounting:
    """One active synchronous attempt per instance; use a separate instance per worker."""
    def __init__(self):
        self.halted = False
        self.halt_reason = None
        self._active = None
        self._http_response = None
        self.records = []

    def _stop(self, code):
        self.halted = True
        self.halt_reason = code
        raise AccountingStop(code)

    def begin(self, client_request_id, requested_model):
        if self.halted:
            raise AccountingStop("ACCOUNTING_HALTED_NO_NEW_CALLS")
        if self._active is not None:
            self._stop("UNFINISHED_ATTEMPT_NO_NEW_CALLS")
        safe_id(client_request_id)
        if any(row["client_request_id"] == client_request_id for row in self.records):
            self._stop("REUSED_ATTEMPT_ID")
        self._active = {"client_request_id": client_request_id, "requested_model": requested_model,
                        "started_utc": now(), "http_dispatch_count": 0, "http_response_count": 0,
                        "http_status": None, "response_ID_headers": {}, "usage_raw_present": False,
                        "usage_raw": None, "usage_complete": False}
        self._http_response = None

    def request_hook(self, request):
        if self.halted or self._active is None:
            self._stop("HTTP_DISPATCH_WITHOUT_APPROVED_ATTEMPT")
        actual = request.headers.get("x-client-request-id")
        if actual != self._active["client_request_id"]:
            self._stop("CLIENT_REQUEST_ID_HEADER_MISMATCH")
        self._active["actual_X_Client_Request_Id"] = safe_id(actual)
        self._active["http_dispatch_count"] += 1
        if self._active["http_dispatch_count"] != 1:
            self._stop("SECOND_HTTP_DISPATCH_BLOCKED")

    def response_hook(self, response):
        if self._active is None:
            self._stop("UNASSOCIATED_HTTP_RESPONSE")
        if response.request.headers.get("x-client-request-id") != self._active["client_request_id"]:
            self._stop("RESPONSE_CLIENT_ID_MISMATCH")
        self._active["http_response_count"] += 1
        if self._active["http_response_count"] != 1:
            self._stop("MULTIPLE_HTTP_RESPONSES")
        self._active["http_status"] = response.status_code
        self._active["response_ID_headers"] = safe_id_headers(response.headers)
        self._http_response = response
        # Do not read body here: preserve normal SDK transport/read timing.

    def _body_usage(self, response):
        if response is None:
            return None
        try:
            body = response.json()  # after SDK completion/error; body is already read by SDK
        except Exception as exc:
            self._active["HTTP_body_decode_error_type"] = type(exc).__name__
            return None
        if isinstance(body, dict):
            self._active["usage_raw_present"] = "usage" in body
            self._active["usage_raw"] = json_copy(body.get("usage"))
        return body

    def _finish(self, code, halt):
        row = self._active
        row.update(status=code, finished_utc=now(), new_calls_permitted=not halt,
                   ID_semantics="client header / HTTP response header IDs / SDK attribute / body ID are separate named fields; billing mapping needs independent exact evidence")
        if halt:
            self.halted = True
            self.halt_reason = code
        self.records.append(json_copy(row))
        self._active = None
        self._http_response = None
        return copy.deepcopy(self.records[-1])

    def finish_success(self, sdk_response):
        if self._active is None:
            self._stop("NO_ACTIVE_ATTEMPT")
        self._body_usage(self._http_response)
        self._active["SDK_request_id"] = safe_id(getattr(sdk_response, "_request_id", None))
        self._active["ChatCompletion_id"] = safe_id(getattr(sdk_response, "id", None))
        usage = getattr(sdk_response, "usage", None)
        sdk_usage = usage.model_dump() if usage is not None else None
        self._active["SDK_usage_present"] = usage is not None
        self._active["SDK_usage_model_dump"] = json_copy(sdk_usage)
        raw = self._active["usage_raw"]
        complete = (self._active["http_dispatch_count"] == self._active["http_response_count"] == 1
                    and self._active["http_status"] == 200 and usage_valid(raw) and usage_valid(sdk_usage)
                    and all(raw[key] == sdk_usage[key] for key in CORE))
        self._active["usage_complete"] = complete
        return self._finish("SUCCESS_COMPLETE_USAGE" if complete else "STOP_MISSING_INVALID_OR_INCONSISTENT_USAGE", not complete)

    def finish_error(self, error):
        if self._active is None:
            self._stop("NO_ACTIVE_ATTEMPT")
        response = getattr(error, "response", None)
        if response is None:
            response = self._http_response
        if response is not None:
            self._active["http_status"] = response.status_code
            self._active["response_ID_headers"] = safe_id_headers(response.headers)
        body = self._body_usage(response)
        self._active["exception_class"] = type(error).__name__
        self._active["APIStatusError_request_id"] = safe_id(getattr(error, "request_id", None))
        self._active["safe_error_body_projection"] = error_projection(body)
        self._active["transport_outcome"] = "HTTP_RESPONSE_OBSERVED" if response is not None else "NETWORK_FAILURE_SERVER_RECEIPT_UNCONFIRMED"
        self._active["usage_complete"] = usage_valid(self._active["usage_raw"])
        # Errors are retained for review; this module never performs automatic retries.
        return self._finish("STOP_ERROR_NO_AUTOMATIC_NEW_CALLS", True)
