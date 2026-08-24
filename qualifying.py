import random

from engine import (
    TIRES, Weather, Driver,
    BASE_LAP_TIME, GRIP_PENALTY_FACTOR, QUALI_RANDOM_VARIANCE,
)


def aggression_pace_bonus(aggression: int) -> float:
    return -0.03 * aggression


def aggression_mistake_risk(aggression: int) -> float:
    """Chance of a lockup/mistake on the flying lap."""
    return 0.01 * aggression  # 1% per level, so 5% at max aggression


def run_qualifying(drivers: list[Driver], weather: Weather, rng: random.Random):
    """
    Each driver has already chosen driver.quali_compound and driver.aggression.
    Simulates the single flying lap, sets driver.quali_time, then sorts
    drivers into grid_position order (1 = pole).
    """
    for d in drivers:
        tire = TIRES[d.quali_compound]
        grip = weather.effective_grip(tire)
        grip_penalty = (1 - grip) * GRIP_PENALTY_FACTOR

        lap_time = (
            BASE_LAP_TIME
            + tire.base_pace
            + grip_penalty
            + aggression_pace_bonus(d.aggression)
            + rng.uniform(-QUALI_RANDOM_VARIANCE, QUALI_RANDOM_VARIANCE)
        )

        # Mistake chance: costs time but does NOT eliminate the driver from
        # the whole sim -- it just wrecks their quali lap (sent to back of grid).
        mistake_roll = rng.random()
        made_mistake = mistake_roll < aggression_mistake_risk(d.aggression)
        if made_mistake:
            lap_time += rng.uniform(1.5, 4.0)  # lockup / correction time loss

        d.quali_time = lap_time
        d._quali_mistake = made_mistake  # stashed for reporting

    # Sort: normal times ascending, but anyone who made a mistake is still
    # ranked by their (inflated) time -- naturally pushes them back.
    ordered = sorted(drivers, key=lambda d: d.quali_time)
    for i, d in enumerate(ordered, start=1):
        d.grid_position = i

    return ordered
