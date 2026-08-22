"""Public monthly funding snapshot and private Ko-fi payment webhook."""

from __future__ import annotations

import hmac
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response

from tara_web.api.problem_details import problem
from tara_web.api.schemas import MonthlyFundingSnapshot
from tara_web.db.repositories.funding import FundingRepository

router = APIRouter(prefix="/funding", tags=["funding"])
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
# Ko-fi currently emits ``Tip`` for one-off support. ``Donation`` is retained
# for compatibility with older deliveries and the dashboard test sender.
_COUNTED_TYPES = {"Tip", "Donation", "Subscription"}
_MAX_BODY_BYTES = 65_536


@router.get("/monthly", response_model=MonthlyFundingSnapshot)
def monthly_funding(request: Request) -> MonthlyFundingSnapshot:
    config = request.app.state.runtime_config.web.kofi
    timezone = ZoneInfo(config.timezone)
    local_now = datetime.now(UTC).astimezone(timezone)
    local_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if local_start.month == 12:
        local_end = local_start.replace(year=local_start.year + 1, month=1)
    else:
        local_end = local_start.replace(month=local_start.month + 1)
    totals = FundingRepository(request.app.state.database).monthly_totals(
        start_utc=local_start.astimezone(UTC).isoformat(),
        end_utc=local_end.astimezone(UTC).isoformat(),
    )
    return MonthlyFundingSnapshot(
        enabled=config.enabled,
        month=f"{local_start.year:04d}-{local_start.month:02d}",
        timezone=config.timezone,
        currency="EUR",
        donations_micro_eur=totals.donations_micros,
        estimated_consumption_micro_eur=(
            totals.estimated_consumption_micro_eur
        ),
        estimate_partial=totals.estimate_partial,
        monthly_goal_micro_eur=config.monthly_goal_micro_eur,
        kofi_page_url=str(config.page_url) if config.page_url else None,
    )


@router.post("/kofi/webhook", status_code=200, response_class=Response)
async def kofi_webhook(request: Request) -> Response:
    runtime = request.app.state.runtime_config
    token = runtime.kofi_verification_token
    if not runtime.web.kofi.enabled or token is None:
        return problem(request, 404)
    try:
        payload = await _payload(request)
        supplied_token = payload.get("verification_token")
        if not isinstance(supplied_token, str) or not hmac.compare_digest(
            supplied_token, token.get_secret_value()
        ):
            return problem(request, 403)
        event = _validated_event(payload)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return problem(request, 400)
    FundingRepository(request.app.state.database).record_kofi_event(
        **event,
        received_at=datetime.now(UTC).isoformat(),
    )
    # Ko-fi retries non-200 responses with the same message_id. Duplicates are
    # therefore acknowledged normally and remain a single database row.
    return Response(status_code=200)


async def _payload(request: Request) -> dict[str, object]:
    length = request.headers.get("content-length")
    if length is None or not length.isdecimal() or int(length) > _MAX_BODY_BYTES:
        raise ValueError("invalid webhook body length")
    body = await request.body()
    if not body or len(body) > _MAX_BODY_BYTES:
        raise ValueError("invalid webhook body")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type == "application/x-www-form-urlencoded":
        form = parse_qs(
            body.decode("utf-8"),
            strict_parsing=True,
            max_num_fields=4,
        )
        values = form.get("data")
        if values is None or len(values) != 1:
            raise ValueError("missing webhook data")
        document = json.loads(values[0])
    elif content_type == "application/json":
        document = json.loads(body)
    else:
        raise ValueError("unsupported webhook content type")
    if not isinstance(document, dict):
        raise ValueError("invalid webhook document")
    return document


def _validated_event(payload: dict[str, object]) -> dict[str, object]:
    message_id = payload.get("message_id")
    event_type = payload.get("type")
    currency = payload.get("currency")
    amount = payload.get("amount")
    timestamp = payload.get("timestamp")
    is_test_transaction = payload.get("is_test_transaction", False)
    if not isinstance(message_id, str) or not _MESSAGE_ID.fullmatch(message_id):
        raise ValueError("invalid message id")
    if not isinstance(event_type, str) or not 1 <= len(event_type) <= 32:
        raise ValueError("invalid event type")
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
        raise ValueError("invalid currency")
    if not isinstance(amount, str) or len(amount) > 32:
        raise ValueError("invalid amount")
    try:
        decimal_amount = Decimal(amount)
    except InvalidOperation as exc:
        raise ValueError("invalid amount") from exc
    micros = decimal_amount * Decimal(1_000_000)
    if (
        not decimal_amount.is_finite()
        or decimal_amount < 0
        or micros != micros.to_integral_value()
        or micros > 10**15
    ):
        raise ValueError("invalid amount")
    if not isinstance(timestamp, str) or len(timestamp) > 64:
        raise ValueError("invalid timestamp")
    if not isinstance(is_test_transaction, bool):
        raise ValueError("invalid test transaction marker")
    try:
        occurred = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        occurred_at = occurred.astimezone(UTC).isoformat()
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    # Persist non-funding events for idempotency, but force their amount to zero
    # so a future query can never accidentally count shop revenue as donations.
    return {
        "message_id": message_id,
        "event_type": event_type,
        "amount_micros": (
            int(micros)
            if event_type in _COUNTED_TYPES and not is_test_transaction
            else 0
        ),
        "currency": currency,
        "occurred_at": occurred_at,
    }
