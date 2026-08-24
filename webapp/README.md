# Pit Wall League Rooms

A real-time multiplayer version of the Fantasy Draft F1 Simulator: any
league can spin up its own draft room, share one link, and race live
together. Built on Cloudflare Workers + Durable Objects.

## How a room works

1. **Create** &mdash; anyone can create a room and picks the grid size (2&ndash;20
   cars). That person becomes the room's **commissioner**.
2. **Weather reveal + qualifying picks** &mdash; the link is shared with the
   league. Everyone who opens it claims an open car (first-come,
   first-served) and sets their own name, aggression, and qualifying tire
   &mdash; all live, everyone sees everyone else's picks land in real time.
   Only the commissioner can start qualifying.
3. **Qualifying results + fresh race weather** &mdash; the server runs
   qualifying once, authoritatively, for everyone. A new, independent
   weather roll reveals the conditions for the race. Drivers pick their pit
   strategy (1 or 2 stops) and race tires (their qualifying compound is
   locked out). Only the commissioner can start the race.
4. **Race + replay** &mdash; the server runs the full 20-lap race once and
   broadcasts the identical result to everyone in the room, who then watch
   the same animated replay (the same car-sprite/leaderboard/event-feed
   viewer from `../viz/index.html`) and see the draft order lock in.

Every participant's picks are only editable by them &mdash; the Durable
Object checks the submitting connection's identity against the slot before
applying any change, and only the commissioner's connection can trigger
`start_qualifying` / `start_race`. The whole simulation (qualifying + race)
runs once, server-side, in the room's Durable Object, so there's no
trust-the-client risk and every viewer converges on the same result.

## Architecture

- **`src/worker.js`** &mdash; HTTP entry point: room creation (`POST
  /api/create`), routes `/room/:code` to the room page, and proxies
  `/api/room/:code/ws` to that room's Durable Object.
- **`src/room.js`** &mdash; the `LeagueRoom` Durable Object. One instance per
  room (addressed by room code), holding the authoritative room state,
  handling the WebSocket protocol, and broadcasting the full state to every
  connected participant on every change.
- **`src/engine.js`** &mdash; the simulation engine, ported from
  `../engine.py` / `../qualifying.py` / `../race.py` and kept
  formula-for-formula in sync with them (see that file's header comment for
  what's intentionally different: independently-seeded phases instead of
  one continuous RNG stream, since qualifying and the race can happen far
  apart in wall-clock time in a room).
- **`public/`** &mdash; static frontend: `index.html` (create/join a room),
  `room.html` (the WebSocket client + picks UI + race replay, reusing the
  car-sprite canvas renderer from `../viz/index.html`), `style.css` (shared
  design tokens, matching the CLI tool's "Pit Wall" look).

### Message protocol (`/api/room/:code/ws`)

Every client &rarr; server message includes `participantId` (a random id the
client generates once and keeps in `localStorage`, so a refresh doesn't lose
your seat). The room creator's browser additionally sends the
`commissionerToken` it got back from `POST /api/create` on its first `join`
message, which is how the Durable Object recognizes the commissioner &mdash;
that token is never included in the broadcast state.

| Type | Who | Effect |
|---|---|---|
| `join` | anyone | registers the connection; binds commissioner if the token matches |
| `claim_slot` | anyone, `picks_quali` phase | claims the next open car with a name |
| `update_quali_pick` | the slot's owner, `picks_quali` phase | name / aggression / qualifying tire |
| `update_race_pick` | the slot's owner, `picks_race` phase | pit strategy / race tires |
| `start_qualifying` | commissioner only, `picks_quali` phase | runs qualifying, rolls race weather, advances phase |
| `start_race` | commissioner only, `picks_race` phase | runs the race, advances phase |

Server &rarr; client: `{"type":"state","room":{...}}` (full room state,
commissioner token stripped) on every change, or `{"type":"error","message":"..."}`
for a rejected action.

## Running it locally

```
npm install
npm run dev        # wrangler dev, http://localhost:8787
```

`wrangler dev --local` runs the Worker and Durable Object entirely on your
machine (Miniflare) &mdash; no Cloudflare account or network access needed
to develop.

## Deploying your own instance

1. **Get a Cloudflare account** (free tier works to start) at
   [dash.cloudflare.com](https://dash.cloudflare.com/sign-up) if you don't
   have one.
2. **Log in from the CLI**:
   ```
   cd webapp
   npm install
   npx wrangler login
   ```
   This opens a browser to authorize Wrangler against your account.
3. **Deploy**:
   ```
   npm run deploy
   ```
   Wrangler creates the Worker, the `LeagueRoom` Durable Object class, and
   uploads `public/` as static assets, then prints your live URL &mdash;
   by default `https://f1sim-league.<your-subdomain>.workers.dev`. That's
   the link a commissioner shares to create a room; `/room/:code` is what
   they then share with their league.
4. **Custom domain (optional)**: add a `routes` entry to `wrangler.toml`
   (see the [Workers routing docs](https://developers.cloudflare.com/workers/configuration/routing/))
   or attach a custom domain from the Cloudflare dashboard once deployed.

**On Durable Objects billing**: Cloudflare has been expanding Durable
Object availability on the free plan, but check current pricing/limits for
your account before deploying &mdash; this app creates one small Durable
Object per room (a handful of KV-sized state, a short-lived WebSocket
connection per participant), which is inexpensive at league-night scale but
worth confirming against whatever plan you're on.

### Re-deploying after changes

```
npm run deploy
```

Existing rooms (Durable Object instances) keep their state across
deploys &mdash; only the code changes.

## Known limitations

- No accounts/auth: identity is a random id in `localStorage`, and the
  commissioner role is a bearer token in the room-creator's browser. Good
  enough for casual league use; don't rely on it if you need to formally
  restrict who can join.
- No room directory or expiry: rooms live as long as their Durable Object's
  storage does (indefinitely, in practice). There's no "list all my
  leagues" view &mdash; keep track of your room links.
- Each room supports one simulation run (qualifying + one race); there's no
  "run it again" or multi-race season support yet.
