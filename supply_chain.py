"""Supply chain model (Step 2): the inventory formulas for TapHouse.

Kept in its own file with no database or Flask imports, so the maths can be
unit tested on its own (tests/test_inventory.py) and reused by models.py,
init_db.py and the API. Every function takes plain numbers and returns
plain numbers.

Notation
    d        average daily demand (units/day)
    sigma_d  standard deviation of daily demand
    L        average supplier lead time (days)
    sigma_L  standard deviation of the lead time (days)
    z        service level factor (1.645 for 95%)
    D, S, H  annual demand, cost per order, holding cost per unit per year
"""
import math
from statistics import NormalDist

# Proposal objective 3: a cycle service level of at least 95%
DEFAULT_SERVICE_LEVEL = 0.95


def calculate_eoq(annual_demand, order_cost, holding_cost):
    """Economic Order Quantity, EOQ = sqrt(2DS / H), rounded to whole units."""
    if annual_demand < 0 or order_cost < 0 or holding_cost <= 0:
        raise ValueError("Demand and order cost must be non-negative, and holding cost must be positive.")
    return round(math.sqrt((2 * annual_demand * order_cost) / holding_cost))


def calculate_safety_stock(z_score, avg_lead_time, std_dev_demand, avg_demand, std_dev_lead_time):
    """Safety stock with variable demand AND variable lead time, rounded up.

    SS = z * sqrt(L * sigma_d^2 + d^2 * sigma_L^2)
    The first term covers demand changing day to day during the lead time;
    the second covers the supplier delivering early or late.
    """
    if min(z_score, avg_lead_time, std_dev_demand, avg_demand, std_dev_lead_time) < 0:
        raise ValueError("Safety stock inputs cannot be negative.")
    demand_variability_term = avg_lead_time * (std_dev_demand ** 2)
    lead_time_variability_term = (avg_demand ** 2) * (std_dev_lead_time ** 2)
    return math.ceil(z_score * math.sqrt(demand_variability_term + lead_time_variability_term))


def calculate_reorder_point(avg_demand, avg_lead_time, safety_stock):
    """Reorder point, ROP = d * L + SS (expected lead-time demand plus the buffer)."""
    if avg_demand < 0 or avg_lead_time < 0 or safety_stock < 0:
        raise ValueError("Reorder point inputs cannot be negative.")
    return math.ceil((avg_demand * avg_lead_time) + safety_stock)


def z_from_service_level(service_level):
    """z-score for a cycle service level, e.g. 0.95 -> 1.645 (0.5 <= level < 1)."""
    if not (0.5 <= service_level < 1):
        raise ValueError("Service level must be at least 0.5 (50%) and strictly less than 1.")
    return NormalDist().inv_cdf(service_level)


def recommend(avg_demand, std_dev_demand, avg_lead_time, std_dev_lead_time,
              annual_demand, order_cost, holding_cost, service_level=DEFAULT_SERVICE_LEVEL):
    """Full replenishment recommendation for one product.

    Returns a dict with z, safety_stock, reorder_point and eoq (None when the
    cost data needed for EOQ is missing).
    """
    z = z_from_service_level(service_level)
    safety_stock = calculate_safety_stock(z, avg_lead_time, std_dev_demand, avg_demand, std_dev_lead_time)
    eoq = (calculate_eoq(annual_demand, order_cost, holding_cost)
           if holding_cost > 0 and annual_demand > 0 else None)
    return {
        "service_level": service_level,
        "z_score": round(z, 3),
        "safety_stock": safety_stock,
        "reorder_point": calculate_reorder_point(avg_demand, avg_lead_time, safety_stock),
        "eoq": eoq,
    }
