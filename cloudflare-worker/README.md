# Morristown Eats — Tier List Aggregator

A tiny Cloudflare Worker backing the **Morris Watches** page on morristowneats.com.

## What it does

- `POST /submit` — accept a tier-list snapshot, update the rolling aggregate, rate-limit the submitter by IP (1 per 24h).
- `GET /aggregate` — return the average tier per restaurant across all submissions, plus total submission count.

Storage is one Cloudflare KV namespace. There are no accounts, no PII, no logs of plaintext IPs — submitter IPs are SHA-256 hashed and used only for rate-limiting.

## Deploy (one-time setup)

1. **Install wrangler**

   ```bash
   cd cloudflare-worker
   npm install
   ```

2. **Authenticate**

   ```bash
   npx wrangler login
   ```

3. **Create the KV namespace**

   ```bash
   npx wrangler kv:namespace create TIERS
   ```

   This prints an `id`. Open `wrangler.toml` and replace `REPLACE_WITH_KV_NAMESPACE_ID` with that value.

4. **Deploy**

   ```bash
   npx wrangler deploy
   ```

   Wrangler will print the live URL, something like:
   `https://morristown-eats-tiers.<your-subdomain>.workers.dev`

5. **Wire it into the site**

   Open `../src/pages/watches.astro` and update the `WORKER_URL` constant near the top of the frontmatter to the URL from step 4.

6. **Rebuild and redeploy the Astro site.**

## Optional: custom domain

If you want `api.morristowneats.com` instead of the workers.dev URL:

1. In the Cloudflare dashboard, add `morristowneats.com` as a zone (if not already).
2. Uncomment the `[[routes]]` block in `wrangler.toml` and redeploy.
3. Add a CNAME record for `api` pointing to the worker (Cloudflare may auto-create it).
4. Update `WORKER_URL` in `watches.astro` to `https://api.morristowneats.com`.

## Local dev

```bash
npx wrangler dev
```

Worker runs at `http://localhost:8787`. The Astro page allows `localhost:4321` as an origin, so the two play together if you also run `npm run dev` in the site root.

## Resetting the tally

```bash
npx wrangler kv:key delete --binding=TIERS aggregate
```

(Use with care — there's no undo.)

## What lives in KV

- `aggregate` — JSON: `{ totals: { slug: { sum, count } }, submissions }`
- `ratelimit:<sha256>` — `"1"` with 24h TTL, one per submitting IP

That's it. No other keys, no other writes.
