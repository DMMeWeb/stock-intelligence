# Stock Intelligence

Private Shopify stock and sales dashboard for FAUNE.

## Local Run

Create `.env` from `.env.example`, then run:

```bash
npm run dev
```

Open:

```text
http://localhost:4173
```

## Manual Shopify Sync

```bash
npm run sync:shopify
```

## Railway Deploy

Set these environment variables in Railway:

```env
SHOPIFY_STORE=f-a-u-n-e.myshopify.com
SHOPIFY_CLIENT_ID=...
SHOPIFY_CLIENT_SECRET=...
SHOPIFY_API_VERSION=2026-04
SHOPIFY_ORDER_LOOKBACK_DAYS=92
APP_PASSWORD=...
APP_SESSION_SECRET=...
SYNC_TIME=08:00
SYNC_ON_STARTUP=true
```

Do not set `PORT` on Railway. Railway provides it automatically.

The app runs:

```bash
python3 server.py
```

The server:

- password-protects the dashboard
- runs a Shopify sync on startup when `SYNC_ON_STARTUP=true`
- runs a scheduled sync daily at `SYNC_TIME` in Europe/London time
- exposes a manual Sync button in the dashboard

## Sensitive Files

Do not commit:

- `.env`
- raw Shopify CSV exports in `data/*.csv`
- raw Shopify API snapshots in `data/shopify-raw/`
