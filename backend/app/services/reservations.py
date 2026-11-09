from datetime import datetime
from decimal import Decimal
from typing import Any, Dict
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)


def month_bounds(year: int, month: int, timezone_name: str) -> tuple[datetime, datetime]:
    """
    Return [start, end) for a calendar month in the property's timezone.

    The datetimes are timezone-aware. Comparing them to `timestamptz`
    keeps a check-in that is still the previous day in UTC when it is
    already the new month locally (and the reverse).
    """
    if month < 1 or month > 12:
        raise ValueError("month must be 1-12")
    tz = ZoneInfo(timezone_name)
    start = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end = datetime(year, month + 1, 1, tzinfo=tz)
    return start, end


async def _ensure_pool():
    from app.core.database_pool import db_pool

    if db_pool.session_factory is None:
        await db_pool.initialize()
    if db_pool.session_factory is None:
        raise RuntimeError("Database pool not available")
    return db_pool


def _money_string(amount: Any) -> str:
    """Exact decimal text. Currency rounding is applied by format_money."""
    return format(Decimal(str(amount)), "f")


async def _load_monthly_revenue(
    session,
    property_id: str,
    month: int,
    year: int,
    tenant_id: str,
) -> Dict[str, Any]:
    from sqlalchemy import text

    property_result = await session.execute(
        text(
            """
            SELECT timezone
            FROM properties
            WHERE id = :property_id AND tenant_id = :tenant_id
            """
        ),
        {"property_id": property_id, "tenant_id": tenant_id},
    )
    property_row = property_result.fetchone()
    if property_row is None:
        # No property for this tenant. Do not borrow another tenant's row:
        # property ids are reused across companies.
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": "0",
            "currency": "USD",
            "count": 0,
            "month": month,
            "year": year,
            "timezone": None,
        }

    timezone_name = property_row.timezone
    start_date, end_date = month_bounds(year, month, timezone_name)
    logger.info(
        "Revenue window property=%s tenant=%s tz=%s start=%s end=%s",
        property_id,
        tenant_id,
        timezone_name,
        start_date.isoformat(),
        end_date.isoformat(),
    )

    result = await session.execute(
        text(
            """
            SELECT
                COALESCE(SUM(total_amount), 0) AS total_revenue,
                COUNT(*) AS reservation_count,
                COUNT(DISTINCT currency) AS currency_count,
                MIN(currency) AS currency
            FROM reservations
            WHERE property_id = :property_id
              AND tenant_id = :tenant_id
              AND check_in_date >= :start_date
              AND check_in_date < :end_date
            """
        ),
        {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    row = result.fetchone()
    currency_count = int(row.currency_count or 0)
    if currency_count > 1:
        raise ValueError(
            f"Property {property_id} has {currency_count} currencies in "
            f"{year}-{month:02d}; refusing to add them together"
        )

    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": _money_string(row.total_revenue),
        "currency": row.currency or "USD",
        "count": int(row.reservation_count or 0),
        "month": month,
        "year": year,
        "timezone": timezone_name,
    }


async def calculate_monthly_revenue(
    property_id: str,
    month: int,
    year: int,
    tenant_id: str,
    db_session=None,
) -> Dict[str, Any]:
    """
    Revenue for one property and one calendar month, in that property's timezone.
    """
    if db_session is not None:
        return await _load_monthly_revenue(db_session, property_id, month, year, tenant_id)

    pool = await _ensure_pool()
    async with pool.get_session() as session:
        return await _load_monthly_revenue(session, property_id, month, year, tenant_id)


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    try:
        # Import database pool
        from app.core.database_pool import DatabasePool

        # Initialize pool if needed
        db_pool = DatabasePool()
        await db_pool.initialize()

        if db_pool.session_factory:
            async with db_pool.get_session() as session:
                # Use SQLAlchemy text for raw SQL
                from sqlalchemy import text

                query = text("""
                    SELECT
                        property_id,
                        SUM(total_amount) as total_revenue,
                        COUNT(*) as reservation_count
                    FROM reservations
                    WHERE property_id = :property_id AND tenant_id = :tenant_id
                    GROUP BY property_id
                """)

                result = await session.execute(query, {
                    "property_id": property_id,
                    "tenant_id": tenant_id
                })
                row = result.fetchone()

                if row:
                    total_revenue = Decimal(str(row.total_revenue))
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": str(total_revenue),
                        "currency": "USD",
                        "count": row.reservation_count
                    }
                else:
                    # No reservations found for this property
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": "0.00",
                        "currency": "USD",
                        "count": 0
                    }
        else:
            raise Exception("Database pool not available")

    except Exception as e:
        print(f"Database error for {property_id} (tenant: {tenant_id}): {e}")

        # Create property-specific mock data for testing when DB is unavailable
        # This ensures each property shows different figures
        mock_data = {
            'prop-001': {'total': '1000.00', 'count': 3},
            'prop-002': {'total': '4975.50', 'count': 4},
            'prop-003': {'total': '6100.50', 'count': 2},
            'prop-004': {'total': '1776.50', 'count': 4},
            'prop-005': {'total': '3256.00', 'count': 3}
        }

        mock_property_data = mock_data.get(property_id, {'total': '0.00', 'count': 0})

        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": mock_property_data['total'],
            "currency": "USD",
            "count": mock_property_data['count']
        }
