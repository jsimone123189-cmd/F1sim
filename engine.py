"""
Fantasy Draft F1 Simulator - core engine

Two stages:
  1. Qualifying (1 lap) -> sets starting grid, locks the used tire compound
  2. Race (20 laps)      -> final order becomes the draft order

Run `demo.py` for an interactive (or randomized) playtest session.
"""

import random
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Config constants (all tunable)
# ---------------------------------------------------------------------------

BASE_LAP_TIME = 15.0
NUM_LAPS = 20
GRIP_PENALTY_FACTOR = 2.0          # max seconds lost to zero grip (softened from 3.0 - see README)
QUALI_RANDOM_VARIANCE = 0.15       # +/- seconds
RACE_RANDOM_VARIANCE = 0.25        # +/- seconds per lap
PIT_STOP_BASE_RANGE = (2.5, 4.5)   # seconds
PIT_STOP_SLOW_CHANCE = 0.05
PIT_STOP_SLOW_EXTRA_RANGE = (2.0, 5.0)
BASE_MECHANICAL_RATE = 0.001       # per lap
TRAFFIC_GAP_THRESHOLD = 0.5        # seconds - "close" to car ahead
TRAFFIC_PENALTY = 0.08             # seconds lost sitting in dirty air
DRAFT_ORDER_REVERSED = False       # flip to True to make P1 finisher = last pick
WEATHER_EVOLVE_CHANCE = 0.03       # per-lap chance the track state shifts one step

# Position/proximity risk model: how a driver's own aggression and nearby
# rivals' aggression combine into incident risk (see race.py::crowdedness).
NEIGHBOR_DANGER_WINDOW = 1.2        # seconds gap within which a rival counts as "nearby"
AGGRESSION_RISK_RATE = 0.0005       # your own aggression's base risk contribution
AMBIENT_DANGER_RATE = 0.0008        # risk from being near aggressive rivals, independent of your own aggression


class Compound(Enum):
    SOFT = "Soft"
    MEDIUM = "Medium"
    HARD = "Hard"
    WET = "Wet"


class TrackState(Enum):
    DRY = "Dry"
    DAMP = "Damp"
    WET = "Wet"


# Adjacency order for lap-to-lap weather evolution: a track state can only
# step to a neighbor in this list (DRY <-> DAMP <-> WET), never jump straight
# from DRY to WET.
TRACK_STATE_ORDER = [TrackState.DRY, TrackState.DAMP, TrackState.WET]


class Temperature(Enum):
    COLD = "Cold"
    MILD = "Mild"
    HOT = "Hot"


@dataclass
class TireSpec:
    base_pace: float
    degradation_rate: float
    grip_dry: float
    grip_wet: float
    heat_sensitivity: float


TIRES = {
    Compound.SOFT:   TireSpec(base_pace=-0.6, degradation_rate=0.050, grip_dry=0.95, grip_wet=0.40, heat_sensitivity=1.5),
    Compound.MEDIUM: TireSpec(base_pace=0.0,  degradation_rate=0.030, grip_dry=0.85, grip_wet=0.55, heat_sensitivity=1.2),
    Compound.HARD:   TireSpec(base_pace=0.5,  degradation_rate=0.015, grip_dry=0.75, grip_wet=0.60, heat_sensitivity=0.8),
    Compound.WET:    TireSpec(base_pace=1.0,  degradation_rate=0.030, grip_dry=0.50, grip_wet=0.95, heat_sensitivity=2.0),
}

# High-contrast, easy-to-distinguish palette (extend if you support more cars)
COLOR_PALETTE = [
    "Red", "Blue", "Green", "Yellow", "Orange", "Purple", "Cyan", "Magenta",
    "Lime", "Pink", "Teal", "Gold", "Navy", "Maroon", "Turquoise", "Silver",
    "Brown", "Indigo", "Coral", "Olive",
]

# Hex equivalents of COLOR_PALETTE, for the visualization layer.
COLOR_HEX = {
    "Red": "#e6194B", "Blue": "#4363d8", "Green": "#3cb44b", "Yellow": "#ffe119",
    "Orange": "#f58231", "Purple": "#911eb4", "Cyan": "#42d4f4", "Magenta": "#f032e6",
    "Lime": "#bfef45", "Pink": "#fabed4", "Teal": "#469990", "Gold": "#dcbe23",
    "Navy": "#000075", "Maroon": "#800000", "Turquoise": "#40e0d0", "Silver": "#c0c0c0",
    "Brown": "#9A6324", "Indigo": "#4b0082", "Coral": "#ff7f50", "Olive": "#808000",
}


@dataclass
class Weather:
    track_state: TrackState
    temperature: Temperature

    @staticmethod
    def roll(rng: random.Random) -> "Weather":
        track_state = rng.choices(
            [TrackState.DRY, TrackState.DAMP, TrackState.WET],
            weights=[60, 20, 20],
        )[0]
        temperature = rng.choices(
            [Temperature.COLD, Temperature.MILD, Temperature.HOT],
            weights=[25, 50, 25],
        )[0]
        return Weather(track_state, temperature)

    def effective_grip(self, tire: TireSpec) -> float:
        if self.track_state == TrackState.DRY:
            return tire.grip_dry
        if self.track_state == TrackState.WET:
            return tire.grip_wet
        # DAMP: blend
        return (tire.grip_dry + tire.grip_wet) / 2

    def heat_multiplier(self, tire: TireSpec) -> float:
        if self.temperature == Temperature.HOT:
            return tire.heat_sensitivity
        if self.temperature == Temperature.COLD:
            return 0.85
        return 1.0

    def to_dict(self) -> dict:
        return {"track_state": self.track_state.value, "temperature": self.temperature.value}

    def step(self, rng: random.Random) -> bool:
        """Small per-lap chance the track state evolves one step toward a
        neighboring state (e.g. DRY -> DAMP). Temperature stays fixed for
        the session. Returns True if the state changed this call."""
        if rng.random() >= WEATHER_EVOLVE_CHANCE:
            return False
        idx = TRACK_STATE_ORDER.index(self.track_state)
        options = [i for i in (idx - 1, idx + 1) if 0 <= i < len(TRACK_STATE_ORDER)]
        if not options:
            return False
        self.track_state = TRACK_STATE_ORDER[rng.choice(options)]
        return True


@dataclass
class Circuit:
    """A race track layout. `shape` is a compact procedural description the
    visualization renders from -- a base ellipse (rx, ry) perturbed by a sum
    of cosine harmonics (r(theta) = 1 + sum(amp * cos(k*theta + phase))) --
    rather than a hand-authored point list, so it's cheap to keep the Python
    engine and both JS renderers (viz/index.html, webapp) drawing the exact
    same circuit from the same small config that travels in the JSON log.

    The gameplay attributes give each circuit real strategic identity:
    - deg_multiplier: tire wear multiplier (twisty circuits chew tires faster)
    - overtake_difficulty: >1 harder to pass (reduces attempt/success chance,
      increases time lost stuck in traffic), <1 easier
    - crash_rate_multiplier: scales base + grip-related incident risk
      (tight street circuits are less forgiving of a mistake)
    - pit_loss_bonus: extra seconds added to every pit stop (long pit lane)
    """
    id: str
    name: str
    description: str
    deg_multiplier: float
    overtake_difficulty: float
    crash_rate_multiplier: float
    pit_loss_bonus: float
    shape: dict

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "deg_multiplier": self.deg_multiplier, "overtake_difficulty": self.overtake_difficulty,
            "crash_rate_multiplier": self.crash_rate_multiplier, "pit_loss_bonus": self.pit_loss_bonus,
            "shape": self.shape,
        }


CIRCUITS = [
    Circuit(
        id="sable-bay", name="Sable Bay Circuit",
        description="Long straights and sweeping bends -- low deg, easy to pass, punishing at speed.",
        deg_multiplier=0.85, overtake_difficulty=0.65, crash_rate_multiplier=1.1, pit_loss_bonus=0.0,
        shape={"rx": 260, "ry": 150, "terms": [{"k": 2, "amp": 0.10, "phase": 0.3}, {"k": 3, "amp": 0.06, "phase": 1.8}]},
    ),
    Circuit(
        id="verdant-ridge", name="Verdant Ridge Hillclimb",
        description="Constant direction changes -- technical, hard on tires, very hard to pass.",
        deg_multiplier=1.25, overtake_difficulty=1.3, crash_rate_multiplier=1.0, pit_loss_bonus=0.2,
        shape={"rx": 190, "ry": 160, "terms": [{"k": 5, "amp": 0.14, "phase": 0.4}, {"k": 7, "amp": 0.08, "phase": 2.1}, {"k": 2, "amp": 0.05, "phase": 1.0}]},
    ),
    Circuit(
        id="iron-harbor", name="Iron Harbor Street Circuit",
        description="Tight street course, walls close in -- brutal on mistakes, brutal to overtake.",
        deg_multiplier=1.0, overtake_difficulty=1.6, crash_rate_multiplier=1.5, pit_loss_bonus=0.4,
        shape={"rx": 150, "ry": 120, "terms": [{"k": 4, "amp": 0.16, "phase": 0.9}, {"k": 6, "amp": 0.10, "phase": 2.5}]},
    ),
    Circuit(
        id="sunspire", name="Sunspire Speedway",
        description="Elongated high-speed bowl with a couple of chicanes -- fast, tires take a beating.",
        deg_multiplier=1.1, overtake_difficulty=0.75, crash_rate_multiplier=1.05, pit_loss_bonus=0.0,
        shape={"rx": 280, "ry": 110, "terms": [{"k": 2, "amp": 0.06, "phase": 0.0}, {"k": 8, "amp": 0.03, "phase": 1.2}]},
    ),
    Circuit(
        id="northgate", name="Northgate Endurance Circuit",
        description="A balanced, flowing all-rounder -- no extreme strengths or weaknesses.",
        deg_multiplier=1.0, overtake_difficulty=1.0, crash_rate_multiplier=1.0, pit_loss_bonus=0.1,
        shape={"rx": 230, "ry": 170, "terms": [{"k": 3, "amp": 0.09, "phase": 0.6}, {"k": 5, "amp": 0.05, "phase": 2.0}]},
    ),
]


def roll_circuit(rng: random.Random) -> Circuit:
    return rng.choice(CIRCUITS)


@dataclass
class Driver:
    name: str
    color: str
    aggression: int  # 1-5
    num_stops: int    # 1 or 2, chosen by user
    race_compounds: list = field(default_factory=list)  # compounds for each stint, len = num_stops+1

    # -- state filled in during sim --
    quali_compound: Compound = None
    quali_time: float = None
    grid_position: int = None

    dnf: bool = False
    dnf_lap: int = None
    dnf_reason: str = None
    total_time: float = 0.0
    current_stint_index: int = 0
    laps_on_current_tire: int = 0
    pit_laps: list = field(default_factory=list)  # which laps (1-indexed) include a stop
    lap_log: list = field(default_factory=list)

    def available_compounds_for_race(self):
        """All compounds except the one used in qualifying."""
        return [c for c in Compound if c != self.quali_compound]
