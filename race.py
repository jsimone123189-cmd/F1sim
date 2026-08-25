import random

from engine import (
    TIRES, Weather, Driver, Circuit,
    BASE_LAP_TIME, NUM_LAPS, GRIP_PENALTY_FACTOR, RACE_RANDOM_VARIANCE,
    PIT_STOP_BASE_RANGE, PIT_STOP_SLOW_CHANCE, PIT_STOP_SLOW_EXTRA_RANGE,
    BASE_MECHANICAL_RATE, TRAFFIC_GAP_THRESHOLD, TRAFFIC_PENALTY,
    NEIGHBOR_DANGER_WINDOW, AGGRESSION_RISK_RATE, AMBIENT_DANGER_RATE,
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


def pit_stop_time_loss(rng: random.Random, circuit: Circuit) -> float:
    loss = rng.uniform(*PIT_STOP_BASE_RANGE) + circuit.pit_loss_bonus
    if rng.random() < PIT_STOP_SLOW_CHANCE:
        loss += rng.uniform(*PIT_STOP_SLOW_EXTRA_RANGE)
    return loss


def crowdedness(gap_ahead: float, ahead_aggression: int, gap_behind: float, behind_aggression: int) -> float:
    """How boxed-in a driver is this lap, weighted by how aggressive the
    cars right around them are -- not just whether someone is nearby.
    0 = clean air; up to ~2.0 = squeezed between two maximum-aggression
    rivals both within the danger window."""
    total = 0.0
    if gap_ahead is not None and gap_ahead < NEIGHBOR_DANGER_WINDOW:
        closeness = 1 - gap_ahead / NEIGHBOR_DANGER_WINDOW
        total += closeness * (0.4 + 0.6 * ahead_aggression / 5)
    if gap_behind is not None and gap_behind < NEIGHBOR_DANGER_WINDOW:
        closeness = 1 - gap_behind / NEIGHBOR_DANGER_WINDOW
        total += closeness * (0.4 + 0.6 * behind_aggression / 5)
    return total


def incident_probability(driver: Driver, weather: Weather, tire_spec, attempting_overtake: bool,
                          circuit: Circuit, crowd: float) -> float:
    grip = weather.effective_grip(tire_spec)
    p = BASE_MECHANICAL_RATE * circuit.crash_rate_multiplier
    p += (1 - grip) * 0.004 * circuit.crash_rate_multiplier  # low grip risk (bigger on wet track w/ bad-wet tire)
    p += AMBIENT_DANGER_RATE * crowd  # nearby aggressive rivals endanger you regardless of your own choices
    p += AGGRESSION_RISK_RATE * driver.aggression * (1 + crowd)  # your own aggression, amplified by crowding
    if attempting_overtake:
        p += (0.002 + 0.001 * driver.aggression) * circuit.overtake_difficulty
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


def run_race(drivers: list[Driver], weather: Weather, circuit: Circuit, rng: random.Random):
    """Returns (classified, weather_timeline). weather_timeline is a list of
    {"lap", "track_state", "temperature"} snapshots, one per lap, reflecting
    any lap-to-lap weather evolution (engine.Weather.step)."""
    setup_race_strategies(drivers, rng)
    weather_timeline = []

    for lap in range(1, NUM_LAPS + 1):
        weather.step(rng)
        weather_timeline.append({
            "lap": lap,
            "track_state": weather.track_state.value,
            "temperature": weather.temperature.value,
        })

        active = [d for d in drivers if not d.dnf]
        # running order & gaps BEFORE this lap's time is added. Each driver
        # gets both neighbors (not just the one ahead) so crash risk can
        # reflect who's actually racing around them this lap.
        running_order = sorted(active, key=lambda d: d.total_time)
        neighbors = {}
        for i, d in enumerate(running_order):
            ahead = running_order[i - 1] if i > 0 else None
            behind = running_order[i + 1] if i + 1 < len(running_order) else None
            neighbors[d.name] = {
                "gap_ahead": (d.total_time - ahead.total_time) if ahead else None,
                "ahead_aggression": ahead.aggression if ahead else None,
                "gap_behind": (behind.total_time - d.total_time) if behind else None,
                "behind_aggression": behind.aggression if behind else None,
            }

        for d in active:
            d.laps_on_current_tire += 1
            compound = d.race_compounds[d.current_stint_index]
            tire = TIRES[compound]

            grip = weather.effective_grip(tire)
            grip_penalty = (1 - grip) * GRIP_PENALTY_FACTOR
            heat_mult = weather.heat_multiplier(tire)
            degradation_penalty = (
                tire.degradation_rate
                * circuit.deg_multiplier
                * heat_mult
                * aggression_degradation_multiplier(d.aggression)
                * (d.laps_on_current_tire ** 1.15)
            )

            nb = neighbors[d.name]
            gap = nb["gap_ahead"]
            in_traffic = gap is not None and gap < TRAFFIC_GAP_THRESHOLD
            crowd = crowdedness(nb["gap_ahead"], nb["ahead_aggression"], nb["gap_behind"], nb["behind_aggression"])
            attempting_overtake = False
            traffic_penalty = 0.0
            overtake_note = None

            if in_traffic:
                attempt_prob = min(0.9, (0.15 + 0.10 * d.aggression) / circuit.overtake_difficulty)
                attempting_overtake = rng.random() < attempt_prob
                if attempting_overtake:
                    # success more likely if this car is just plain faster
                    success_prob = min(0.9, (0.4 + 0.05 * d.aggression) / circuit.overtake_difficulty)
                    if rng.random() < success_prob:
                        overtake_note = "overtake_success"
                    else:
                        traffic_penalty = TRAFFIC_PENALTY * circuit.overtake_difficulty
                        overtake_note = "overtake_failed"
                else:
                    traffic_penalty = TRAFFIC_PENALTY * circuit.overtake_difficulty

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
                pit_loss = pit_stop_time_loss(rng, circuit)
                lap_time += pit_loss

            # incident roll
            p_incident = incident_probability(d, weather, tire, attempting_overtake, circuit, crowd)
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
    return finishers + dnfs, weather_timeline
