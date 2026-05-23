// Morris Watches — tier list aggregator (Cloudflare Worker).
//
// Endpoints:
//   POST /submit     — submit a tier-list snapshot { ranks: { slug: "S"|"A"|"B"|"C"|"D"|"F" } }
//   GET  /aggregate  — return averaged tiers across all submissions
//   OPTIONS *        — CORS preflight
//
// Storage: a single KV namespace (binding: TIERS).
//   key "aggregate"      → { totals: { slug: { sum, count } }, submissions }
//   key "ratelimit:<h>"  → "1" with 24h TTL (h = SHA-256 of client IP)
//
// Tier scale: S=6, A=5, B=4, C=3, D=2, F=1. Higher = better.

const TIER_VALUE = { S: 6, A: 5, B: 4, C: 3, D: 2, F: 1 };
const VALID_TIERS = new Set(Object.keys(TIER_VALUE));

const ALLOWED_ORIGINS = new Set([
  'https://morristowneats.com',
  'https://www.morristowneats.com',
  'http://localhost:4321',
  'http://127.0.0.1:4321',
]);

const RATE_LIMIT_SECONDS = 60 * 60 * 24; // 24 hours
const MAX_RANKS_PER_SUBMISSION = 100;
const MAX_SLUG_LEN = 80;

function corsHeaders(origin) {
  const allow = ALLOWED_ORIGINS.has(origin) ? origin : 'https://morristowneats.com';
  return {
    'Access-Control-Allow-Origin': allow,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Vary': 'Origin',
  };
}

function json(body, init = {}, origin = '') {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...corsHeaders(origin),
      ...(init.headers || {}),
    },
  });
}

async function hashIP(ip) {
  const data = new TextEncoder().encode(ip || 'unknown');
  const buf = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    if (url.pathname === '/aggregate' && request.method === 'GET') {
      return handleAggregate(env, origin);
    }

    if (url.pathname === '/submit' && request.method === 'POST') {
      return handleSubmit(request, env, origin);
    }

    return json({ error: 'not found' }, { status: 404 }, origin);
  },
};

async function handleAggregate(env, origin) {
  const raw = await env.TIERS.get('aggregate', { type: 'json' });
  if (!raw || !raw.totals) {
    return json({ restaurants: {}, submissions: 0 }, {}, origin);
  }
  const restaurants = {};
  for (const [slug, info] of Object.entries(raw.totals)) {
    if (info.count > 0) {
      restaurants[slug] = { avg: info.sum / info.count, count: info.count };
    }
  }
  return json({ restaurants, submissions: raw.submissions || 0 }, {}, origin);
}

async function handleSubmit(request, env, origin) {
  // Parse body.
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: 'invalid json' }, { status: 400 }, origin);
  }

  const ranks = body?.ranks;
  if (!ranks || typeof ranks !== 'object') {
    return json({ error: 'missing ranks' }, { status: 400 }, origin);
  }

  const entries = Object.entries(ranks);
  if (entries.length === 0) {
    return json({ error: 'no ranks' }, { status: 400 }, origin);
  }
  if (entries.length > MAX_RANKS_PER_SUBMISSION) {
    return json({ error: 'too many ranks' }, { status: 400 }, origin);
  }

  // Validate every entry: slug is a sane string, tier is in S/A/B/C/D/F.
  for (const [slug, tier] of entries) {
    if (typeof slug !== 'string' || slug.length === 0 || slug.length > MAX_SLUG_LEN) {
      return json({ error: 'invalid slug' }, { status: 400 }, origin);
    }
    if (!/^[a-z0-9-]+$/.test(slug)) {
      return json({ error: 'invalid slug format' }, { status: 400 }, origin);
    }
    if (!VALID_TIERS.has(tier)) {
      return json({ error: `invalid tier for ${slug}` }, { status: 400 }, origin);
    }
  }

  // Rate limit: 1 submission per IP per 24 hours.
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const ipHash = await hashIP(ip);
  const rlKey = `ratelimit:${ipHash}`;
  const existing = await env.TIERS.get(rlKey);
  if (existing) {
    return json({ error: 'already submitted recently' }, { status: 429 }, origin);
  }

  // Read-modify-write the running aggregate. KV is eventually consistent —
  // at hobby-scale traffic the occasional race is acceptable.
  const current = (await env.TIERS.get('aggregate', { type: 'json' })) || { totals: {}, submissions: 0 };
  if (!current.totals) current.totals = {};

  for (const [slug, tier] of entries) {
    const value = TIER_VALUE[tier];
    if (!current.totals[slug]) {
      current.totals[slug] = { sum: 0, count: 0 };
    }
    current.totals[slug].sum += value;
    current.totals[slug].count += 1;
  }
  current.submissions = (current.submissions || 0) + 1;

  await env.TIERS.put('aggregate', JSON.stringify(current));
  await env.TIERS.put(rlKey, '1', { expirationTtl: RATE_LIMIT_SECONDS });

  return json({ ok: true, submissions: current.submissions }, {}, origin);
}
