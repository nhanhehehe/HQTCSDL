from datetime import UTC, datetime


def utc_now() -> datetime:
    """Backend clock at SQL DATETIME2(3) precision; injectable by services in tests."""
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def sql_utc(value: datetime) -> datetime:
    """Attach UTC only at the boundary reading UTC DATETIME2 columns from SQL."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
