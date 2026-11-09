"""Revenue cache and tenant resolution must not cross companies."""

import os
import unittest
from decimal import Decimal

os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/propertyflow"
)
os.environ.setdefault("SECRET_KEY", "debug_challenge_secret")
# Separate Redis DB from the API process so this test can flush freely.
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/1"

from app.core.tenant_resolver import TenantResolver
from app.services.cache import get_revenue_summary, revenue_cache_key


class CacheKeyTests(unittest.TestCase):
    def test_shared_property_id_does_not_share_a_key(self):
        sunset = revenue_cache_key("tenant-a", "prop-001", 2024, 3)
        ocean = revenue_cache_key("tenant-b", "prop-001", 2024, 3)
        self.assertNotEqual(sunset, ocean)
        self.assertIn("tenant-a", sunset)
        self.assertIn("tenant-b", ocean)
        self.assertNotEqual(
            revenue_cache_key("tenant-a", "prop-001", 2024, 3),
            revenue_cache_key("tenant-a", "prop-001", 2024, 4),
        )

    def test_missing_tenant_is_rejected(self):
        with self.assertRaises(ValueError):
            revenue_cache_key("", "prop-001", 2024, 3)


class TenantResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_clients_keep_their_tenants(self):
        sunset = await TenantResolver.resolve_tenant_id("user-sunset", "sunset@propertyflow.com")
        ocean = await TenantResolver.resolve_tenant_id("user-ocean", "ocean@propertyflow.com")
        self.assertEqual(sunset, "tenant-a")
        self.assertEqual(ocean, "tenant-b")

    async def test_unknown_user_is_not_given_sunset(self):
        tenant = await TenantResolver.resolve_tenant_id("user-x", "stranger@example.com")
        self.assertIsNone(tenant)

    async def test_jwt_tenant_is_used(self):
        from jose import jwt

        token = jwt.encode(
            {
                "sub": "user-x",
                "id": "user-x",
                "email": "stranger@example.com",
                "tenant_id": "tenant-b",
                "aud": "authenticated",
            },
            "debug_challenge_secret",
            algorithm="HS256",
        )
        tenant = await TenantResolver.resolve_tenant_id(
            "user-x", "stranger@example.com", token=token
        )
        self.assertEqual(tenant, "tenant-b")


class CachedRevenueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.services.cache import redis_client

        await redis_client.flushdb()

    async def asyncTearDown(self):
        from app.core.database_pool import db_pool

        await db_pool.close()

    async def test_refresh_does_not_return_the_other_company(self):
        from app.core.database_pool import db_pool

        await db_pool.initialize()
        if db_pool.session_factory is None:
            self.skipTest("database not available")

        # Ocean's lodge is also prop-001. Cache Sunset first, then refresh as Ocean.
        sunset = await get_revenue_summary("prop-001", "tenant-a", 3, 2024)
        ocean = await get_revenue_summary("prop-001", "tenant-b", 3, 2024)

        self.assertEqual(sunset["tenant_id"], "tenant-a")
        self.assertEqual(sunset["timezone"], "Europe/Paris")
        self.assertEqual(int(sunset["count"]), 4)
        self.assertEqual(Decimal(sunset["total"]), Decimal("2250.000"))

        self.assertEqual(ocean["tenant_id"], "tenant-b")
        self.assertEqual(ocean["timezone"], "America/New_York")
        self.assertEqual(int(ocean["count"]), 0)
        self.assertEqual(Decimal(ocean["total"]), Decimal("0"))

        # Sunset must not see Lakeside Cottage after Ocean has loaded it.
        await get_revenue_summary("prop-004", "tenant-b", 3, 2024)
        sunset_cottage = await get_revenue_summary("prop-004", "tenant-a", 3, 2024)
        self.assertEqual(Decimal(sunset_cottage["total"]), Decimal("0"))
        self.assertIsNone(sunset_cottage["timezone"])
        self.assertEqual(int(sunset_cottage["count"]), 0)


if __name__ == "__main__":
    unittest.main()
