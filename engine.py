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
    """A race track layout. `shape` is a compact description the
    visualization renders from: a list of `vertices` (x, y, corner
    roundedness 0-1) in raw drawing-space units. Unlike a polar
    radius(theta) description, these are explicit waypoints -- not
    constrained to one point per angle from a shared center -- so a track
    can genuinely fold back on itself: a real hairpin (the path curves
    ~180 degrees and heads back roughly the way it came) or an esses
    complex, instead of only ever bulging in and out of a rounded blob.
    The renderer connects consecutive vertices with straight lines and
    rounds each corner with a circular fillet sized by that vertex's
    `corner` value -- the same straight-plus-corner vocabulary a real
    circuit blueprint is drawn with. Vertex positions and corner values
    are hand-tuned (see tools/verify_circuit_shapes.py) so every fillet's
    radius clears half the rendered track width -- otherwise the track's
    two edges cross over each other at that corner, same as a real road
    can't have a curve tighter than its own width. A small config like
    this is cheap to keep the Python engine and both JS renderers
    (viz/index.html, webapp) drawing the exact same circuit from what
    travels in the JSON log.

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


def _track_shape(points: list[tuple[float, float, float]]) -> dict:
    """points: list of (x, y, corner_roundedness) waypoints. See Circuit.shape
    docstring above."""
    return {"vertices": [{"x": x, "y": y, "corner": c} for x, y, c in points]}


CIRCUITS = [
    # Sable Bay is a direct trace of a real street-circuit blueprint (Baku):
    # long start/finish straight, a fast-medium corner complex, a run of
    # esses, a slow chicane, a needle-thin hairpin that folds back on
    # itself, a hook-shaped castle section, and a run back to the line.
    # The other four circuits are that same verified shape rotated,
    # mirrored, and rescaled (angle- and distance-preserving, so every
    # corner's fillet radius clearing half the track width carries over
    # exactly -- see tools/verify_circuit_shapes.py) -- giving each a
    # distinct silhouette and orientation while keeping the same DNA of
    # genuine hairpins and esses instead of a smooth oval.
    Circuit(
        id="sable-bay", name="Sable Bay Circuit",
        description="Long straights and sweeping bends -- low deg, easy to pass, punishing at speed.",
        deg_multiplier=0.85, overtake_difficulty=0.65, crash_rate_multiplier=1.1, pit_loss_bonus=0.0,
        shape=_track_shape([
            (400, -180, 0.47), (380, -280, 0.87), (280, -260, 0.5), (236.2, -140.1, 0.6),
            (176.8, -114.7, 0.5), (144.4, -67.2, 0.5), (93.1, 4.1, 0.82), (-56.5, -62.3, 0.35),
            (-138.4, -55.4, 1), (-158.6, 80.5, 1), (-205.5, 158.3, 1), (-303.8, 95.9, 0.69),
            (-414.5, 225.5, 0.53), (-526.3, 183.9, 0.71), (-600, 350, 0.8), (-150, 325, 0.4),
            (-117.5, 13.3, 0.93), (14.8, 105.6, 0.58), (58, 108.3, 0.3), (105.5, 117.5, 0.98),
        ]),
    ),
    Circuit(
        id="verdant-ridge", name="Verdant Ridge Hillclimb",
        description="Constant direction changes -- technical, hard on tires, very hard to pass.",
        deg_multiplier=1.25, overtake_difficulty=1.3, crash_rate_multiplier=1.0, pit_loss_bonus=0.2,
        shape=_track_shape([
            (147.6, -328, 0.47), (229.6, -311.6, 0.87), (213.2, -229.6, 0.5), (114.88, -193.68, 0.6),
            (94.05, -144.98, 0.5), (55.1, -118.41, 0.5), (-3.36, -76.34, 0.82), (51.09, 46.33, 0.35),
            (45.43, 113.49, 1), (-66.01, 130.05, 1), (-129.81, 168.51, 1), (-78.64, 249.12, 0.69),
            (-184.91, 339.89, 0.53), (-150.8, 431.57, 0.71), (-287, 492, 0.8), (-266.5, 123, 0.4),
            (-10.91, 96.35, 0.93), (-86.59, -12.14, 0.58), (-88.81, -47.56, 0.3), (-96.35, -86.51, 0.98),
        ]),
    ),
    Circuit(
        id="iron-harbor", name="Iron Harbor Street Circuit",
        description="Tight street course, walls close in -- brutal on mistakes, brutal to overtake.",
        deg_multiplier=1.0, overtake_difficulty=1.6, crash_rate_multiplier=1.5, pit_loss_bonus=0.4,
        shape=_track_shape([
            (-288, 129.6, 0.47), (-273.6, 201.6, 0.87), (-201.6, 187.2, 0.5), (-170.06, 100.87, 0.6),
            (-127.3, 82.58, 0.5), (-103.97, 48.38, 0.5), (-67.03, -2.95, 0.82), (40.68, 44.86, 0.35),
            (99.65, 39.89, 1), (114.19, -57.96, 1), (147.96, -113.98, 1), (218.74, -69.05, 0.69),
            (298.44, -162.36, 0.53), (378.94, -132.41, 0.71), (432, -252, 0.8), (108, -234, 0.4),
            (84.6, -9.58, 0.93), (-10.66, -76.03, 0.58), (-41.76, -77.98, 0.3), (-75.96, -84.6, 0.98),
        ]),
    ),
    Circuit(
        id="sunspire", name="Sunspire Speedway",
        description="Elongated high-speed bowl with a couple of chicanes -- fast, tires take a beating.",
        deg_multiplier=1.1, overtake_difficulty=0.75, crash_rate_multiplier=1.05, pit_loss_bonus=0.0,
        shape=_track_shape([
            (61.77, -456.4, 0.47), (-38.44, -494.12, 0.87), (-76.16, -393.91, 0.5), (6.35, -288.29, 0.6),
            (-4.01, -221.25, 0.5), (20.32, -166, 0.5), (55.36, -80.68, 0.82), (-86.89, 15.74, 0.35),
            (-126.23, 92.56, 1), (-16.36, 186.04, 1), (26.92, 271.04, 1), (-83.28, 323.97, 0.69),
            (-29.28, 494.6, 0.53), (-128.44, 571.11, 0.71), (-21.37, 729.04, 0.8), (206.29, 314.17, 0.4),
            (-53.41, 112.09, 0.93), (102.32, 45.46, 0.58), (128.72, 8.47, 0.3), (163.3, -28.75, 0.98),
        ]),
    ),
    Circuit(
        id="northgate", name="Northgate Endurance Circuit",
        description="A balanced, flowing all-rounder -- no extreme strengths or weaknesses.",
        deg_multiplier=1.0, overtake_difficulty=1.0, crash_rate_multiplier=1.0, pit_loss_bonus=0.1,
        shape=_track_shape([
            (-163.57, -368.91, 0.47), (-88.47, -425.15, 0.87), (-32.23, -350.05, 0.5), (-75.83, -241.01, 0.6),
            (-50.72, -187.13, 0.5), (-57.69, -134.7, 0.5), (-66.31, -54.35, 0.82), (76.96, -8, 0.35),
            (128.84, 47.01, 1), (59.25, 152.53, 1), (43.62, 234.63, 1), (149.28, 252.22, 0.69),
            (145.57, 408.99, 0.53), (247.71, 449.12, 0.71), (196.24, 608.18, 0.8), (-96.75, 314.78, 0.4),
            (72.34, 81.25, 0.93), (-74.97, 63.27, 0.58), (-106.23, 38.6, 0.3), (-144.42, 15.74, 0.98),
        ]),
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
