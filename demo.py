"""
CLI runner for the Fantasy Draft F1 Simulator.

Usage:
    python3 demo.py [num_cars] [seed]           interactive strategy picks
    python3 demo.py [num_cars] [seed] --random  randomized strategies (old demo behavior)
    python3 demo.py --json out.json             also export the full lap-by-lap log

Weather is rolled and revealed first, before any strategy is chosen, per spec.
"""

import argparse
import random
import sys

from engine import Compound, Driver, Weather, COLOR_PALETTE
from simulation import build_driver, export_json, run_simulation

COMPOUND_LIST = list(Compound)


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def prompt_choice(prompt: str, options: list[str], default_index: int = 0) -> int:
    while True:
        print(prompt)
        for i, opt in enumerate(options, start=1):
            marker = " (default)" if i - 1 == default_index else ""
            print(f"  {i}. {opt}{marker}")
        raw = input(f"Choose 1-{len(options)} [{default_index + 1}]: ").strip()
        if raw == "":
            return default_index
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("Invalid choice, try again.\n")


def prompt_int(prompt: str, low: int, high: int, default: int) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if raw == "":
            return default
        if raw.lstrip("-").isdigit() and low <= int(raw) <= high:
            return int(raw)
        print(f"Enter a number between {low} and {high}.")


def prompt_text(prompt: str, default: str) -> str:
    raw = input(f"{prompt} [{default}]: ").strip()
    return raw or default


# ---------------------------------------------------------------------------
# Driver setup
# ---------------------------------------------------------------------------

def make_random_drivers(num_cars: int, rng: random.Random) -> list[Driver]:
    drivers = []
    for i in range(num_cars):
        name = f"Driver {i + 1}"
        color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
        aggression = rng.randint(1, 5)
        num_stops = rng.choice([1, 2])
        quali_compound = rng.choice(COMPOUND_LIST)
        available = [c for c in COMPOUND_LIST if c != quali_compound]
        race_compounds = [rng.choice(available) for _ in range(num_stops + 1)]
        drivers.append(build_driver(name, color, aggression, num_stops, quali_compound, race_compounds))
    return drivers


def make_interactive_drivers(num_cars: int, rng: random.Random) -> list[Driver]:
    drivers = []
    for i in range(num_cars):
        print(f"\n--- Car {i + 1} of {num_cars} ---")
        name = prompt_text("Driver/team name", f"Driver {i + 1}")
        color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
        print(f"Assigned color: {color}")

        aggression = prompt_int("Aggression (1=Conservative .. 5=Aggressive)", 1, 5, 3)

        stops_idx = prompt_choice("Pit strategy:", ["1-stop", "2-stop"], default_index=0)
        num_stops = 1 if stops_idx == 0 else 2

        q_idx = prompt_choice("Qualifying tire compound (this one is locked out of the race):",
                               [c.value for c in COMPOUND_LIST], default_index=1)
        quali_compound = COMPOUND_LIST[q_idx]

        available = [c for c in COMPOUND_LIST if c != quali_compound]
        race_compounds = []
        for stint in range(num_stops + 1):
            idx = prompt_choice(f"Race stint {stint + 1} tire compound:",
                                 [c.value for c in available], default_index=0)
            race_compounds.append(available[idx])

        drivers.append(build_driver(name, color, aggression, num_stops, quali_compound, race_compounds))
    return drivers


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_weather(weather: Weather):
    print("\n=== WEATHER ===")
    print(f"Track: {weather.track_state.value}   Temperature: {weather.temperature.value}")
    print("(track state can shift lap-to-lap during the race -- plan accordingly)\n")


def print_weather_changes(sim_result: dict):
    changes = [e for e in sim_result["race"]["events"] if e["type"] == "WEATHER"]
    if not changes:
        return
    print("=== WEATHER CHANGES ===")
    for e in changes:
        print(f"Lap {e['lap']:<3} {e['detail']}")
    print()


def print_quali_results(ordered: list[Driver]):
    print("=== QUALIFYING RESULTS ===")
    print(f"{'Pos':<4}{'Driver':<14}{'Color':<10}{'Tire':<8}{'Time':<8}{'Note'}")
    for d in ordered:
        note = "MISTAKE" if getattr(d, "_quali_mistake", False) else ""
        print(f"{d.grid_position:<4}{d.name:<14}{d.color:<10}{d.quali_compound.value:<8}{d.quali_time:<8.3f}{note}")
    print()


def print_race_results(classified: list[Driver]):
    print("=== RACE RESULTS (this order = DRAFT ORDER) ===")
    print(f"{'Pick':<5}{'Driver':<14}{'Color':<10}{'Result':<14}{'Total Time / Info'}")
    leader_time = None
    for i, d in enumerate(classified, start=1):
        if d.dnf:
            info = f"DNF lap {d.dnf_lap} ({d.dnf_reason})"
            result = "DNF"
        else:
            if leader_time is None:
                leader_time = d.total_time
                info = f"{d.total_time:.2f}s"
            else:
                info = f"+{d.total_time - leader_time:.2f}s"
            result = "Classified"
        print(f"{i:<5}{d.name:<14}{d.color:<10}{result:<14}{info}")
    print()


def print_strategy_summary(drivers: list[Driver]):
    print("=== STRATEGIES USED ===")
    for d in drivers:
        compounds = " -> ".join(c.value for c in d.race_compounds)
        print(f"{d.name:<14} quali={d.quali_compound.value:<7} aggression={d.aggression}  "
              f"stops={d.num_stops}  race tires: {compounds}  pit laps: {d.pit_laps}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fantasy Draft F1 Simulator")
    parser.add_argument("cars", nargs="?", type=int, default=None, help="number of cars / draft slots")
    parser.add_argument("seed", nargs="?", type=int, default=None, help="rng seed, for a reproducible sim")
    parser.add_argument("--random", action="store_true", help="randomize all strategies instead of prompting")
    parser.add_argument("--json", metavar="PATH", help="export the full lap-by-lap log as JSON to PATH")
    args = parser.parse_args()

    # Fall back to randomized strategies if we can't prompt (no cars given
    # and stdin isn't an interactive terminal) so the tool never hangs.
    interactive_ok = sys.stdin.isatty()
    use_random = args.random or not interactive_ok

    num_cars = args.cars
    if num_cars is None:
        num_cars = prompt_int("Number of cars / draft slots", 2, len(COLOR_PALETTE), 8) if interactive_ok else 8

    rng = random.Random(args.seed)

    # Weather is rolled and revealed before anyone picks a strategy.
    weather = Weather.roll(rng)
    print_weather(weather)

    drivers = make_random_drivers(num_cars, rng) if use_random else make_interactive_drivers(num_cars, rng)

    sim_result = run_simulation(drivers, weather, rng)

    quali_order = sorted(drivers, key=lambda d: d.grid_position)
    print_quali_results(quali_order)

    id_to_driver = dict(enumerate(drivers))
    classified = (
        [id_to_driver[e["driver_id"]] for e in sim_result["result"]["classified"]]
        + [id_to_driver[e["driver_id"]] for e in sim_result["result"]["dnfs"]]
    )

    print_strategy_summary(drivers)
    print_weather_changes(sim_result)
    print_race_results(classified)

    print("=== DRAFT ORDER ===")
    for pick, did in enumerate(sim_result["result"]["draft_order"], start=1):
        print(f"Pick {pick}: {id_to_driver[did].name}")
    print()

    if args.json:
        export_json(sim_result, args.json)
        print(f"Full lap-by-lap log written to {args.json}")
        print("Open viz/index.html and load that file to watch the replay.\n")


if __name__ == "__main__":
    main()
