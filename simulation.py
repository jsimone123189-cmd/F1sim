"""
Orchestration + JSON export layer.

Runs qualifying and the race for a set of already-configured Driver objects
and assembles the full lap-by-lap log described in the build spec (§10):
position, gap, tire, stint lap, lap time, pit stops and incidents per car
per lap, plus the final result table and draft order.
"""

import json
import random

from engine import Compound, Driver, Weather, COLOR_HEX, DRAFT_ORDER_REVERSED, NUM_LAPS
from qualifying import run_qualifying
from race import run_race


def build_driver(name: str, color: str, aggression: int, num_stops: int,
                  quali_compound: Compound, race_compounds: list) -> Driver:
    d = Driver(name=name, color=color, aggression=aggression, num_stops=num_stops)
    d.quali_compound = quali_compound
    d.race_compounds = race_compounds
    return d


def run_simulation(drivers: list[Driver], weather: Weather, rng: random.Random) -> dict:
    """Run qualifying + race for the given drivers/weather and return a
    JSON-serializable dict containing the full lap-by-lap log (spec §10).
    """
    driver_ids = {id(d): i for i, d in enumerate(drivers)}

    quali_order = run_qualifying(drivers, weather, rng)
    quali_results = [
        {
            "driver_id": driver_ids[id(d)],
            "grid_position": d.grid_position,
            "time": round(d.quali_time, 3),
            "compound": d.quali_compound.value,
            "mistake": getattr(d, "_quali_mistake", False),
        }
        for d in quali_order
    ]

    pre_race_weather = weather.to_dict()
    classified, weather_timeline = run_race(drivers, weather, rng)

    laps = []
    for lap in range(1, NUM_LAPS + 1):
        cars = []
        for d in drivers:
            entry = next((e for e in d.lap_log if e["lap"] == lap), None)
            if entry is None:
                continue
            cars.append({"driver_id": driver_ids[id(d)], **entry})
        laps.append({"lap": lap, "cars": cars})

    events = []
    prev_state = pre_race_weather["track_state"]
    for entry in weather_timeline:
        if entry["track_state"] != prev_state:
            events.append({
                "lap": entry["lap"], "driver_id": None, "type": "WEATHER",
                "detail": f"Track now {entry['track_state']}",
            })
            prev_state = entry["track_state"]
    for d in drivers:
        for e in d.lap_log:
            did = driver_ids[id(d)]
            if e.get("event") == "DNF":
                events.append({"lap": e["lap"], "driver_id": did, "type": "DNF", "detail": e["reason"]})
            if e.get("pit"):
                slow = bool(e.get("pit_loss") and e["pit_loss"] > 5)
                events.append({
                    "lap": e["lap"], "driver_id": did,
                    "type": "PIT",
                    "detail": f"{'Slow stop' if slow else 'Pit stop'} (+{e['pit_loss']}s)",
                })
            if e.get("overtake"):
                events.append({
                    "lap": e["lap"], "driver_id": did,
                    "type": e["overtake"].upper(),
                    "detail": e["overtake"].replace("_", " ").title(),
                })
    events.sort(key=lambda e: e["lap"])

    leader_time = classified[0].total_time if classified and not classified[0].dnf else None
    result_classified = []
    result_dnfs = []
    for i, d in enumerate(classified, start=1):
        did = driver_ids[id(d)]
        if d.dnf:
            result_dnfs.append({"position": i, "driver_id": did, "dnf_lap": d.dnf_lap, "reason": d.dnf_reason})
        else:
            gap = None if leader_time is None else round(d.total_time - leader_time, 3)
            result_classified.append({"position": i, "driver_id": did, "total_time": round(d.total_time, 3), "gap": gap})

    draft_pool = list(classified)
    if DRAFT_ORDER_REVERSED:
        draft_pool = draft_pool[::-1]
    draft_order = [driver_ids[id(d)] for d in draft_pool]

    return {
        "num_laps": NUM_LAPS,
        "weather": pre_race_weather,
        "weather_timeline": weather_timeline,
        "drivers": [
            {
                "driver_id": driver_ids[id(d)],
                "name": d.name,
                "color": d.color,
                "color_hex": COLOR_HEX.get(d.color, "#888888"),
                "aggression": d.aggression,
                "num_stops": d.num_stops,
                "quali_compound": d.quali_compound.value,
                "race_compounds": [c.value for c in d.race_compounds],
                "pit_laps": d.pit_laps,
            }
            for d in drivers
        ],
        "qualifying": {"results": quali_results},
        "race": {"laps": laps, "events": events},
        "result": {
            "classified": result_classified,
            "dnfs": result_dnfs,
            "draft_order": draft_order,
            "draft_order_reversed": DRAFT_ORDER_REVERSED,
        },
    }


def export_json(sim_result: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(sim_result, f, indent=2)
