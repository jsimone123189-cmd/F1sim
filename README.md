# Fantasy Draft F1 Simulator

A two-stage race simulation (Qualifying &rarr; Race) that turns a randomized,
tire-strategy-driven F1-style race into a fantasy draft order. Each
participant is a "car" with a distinct color; the final race classification
becomes the draft pick order (P1 = pick 1, by default).

Full design spec: see the tire/weather/aggression/crash model this engine
implements in the original build spec handed off with this prototype.

## What's here

- `engine.py` &mdash; data model + tunable config constants (tires, weather, drivers).
- `qualifying.py` &mdash; 1-lap qualifying simulation; sets the grid and locks each
  driver's qualifying tire compound out of the race.
- `race.py` &mdash; 20-lap race simulation: degradation, pit stops, traffic,
  overtakes, incidents, and per-lap position/gap tracking.
- `simulation.py` &mdash; orchestrates qualifying + race and assembles the full
  lap-by-lap JSON log described in the spec (§10).
- `demo.py` &mdash; interactive CLI: reveals weather, then prompts each driver for
  aggression / pit strategy / tire compounds (or randomizes with `--random`).
- `viz/index.html` &mdash; standalone HTML/Canvas replay viewer: a **New Draft**
  wizard for multiplayer strategy entry (runs an in-browser JS port of the
  engine, no server needed), a Race tab (animated top-down cars, live running
  order, event feed, draft board), and a Qualifying tab. Also loads a JSON
  log exported via `--json`.
- `tests/` &mdash; pytest suite locking in determinism and the balance numbers
  called out below.

## Running it

```
pip install -r requirements.txt   # only needed to run the tests

python3 demo.py                        # interactive: prompts for each driver's strategy
python3 demo.py 10 42                  # 10 cars, fixed seed, interactive prompts
python3 demo.py 8 42 --random          # randomized strategies (old prototype behavior)
python3 demo.py 8 42 --json race.json  # also export the full lap-by-lap log
```

Weather is rolled and printed **before** any strategy prompts, matching the
spec ("reveal it to all users before they pick anything"). If stdin isn't an
interactive terminal, `demo.py` automatically falls back to randomized
strategies instead of hanging on a prompt.

### Running a multiplayer draft (no Python needed)

Open `viz/index.html` in a browser (a copy is also published as an Artifact
in this conversation) and click **New Draft**:

1. Set the number of drafters (and an optional seed for a reproducible sim),
   then reveal the weather &mdash; shown to everyone before anyone picks.
2. Pass the device around: each drafter gets a card to set their name,
   aggression, pit strategy, qualifying tire, and race tires (the qualifying
   compound is locked out of the race-tire choices automatically).
3. After the last driver, it simulates qualifying + the race right in the
   browser and drops you into the Race tab to watch the replay and see the
   draft order lock in.

This runs a JS port of the same engine (`engine.py`/`qualifying.py`/`race.py`)
embedded in the page &mdash; see "In-browser simulation engine" in
`viz/index.html`'s script for details. It's kept formula-for-formula in sync
with the Python source of truth, but uses its own seeded PRNG, so a given
seed does **not** reproduce the same race between the CLI and the browser
(each is independently reproducible).

### Watching a replay from the CLI

The page also loads with a bundled sample race so it's watchable immediately
without going through the wizard; click **Load log** to replay a JSON file
exported via `python3 demo.py --json out.json` instead.

### Running the tests

```
python3 -m pytest tests/ -v
```

## Design notes / changes from the original prototype

This bundle started as a math/balance prototype (randomized strategies, text
output only). On top of that:

1. **Real user input.** `demo.py` now prompts for each driver's name,
   aggression, pit strategy, and tire compounds (qualifying tire first, which
   locks that compound out of the race per §5) instead of randomizing
   everything.
2. **JSON lap-log export** (`simulation.py`, spec §10): position, gap-to-leader,
   tire, stint lap, lap time, pit stops, and incidents for every car on every
   lap, plus qualifying results, the final result table, and the draft order.
3. **HTML/Canvas visualization** (`viz/index.html`) consuming that log, with
   cars drawn as small top-down car silhouettes (body, wings, cockpit,
   oriented to the direction of travel) rather than plain dots.
4. **Balance-locking tests** (`tests/test_balance.py`) so future constant
   tweaks don't silently drift the tuning.
5. **In-browser multiplayer draft wizard**: a JS port of the whole engine
   embedded in `viz/index.html` so a group can enter strategies on one shared
   screen and simulate the race with no server or Python install &mdash; see
   "Running a multiplayer draft" above.
6. **Lap-to-lap weather evolution** (the spec's stretch goal): track state
   can shift one step (Dry&harr;Damp&harr;Wet) each lap with a small
   probability (`WEATHER_EVOLVE_CHANCE`); the JSON log's `weather_timeline`
   records what was in effect on every lap, and the replay's weather chip and
   event feed update live as it changes.

It also addresses the three "known things worth revisiting" from the
prototype's own README:

- **Wet-tire-on-dry-track penalty softened**: `GRIP_PENALTY_FACTOR` reduced
  from `3.0` to `2.0` in `engine.py`.
- **DNF reason weighting**: incidents caused by traffic or an overtake
  attempt now skew toward *Collision* / *Spin*; base-rate incidents (nobody
  nearby) skew toward *Mechanical failure*, instead of picking uniformly at
  random (`race.py::roll_dnf_reason`).
- **Real per-lap running order**: the race loop now stamps `position` and
  `gap_to_leader` onto each car's lap log entry after every lap, and the
  visualization's cars move continuously between those checkpoints &mdash;
  driven by actual simulated gaps, not just lap percentage.

## Tuning numbers (locked by tests)

- DNF rate lands around **~9% per driver** over a large sample of races
  (measured ~8.7% over 200 x 8-car sims at time of writing); softening the
  grip penalty above doesn't change this, since incident probability is
  independent of `GRIP_PENALTY_FACTOR`.
- `draft_order_reversed` is a single config constant in `engine.py`
  (`DRAFT_ORDER_REVERSED`) if you want pick 1 to go to the *last* finisher
  instead.

## Known limitations / next steps

- Overtaking is still time-based (gap + probability roll), not true
  on-track position tracking mid-lap &mdash; fine for determining the draft
  order and reasonable for the visualization (which now interpolates
  between real per-lap checkpoints), but a full on-track passing model would
  need finer-grained position data than the sim currently produces.
- Temperature is still static for the session &mdash; only track state
  (Dry/Damp/Wet) evolves lap-to-lap; picks are still made against the
  pre-race reveal, so a mid-race shift is a risk drafters take on knowingly,
  not something they can plan around exactly.
- The in-browser wizard's JS engine uses its own seeded PRNG (mulberry32),
  not Python's Mersenne Twister, so a seed only reproduces a race within
  whichever implementation ran it, not across the CLI and the browser.
- The wizard is designed for one shared screen passed around a room (a
  "snake draft night" flow); there's no over-the-network multiplayer
  (separate devices, live sync) &mdash; that would need a small backend.
