"""
Locks in the tuning numbers called out in the prototype README so future
changes to the balance constants don't silently drift:

  - DNF rate should land around ~9% per driver over a large sample of races
    (README: "checked, not just eyeballed" over 200 sims).
"""

import random

from engine import Compound, Weather, roll_circuit
from simulation import build_driver, run_simulation
from tests.test_engine import make_random_drivers

NUM_RACES = 200
NUM_CARS = 8


def test_dnf_rate_around_nine_percent():
    total_driver_races = 0
    total_dnfs = 0

    for seed in range(NUM_RACES):
        rng = random.Random(seed)
        circuit = roll_circuit(rng)
        weather = Weather.roll(rng)
        drivers = make_random_drivers(NUM_CARS, rng)
        result = run_simulation(drivers, weather, circuit, rng)
        total_driver_races += len(drivers)
        total_dnfs += len(result["result"]["dnfs"])

    dnf_rate = total_dnfs / total_driver_races
    # ~8.7% measured with the original flat-risk model; keep a generous band
    # so normal constant tweaks (or the mix of circuits rolled) don't fail
    # the test, but a real balance regression will. Averaged across the 5
    # circuits' crash_rate_multiplier (~1.13x), this may land a bit higher
    # than the original number -- see test_dnf_rate_varies_by_circuit below
    # for why that's expected, not a regression.
    assert 0.05 <= dnf_rate <= 0.16, f"DNF rate drifted to {dnf_rate:.3%}"


def test_dnf_rate_varies_by_circuit():
    """Iron Harbor (crash_rate_multiplier=1.5, tight/street) should produce
    a meaningfully higher DNF rate than Sable Bay (1.1, fast/flowing) --
    circuits are supposed to have real strategic/risk identity, not just
    cosmetic shape differences."""
    from engine import CIRCUITS

    iron_harbor = next(c for c in CIRCUITS if c.id == "iron-harbor")
    sable_bay = next(c for c in CIRCUITS if c.id == "sable-bay")

    def dnf_rate_for(circuit):
        total_driver_races = 0
        total_dnfs = 0
        for seed in range(150):
            rng = random.Random(seed)
            weather = Weather.roll(rng)
            drivers = make_random_drivers(NUM_CARS, rng)
            result = run_simulation(drivers, weather, circuit, rng)
            total_driver_races += len(drivers)
            total_dnfs += len(result["result"]["dnfs"])
        return total_dnfs / total_driver_races

    assert dnf_rate_for(iron_harbor) > dnf_rate_for(sable_bay)


def test_dnf_reason_weighting_matches_cause():
    """Traffic-caused DNFs should skew toward Collision; base-rate DNFs
    should skew toward Mechanical failure (README known issue #2)."""
    from race import roll_dnf_reason

    rng = random.Random(0)
    traffic_reasons = [roll_dnf_reason(rng, in_traffic=True, attempting_overtake=False) for _ in range(2000)]
    base_reasons = [roll_dnf_reason(rng, in_traffic=False, attempting_overtake=False) for _ in range(2000)]

    traffic_collision_share = traffic_reasons.count("Collision") / len(traffic_reasons)
    base_mechanical_share = base_reasons.count("Mechanical failure") / len(base_reasons)

    assert traffic_collision_share > 0.35
    assert base_mechanical_share > 0.40
