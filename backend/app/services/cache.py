import json
import logging
import os
from typing import Any, Dict

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Initialize Redis client (typically configured centrally).
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def revenue_cache_key(tenant_id: str, property_id: str, year: int, month: int) -> str:
    """Cache entries are per tenant. Property ids are reused across companies."""
    if not tenant_id:
        raise ValueError("tenant_id is required for a revenue cache key")
    return f"revenue:{tenant_id}:{property_id}:{year:04d}-{month:02d}"


async def get_revenue_summary(
    property_id: str,
    tenant_id: str,
    month: int = 3,
    year: int = 2024,
) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.
    """
    cache_key = revenue_cache_key(tenant_id, property_id, year, month)

    try:
        cached = await redis_client.get(cache_key)
        if cached:
            payload = json.loads(cached)
            cached_tenant = payload.get("tenant_id")
            cached_property = payload.get("property_id")
            if cached_tenant == tenant_id and cached_property == property_id:
                return payload
            logger.warning(
                "Discarding revenue cache entry %s with tenant %s",
                cache_key,
                cached_tenant,
            )
    except Exception as exc:
        logger.warning("Revenue cache read failed: %s", exc)

    from app.services.reservations import calculate_monthly_revenue

    result = await calculate_monthly_revenue(property_id, month, year, tenant_id)

    try:
        await redis_client.set(cache_key, json.dumps(result), ex=300)
    except Exception as exc:
        logger.warning("Revenue cache write failed: %s", exc)

    return result
