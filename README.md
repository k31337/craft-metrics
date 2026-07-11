# craft-metrics

A self-hosted Docker stack for a personal, single-player Minecraft Fabric server with historical
gameplay statistics in Grafana: mining, combat, movement, automation, villager trading, and a
timeline of every play session.

## What's in the stack

- **Minecraft server** — [itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server), Fabric 1.21.1, `MAX_PLAYERS=1`, no third-party mods.
- **Exporter** — a small Python container that polls the server's vanilla `stats.json` and log files and writes metrics to InfluxDB. No mods or plugins required.
- **InfluxDB 2.x** — stores the time-series data.
- **Grafana** — dashboard is provisioned automatically; no manual clicking required.

## Requirements

- Docker Engine with the Compose plugin (`docker compose version`)
- A few GB of free disk space for the world save and the InfluxDB/Grafana volumes
- `openssl` (or PowerShell, see below) to generate a random token

## Quick start

1. Copy the environment template:

   ```sh
   cp .env.example .env
   ```

2. Generate a secure InfluxDB admin token and passwords, then fill them into `.env`:

   ```sh
   openssl rand -hex 32
   ```

   On Windows PowerShell, if `openssl` isn't available:

   ```powershell
   -join ((1..32) | ForEach-Object { "{0:x2}" -f (Get-Random -Max 256) })
   ```

   Use a freshly generated value for `INFLUXDB_INIT_ADMIN_TOKEN`, and strong passwords for
   `INFLUXDB_INIT_PASSWORD` and `GRAFANA_ADMIN_PASSWORD`. Never reuse these values elsewhere.

3. Start everything:

   ```sh
   docker compose up -d
   ```

4. Open Grafana at [http://localhost:3000](http://localhost:3000) and log in with
   `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` from your `.env`. The "Craft Metrics"
   dashboard is already there, with an InfluxDB datasource pre-configured — no setup needed.

5. Connect to the Minecraft server at `localhost:25565` (or your machine's IP on the same
   network) and play. The exporter starts reading stats and logs automatically once the world
   exists.

## Project structure

```
craft-metrics/
  docker-compose.yml
  .env.example
  exporter/                    # Python stats + session exporter
    Dockerfile
    main.py                    # polling loop
    parsing.py                 # pure functions: stats.json + log lines -> line protocol
    influx.py                  # InfluxDB HTTP write client with retries
    tests/
  grafana/
    provisioning/
      datasources/              # auto-configured InfluxDB datasource
      dashboards/                # auto-loaded "Craft Metrics" dashboard
  data/                         # Minecraft world data (gitignored, created on first run)
  influxdb-data/                # InfluxDB storage (gitignored)
  grafana-data/                 # Grafana storage (gitignored)
  exporter-state/               # exporter's own persisted state (gitignored)
```

## How the exporter works

Every `EXPORTER_SCRAPE_INTERVAL_SECONDS` (default 60s), the exporter:

1. Reads any new lines appended to `logs/latest.log` and detects `joined the game` /
   `left the game` events, closing out completed sessions. Vanilla logs only carry a
   time-of-day (no date), so events are timestamped with the wall-clock time the exporter
   observed them — accurate within one scrape interval.
2. Reads `world/stats/<uuid>.json` and writes a summary, a per-block-mined breakdown, a
   per-mob-killed breakdown, crafting station usage, redstone/automation interactions, and
   villager/economy stats.
3. Computes derived rates (blocks mined per hour, items picked up per hour) from the delta
   between this snapshot and the previous one — not from the lifetime cumulative total — so
   they reflect recent activity.
4. Writes everything to InfluxDB over the HTTP v2 API. A failed write is retried a few times
   with backoff; if it still fails, the error is logged and the loop continues on the next
   cycle instead of crashing.

State (log read offset, open sessions, session history, per-player stat snapshots) is kept in
`exporter-state/exporter_state.json` so counters and session tracking survive container
restarts.

## Running the tests

The parsing logic is pure functions tested against fixture files, no live server needed:

```sh
cd exporter
python -m unittest discover -s tests -v
```

## Notes and limitations

- Single-player only, by design (`MAX_PLAYERS=1`).
- The Grafana dashboard's queries reference the InfluxDB bucket name directly (default
  `minecraft`). If you change `INFLUXDB_INIT_BUCKET` in `.env`, update the bucket name in
  `grafana/provisioning/dashboards/craft-metrics.json` to match.
- The Grafana datasource is provisioned as read-only; edit it via `grafana/provisioning/`, not
  the UI.

## Security notes

- `docker-compose.yml` binds Grafana (`3000`) and InfluxDB (`8086`) to `127.0.0.1` only — they
  are **not** reachable from other machines by default. Keep it that way; neither service should
  ever be exposed directly to the internet.
- The Minecraft port (`25565`) is bound to all interfaces since you need it reachable to
  actually play. If you want to play or view dashboards remotely, put the host behind a VPN
  (e.g. [Tailscale](https://tailscale.com/) or WireGuard) instead of opening ports on your
  router/firewall.
- `.env` holds your InfluxDB admin token and Grafana/InfluxDB passwords. It's gitignored —
  never commit it, and never reuse the generated token or passwords elsewhere.
- Rotate `INFLUXDB_INIT_ADMIN_TOKEN` and the admin passwords if you ever suspect `.env` leaked;
  since InfluxDB only reads init variables on first setup, you'll need to change them directly
  in InfluxDB (or wipe `influxdb-data/` and re-init) for a rotation to take effect.
