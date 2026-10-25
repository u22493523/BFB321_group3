
import math  # sqrt and ceil
from statistics import NormalDist  # built into Python, gives us z-values without needing scipy


# helper so every function checks its inputs the same way
# flags negative demand or a negative lead time 
def _check_not_negative(**values):
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} cannot be negative (got {value}).")


# turns the service level into z, e.g. 0.95 -> 1.645
# added this so we stop typing z by hand (we had 1.65 in the tests when the proper value is 1.645)
def z_from_service_level(service_level):
    """
    Converts a target cycle service level into its z-score.

    Inputs:
        service_level (float): Probability of not running out during a lead time,
            e.g. 0.95 for 95%. Must be between 0.5 and 1 (not including 1).

    Returns:
        float: The z-score from the standard normal distribution (0.95 -> 1.645).
    """
    # below 50% would give a negative z (negative safety stock) and 100% would need infinite stock
    if not 0.5 <= service_level < 1:
        raise ValueError("Service level must be at least 0.5 and less than 1.")
    # inv_cdf = inverse of the normal CDF, the same as looking z up in the table
    return NormalDist().inv_cdf(service_level)


# EOQ first 
def calculate_eoq(annual_demand, ordering_cost, holding_cost_per_unit):
    """
    Calculates the Economic Order Quantity (EOQ).

    Inputs:
        annual_demand (float): Total expected demand for the year.
        ordering_cost (float): Fixed cost incurred per order (e.g., delivery fee).
        holding_cost_per_unit (float): Cost to hold one unit in inventory for a year.

    Returns:
        int: The optimal order quantity rounded to the nearest whole unit.
    """
    # H is in the denominator, stop if 0 
    if holding_cost_per_unit <= 0:
        raise ValueError("Holding cost must be greater than zero.")
    # D and S can be 0 (then EOQ is 0) but never negative
    _check_not_negative(annual_demand=annual_demand, ordering_cost=ordering_cost)

    # Q* = sqrt(2 * D * S / H)
    eoq = math.sqrt((2 * annual_demand * ordering_cost) / holding_cost_per_unit)
    # can't order part of a bottle, round to the nearest whole number
    return round(eoq)


# Safety stock when both demand and supplier lead time vary
# SS = z * sqrt(L * sigma_d^2 + d^2 * sigma_L^2)
def calculate_safety_stock(z_score, avg_lead_time, std_dev_demand, avg_daily_demand, std_dev_lead_time):
    """
    Calculates safety stock accounting for both demand and lead-time variability.

    Inputs:
        z_score (float): The service level factor (e.g., 1.645 for 95% service level).
            Use z_from_service_level() to get it from a service level.
        avg_lead_time (float): Average supplier lead time in days.
        std_dev_demand (float): Standard deviation of daily demand.
        avg_daily_demand (float): Average daily demand.
        std_dev_lead_time (float): Standard deviation of supplier lead time in days.

    Returns:
        int: Required safety stock rounded up to the nearest whole unit.
    """
    _check_not_negative(z_score=z_score, avg_lead_time=avg_lead_time, std_dev_demand=std_dev_demand,
                        avg_daily_demand=avg_daily_demand, std_dev_lead_time=std_dev_lead_time)

    # Variance of demand during lead time = two parts added together:
    # 1) L * sigma_d^2 -> the spread from demand changing day to day over the lead time
    demand_variability_term = avg_lead_time * (std_dev_demand ** 2)
    # 2) d^2 * sigma_L^2 -> the spread from the supplier being late or early
    
    lead_time_variability_term = (avg_daily_demand ** 2) * (std_dev_lead_time ** 2)

    # add the two variances, square root to get back to a std dev, times z for the service level
    safety_stock = z_score * math.sqrt(demand_variability_term + lead_time_variability_term)
    # round UP not normal rounding, otherwise we'd sit just under the service level we want
    return math.ceil(safety_stock)


# ROP = expected demand while we wait for the delivery + the safety stock buffer
def calculate_reorder_point(avg_daily_demand, avg_lead_time, safety_stock):
    """
    Calculates the Reorder Point (ROP).

    Inputs:
        avg_daily_demand (float): Average units sold per day.
        avg_lead_time (float): Average supplier lead time in days.
        safety_stock (int): Calculated safety stock buffer.

    Returns:
        int: The inventory level at which a new order should be placed.
    """
    _check_not_negative(avg_daily_demand=avg_daily_demand, avg_lead_time=avg_lead_time,
                        safety_stock=safety_stock)
    # d * L = how much we expect to sell before the order arrives
    lead_time_demand = avg_daily_demand * avg_lead_time
    rop = lead_time_demand + safety_stock
    # round up again to stay on the safe side
    return math.ceil(rop)
