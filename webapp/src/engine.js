// Server-side simulation engine for League Rooms.
//
// A straight port of engine.py / qualifying.py / race.py, kept
// formula-for-formula in sync with the Python source of truth (see
// ../../engine.py, ../../qualifying.py, ../../race.py) and with the
// identical JS port embedded in ../../viz/index.html for the single-device
// wizard. This copy runs authoritatively inside the LeagueRoom Durable
// Object so the simulation result is the same for every participant,
// computed once server-side rather than trusted from a client.
//
// Unlike the single-device flow, a league room runs qualifying and the race
// as two separate phases, potentially far apart in wall-clock time, so each
// phase gets its own independently seeded RNG rather than one continuous
// stream threaded through both.

export const CFG = {
  BASE_LAP_TIME: 15.0,
  NUM_LAPS: 20,
  GRIP_PENALTY_FACTOR: 2.0,
  QUALI_RANDOM_VARIANCE: 0.15,
  RACE_RANDOM_VARIANCE: 0.25,
  PIT_STOP_BASE_MIN: 2.5, PIT_STOP_BASE_MAX: 4.5,
  PIT_STOP_SLOW_CHANCE: 0.05,
  PIT_STOP_SLOW_EXTRA_MIN: 2.0, PIT_STOP_SLOW_EXTRA_MAX: 5.0,
  BASE_MECHANICAL_RATE: 0.001,
  TRAFFIC_GAP_THRESHOLD: 0.5,
  TRAFFIC_PENALTY: 0.08,
  WEATHER_EVOLVE_CHANCE: 0.03,
  DRAFT_ORDER_REVERSED: false,
  NEIGHBOR_DANGER_WINDOW: 1.2,
  AGGRESSION_RISK_RATE: 0.0005,
  AMBIENT_DANGER_RATE: 0.0008
};

export const COMPOUNDS = ['Soft', 'Medium', 'Hard', 'Wet'];
export const TIRES = {
  Soft:   { base_pace:-0.6, deg:0.050, grip_dry:0.95, grip_wet:0.40, heat:1.5 },
  Medium: { base_pace: 0.0, deg:0.030, grip_dry:0.85, grip_wet:0.55, heat:1.2 },
  Hard:   { base_pace: 0.5, deg:0.015, grip_dry:0.75, grip_wet:0.60, heat:0.8 },
  Wet:    { base_pace: 1.0, deg:0.030, grip_dry:0.50, grip_wet:0.95, heat:2.0 }
};
export const TRACK_STATES = ['Dry', 'Damp', 'Wet'];
export const TEMPS = ['Cold', 'Mild', 'Hot'];

// Circuit layouts -- kept in exact sync with engine.py's CIRCUITS and the
// copy embedded in ../../viz/index.html. `shape` drives the renderer: an
// explicit list of (x, y, corner roundedness 0-1) waypoints -- not one
// point per angle from a shared center, so the track can genuinely fold
// back on itself (real hairpins, esses) -- connected with straights and
// rounded corners, the same vocabulary a real circuit blueprint is drawn
// with; the rest are gameplay attributes giving each circuit real
// strategic identity.
function trackShape(points){
  return { vertices: points.map(function(p){ return { x: p[0], y: p[1], corner: p[2] }; }) };
}
// Sable Bay is a direct trace of a real street-circuit blueprint (Baku):
// long start/finish straight, a fast-medium corner complex, a run of
// esses, a slow chicane, a needle-thin hairpin that folds back on itself,
// a hook-shaped castle section, and a run back to the line. The other
// four circuits are that same verified shape rotated, mirrored, and
// rescaled (angle- and distance-preserving, so every corner's fillet
// radius clearing half the track width carries over exactly) -- giving
// each a distinct silhouette while keeping the same DNA of genuine
// hairpins and esses instead of a smooth oval.
export const CIRCUITS = [
  { id: 'sable-bay', name: 'Sable Bay Circuit',
    description: 'Long straights and sweeping bends -- low deg, easy to pass, punishing at speed.',
    deg_multiplier: 0.85, overtake_difficulty: 0.65, crash_rate_multiplier: 1.1, pit_loss_bonus: 0.0,
    shape: trackShape([
      [400, -180, 0.47], [380, -280, 0.87], [280, -260, 0.5], [236.2, -140.1, 0.6],
      [176.8, -114.7, 0.5], [144.4, -67.2, 0.5], [93.1, 4.1, 0.82], [-56.5, -62.3, 0.35],
      [-138.4, -55.4, 1], [-158.6, 80.5, 1], [-205.5, 158.3, 1], [-303.8, 95.9, 0.69],
      [-414.5, 225.5, 0.53], [-526.3, 183.9, 0.71], [-600, 350, 0.8], [-150, 325, 0.4],
      [-117.5, 13.3, 0.93], [14.8, 105.6, 0.58], [58, 108.3, 0.3], [105.5, 117.5, 0.98]
    ]) },
  { id: 'verdant-ridge', name: 'Verdant Ridge Hillclimb',
    description: 'Constant direction changes -- technical, hard on tires, very hard to pass.',
    deg_multiplier: 1.25, overtake_difficulty: 1.3, crash_rate_multiplier: 1.0, pit_loss_bonus: 0.2,
    shape: trackShape([
      [147.6, -328, 0.47], [229.6, -311.6, 0.87], [213.2, -229.6, 0.5], [114.88, -193.68, 0.6],
      [94.05, -144.98, 0.5], [55.1, -118.41, 0.5], [-3.36, -76.34, 0.82], [51.09, 46.33, 0.35],
      [45.43, 113.49, 1], [-66.01, 130.05, 1], [-129.81, 168.51, 1], [-78.64, 249.12, 0.69],
      [-184.91, 339.89, 0.53], [-150.8, 431.57, 0.71], [-287, 492, 0.8], [-266.5, 123, 0.4],
      [-10.91, 96.35, 0.93], [-86.59, -12.14, 0.58], [-88.81, -47.56, 0.3], [-96.35, -86.51, 0.98]
    ]) },
  { id: 'iron-harbor', name: 'Iron Harbor Street Circuit',
    description: 'Tight street course, walls close in -- brutal on mistakes, brutal to overtake.',
    deg_multiplier: 1.0, overtake_difficulty: 1.6, crash_rate_multiplier: 1.5, pit_loss_bonus: 0.4,
    shape: trackShape([
      [-288, 129.6, 0.47], [-273.6, 201.6, 0.87], [-201.6, 187.2, 0.5], [-170.06, 100.87, 0.6],
      [-127.3, 82.58, 0.5], [-103.97, 48.38, 0.5], [-67.03, -2.95, 0.82], [40.68, 44.86, 0.35],
      [99.65, 39.89, 1], [114.19, -57.96, 1], [147.96, -113.98, 1], [218.74, -69.05, 0.69],
      [298.44, -162.36, 0.53], [378.94, -132.41, 0.71], [432, -252, 0.8], [108, -234, 0.4],
      [84.6, -9.58, 0.93], [-10.66, -76.03, 0.58], [-41.76, -77.98, 0.3], [-75.96, -84.6, 0.98]
    ]) },
  { id: 'sunspire', name: 'Sunspire Speedway',
    description: 'Elongated high-speed bowl with a couple of chicanes -- fast, tires take a beating.',
    deg_multiplier: 1.1, overtake_difficulty: 0.75, crash_rate_multiplier: 1.05, pit_loss_bonus: 0.0,
    shape: trackShape([
      [61.77, -456.4, 0.47], [-38.44, -494.12, 0.87], [-76.16, -393.91, 0.5], [6.35, -288.29, 0.6],
      [-4.01, -221.25, 0.5], [20.32, -166, 0.5], [55.36, -80.68, 0.82], [-86.89, 15.74, 0.35],
      [-126.23, 92.56, 1], [-16.36, 186.04, 1], [26.92, 271.04, 1], [-83.28, 323.97, 0.69],
      [-29.28, 494.6, 0.53], [-128.44, 571.11, 0.71], [-21.37, 729.04, 0.8], [206.29, 314.17, 0.4],
      [-53.41, 112.09, 0.93], [102.32, 45.46, 0.58], [128.72, 8.47, 0.3], [163.3, -28.75, 0.98]
    ]) },
  { id: 'northgate', name: 'Northgate Endurance Circuit',
    description: 'A balanced, flowing all-rounder -- no extreme strengths or weaknesses.',
    deg_multiplier: 1.0, overtake_difficulty: 1.0, crash_rate_multiplier: 1.0, pit_loss_bonus: 0.1,
    shape: trackShape([
      [-163.57, -368.91, 0.47], [-88.47, -425.15, 0.87], [-32.23, -350.05, 0.5], [-75.83, -241.01, 0.6],
      [-50.72, -187.13, 0.5], [-57.69, -134.7, 0.5], [-66.31, -54.35, 0.82], [76.96, -8, 0.35],
      [128.84, 47.01, 1], [59.25, 152.53, 1], [43.62, 234.63, 1], [149.28, 252.22, 0.69],
      [145.57, 408.99, 0.53], [247.71, 449.12, 0.71], [196.24, 608.18, 0.8], [-96.75, 314.78, 0.4],
      [72.34, 81.25, 0.93], [-74.97, 63.27, 0.58], [-106.23, 38.6, 0.3], [-144.42, 15.74, 0.98]
    ]) }
];
export function rollCircuit(rng){ return CIRCUITS[Math.floor(rng.random() * CIRCUITS.length)]; }
const TRAFFIC_DNF_REASONS = ['Collision', 'Spin', 'Loss of control', 'Mechanical failure'];
const TRAFFIC_DNF_WEIGHTS = [50, 25, 15, 10];
const BASE_DNF_REASONS = ['Mechanical failure', 'Spin', 'Loss of control', 'Collision'];
const BASE_DNF_WEIGHTS = [55, 20, 15, 10];

export function round3(x){ return Math.round(x * 1000) / 1000; }

function mulberry32(seed){
  return function(){
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function makeRng(seed){
  const next = mulberry32(seed >>> 0);
  return {
    random(){ return next(); },
    uniform(a, b){ return a + (b - a) * next(); },
    choice(arr){ return arr[Math.floor(next() * arr.length)]; },
    choices(arr, weights){
      const total = weights.reduce((a, b) => a + b, 0);
      let r = next() * total;
      for (let i = 0; i < arr.length; i++){ r -= weights[i]; if (r <= 0) return arr[i]; }
      return arr[arr.length - 1];
    }
  };
}

// Cloudflare Workers provide Web Crypto; use it to seed each phase's RNG
// rather than Math.random(), which is fine for gameplay but not intended
// as a strong entropy source across isolates.
export function randomSeed(){
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return buf[0];
}

export function rollWeather(rng){
  return {
    track_state: rng.choices(TRACK_STATES, [60, 20, 20]),
    temperature: rng.choices(TEMPS, [25, 50, 25])
  };
}
function effectiveGrip(trackState, tire){
  if (trackState === 'Dry') return tire.grip_dry;
  if (trackState === 'Wet') return tire.grip_wet;
  return (tire.grip_dry + tire.grip_wet) / 2;
}
function heatMultiplier(temp, tire){
  if (temp === 'Hot') return tire.heat;
  if (temp === 'Cold') return 0.85;
  return 1.0;
}
function stepWeather(weather, rng){
  if (rng.random() >= CFG.WEATHER_EVOLVE_CHANCE) return false;
  const idx = TRACK_STATES.indexOf(weather.track_state);
  const options = [];
  if (idx > 0) options.push(idx - 1);
  if (idx < TRACK_STATES.length - 1) options.push(idx + 1);
  if (!options.length) return false;
  weather.track_state = TRACK_STATES[rng.choice(options)];
  return true;
}

function aggressionPaceBonus(agg){ return -0.03 * agg; }
function aggressionMistakeRisk(agg){ return 0.01 * agg; }

// Mutates each driver in place (quali_time, _mistake, grid_position) and
// returns them sorted by finishing time (pole first).
export function jsRunQualifying(drivers, weather, rng){
  drivers.forEach((d) => {
    const tire = TIRES[d.quali_compound];
    const gripPenalty = (1 - effectiveGrip(weather.track_state, tire)) * CFG.GRIP_PENALTY_FACTOR;
    let lapTime = CFG.BASE_LAP_TIME + tire.base_pace + gripPenalty + aggressionPaceBonus(d.aggression) +
      rng.uniform(-CFG.QUALI_RANDOM_VARIANCE, CFG.QUALI_RANDOM_VARIANCE);
    const mistake = rng.random() < aggressionMistakeRisk(d.aggression);
    if (mistake) lapTime += rng.uniform(1.5, 4.0);
    d.quali_time = lapTime;
    d._mistake = mistake;
  });
  const ordered = drivers.slice().sort((a, b) => a.quali_time - b.quali_time);
  ordered.forEach((d, i) => { d.grid_position = i + 1; });
  return ordered;
}

function planPitLaps(numStops, rng){
  if (numStops === 1){
    const lap = Math.round(CFG.NUM_LAPS * 0.5 + rng.uniform(-2, 2));
    return [Math.max(3, Math.min(CFG.NUM_LAPS - 3, lap))];
  }
  let lap1 = Math.round(CFG.NUM_LAPS / 3 + rng.uniform(-1.5, 1.5));
  let lap2 = Math.round(CFG.NUM_LAPS * 2 / 3 + rng.uniform(-1.5, 1.5));
  lap1 = Math.max(3, Math.min(CFG.NUM_LAPS - 6, lap1));
  lap2 = Math.max(lap1 + 3, Math.min(CFG.NUM_LAPS - 2, lap2));
  return [lap1, lap2];
}
function pitStopTimeLoss(rng, circuit){
  let loss = rng.uniform(CFG.PIT_STOP_BASE_MIN, CFG.PIT_STOP_BASE_MAX) + circuit.pit_loss_bonus;
  if (rng.random() < CFG.PIT_STOP_SLOW_CHANCE) loss += rng.uniform(CFG.PIT_STOP_SLOW_EXTRA_MIN, CFG.PIT_STOP_SLOW_EXTRA_MAX);
  return loss;
}
// How boxed-in a driver is this lap, weighted by how aggressive the cars
// right around them are -- not just whether someone is nearby. 0 = clean
// air; up to ~2.0 = squeezed between two max-aggression rivals both within
// the danger window.
export function crowdedness(gapAhead, aheadAggression, gapBehind, behindAggression){
  let total = 0;
  if (gapAhead != null && gapAhead < CFG.NEIGHBOR_DANGER_WINDOW){
    const closenessA = 1 - gapAhead / CFG.NEIGHBOR_DANGER_WINDOW;
    total += closenessA * (0.4 + 0.6 * aheadAggression / 5);
  }
  if (gapBehind != null && gapBehind < CFG.NEIGHBOR_DANGER_WINDOW){
    const closenessB = 1 - gapBehind / CFG.NEIGHBOR_DANGER_WINDOW;
    total += closenessB * (0.4 + 0.6 * behindAggression / 5);
  }
  return total;
}
function incidentProbability(driver, weather, tire, attemptingOvertake, circuit, crowd){
  const grip = effectiveGrip(weather.track_state, tire);
  let p = CFG.BASE_MECHANICAL_RATE * circuit.crash_rate_multiplier;
  p += (1 - grip) * 0.004 * circuit.crash_rate_multiplier;
  p += CFG.AMBIENT_DANGER_RATE * crowd;
  p += CFG.AGGRESSION_RISK_RATE * driver.aggression * (1 + crowd);
  if (attemptingOvertake) p += (0.002 + 0.001 * driver.aggression) * circuit.overtake_difficulty;
  return p;
}
function rollDnfReason(rng, inTraffic, attemptingOvertake){
  if (inTraffic || attemptingOvertake) return rng.choices(TRAFFIC_DNF_REASONS, TRAFFIC_DNF_WEIGHTS);
  return rng.choices(BASE_DNF_REASONS, BASE_DNF_WEIGHTS);
}

// Mutates each driver in place (pit_laps, total_time, dnf*, lap_log) and
// returns { classified, weatherTimeline, weatherEvents }.
export function jsRunRace(drivers, weather, circuit, rng){
  drivers.forEach((d) => {
    d.pit_laps = planPitLaps(d.num_stops, rng);
    d.current_stint_index = 0;
    d.laps_on_current_tire = 0;
    d.total_time = 0.0;
    d.dnf = false;
    d.lap_log = [];
  });

  const weatherTimeline = [];
  const weatherEvents = [];

  for (let lap = 1; lap <= CFG.NUM_LAPS; lap++){
    const changed = stepWeather(weather, rng);
    weatherTimeline.push({ lap, track_state: weather.track_state, temperature: weather.temperature });
    if (changed) weatherEvents.push({ lap, driver_id: null, type: 'WEATHER', detail: 'Track now ' + weather.track_state });

    const active = drivers.filter((d) => !d.dnf);
    // Each driver gets both neighbors (not just the one ahead) so crash
    // risk can reflect who's actually racing around them this lap.
    const runningOrder = active.slice().sort((a, b) => a.total_time - b.total_time);
    const neighbors = {};
    runningOrder.forEach((d, i) => {
      const ahead = i > 0 ? runningOrder[i - 1] : null;
      const behind = i + 1 < runningOrder.length ? runningOrder[i + 1] : null;
      neighbors[d.driver_id] = {
        gapAhead: ahead ? d.total_time - ahead.total_time : null,
        aheadAgg: ahead ? ahead.aggression : null,
        gapBehind: behind ? behind.total_time - d.total_time : null,
        behindAgg: behind ? behind.aggression : null
      };
    });

    active.forEach((d) => {
      d.laps_on_current_tire += 1;
      const compoundName = d.race_compounds[d.current_stint_index];
      const tire = TIRES[compoundName];

      const gripPenalty = (1 - effectiveGrip(weather.track_state, tire)) * CFG.GRIP_PENALTY_FACTOR;
      const degPenalty = tire.deg * circuit.deg_multiplier * heatMultiplier(weather.temperature, tire) * (1 + 0.08 * d.aggression) *
        Math.pow(d.laps_on_current_tire, 1.15);

      const nb = neighbors[d.driver_id];
      const gap = nb.gapAhead;
      const inTraffic = gap != null && gap < CFG.TRAFFIC_GAP_THRESHOLD;
      const crowd = crowdedness(nb.gapAhead, nb.aheadAgg, nb.gapBehind, nb.behindAgg);
      let attemptingOvertake = false;
      let trafficPenalty = 0;
      let overtakeNote = null;

      if (inTraffic){
        const attemptProb = Math.min(0.9, (0.15 + 0.10 * d.aggression) / circuit.overtake_difficulty);
        attemptingOvertake = rng.random() < attemptProb;
        if (attemptingOvertake){
          const successProb = Math.min(0.9, (0.4 + 0.05 * d.aggression) / circuit.overtake_difficulty);
          if (rng.random() < successProb){ overtakeNote = 'overtake_success'; }
          else { trafficPenalty = CFG.TRAFFIC_PENALTY * circuit.overtake_difficulty; overtakeNote = 'overtake_failed'; }
        } else {
          trafficPenalty = CFG.TRAFFIC_PENALTY * circuit.overtake_difficulty;
        }
      }

      let lapTime = CFG.BASE_LAP_TIME + tire.base_pace + degPenalty + gripPenalty + aggressionPaceBonus(d.aggression) +
        trafficPenalty + rng.uniform(-CFG.RACE_RANDOM_VARIANCE, CFG.RACE_RANDOM_VARIANCE);

      const pitThisLap = d.pit_laps.indexOf(lap) !== -1;
      let pitLoss = 0;
      if (pitThisLap){ pitLoss = pitStopTimeLoss(rng, circuit); lapTime += pitLoss; }

      if (rng.random() < incidentProbability(d, weather, tire, attemptingOvertake, circuit, crowd)){
        d.dnf = true;
        d.dnf_lap = lap;
        d.dnf_reason = rollDnfReason(rng, inTraffic, attemptingOvertake);
        d.lap_log.push({ lap, event: 'DNF', reason: d.dnf_reason, compound: compoundName, position: null, gap_to_leader: null });
        return;
      }

      d.total_time += lapTime;
      d.lap_log.push({
        lap, lap_time: round3(lapTime), total_time: round3(d.total_time),
        compound: compoundName, stint_lap: d.laps_on_current_tire,
        pit: pitThisLap, pit_loss: pitThisLap ? round3(pitLoss) : null,
        overtake: overtakeNote, event: null, position: null, gap_to_leader: null
      });

      if (pitThisLap){ d.current_stint_index += 1; d.laps_on_current_tire = 0; }
    });

    const finishedThisLap = drivers.filter((d) => !d.dnf && d.lap_log.length && d.lap_log[d.lap_log.length - 1].lap === lap);
    finishedThisLap.sort((a, b) => a.total_time - b.total_time);
    const leaderTime = finishedThisLap.length ? finishedThisLap[0].total_time : null;
    finishedThisLap.forEach((d, i) => {
      const e = d.lap_log[d.lap_log.length - 1];
      e.position = i + 1;
      e.gap_to_leader = round3(d.total_time - leaderTime);
    });
  }

  const finishers = drivers.filter((d) => !d.dnf).sort((a, b) => a.total_time - b.total_time);
  const dnfs = drivers.filter((d) => d.dnf).sort((a, b) => (b.dnf_lap || 0) - (a.dnf_lap || 0));
  return { classified: finishers.concat(dnfs), weatherTimeline, weatherEvents };
}

function titleCase(s){ return s.replace(/\b\w/g, (c) => c.toUpperCase()); }

// Assembles the same JSON shape simulation.py exports, given drivers that
// have already been through jsRunQualifying and jsRunRace.
export function buildResultPayload(drivers, circuit, qualiWeather, qualiResults, raceWeather, raceOut){
  const classified = raceOut.classified;

  const laps = [];
  for (let lap = 1; lap <= CFG.NUM_LAPS; lap++){
    const cars = [];
    drivers.forEach((d) => {
      const entry = d.lap_log.filter((e) => e.lap === lap)[0];
      if (entry) cars.push(Object.assign({ driver_id: d.driver_id }, entry));
    });
    laps.push({ lap, cars });
  }

  const events = raceOut.weatherEvents.slice();
  drivers.forEach((d) => {
    d.lap_log.forEach((e) => {
      if (e.event === 'DNF'){ events.push({ lap: e.lap, driver_id: d.driver_id, type: 'DNF', detail: e.reason }); }
      if (e.pit){
        const slow = e.pit_loss && e.pit_loss > 5;
        events.push({ lap: e.lap, driver_id: d.driver_id, type: 'PIT', detail: (slow ? 'Slow stop' : 'Pit stop') + ' (+' + e.pit_loss + 's)' });
      }
      if (e.overtake){
        events.push({ lap: e.lap, driver_id: d.driver_id, type: e.overtake.toUpperCase(), detail: titleCase(e.overtake.replace('_', ' ')) });
      }
    });
  });
  events.sort((a, b) => a.lap - b.lap);

  const leaderTime = (classified.length && !classified[0].dnf) ? classified[0].total_time : null;
  const resultClassified = [], resultDnfs = [];
  classified.forEach((d, i) => {
    if (d.dnf){ resultDnfs.push({ position: i + 1, driver_id: d.driver_id, dnf_lap: d.dnf_lap, reason: d.dnf_reason }); }
    else {
      const gap = leaderTime == null ? null : round3(d.total_time - leaderTime);
      resultClassified.push({ position: i + 1, driver_id: d.driver_id, total_time: round3(d.total_time), gap });
    }
  });

  const draftPool = classified.slice();
  if (CFG.DRAFT_ORDER_REVERSED) draftPool.reverse();

  return {
    num_laps: CFG.NUM_LAPS,
    circuit,
    quali_weather: qualiWeather,
    weather: raceWeather,
    weather_timeline: raceOut.weatherTimeline,
    drivers: drivers.map((d) => ({
      driver_id: d.driver_id, name: d.name, color: d.color, color_hex: d.color_hex,
      aggression: d.aggression, num_stops: d.num_stops, quali_compound: d.quali_compound,
      race_compounds: d.race_compounds, pit_laps: d.pit_laps
    })),
    qualifying: { results: qualiResults },
    race: { laps, events },
    result: {
      classified: resultClassified, dnfs: resultDnfs,
      draft_order: draftPool.map((d) => d.driver_id),
      draft_order_reversed: CFG.DRAFT_ORDER_REVERSED
    }
  };
}
