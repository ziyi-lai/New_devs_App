"""March revenue uses each property's timezone, not UTC."""

import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/propertyflow"
)
os.environ.setdefault("SECRET_KEY", "debug_challenge_secret")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")

from app.services.reservations import calculate_monthly_revenue, month_bounds


class MonthBoundaryTests(unittest.TestCase):
    def test_paris_march_includes_late_february_utc(self):
        # res-tz-1 checks in at 29 Feb 2024 23:30 UTC, which is 1 Mar 00:30 in Paris.
        check_in = datetime(2024, 2, 29, 23, 30, tzinfo=timezone.utc)
        start, end = month_bounds(2024, 3, "Europe/Paris")
        self.assertLessEqual(start, check_in)
        self.assertLess(check_in, end)

        utc_start, utc_end = month_bounds(2024, 3, "UTC")
        self.assertFalse(utc_start <= check_in < utc_end)

    def test_new_york_march_excludes_early_utc_march(self):
        # 1 Mar 2024 04:30 UTC is still 29 Feb 23:30 in New York (EST, UTC-5).
        check_in = datetime(2024, 3, 1, 4, 30, tzinfo=timezone.utc)
        start, end = month_bounds(2024, 3, "America/New_York")
        self.assertFalse(start <= check_in < end)

        utc_start, utc_end = month_bounds(2024, 3, "UTC")
        self.assertLessEqual(utc_start, check_in)
        self.assertLess(check_in, utc_end)

    def test_paris_april_starts_after_dst(self):
        # 2024-04-01 00:00 in Paris is CEST (UTC+2), so the March window ends at 22:00 UTC.
        _, end = month_bounds(2024, 3, "Europe/Paris")
        self.assertEqual(end.astimezone(timezone.utc), datetime(2024, 3, 31, 22, 0, tzinfo=timezone.utc))


class MonthlyRevenueQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from app.core.database_pool import db_pool

        await db_pool.close()

    async def _require_db(self):
        from app.core.database_pool import db_pool

        await db_pool.initialize()
        if db_pool.session_factory is None:
            self.skipTest("database not available")
        return db_pool

    async def test_beach_house_march_is_local_not_utc(self):
        from sqlalchemy import text

        pool = await self._require_db()
        async with pool.get_session() as session:
            utc = await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS bookings, COALESCE(SUM(total_amount), 0) AS total
                    FROM reservations
                    WHERE property_id = 'prop-001'
                      AND tenant_id = 'tenant-a'
                      AND check_in_date >= :start_date
                      AND check_in_date < :end_date
                    """
                ),
                {
                    "start_date": datetime(2024, 3, 1, tzinfo=timezone.utc),
                    "end_date": datetime(2024, 4, 1, tzinfo=timezone.utc),
                },
            )
            utc_row = utc.fetchone()

        self.assertEqual(int(utc_row.bookings), 3)
        self.assertEqual(Decimal(utc_row.total), Decimal("1000.000"))

        local = await calculate_monthly_revenue("prop-001", 3, 2024, "tenant-a")
        self.assertEqual(local["timezone"], "Europe/Paris")
        self.assertEqual(int(local["count"]), 4)
        self.assertEqual(Decimal(local["total"]), Decimal("2250.000"))
        self.assertEqual(local["tenant_id"], "tenant-a")

    async def test_shared_property_id_stays_on_its_own_tenant(self):
        await self._require_db()
        ocean = await calculate_monthly_revenue("prop-001", 3, 2024, "tenant-b")
        self.assertEqual(ocean["timezone"], "America/New_York")
        self.assertEqual(int(ocean["count"]), 0)
        self.assertEqual(Decimal(ocean["total"]), Decimal("0"))


if __name__ == "__main__":
    unittest.main()
