import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from supply_chain import (calculate_eoq, calculate_safety_stock, calculate_reorder_point,
                          recommend, z_from_service_level)


# each formula checked against a hand calculation, plus the "bad input" cases
class TestInventoryModel(unittest.TestCase):

    def test_z_from_service_level(self):
        # values straight from the standard normal table
        self.assertAlmostEqual(z_from_service_level(0.95), 1.645, places=3)
        self.assertAlmostEqual(z_from_service_level(0.99), 2.326, places=3)

    def test_z_rejects_impossible_service_levels(self):
        # 100% would need infinite stock, and below 50% gives a negative z
        with self.assertRaises(ValueError):
            z_from_service_level(1.0)
        with self.assertRaises(ValueError):
            z_from_service_level(0.3)

    def test_calculate_eoq(self):
        # Hand-calculated scenario: D=10000, S=50, H=2 -> EOQ = sqrt((2*10000*50)/2) = 707.10
        self.assertEqual(calculate_eoq(10000, 50, 2), 707)

    def test_eoq_rejects_bad_inputs(self):
        # H = 0 would divide by zero, negative demand is a typo
        with self.assertRaises(ValueError):
            calculate_eoq(10000, 50, 0)
        with self.assertRaises(ValueError):
            calculate_eoq(-10000, 50, 2)

    def test_calculate_safety_stock(self):
        # Scenario: 95% service level (Z=1.645), L=4 days, StdDevD=10, AvgD=50, StdDevL=1
        # SS = 1.645 * sqrt(4*(10^2) + (50^2)*(1^2)) = 1.645 * sqrt(400 + 2500) = 1.645 * 53.85 = 88.59 -> 89
        # (88.59 goes UP to 89 because the function uses ceil)
        self.assertEqual(calculate_safety_stock(1.645, 4, 10, 50, 1), 89)

    def test_safety_stock_using_service_level_helper(self):
        # same scenario but letting the helper work out z, should give the same answer
        z = z_from_service_level(0.95)
        self.assertEqual(calculate_safety_stock(z, 4, 10, 50, 1), 89)

    def test_safety_stock_demand_variability_only(self):
        # if the supplier is always on time (StdDevL = 0) it drops to SS = z * sigma_d * sqrt(L)
        # 1.645 * 10 * sqrt(4) = 32.9 -> 33
        self.assertEqual(calculate_safety_stock(1.645, 4, 10, 50, 0), 33)

    def test_safety_stock_lead_time_variability_only(self):
        # if demand never changes (StdDevD = 0) it drops to SS = z * d * sigma_L
        # 1.645 * 50 * 1 = 82.25 -> 83
        self.assertEqual(calculate_safety_stock(1.645, 4, 0, 50, 1), 83)

    def test_safety_stock_rejects_negative_inputs(self):
        with self.assertRaises(ValueError):
            calculate_safety_stock(1.645, -4, 10, 50, 1)

    def test_calculate_reorder_point(self):
        # Scenario: AvgD=50, L=4, SS=89 -> ROP = (50*4) + 89 = 289
        self.assertEqual(calculate_reorder_point(50, 4, 89), 289)

    def test_recommend_for_seeded_lager(self):
        # Lager 500ml from seed.sql: d=10, sigma_d=3.2, SAB lead time L=4, sigma_L=1, 95%
        # SS  = 1.645 * sqrt(4*3.2^2 + 10^2*1^2) = 1.645 * sqrt(140.96) = 19.53 -> 20
        # ROP = 10*4 + 20 = 60   (matches the reorder point in seed.sql)
        # EOQ = sqrt(2*3650*120 / 3.10) = 531.6 -> 532
        rec = recommend(10, 3.2, 4, 1.0, 3650, 120, 3.10)
        self.assertEqual(rec["safety_stock"], 20)
        self.assertEqual(rec["reorder_point"], 60)
        self.assertEqual(rec["eoq"], 532)

    def test_recommend_without_cost_data_has_no_eoq(self):
        # a new product with no holding cost yet still gets SS and ROP, but EOQ is None
        rec = recommend(10, 3.2, 4, 1.0, 0, 0, 0)
        self.assertIsNone(rec["eoq"])
        self.assertEqual(rec["reorder_point"], 60)


if __name__ == '__main__':
    unittest.main()
