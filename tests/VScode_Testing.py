import sys
import os


project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from models.inventory import (calculate_eoq, calculate_safety_stock, calculate_reorder_point,
                              z_from_service_level)


# small helper so every test prints the same way (expected vs actual, then pass/fail)
def print_test_result(test_name, expected, actual):
    print(f"--- {test_name} ---")
    print(f"Expected output: {expected}")
    print(f"Actual output: {actual}")
    if expected == actual:
        print("Test status: Passed\n")
    else:
        print("Test status: Failed\n")


def run_all_tests():
    # expected values below were worked out by hand first, then compared to the code

    # Test 1: Economic Order Quantity (EOQ)
    # Scenario: Annual Demand=10000, Order Cost=50, Holding Cost=2
    # by hand: sqrt(2*10000*50 / 2) = sqrt(500000) = 707.1 -> 707
    expected_eoq = 707
    actual_eoq = calculate_eoq(10000, 50, 2)
    print_test_result("Test 1: calculate_eoq", expected_eoq, actual_eoq)

    # Test 2: Safety Stock
    # Scenario: 95% service level, Avg Lead Time=4, StdDev Demand=10, Avg Demand=50, StdDev Lead Time=1
    # z for 95% is 1.645 (from the table), now worked out by the helper instead of typed in
    # by hand: 1.645 * sqrt(4*10^2 + 50^2*1^2) = 1.645 * sqrt(2900) = 88.59 -> round up to 89
    z_95 = z_from_service_level(0.95)
    expected_ss = 89
    actual_ss = calculate_safety_stock(z_95, 4, 10, 50, 1)
    print_test_result("Test 2: calculate_safety_stock", expected_ss, actual_ss)

    # Test 3: Reorder Point
    # Scenario: Avg Demand=50, Avg Lead Time=4, Safety Stock=89
    # by hand: 50*4 + 89 = 289 (using the SS from test 2 so the two tests link up)
    expected_rop = 289
    actual_rop = calculate_reorder_point(50, 4, 89)
    print_test_result("Test 3: calculate_reorder_point", expected_rop, actual_rop)


# only run the tests when this file is run directly, not when it's imported
if __name__ == '__main__':
    run_all_tests()
