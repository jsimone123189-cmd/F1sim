import { DurableObject } from 'cloudflare:workers';
import {
  COMPOUNDS, makeRng, randomSeed, rollWeather, rollCircuit,
  jsRunQualifying, jsRunRace, buildResultPayload, round3
} from './engine.js';

// Same high-contrast palette as engine.py's COLOR_PALETTE/COLOR_HEX.
const PALETTE = [
  ['Red', '#e6194B'], ['Blue', '#4363d8'], ['Green', '#3cb44b'], ['Yellow', '#ffe119'],
  ['Orange', '#f58231'], ['Purple', '#911eb4'], ['Cyan', '#42d4f4'], ['Magenta', '#f032e6'],
  ['Lime', '#bfef45'], ['Pink', '#fabed4'], ['Teal', '#469990'], ['Gold', '#dcbe23'],
  ['Navy', '#000075'], ['Maroon', '#800000'], ['Turquoise', '#40e0d0'], ['Silver', '#c0c0c0'],
  ['Brown', '#9A6324'], ['Indigo', '#4b0082'], ['Coral', '#ff7f50'], ['Olive', '#808000']
];

const MAX_MESSAGE_BYTES = 8 * 1024;

// One LeagueRoom Durable Object instance == one league's draft room, keyed
// by its room code. Holds the authoritative room state, runs qualifying and
// the race itself (so every viewer sees the identical result), and
// broadcasts the full state to every connected participant over
// WebSocket on every change. See ../README.md for the message protocol.
export class LeagueRoom extends DurableObject {
  constructor(ctx, env){
    super(ctx, env);
    this.sessions = new Set();
    this.room = null;
  }

  async ensureLoaded(){
    if (this.room === null){
      this.room = (await this.ctx.storage.get('room')) || null;
    }
  }

  async persist(){
    await this.ctx.storage.put('room', this.room);
  }

  publicView(){
    if (!this.room) return null;
    const { commissionerToken, ...pub } = this.room;
    return pub;
  }

  broadcast(){
    const msg = JSON.stringify({ type: 'state', room: this.publicView() });
    for (const ws of this.sessions){
      try { ws.send(msg); } catch (err) { this.sessions.delete(ws); }
    }
  }

  // --- RPC entry point, called directly by the Worker on room creation ---
  async initRoom(code, numCars, commissionerToken){
    await this.ensureLoaded();
    if (this.room) return this.publicView();

    const rng = makeRng(randomSeed());
    const circuit = rollCircuit(rng);
    const qualiWeather = rollWeather(rng);
    const palette = PALETTE.slice(0, numCars);

    this.room = {
      code,
      createdAt: Date.now(),
      commissionerToken,
      commissionerId: null,
      numCars,
      phase: 'picks_quali', // picks_quali -> picks_race -> race_done
      circuit,
      quali_weather: qualiWeather,
      slots: palette.map((p, i) => ({
        slot: i,
        claimedBy: null,
        name: null,
        color: p[0],
        color_hex: p[1],
        aggression: 3,
        race_aggression: 3,
        quali_compound: 'Medium',
        num_stops: 1,
        race_compounds: ['Soft', 'Hard']
      })),
      quali_result: null,
      race_weather: null,
      final_result: null
    };
    await this.persist();
    return this.publicView();
  }

  // --- HTTP entry point: only handles the WebSocket upgrade ---
  async fetch(request){
    if (request.headers.get('Upgrade') !== 'websocket'){
      return new Response('Expected WebSocket upgrade', { status: 426 });
    }
    await this.ensureLoaded();
    if (!this.room){
      return new Response('Room not found', { status: 404 });
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();
    this.sessions.add(server);
    server.addEventListener('message', (evt) => this.handleMessage(server, evt));
    server.addEventListener('close', () => this.sessions.delete(server));
    server.addEventListener('error', () => this.sessions.delete(server));
    server.send(JSON.stringify({ type: 'state', room: this.publicView() }));

    return new Response(null, { status: 101, webSocket: client });
  }

  async handleMessage(ws, evt){
    if (typeof evt.data !== 'string' || evt.data.length > MAX_MESSAGE_BYTES) return;
    await this.ensureLoaded();
    if (!this.room) return;

    let msg;
    try { msg = JSON.parse(evt.data); } catch (err) { return; }
    const { type, participantId } = msg;
    if (typeof type !== 'string' || typeof participantId !== 'string' || !participantId) return;

    // The room creator's browser presents the commissioner token on its
    // first message; whoever's participantId is attached to a valid,
    // not-yet-claimed token becomes (and stays) the commissioner. This must
    // be broadcast even on an otherwise no-op message (e.g. 'join') so the
    // commissioner's own client learns its role right away.
    let boundCommissioner = false;
    if (typeof msg.commissionerToken === 'string' &&
        msg.commissionerToken === this.room.commissionerToken &&
        !this.room.commissionerId){
      this.room.commissionerId = participantId;
      boundCommissioner = true;
    }

    // Only the mutating branches below set `changed = true`; the `join`
    // case and every guard/rejection path leave it false. Whether we
    // persist+broadcast at the end is `changed || boundCommissioner`, so a
    // commissioner-binding is never lost even on an otherwise no-op message.
    let changed = false;
    switch (type){
      case 'join':
        break;

      case 'claim_slot': {
        if (this.room.phase !== 'picks_quali') break;
        if (this.room.slots.some((s) => s.claimedBy === participantId)) break;
        const open = this.room.slots.find((s) => s.claimedBy === null);
        if (!open){
          ws.send(JSON.stringify({ type: 'error', message: 'This room is full.' }));
          break;
        }
        open.claimedBy = participantId;
        open.name = safeName(msg.name) || `Driver ${open.slot + 1}`;
        changed = true;
        break;
      }

      case 'update_quali_pick': {
        if (this.room.phase !== 'picks_quali') break;
        const slot = this.room.slots.find((s) => s.claimedBy === participantId);
        if (!slot) break;
        const name = safeName(msg.name);
        if (name) slot.name = name;
        if (Number.isInteger(msg.aggression) && msg.aggression >= 1 && msg.aggression <= 5){
          slot.aggression = msg.aggression;
        }
        if (COMPOUNDS.includes(msg.quali_compound)) slot.quali_compound = msg.quali_compound;
        changed = true;
        break;
      }

      case 'update_race_pick': {
        if (this.room.phase !== 'picks_race') break;
        const slot = this.room.slots.find((s) => s.claimedBy === participantId);
        if (!slot) break;
        if (Number.isInteger(msg.aggression) && msg.aggression >= 1 && msg.aggression <= 5){
          slot.race_aggression = msg.aggression;
        }
        const numStops = msg.num_stops === 2 ? 2 : 1;
        slot.num_stops = numStops;
        const available = COMPOUNDS.filter((c) => c !== slot.quali_compound);
        const stints = numStops + 1;
        let rc = Array.isArray(msg.race_compounds) ? msg.race_compounds.slice(0, stints) : [];
        rc = rc.map((c) => (available.includes(c) ? c : null));
        // Each stint must use a distinct compound: drop any repeat (keeping
        // its first occurrence), then backfill empty/dropped slots from
        // whatever compounds are still unused.
        const seen = new Set();
        rc = rc.map((c) => {
          if (c && !seen.has(c)){ seen.add(c); return c; }
          return null;
        });
        rc = rc.map((c) => {
          if (c) return c;
          const fill = available.find((cand) => !seen.has(cand));
          if (fill) seen.add(fill);
          return fill || available[0];
        });
        while (rc.length < stints){
          const fill = available.find((cand) => !seen.has(cand)) || available[0];
          seen.add(fill);
          rc.push(fill);
        }
        slot.race_compounds = rc;
        changed = true;
        break;
      }

      case 'start_qualifying': {
        if (participantId !== this.room.commissionerId){
          ws.send(JSON.stringify({ type: 'error', message: 'Only the commissioner can start qualifying.' }));
          break;
        }
        if (this.room.phase !== 'picks_quali') break;
        this.runQualifying();
        changed = true;
        break;
      }

      case 'start_race': {
        if (participantId !== this.room.commissionerId){
          ws.send(JSON.stringify({ type: 'error', message: 'Only the commissioner can start the race.' }));
          break;
        }
        if (this.room.phase !== 'picks_race') break;
        this.runRace();
        changed = true;
        break;
      }

      default:
        break;
    }

    if (changed || boundCommissioner){
      await this.persist();
      this.broadcast();
    }
  }

  runQualifying(){
    this.room.slots.forEach((s) => { if (!s.name) s.name = `Driver ${s.slot + 1}`; });

    const rng = makeRng(randomSeed());
    const weather = { ...this.room.quali_weather };
    const drivers = this.room.slots.map((s) => ({
      driver_id: s.slot, name: s.name, color: s.color, color_hex: s.color_hex,
      aggression: s.aggression, quali_compound: s.quali_compound
    }));
    const ordered = jsRunQualifying(drivers, weather, rng);

    this.room.quali_result = {
      results: ordered.map((d) => ({
        driver_id: d.driver_id, grid_position: d.grid_position,
        time: round3(d.quali_time), compound: d.quali_compound, mistake: !!d._mistake
      }))
    };

    // Seed each slot's race-tire and race-aggression defaults now that the
    // qualifying compound (and therefore the lockout) is fixed.
    this.room.slots.forEach((s) => {
      const available = COMPOUNDS.filter((c) => c !== s.quali_compound);
      s.num_stops = 1;
      s.race_compounds = [available[0], available[1] || available[0]];
      s.race_aggression = s.aggression;
    });

    const wRng = makeRng(randomSeed());
    this.room.race_weather = rollWeather(wRng);
    this.room.phase = 'picks_race';
  }

  runRace(){
    const rng = makeRng(randomSeed());
    const weather = { ...this.room.race_weather };
    const drivers = this.room.slots.map((s) => ({
      driver_id: s.slot, name: s.name, color: s.color, color_hex: s.color_hex,
      aggression: s.race_aggression, num_stops: s.num_stops, quali_compound: s.quali_compound,
      race_compounds: s.race_compounds.slice()
    }));
    const raceOut = jsRunRace(drivers, weather, this.room.circuit, rng);
    this.room.final_result = buildResultPayload(
      drivers, this.room.circuit, this.room.quali_weather, this.room.quali_result.results, this.room.race_weather, raceOut
    );
    this.room.phase = 'race_done';
  }
}

function safeName(raw){
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim().slice(0, 24);
  return trimmed || null;
}
