"""Revenue totals stay in decimal cents instead of binary floats."""

import os
import unittest
from decimal import Decimal

os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/propertyflow"
)
os.environ.setdefault("SECRET_KEY", "debug_challenge_secret")

from app.services.reservations import calculate_monthly_revenue, format_money


class FormatMoneyTests(unittest.TestCase):
    def test_half_up_beats_binary_rounding(self):
        self.assertEqual(format_money(Decimal("1.005")), "1.01")
        self.assertEqual(format_money(Decimal("10.015")), "10.02")
        # 1.005 is not exact in binary, so the built-in round drops the cent.
        self.assertEqual(round(1.005, 2), 1.0)

    def test_seed_line_items_that_are_not_binary_exact(self):
        self.assertNotEqual(
            format(float(Decimal("1080.40")), ".20f"),
            "1080.40000000000000000000",
        )
        self.assertNotEqual(
            format(float(Decimal("1255.60")), ".20f"),
            "1255.60000000000000000000",
        )
        self.assertEqual(
            format_money(Decimal("920.00") + Decimal("1080.40") + Decimal("1255.60")),
            "3256.00",
        )
        self.assertEqual(
            format_money(Decimal("420.00") + Decimal("560.75") + Decimal("480.25") + Decimal("315.50")),
            "1776.50",
        )

    def test_thousandths_sum_before_rounding(self):
        # 333.333 + 333.333 + 333.334 = 1000.000 exactly.
        # Rounding each line first drops the cent those thousandths make together.
        total = Decimal("333.333") + Decimal("333.333") + Decimal("333.334")
        self.assertEqual(format_money(total), "1000.00")
        per_line = Decimal("333.333").quantize(Decimal("0.01"))
        self.assertEqual(per_line * 2 + Decimal("333.334").quantize(Decimal("0.01")), Decimal("999.99"))


class SeededMoneyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from app.core.database_pool import db_pool

        await db_pool.close()

    async def test_seed_totals_are_cent_strings(self):
        from app.core.database_pool import db_pool

        await db_pool.initialize()
        if db_pool.session_factory is None:
            self.skipTest("database not available")

        beach = await calculate_monthly_revenue("prop-001", 3, 2024, "tenant-a")
        apartment = await calculate_monthly_revenue("prop-002", 3, 2024, "tenant-a")
        villa = await calculate_monthly_revenue("prop-003", 3, 2024, "tenant-a")
        cottage = await calculate_monthly_revenue("prop-004", 3, 2024, "tenant-b")
        loft = await calculate_monthly_revenue("prop-005", 3, 2024, "tenant-b")
        lodge = await calculate_monthly_revenue("prop-001", 3, 2024, "tenant-b")

        self.assertEqual(beach["total"], "2250.00")
        self.assertEqual(apartment["total"], "4975.50")
        self.assertEqual(villa["total"], "6100.50")
        self.assertEqual(cottage["total"], "1776.50")
        self.assertEqual(loft["total"], "3256.00")
        self.assertEqual(lodge["total"], "0.00")
        for row in (beach, apartment, villa, cottage, loft, lodge):
            self.assertIsInstance(row["total"], str)
            self.assertRegex(row["total"], r"^\d+\.\d{2}$")


if __name__ == "__main__":
    unittest.main()
