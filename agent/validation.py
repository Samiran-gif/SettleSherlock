"""Stage 1 of the pipeline: ingest, validate and normalize raw evidence.

The agent is deliberately permissive. A record missing a timestamp or an
amount is still useful evidence, so instead of raising we record a
:class:`~agent.models.ValidationIssue` and keep going. Only records that are
not dict-like at all are rejected outright.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .models import (
    DataQuality,
    Severity,
    Transaction,
    TransactionStatus,
    ValidationIssue,
)

__all__ = [
    "CORE_FIELDS",
    "normalize_records",
    "parse_amount",
    "parse_timestamp",
    "normalize_status",
]


#: Fields used to score completeness of the submitted evidence.
CORE_FIELDS: tuple[str, ...] = (
    "transaction_id",
    "timestamp",
    "sender",
    "receiver",
    "amount",
    "currency",
    "status",
)

#: Alternative spellings accepted for each canonical field.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_id": ("transaction_id", "txn_id", "tx_id", "id", "transactionId"),
    "timestamp": ("timestamp", "time", "created_at", "datetime", "date", "ts"),
    "sender": ("sender", "from", "source", "payer", "sender_id", "from_account"),
    "receiver": ("receiver", "to", "destination", "payee", "receiver_id", "to_account", "merchant"),
    "amount": ("amount", "value", "amt", "transaction_amount"),
    "currency": ("currency", "currency_code", "ccy"),
    "transaction_type": ("transaction_type", "type", "txn_type", "kind"),
    "account_id": ("account_id", "account", "acct_id", "accountId"),
    "bank": ("bank", "bank_name", "institution"),
    "gateway": ("gateway", "psp", "processor", "payment_gateway"),
    "status": ("status", "state", "result", "outcome"),
    "reference_id": ("reference_id", "reference", "ref", "ref_id", "idempotency_key", "order_id"),
    "metadata": ("metadata", "meta", "extra", "attributes"),
}

#: Raw status strings mapped onto canonical states.
_STATUS_MAP: dict[str, TransactionStatus] = {
    # success family
    "success": TransactionStatus.SUCCESS,
    "successful": TransactionStatus.SUCCESS,
    "succeeded": TransactionStatus.SUCCESS,
    "completed": TransactionStatus.SUCCESS,
    "complete": TransactionStatus.SUCCESS,
    "settled": TransactionStatus.SUCCESS,
    "captured": TransactionStatus.SUCCESS,
    "paid": TransactionStatus.SUCCESS,
    "approved": TransactionStatus.SUCCESS,
    "ok": TransactionStatus.SUCCESS,
    "posted": TransactionStatus.SUCCESS,
    # failure family
    "failed": TransactionStatus.FAILED,
    "failure": TransactionStatus.FAILED,
    "fail": TransactionStatus.FAILED,
    "declined": TransactionStatus.FAILED,
    "rejected": TransactionStatus.FAILED,
    "denied": TransactionStatus.FAILED,
    "error": TransactionStatus.FAILED,
    "timeout": TransactionStatus.FAILED,
    "timed_out": TransactionStatus.FAILED,
    # in-flight family
    "pending": TransactionStatus.PENDING,
    "processing": TransactionStatus.PENDING,
    "in_progress": TransactionStatus.PENDING,
    "initiated": TransactionStatus.PENDING,
    "created": TransactionStatus.PENDING,
    "authorized": TransactionStatus.PENDING,
    "on_hold": TransactionStatus.PENDING,
    # unwind family
    "reversed": TransactionStatus.REVERSED,
    "reversal": TransactionStatus.REVERSED,
    "chargeback": TransactionStatus.REVERSED,
    "charged_back": TransactionStatus.REVERSED,
    "returned": TransactionStatus.REVERSED,
    "refunded": TransactionStatus.REFUNDED,
    "refund": TransactionStatus.REFUNDED,
    "cancelled": TransactionStatus.CANCELLED,
    "canceled": TransactionStatus.CANCELLED,
    "voided": TransactionStatus.CANCELLED,
    "void": TransactionStatus.CANCELLED,
}

_AMOUNT_CLEAN_RE = re.compile(r"[^0-9eE+\-.]")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


# --------------------------------------------------------------------------- #
# Field-level parsers
# --------------------------------------------------------------------------- #
def _pick(record: Mapping[str, Any], canonical: str) -> tuple[Any, str | None]:
    """Return ``(value, source_key)`` for ``canonical`` using alias lookup."""
    lowered = {str(k).lower(): k for k in record.keys()}
    for alias in _FIELD_ALIASES.get(canonical, (canonical,)):
        key = lowered.get(alias.lower())
        if key is not None:
            value = record[key]
            if value is not None and value != "":
                return value, str(key)
    return None, None


def parse_amount(value: Any) -> Decimal | None:
    """Parse a monetary amount into :class:`~decimal.Decimal`.

    Accepts numbers and strings such as ``"1,250.00"``, ``"$1250"`` or
    ``"USD 1250"``. Returns ``None`` when the value cannot be interpreted.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str):
        cleaned = _AMOUNT_CLEAN_RE.sub("", value.replace(",", "").strip())
        if cleaned in ("", "-", "+", ".", "-.", "+."):
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    return None


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a timestamp into a timezone-aware UTC :class:`datetime`.

    Supports ISO-8601 (with or without a trailing ``Z``), a few common
    ``strptime`` layouts, and epoch seconds/milliseconds. Naive datetimes are
    assumed to be UTC so that ordering stays well defined.
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        seconds = float(value)
        # Heuristic: values this large are milliseconds, not seconds.
        if abs(seconds) > 1e11:
            seconds /= 1000.0
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = None
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%d-%m-%Y %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            # Last resort: a bare epoch encoded as a string.
            try:
                return parse_timestamp(float(text))
            except ValueError:
                return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_status(value: Any) -> TransactionStatus:
    """Map a raw status string onto a :class:`TransactionStatus`."""
    if value is None:
        return TransactionStatus.UNKNOWN
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return _STATUS_MAP.get(key, TransactionStatus.UNKNOWN)


def _normalize_entity(value: Any) -> str | None:
    """Normalize an entity identifier (account, bank, gateway, …)."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in ("id", "account_id", "name", "identifier"):
            if key in value and value[key]:
                return str(value[key]).strip()
        return None
    text = str(value).strip()
    return text or None


# --------------------------------------------------------------------------- #
# Record-level normalization
# --------------------------------------------------------------------------- #
def _normalize_record(index: int, record: Any) -> tuple[Transaction | None, list[ValidationIssue]]:
    """Normalize one raw record into a :class:`Transaction`."""
    issues: list[ValidationIssue] = []

    if not isinstance(record, Mapping):
        issues.append(
            ValidationIssue(
                record_index=index,
                field="<record>",
                problem=f"record is {type(record).__name__}, expected an object/mapping",
                severity=Severity.HIGH,
            )
        )
        return None, issues

    raw = {str(k): v for k, v in record.items()}

    raw_txn_id, _ = _pick(raw, "transaction_id")
    synthetic_id = raw_txn_id is None
    txn_id = str(raw_txn_id).strip() if raw_txn_id is not None else f"UNIDENTIFIED-{index}"
    if synthetic_id:
        issues.append(
            ValidationIssue(
                record_index=index,
                field="transaction_id",
                problem="missing transaction_id; a synthetic placeholder id was assigned",
                severity=Severity.MEDIUM,
                transaction_id=txn_id,
            )
        )

    # -- timestamp -------------------------------------------------------- #
    raw_ts, _ = _pick(raw, "timestamp")
    timestamp = parse_timestamp(raw_ts)
    if raw_ts is None:
        issues.append(
            ValidationIssue(index, "timestamp", "missing timestamp", Severity.MEDIUM, txn_id)
        )
    elif timestamp is None:
        issues.append(
            ValidationIssue(
                index, "timestamp", f"unparseable timestamp {raw_ts!r}", Severity.MEDIUM, txn_id
            )
        )

    # -- amount ----------------------------------------------------------- #
    raw_amount, _ = _pick(raw, "amount")
    amount = parse_amount(raw_amount)
    if raw_amount is None:
        issues.append(ValidationIssue(index, "amount", "missing amount", Severity.MEDIUM, txn_id))
    elif amount is None:
        issues.append(
            ValidationIssue(
                index, "amount", f"unparseable amount {raw_amount!r}", Severity.MEDIUM, txn_id
            )
        )
    elif amount < 0:
        issues.append(
            ValidationIssue(
                index, "amount", "negative amount (credit/reversal?)", Severity.LOW, txn_id
            )
        )
    elif amount == 0:
        issues.append(ValidationIssue(index, "amount", "zero amount", Severity.LOW, txn_id))

    # -- currency --------------------------------------------------------- #
    raw_currency, _ = _pick(raw, "currency")
    currency = str(raw_currency).strip().upper() if raw_currency is not None else None
    if currency is None:
        issues.append(
            ValidationIssue(index, "currency", "missing currency", Severity.LOW, txn_id)
        )
    elif not _CURRENCY_RE.match(currency):
        issues.append(
            ValidationIssue(
                index,
                "currency",
                f"currency {currency!r} is not a 3-letter ISO-4217 code",
                Severity.LOW,
                txn_id,
            )
        )

    # -- status ----------------------------------------------------------- #
    raw_status, _ = _pick(raw, "status")
    status = normalize_status(raw_status)
    if raw_status is None:
        issues.append(ValidationIssue(index, "status", "missing status", Severity.MEDIUM, txn_id))
    elif status is TransactionStatus.UNKNOWN:
        issues.append(
            ValidationIssue(
                index, "status", f"unrecognized status {raw_status!r}", Severity.LOW, txn_id
            )
        )

    # -- parties ---------------------------------------------------------- #
    sender = _normalize_entity(_pick(raw, "sender")[0])
    receiver = _normalize_entity(_pick(raw, "receiver")[0])
    if sender is None:
        issues.append(ValidationIssue(index, "sender", "missing sender", Severity.MEDIUM, txn_id))
    if receiver is None:
        issues.append(
            ValidationIssue(index, "receiver", "missing receiver", Severity.MEDIUM, txn_id)
        )
    if sender is not None and sender == receiver:
        issues.append(
            ValidationIssue(
                index, "sender", "sender and receiver are identical", Severity.MEDIUM, txn_id
            )
        )

    raw_metadata, _ = _pick(raw, "metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    if raw_metadata is not None and not isinstance(raw_metadata, Mapping):
        metadata = {"value": raw_metadata}
        issues.append(
            ValidationIssue(
                index, "metadata", "metadata was not an object; wrapped it", Severity.LOW, txn_id
            )
        )

    txn = Transaction(
        index=index,
        transaction_id=txn_id,
        timestamp=timestamp,
        sender=sender,
        receiver=receiver,
        amount=amount,
        currency=currency,
        transaction_type=_normalize_entity(_pick(raw, "transaction_type")[0]),
        account_id=_normalize_entity(_pick(raw, "account_id")[0]),
        bank=_normalize_entity(_pick(raw, "bank")[0]),
        gateway=_normalize_entity(_pick(raw, "gateway")[0]),
        status=status,
        raw_status=str(raw_status) if raw_status is not None else None,
        reference_id=_normalize_entity(_pick(raw, "reference_id")[0]),
        metadata=metadata,
        raw=raw,
        issues=issues,
        synthetic_id=synthetic_id,
    )
    return txn, issues


def _extract_records(evidence: Any) -> tuple[list[Any], list[ValidationIssue]]:
    """Pull a list of raw records out of a variety of accepted payload shapes."""
    issues: list[ValidationIssue] = []

    if evidence is None:
        return [], issues
    if isinstance(evidence, Mapping):
        for key in ("transactions", "records", "evidence", "data", "items"):
            if key in evidence:
                nested = evidence[key]
                if isinstance(nested, Iterable) and not isinstance(nested, (str, bytes, Mapping)):
                    return list(nested), issues
                if isinstance(nested, Mapping):
                    return [nested], issues
        # A single bare transaction object.
        return [evidence], issues
    if isinstance(evidence, (str, bytes)):
        issues.append(
            ValidationIssue(
                record_index=-1,
                field="<payload>",
                problem="evidence payload was a string; expected a list of records",
                severity=Severity.HIGH,
            )
        )
        return [], issues
    if isinstance(evidence, Iterable):
        return list(evidence), issues

    issues.append(
        ValidationIssue(
            record_index=-1,
            field="<payload>",
            problem=f"unsupported evidence payload of type {type(evidence).__name__}",
            severity=Severity.HIGH,
        )
    )
    return [], issues


def _count_conflicts(transactions: list[Transaction]) -> int:
    """Count records that reuse a transaction_id with differing content."""
    by_id: dict[str, list[Transaction]] = {}
    for txn in transactions:
        if not txn.synthetic_id:
            by_id.setdefault(txn.transaction_id, []).append(txn)

    conflicts = 0
    for group in by_id.values():
        if len(group) < 2:
            continue
        signatures = {
            (t.amount, t.currency, t.status, t.sender, t.receiver, t.timestamp) for t in group
        }
        if len(signatures) > 1:
            conflicts += len(group)
    return conflicts


def normalize_records(evidence: Any) -> tuple[list[Transaction], DataQuality]:
    """Normalize an evidence payload into transactions plus a quality report.

    ``evidence`` may be a list of records, a dict wrapping such a list under
    ``transactions``/``records``/``evidence``/``data``/``items``, or a single
    transaction object.
    """
    raw_records, payload_issues = _extract_records(evidence)

    transactions: list[Transaction] = []
    all_issues: list[ValidationIssue] = list(payload_issues)
    rejected = 0

    for index, record in enumerate(raw_records):
        txn, issues = _normalize_record(index, record)
        all_issues.extend(issues)
        if txn is None:
            rejected += 1
        else:
            transactions.append(txn)

    # Field completeness across the core fields, averaged over parsed records.
    missing_counts: dict[str, int] = {f: 0 for f in CORE_FIELDS}
    present_total = 0
    for txn in transactions:
        values = {
            "transaction_id": None if txn.synthetic_id else txn.transaction_id,
            "timestamp": txn.timestamp,
            "sender": txn.sender,
            "receiver": txn.receiver,
            "amount": txn.amount,
            "currency": txn.currency,
            "status": None if txn.status is TransactionStatus.UNKNOWN else txn.status,
        }
        for name, value in values.items():
            if value is None:
                missing_counts[name] += 1
            else:
                present_total += 1

    denominator = len(transactions) * len(CORE_FIELDS)
    completeness = (present_total / denominator) if denominator else 0.0

    quality = DataQuality(
        records_submitted=len(raw_records),
        records_parsed=len(transactions),
        records_rejected=rejected,
        field_completeness=completeness,
        missing_fields={k: v for k, v in missing_counts.items() if v},
        issues=all_issues,
        conflicting_record_count=_count_conflicts(transactions),
    )
    return transactions, quality
