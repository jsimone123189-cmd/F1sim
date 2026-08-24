import random

from engine import NUM_LAPS, Compound, Weather
from race import plan_pit_laps
from simulation import build_driver, run_simulation


def make_random_drivers(num_cars: int, rng: random.Random):
    drivers = []
    for i in range(num_cars):
        aggression = rng.randint(1, 5)
        num_stops = rng.choice([1, 2])
        quali_compound = rng.choice(list(Compound))
        available = [c for c in Compound if c != quali_compound]
        race_compounds = [rng.choice(available) for _ in range(num_stops + 1)]
        drivers.append(build_driver(f"Driver {i + 1}", "Red", aggression, num_stops, quali_compound, race_compounds))
    return drivers


def run_seeded(seed: int, num_cars: int = 8):
    rng = random.Random(seed)
    weather = Weather.roll(rng)
    drivers = make_random_drivers(num_cars, rng)
    return run_simulation(drivers, weather, rng)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_seed_gives_identical_result():
    result_a = run_seeded(42)
    result_b = run_seeded(42)
    assert result_a == result_b


def test_different_seeds_generally_diverge():
    result_a = run_seeded(1)
    result_b = run_seeded(2)
    assert result_a["result"]["draft_order"] != result_b["result"]["draft_order"] \
        or result_a["race"]["laps"] != result_b["race"]["laps"]


# ---------------------------------------------------------------------------
# Qualifying tire lockout (spec §5)
# ---------------------------------------------------------------------------

def test_qualifying_compound_locked_out_of_race():
    result = run_seeded(7)
    for d in result["drivers"]:
        assert d["quali_compound"] not in d["race_compounds"]


# ---------------------------------------------------------------------------
# Pit stop planning
# ---------------------------------------------------------------------------

def test_one_stop_pit_lap_in_valid_range():
    rng = random.Random(123)
    for _ in range(500):
        laps = plan_pit_laps(1, rng)
        assert len(laps) == 1
        assert 3 <= laps[0] <= NUM_LAPS - 3


def test_two_stop_pit_laps_ordered_and_spaced():
    rng = random.Random(456)
    for _ in range(500):
        laps = plan_pit_laps(2, rng)
        assert len(laps) == 2
        lap1, lap2 = laps
        assert 3 <= lap1 <= NUM_LAPS - 6
        assert lap1 + 3 <= lap2 <= NUM_LAPS - 2


# ---------------------------------------------------------------------------
# Full classification / draft order
# ---------------------------------------------------------------------------

def test_every_driver_classified_exactly_once():
    result = run_seeded(99, num_cars=10)
    classified_ids = [c["driver_id"] for c in result["result"]["classified"]]
    dnf_ids = [c["driver_id"] for c in result["result"]["dnfs"]]
    all_ids = classified_ids + dnf_ids
    assert sorted(all_ids) == list(range(10))
    assert sorted(result["result"]["draft_order"]) == list(range(10))


def test_finishers_sorted_by_total_time_ascending():
    result = run_seeded(5)
    times = [c["total_time"] for c in result["result"]["classified"]]
    assert times == sorted(times)


# ---------------------------------------------------------------------------
# JSON log schema (spec §10)
# ---------------------------------------------------------------------------

def test_json_log_has_expected_shape():
    result = run_seeded(11)
    assert set(result.keys()) >= {"num_laps", "weather", "drivers", "qualifying", "race", "result"}
    assert result["num_laps"] == NUM_LAPS
    assert len(result["race"]["laps"]) == NUM_LAPS

    for lap_entry in result["race"]["laps"]:
        for car in lap_entry["cars"]:
            assert "driver_id" in car
            assert "compound" in car
            if car.get("event") == "DNF":
                assert "reason" in car
            else:
                assert "pit" in car


# ---------------------------------------------------------------------------
# Lap-to-lap weather evolution
# ---------------------------------------------------------------------------

def test_weather_timeline_has_one_entry_per_lap():
    result = run_seeded(3)
    timeline = result["weather_timeline"]
    assert len(timeline) == NUM_LAPS
    assert [e["lap"] for e in timeline] == list(range(1, NUM_LAPS + 1))


def test_weather_only_steps_to_a_neighboring_state():
    order = ["Dry", "Damp", "Wet"]
    for seed in range(60):
        result = run_seeded(seed)
        states = [result["weather"]["track_state"]] + [e["track_state"] for e in result["weather_timeline"]]
        for a, b in zip(states, states[1:]):
            assert abs(order.index(a) - order.index(b)) <= 1


def test_weather_eventually_evolves_across_many_races():
    # ~3%/lap over 20 laps means most individual races see no change, but a
    # change should show up somewhere across enough of them.
    changed = False
    for seed in range(150):
        result = run_seeded(seed)
        pre = result["weather"]["track_state"]
        if any(e["track_state"] != pre for e in result["weather_timeline"]):
            changed = True
            break
    assert changed, "expected at least one weather change across 150 seeded races"


def test_dnf_events_recorded_in_race_log():
    # Run a batch until we hit at least one DNF, then check its event log.
    for seed in range(50):
        result = run_seeded(seed, num_cars=12)
        if result["result"]["dnfs"]:
            dnf_events = [e for e in result["race"]["events"] if e["type"] == "DNF"]
            assert len(dnf_events) == len(result["result"]["dnfs"])
            return
    raise AssertionError("expected at least one DNF across 50 seeded races")
