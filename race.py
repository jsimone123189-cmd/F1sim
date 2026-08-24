import random

from engine import (
    TIRES, Weather, Driver,
    BASE_LAP_TIME, NUM_LAPS, GRIP_PENALTY_FACTOR, RACE_RANDOM_VARIANCE,
    PIT_STOP_BASE_RANGE, PIT_STOP_SLOW_CHANCE, PIT_STOP_SLOW_EXTRA_RANGE,
    BASE_MECHANICAL_RATE, TRAFFIC_GAP_THRESHOLD, TRAFFIC_PENALTY,
)

# DNF reason weighting (README known issue #2): a DNF caused by close-quarters
# racing (traffic / an overtake attempt) should skew toward driver-error style
# causes; a DNF from the base per-lap roll (nobody nearby) should skew toward
# a mechanical cause instead of being uniform-random.
TRAFFIC_DNF_REASONS = ["Collision", "Spin", "Loss of control", "Mechanical failure"]
TRAFFIC_DNF_WEIGHTS = [50, 25, 15, 10]
BASE_DNF_REASONS = ["Mechanical failure", "Spin", "Loss of control", "Collision"]
BASE_DNF_WEIGHTS = [55, 20, 15, 10]


def plan_pit_laps(num_stops: int, rng: random.Random) -> list[int]:
    """Return the laps (1-indexed) AFTER which a pit stop happens."""
    if num_stops == 1:
        target = NUM_LAPS * 0.5
        lap = round(target + rng.uniform(-2, 2))
        return [max(3, min(NUM_LAPS - 3, lap))]
    else:  # 2 stops
        t1 = NUM_LAPS / 3
        t2 = NUM_LAPS * 2 / 3
        lap1 = round(t1 + rng.uniform(-1.5, 1.5))
        lap2 = round(t2 + rng.uniform(-1.5, 1.5))
        lap1 = max(3, min(NUM_LAPS - 6, lap1))
        lap2 = max(lap1 + 3, min(NUM_LAPS - 2, lap2))
        return [lap1, lap2]


def aggression_pace_bonus(aggression: int) -> float:
    return -0.03 * aggression


def aggression_degradation_multiplier(aggression: int) -> float:
    return 1 + 0.08 * aggression


def pit_stop_time_loss(rng: random.Random) -> float:
    loss = rng.uniform(*PIT_STOP_BASE_RANGE)
    if rng.random() < PIT_STOP_SLOW_CHANCE:
        loss += rng.uniform(*PIT_STOP_SLOW_EXTRA_RANGE)
    return loss


def incident_probability(driver: Driver, weather: Weather, tire_spec, in_traffic: bool, attempting_overtake: bool) -> float:
    p = BASE_MECHANICAL_RATE
    p += 0.0005 * driver.aggression  # aggression risk
    grip = weather.effective_grip(tire_spec)
    p += (1 - grip) * 0.004  # low grip risk (bigger on wet track w/ bad-wet tire)
    if in_traffic:
        p += 0.0015
    if attempting_overtake:
        p += 0.002 + 0.001 * driver.aggression
    return p


def roll_dnf_reason(rng: random.Random, in_traffic: bool, attempting_overtake: bool) -> str:
    if in_traffic or attempting_overtake:
        return rng.choices(TRAFFIC_DNF_REASONS, weights=TRAFFIC_DNF_WEIGHTS)[0]
    return rng.choices(BASE_DNF_REASONS, weights=BASE_DNF_WEIGHTS)[0]


def setup_race_strategies(drivers: list[Driver], rng: random.Random):
    """Assign pit_laps to each driver based on their chosen num_stops.
    Assumes driver.race_compounds already set (len == num_stops + 1) and
    excludes driver.quali_compound (enforced by caller / UI).
    """
    for d in drivers:
        d.pit_laps = plan_pit_laps(d.num_stops, rng)
        d.current_stint_index = 0
        d.laps_on_current_tire = 0
        d.total_time = 0.0
        d.dnf = False
        d.lap_log = []


def run_race(drivers: list[Driver], weather: Weather, rng: random.Random):
    setup_race_strategies(drivers, rng)

    for lap in range(1, NUM_LAPS + 1):
        active = [d for d in drivers if not d.dnf]
        # running order & gaps BEFORE this lap's time is added
        running_order = sorted(active, key=lambda d: d.total_time)
        gap_ahead = {}
        for i, d in enumerate(running_order):
            gap_ahead[d.name] = None if i == 0 else d.total_time - running_order[i - 1].total_time

        for d in active:
            d.laps_on_current_tire += 1
            compound = d.race_compounds[d.current_stint_index]
            tire = TIRES[compound]

            grip = weather.effective_grip(tire)
            grip_penalty = (1 - grip) * GRIP_PENALTY_FACTOR
            heat_mult = weather.heat_multiplier(tire)
            degradation_penalty = (
                tire.degradation_rate
                * heat_mult
                * aggression_degradation_multiplier(d.aggression)
                * (d.laps_on_current_tire ** 1.15)
            )

            gap = gap_ahead.get(d.name)
            in_traffic = gap is not None and gap < TRAFFIC_GAP_THRESHOLD
            attempting_overtake = False
            traffic_penalty = 0.0
            overtake_note = None

            if in_traffic:
                attempt_prob = 0.15 + 0.10 * d.aggression
                attempting_overtake = rng.random() < attempt_prob
                if attempting_overtake:
                    # success more likely if this car is just plain faster
                    success_prob = 0.4 + 0.05 * d.aggression
                    if rng.random() < success_prob:
                        overtake_note = "overtake_success"
                    else:
                        traffic_penalty = TRAFFIC_PENALTY
                        overtake_note = "overtake_failed"
                else:
                    traffic_penalty = TRAFFIC_PENALTY

            lap_time = (
                BASE_LAP_TIME
                + tire.base_pace
                + degradation_penalty
                + grip_penalty
                + aggression_pace_bonus(d.aggression)
                + traffic_penalty
                + rng.uniform(-RACE_RANDOM_VARIANCE, RACE_RANDOM_VARIANCE)
            )

            pit_this_lap = lap in d.pit_laps
            pit_loss = 0.0
            if pit_this_lap:
                pit_loss = pit_stop_time_loss(rng)
                lap_time += pit_loss

            # incident roll
            p_incident = incident_probability(d, weather, tire, in_traffic, attempting_overtake)
            if rng.random() < p_incident:
                d.dnf = True
                d.dnf_lap = lap
                d.dnf_reason = roll_dnf_reason(rng, in_traffic, attempting_overtake)
                d.lap_log.append({
                    "lap": lap, "event": "DNF", "reason": d.dnf_reason,
                    "compound": compound.value,
                    "position": None, "gap_to_leader": None,
                })
                continue

            d.total_time += lap_time
            d.lap_log.append({
                "lap": lap,
                "lap_time": round(lap_time, 3),
                "total_time": round(d.total_time, 3),
                "compound": compound.value,
                "stint_lap": d.laps_on_current_tire,
                "pit": pit_this_lap,
                "pit_loss": round(pit_loss, 3) if pit_this_lap else None,
                "overtake": overtake_note,
                "event": None,
                "position": None,
                "gap_to_leader": None,
            })

            if pit_this_lap:
                d.current_stint_index += 1
                d.laps_on_current_tire = 0

        # Recompute running order AFTER this lap's times are in, so we can
        # stamp position + gap-to-leader onto the lap_log entry we just
        # appended for every car still running (README known issue #3).
        finished_this_lap = [d for d in drivers if not d.dnf and d.lap_log and d.lap_log[-1]["lap"] == lap]
        finished_this_lap.sort(key=lambda d: d.total_time)
        leader_time = finished_this_lap[0].total_time if finished_this_lap else None
        for i, d in enumerate(finished_this_lap, start=1):
            d.lap_log[-1]["position"] = i
            d.lap_log[-1]["gap_to_leader"] = round(d.total_time - leader_time, 3)

    # Final classification: finishers by total_time, then DNFs by laps completed desc
    finishers = sorted([d for d in drivers if not d.dnf], key=lambda d: d.total_time)
    dnfs = sorted(
        [d for d in drivers if d.dnf],
        key=lambda d: (-(d.dnf_lap or 0),)
    )
    return finishers + dnfs
