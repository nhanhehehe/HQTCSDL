import base64
import hmac
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import WebError
from app.modules.auth import repository as auth_repo
from app.modules.auth.router import origin_guard
from app.modules.auth.security import token_hash, valid_csrf
from app.modules.auth.service import invalid_session, storage_error

MAX_EPOCH_SEC = 253_402_300_799
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=UTC)
_RFC3339_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|z|[+-]\d{2}:\d{2})$"
)
_UTC_MS_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})Z$")
_VI_TZ = timezone(timedelta(hours=7))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, val in pairs:
        if key in obj:
            raise ValueError(f"Duplicate JSON key: {key}")
        obj[key] = val
    return obj


def _reject_non_finite(token: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {token}")


def json_loads_no_duplicates(raw: str | bytes) -> Any:
    text_data = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    return json.loads(
        text_data,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )


@dataclass(frozen=True)
class ParsedTimestamp:
    whole_epoch_sec: int
    frac: Decimal
    exact_epoch: Decimal
    utc_iso: str


@dataclass(frozen=True)
class ResolvedRange:
    from_ts: ParsedTimestamp
    to_ts: ParsedTimestamp
    lo_epoch: int
    hi_epoch: int


def fail(
    status: int,
    code: str,
    message: str | None = None,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    raise WebError(status, code, message or code, details=details)


def serialize_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(tzinfo=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_ms_from_datetime(value: datetime) -> int:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    delta = aware - _EPOCH_UTC
    whole_sec = delta.days * 86400 + delta.seconds
    return whole_sec * 1000 + (delta.microseconds // 1000)


def format_decimal_clean(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _format_utc_iso_exact(utc_dt: datetime, frac_str: str) -> str:
    base = utc_dt.strftime("%Y-%m-%dT%H:%M:%S")
    trimmed = frac_str.rstrip("0")
    if len(trimmed) <= 3:
        frac_part = trimmed.ljust(3, "0")
    else:
        frac_part = trimmed
    return f"{base}.{frac_part}Z"


def parse_rfc3339_utc(raw: str) -> ParsedTimestamp:
    if type(raw) is not str:
        raise ValueError("Timestamp must be string")
    match = _RFC3339_RE.fullmatch(raw)
    if match is None:
        raise ValueError("Invalid RFC3339 timestamp")
    year_s, mon_s, day_s, hour_s, min_s, sec_s, frac_s, tz_s = match.groups()
    frac_digits = frac_s or ""
    if len(frac_digits) > 9:
        raise ValueError("Too many fractional digits")
    if tz_s in {"Z", "z"}:
        offset = timedelta(0)
    else:
        sign = 1 if tz_s[0] == "+" else -1
        tz_h = int(tz_s[1:3])
        tz_m = int(tz_s[4:6])
        if tz_h > 23 or tz_m > 59:
            raise ValueError("Invalid timezone offset")
        offset = timezone(sign * timedelta(hours=tz_h, minutes=tz_m)).utcoffset(None)
        assert offset is not None
    dt = datetime(
        int(year_s),
        int(mon_s),
        int(day_s),
        int(hour_s),
        int(min_s),
        int(sec_s),
        tzinfo=timezone(offset),
    )
    utc_dt = dt.astimezone(UTC)
    delta = utc_dt - _EPOCH_UTC
    whole_epoch_sec = delta.days * 86400 + delta.seconds
    if not (0 <= whole_epoch_sec <= MAX_EPOCH_SEC):
        raise ValueError("Timestamp out of supported UTC range")
    frac = Decimal(f"0.{frac_digits}") if frac_digits else Decimal(0)
    exact_epoch = Decimal(whole_epoch_sec) + frac
    return ParsedTimestamp(
        whole_epoch_sec=whole_epoch_sec,
        frac=frac,
        exact_epoch=exact_epoch,
        utc_iso=_format_utc_iso_exact(utc_dt, frac_digits),
    )


def datetime_to_parsed_utc(value: datetime) -> ParsedTimestamp:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    delta = aware - _EPOCH_UTC
    whole_epoch_sec = delta.days * 86400 + delta.seconds
    if not (0 <= whole_epoch_sec <= MAX_EPOCH_SEC):
        raise ValueError("Timestamp out of supported UTC range")
    ms = delta.microseconds // 1000
    frac = Decimal(ms) / Decimal(1000)
    frac_digits = f"{ms:03d}"
    return ParsedTimestamp(
        whole_epoch_sec=whole_epoch_sec,
        frac=frac,
        exact_epoch=Decimal(whole_epoch_sec) + frac,
        utc_iso=_format_utc_iso_exact(aware, frac_digits),
    )


def _subtract_24h(ts: ParsedTimestamp) -> ParsedTimestamp:
    new_whole = ts.whole_epoch_sec - 86400
    if new_whole < 0:
        raise WebError(400, "RANGE_INVALID", "Khoảng thời gian ngoài giới hạn hợp lệ")
    utc_dt = _EPOCH_UTC + timedelta(seconds=new_whole)
    frac_digits = ""
    if ts.frac > 0:
        frac_digits = format(ts.frac, "f").split(".", 1)[1]
    return ParsedTimestamp(
        whole_epoch_sec=new_whole,
        frac=ts.frac,
        exact_epoch=Decimal(new_whole) + ts.frac,
        utc_iso=_format_utc_iso_exact(utc_dt, frac_digits),
    )


def resolve_event_range(
    from_raw: str | None, to_raw: str | None, *, as_of_dt: datetime
) -> ResolvedRange:
    try:
        to_ts = (
            parse_rfc3339_utc(to_raw) if to_raw is not None else datetime_to_parsed_utc(as_of_dt)
        )
        from_ts = parse_rfc3339_utc(from_raw) if from_raw is not None else _subtract_24h(to_ts)
    except ValueError:
        raise WebError(400, "RANGE_INVALID", "Định dạng thời gian không hợp lệ") from None
    if from_ts.exact_epoch > to_ts.exact_epoch:
        raise WebError(400, "RANGE_INVALID", "Thời gian bắt đầu phải nhỏ hơn hoặc bằng kết thúc")
    lo_epoch = from_ts.whole_epoch_sec + (1 if from_ts.frac > 0 else 0)
    hi_epoch = to_ts.whole_epoch_sec
    return ResolvedRange(from_ts=from_ts, to_ts=to_ts, lo_epoch=lo_epoch, hi_epoch=hi_epoch)


def parse_utc_datetime_ms(utc_iso: str) -> datetime:
    if type(utc_iso) is not str:
        raise ValueError("Timestamp must be string")
    match = _UTC_MS_ISO_RE.fullmatch(utc_iso)
    if match is None:
        raise ValueError("Timestamp must use millisecond UTC Z format")
    y, m, d, hh, mm, ss, ms = (int(x) for x in match.groups())
    return datetime(y, m, d, hh, mm, ss, ms * 1000)


def format_utc_iso_to_vi(utc_iso: str) -> str:
    parsed = parse_rfc3339_utc(utc_iso)
    utc_dt = _EPOCH_UTC + timedelta(seconds=parsed.whole_epoch_sec)
    vi_dt = utc_dt.astimezone(_VI_TZ)
    frac_str = format(parsed.frac, "f").split(".", 1)[1] if parsed.frac > 0 else ""
    trimmed = frac_str.rstrip("0")
    frac_part = trimmed.ljust(3, "0") if len(trimmed) <= 3 else trimmed
    return f"{vi_dt.strftime('%Y-%m-%dT%H:%M:%S')}.{frac_part}+07:00"


def check_request_shape(request: Request, allowed: set[str], body: bool = False) -> None:
    keys = list(request.query_params.keys())
    if any(k not in allowed or len(request.query_params.getlist(k)) != 1 for k in keys):
        raise WebError(400, "INPUT_INVALID", "Tham số không hợp lệ")
    if not body and (
        request.headers.get("content-length", "0") != "0"
        or request.headers.get("transfer-encoding")
    ):
        raise WebError(400, "INPUT_INVALID", "Không nhận request body")


def require_json_mutation(request: Request) -> None:
    check_request_shape(request, set(), body=True)
    origin_guard(request)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise WebError(400, "INPUT_INVALID", "Yêu cầu phải là JSON")


def checked_value(function, value):
    try:
        return function(value)
    except (ValueError, TypeError):
        raise WebError(400, "INPUT_INVALID", "Dữ liệu không hợp lệ") from None


def parse_page_size(request: Request, *, default: int = 50, maximum: int = 200) -> int:
    raw = request.query_params.get("page_size", str(default))
    if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= maximum:
        raise WebError(400, "INPUT_INVALID", "page_size không hợp lệ")
    return int(raw)


@contextmanager
def web_session_transaction(
    database,
    settings,
    cookie: str | None,
    csrf: str | None = None,
    *,
    write: bool = False,
    require_csrf: bool | None = None,
    lock_session: bool | None = None,
    isolation: str | None = None,
    propagate_deadlock: bool = False,
):
    """Open an authenticated SQL transaction and yield (session, auth_context)."""
    digest = token_hash(cookie)
    if digest is None:
        raise invalid_session()
    if database.engine is None:
        raise storage_error()
    csrf_needed = write if require_csrf is None else require_csrf
    session_locked = write if lock_session is None else lock_session
    iso = isolation or ("READ COMMITTED" if write else "SNAPSHOT")
    try:
        with (
            database.engine.connect().execution_options(isolation_level=iso) as connection,
            connection.begin(),
            Session(bind=connection) as session,
        ):
            if write or session_locked or iso == "READ COMMITTED":
                session.execute(text("SET XACT_ABORT ON"))
            auth_row = auth_repo.session_guard(session, digest, lock=session_locked)
            if auth_row is None:
                raise invalid_session()
            if csrf_needed and (
                settings.csrf_secret is None
                or not valid_csrf(settings.csrf_secret.get_secret_value(), digest, csrf)
            ):
                fail(403, "CSRF_INVALID")
            yield session, {**dict(auth_row), "token_digest": digest}
    except SQLAlchemyError as exc:
        if propagate_deadlock and "1205" in str(getattr(exc, "orig", "")):
            raise
        raise storage_error() from None


@contextmanager
def web_transaction(
    database, settings, cookie: str | None, csrf: str | None = None, write: bool = False
):
    """Open a SNAPSHOT read or READ COMMITTED write transaction guarded by AdminSession."""
    with web_session_transaction(database, settings, cookie, csrf, write=write) as (session, _):
        yield session


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign_cursor(settings, payload: dict) -> str:
    secret = settings.cursor_secret
    if secret is None or not secret.get_secret_value():
        raise storage_error()
    key = secret.get_secret_value().encode()
    body = _b64u_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    prefix = "v1." + body
    return prefix + "." + _b64u_encode(hmac.digest(key, prefix.encode(), "sha256"))


def verify_cursor(settings, token: str) -> dict:
    secret = settings.cursor_secret
    if secret is None or not secret.get_secret_value():
        raise storage_error()
    key = secret.get_secret_value().encode()
    try:
        if len(token) > 4096:
            raise ValueError()
        v, body, signature = token.split(".")
        expected = _b64u_encode(hmac.digest(key, (v + "." + body).encode(), "sha256"))
        if v != "v1" or not hmac.compare_digest(signature, expected):
            raise ValueError()
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        if _b64u_encode(raw) != body:
            raise ValueError()
        decoded = json_loads_no_duplicates(raw)
        if not isinstance(decoded, dict):
            raise ValueError()
        return decoded
    except (ValueError, TypeError, UnicodeError):
        fail(400, "CURSOR_INVALID")
