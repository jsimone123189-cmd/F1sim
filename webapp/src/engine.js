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
  DRAFT_ORDER_REVERSED: false
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
function pitStopTimeLoss(rng){
  let loss = rng.uniform(CFG.PIT_STOP_BASE_MIN, CFG.PIT_STOP_BASE_MAX);
  if (rng.random() < CFG.PIT_STOP_SLOW_CHANCE) loss += rng.uniform(CFG.PIT_STOP_SLOW_EXTRA_MIN, CFG.PIT_STOP_SLOW_EXTRA_MAX);
  return loss;
}
function incidentProbability(driver, weather, tire, inTraffic, attemptingOvertake){
  let p = CFG.BASE_MECHANICAL_RATE + 0.0005 * driver.aggression;
  p += (1 - effectiveGrip(weather.track_state, tire)) * 0.004;
  if (inTraffic) p += 0.0015;
  if (attemptingOvertake) p += 0.002 + 0.001 * driver.aggression;
  return p;
}
function rollDnfReason(rng, inTraffic, attemptingOvertake){
  if (inTraffic || attemptingOvertake) return rng.choices(TRAFFIC_DNF_REASONS, TRAFFIC_DNF_WEIGHTS);
  return rng.choices(BASE_DNF_REASONS, BASE_DNF_WEIGHTS);
}

// Mutates each driver in place (pit_laps, total_time, dnf*, lap_log) and
// returns { classified, weatherTimeline, weatherEvents }.
export function jsRunRace(drivers, weather, rng){
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
    const runningOrder = active.slice().sort((a, b) => a.total_time - b.total_time);
    const gapAhead = {};
    runningOrder.forEach((d, i) => {
      gapAhead[d.driver_id] = i === 0 ? null : d.total_time - runningOrder[i - 1].total_time;
    });

    active.forEach((d) => {
      d.laps_on_current_tire += 1;
      const compoundName = d.race_compounds[d.current_stint_index];
      const tire = TIRES[compoundName];

      const gripPenalty = (1 - effectiveGrip(weather.track_state, tire)) * CFG.GRIP_PENALTY_FACTOR;
      const degPenalty = tire.deg * heatMultiplier(weather.temperature, tire) * (1 + 0.08 * d.aggression) *
        Math.pow(d.laps_on_current_tire, 1.15);

      const gap = gapAhead[d.driver_id];
      const inTraffic = gap != null && gap < CFG.TRAFFIC_GAP_THRESHOLD;
      let attemptingOvertake = false;
      let trafficPenalty = 0;
      let overtakeNote = null;

      if (inTraffic){
        attemptingOvertake = rng.random() < (0.15 + 0.10 * d.aggression);
        if (attemptingOvertake){
          if (rng.random() < (0.4 + 0.05 * d.aggression)){ overtakeNote = 'overtake_success'; }
          else { trafficPenalty = CFG.TRAFFIC_PENALTY; overtakeNote = 'overtake_failed'; }
        } else {
          trafficPenalty = CFG.TRAFFIC_PENALTY;
        }
      }

      let lapTime = CFG.BASE_LAP_TIME + tire.base_pace + degPenalty + gripPenalty + aggressionPaceBonus(d.aggression) +
        trafficPenalty + rng.uniform(-CFG.RACE_RANDOM_VARIANCE, CFG.RACE_RANDOM_VARIANCE);

      const pitThisLap = d.pit_laps.indexOf(lap) !== -1;
      let pitLoss = 0;
      if (pitThisLap){ pitLoss = pitStopTimeLoss(rng); lapTime += pitLoss; }

      if (rng.random() < incidentProbability(d, weather, tire, inTraffic, attemptingOvertake)){
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
export function buildResultPayload(drivers, qualiWeather, qualiResults, raceWeather, raceOut){
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
