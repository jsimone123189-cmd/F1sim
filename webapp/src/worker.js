export { LeagueRoom } from './room.js';

const CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // no 0/O/1/I ambiguity
const ROOM_CODE_RE = /^[A-Za-z0-9]{4,12}$/;

function randomCode(len = 6){
  const buf = new Uint8Array(len);
  crypto.getRandomValues(buf);
  let out = '';
  for (let i = 0; i < len; i++) out += CODE_CHARS[buf[i] % CODE_CHARS.length];
  return out;
}

export default {
  async fetch(request, env){
    const url = new URL(request.url);

    if (request.method === 'POST' && url.pathname === '/api/create'){
      let body = {};
      try { body = await request.json(); } catch (err) { /* default {} */ }
      const numCars = Math.max(2, Math.min(20, parseInt(body.numCars, 10) || 8));
      const code = randomCode();
      const commissionerToken = crypto.randomUUID();

      const id = env.ROOMS.idFromName(code);
      const stub = env.ROOMS.get(id);
      await stub.initRoom(code, numCars, commissionerToken);

      return Response.json({ code, commissionerToken });
    }

    const wsMatch = url.pathname.match(/^\/api\/room\/([A-Za-z0-9]{4,12})\/ws$/);
    if (wsMatch){
      const code = wsMatch[1].toUpperCase();
      const id = env.ROOMS.idFromName(code);
      const stub = env.ROOMS.get(id);
      return stub.fetch(request);
    }

    const roomPageMatch = url.pathname.match(/^\/room\/([A-Za-z0-9]{4,12})$/);
    if (roomPageMatch && ROOM_CODE_RE.test(roomPageMatch[1])){
      return env.ASSETS.fetch(new Request(new URL('/room.html', url), request));
    }

    // html_handling is "none" (see wrangler.toml) so extensionless clean
    // URLs aren't auto-served -- map "/" to the landing page ourselves.
    if (url.pathname === '/'){
      return env.ASSETS.fetch(new Request(new URL('/index.html', url), request));
    }

    return env.ASSETS.fetch(request);
  }
};
