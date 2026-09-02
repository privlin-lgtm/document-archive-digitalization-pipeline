"""Parse entity normalized_value into typed search columns."""

from datetime import date

from extraction.entities import ExtractedEntity


def parse_date_value(normalized_value: str | None) -> date | None:
    if not normalized_value:
        return None
    try:
        return date.fromisoformat(normalized_value.strip()[:10])
    except ValueError:
        return None


def parse_amount_value(normalized_value: str | None) -> tuple[float | None, str | None]:
    if not normalized_value:
        return None, None
    parts = normalized_value.split(maxsplit=1)
    if len(parts) != 2:
        return None, None
    currency, amount_str = parts
    try:
        return float(amount_str), currency
    except ValueError:
        return None, None


def parse_date_from_entity(entity: ExtractedEntity) -> date | None:
    return parse_date_value(entity.normalized_value)


def parse_amount_from_entity(entity: ExtractedEntity) -> tuple[float | None, str | None]:
    return parse_amount_value(entity.normalized_value)
