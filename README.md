# craft-metrics

![Minecraft](https://img.shields.io/badge/minecraft-1.21.1_fabric-brightgreen)
![Docker](https://img.shields.io/badge/docker-compose-blue)
![Grafana](https://img.shields.io/badge/grafana-%2B_influxdb-orange)
![License](https://img.shields.io/badge/license-MIT-green)

A self-hosted Docker Compose stack that runs a single-player Minecraft Fabric server and turns its gameplay into a live, always-updated Grafana dashboard. A small Python exporter polls the server's vanilla `stats.json` and log files every minute — no mods, no plugins — and streams the numbers into InfluxDB: mining, combat, movement, automation, villager trading, container use, server-health telemetry (lag, ticks skipped, restarts), and a timeline of every play session. Bring the stack up with one command and open Grafana; the dashboard is already there.

## Contents

- [What's in the stack](#whats-in-the-stack)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Running as a service](#running-as-a-service)
- [Everyday commands](#everyday-commands)
- [What gets tracked](#what-gets-tracked)
- [How it works](#how-it-works)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Disclaimer](#disclaimer)
- [License](#license)

## What's in the stack

Four containers, orchestrated by `docker-compose.yml`:

| Service | Container | Port | Role |
| --- | --- | --- | --- |
| `mc` | `craft-metrics-mc` | `25565` | Fabric Minecraft server (`MAX_PLAYERS=1`), from [itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server) |
| `influxdb` | `craft-metrics-influxdb` | `127.0.0.1:8086` | InfluxDB 2.x time-series storage |
| `grafana` | `craft-metrics-grafana` | `127.0.0.1:3000` | Grafana with an auto-provisioned dashboard |
| `exporter` | `craft-metrics-exporter` | — | Python service that reads stats/logs and writes to InfluxDB |

The exporter reads the world data read-only and only starts once `mc` and `influxdb` are healthy, so it never interferes with the running server. Grafana's datasource and the "Craft Metrics" dashboard are provisioned from files, so there's no manual setup after the first launch.

## Requirements

- Docker Engine with the Compose plugin (`docker compose version`)
- A few GB of free disk for the world save and the InfluxDB/Grafana volumes
- `openssl` (or PowerShell, see [Configuration](#configuration)) to generate a token

All runtime state lives in gitignored directories created on first run: `data/` (world), `influxdb-data/`, `grafana-data/`, and `exporter-state/`.

## Quick start

```bash
git clone https://github.com/k31337/craft-metrics.git
cd craft-metrics
cp .env.example .env
# edit .env — set the token and passwords (see Configuration)
docker compose up -d
```

```text
[+] Running 4/4
 ✔ Container craft-metrics-influxdb   Healthy
 ✔ Container craft-metrics-mc         Healthy
 ✔ Container craft-metrics-grafana    Started
 ✔ Container craft-metrics-exporter   Started
```

Then:

1. Open Grafana at [http://localhost:3000](http://localhost:3000) and log in with `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` from your `.env`. The "Craft Metrics" dashboard is already loaded, with the InfluxDB datasource wired up.
2. Connect to the server at `localhost:25565` (or your machine's IP on the LAN) and play. The exporter starts reading stats and logs automatically once the world exists.

## Configuration

Everything is driven by `.env`. Copy the template and fill in secure values **before** the first `docker compose up`.

Generate a random InfluxDB admin token and strong passwords:

```bash
openssl rand -hex 32
```

On Windows PowerShell, if `openssl` isn't available:

```powershell
-join ((1..32) | ForEach-Object { "{0:x2}" -f (Get-Random -Max 256) })
```

Use a freshly generated value for `INFLUXDB_INIT_ADMIN_TOKEN` and strong, unique passwords for `INFLUXDB_INIT_PASSWORD` and `GRAFANA_ADMIN_PASSWORD`. Keep `.env` private — never commit it or reuse these secrets elsewhere.

| Variable | Description | Default |
| --- | --- | --- |
| `MC_VERSION` | Minecraft/Fabric version to run | `1.21.1` |
| `MC_MEMORY` | Memory allocated to the server | `2G` |
| `MC_PORT` | Host port for the Minecraft server | `25565` |
| `INFLUXDB_INIT_USERNAME` | InfluxDB admin username | `admin` |
| `INFLUXDB_INIT_PASSWORD` | InfluxDB admin password | *(set this)* |
| `INFLUXDB_INIT_ORG` | InfluxDB organization | `craft-metrics` |
| `INFLUXDB_INIT_BUCKET` | InfluxDB bucket for metrics | `minecraft` |
| `INFLUXDB_INIT_ADMIN_TOKEN` | InfluxDB admin token (shared by exporter and Grafana) | *(set this)* |
| `INFLUXDB_PORT` | Host port for InfluxDB | `8086` |
| `GRAFANA_ADMIN_USER` | Grafana admin username | `admin` |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin password | *(set this)* |
| `GRAFANA_PORT` | Host port for Grafana | `3000` |
| `EXPORTER_SCRAPE_INTERVAL_SECONDS` | How often the exporter polls stats and logs | `60` |

> **Note:** the Grafana dashboard queries the bucket by name. If you change `INFLUXDB_INIT_BUCKET`, update it in `grafana/provisioning/dashboards/craft-metrics.json` to match.

### Minecraft server settings

The `mc` service is the [itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server) image, which is configured entirely through environment variables — difficulty, gamemode, MOTD, seed, view distance, ops and whitelist all map to a variable. Add them under `environment:` for the `mc` service in `docker-compose.yml`:

```yaml
  mc:
    environment:
      EULA: "TRUE"
      TYPE: "FABRIC"
      DIFFICULTY: "normal"        # peaceful | easy | normal | hard
      MODE: "survival"            # survival | creative | adventure
      MOTD: "craft-metrics server"
      VIEW_DISTANCE: "12"
      OPS: "YourPlayerName"       # comma-separated names granted operator
      SEED: ""                    # fixed world seed, blank for random
```

To keep secrets and per-host tweaks out of the compose file, reference `.env` variables instead — e.g. `DIFFICULTY: ${MC_DIFFICULTY:-normal}` in `docker-compose.yml` and `MC_DIFFICULTY=hard` in `.env`.

> **Note:** most of these are applied to `server.properties` only on first launch (or when `OVERRIDE_SERVER_PROPERTIES=true`). After the world exists, edit `data/server.properties` directly and restart the `mc` service. See the [image docs](https://docker-minecraft-server.readthedocs.io/) for the full list of variables.

## Running as a service

`docker-compose.yml` already sets `restart: unless-stopped` on every container, so the stack recovers from crashes and restarts. For it to also come up automatically after a **reboot**, make sure the Docker daemon starts on boot:

```bash
sudo systemctl enable --now docker
```

That's enough on most hosts — Docker restarts the containers itself. If you'd rather manage the stack as its own unit (so `systemctl start/stop craft-metrics` controls it), create `/etc/systemd/system/craft-metrics.service`:

```ini
[Unit]
Description=craft-metrics Minecraft stack
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/path/to/craft-metrics
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now craft-metrics
```

> **Note:** set `WorkingDirectory` to the absolute path of your clone so Compose finds `docker-compose.yml` and `.env`. On Docker Desktop (macOS/Windows), skip systemd and enable "Start Docker Desktop when you log in" plus the `restart: unless-stopped` policy instead.

## Everyday commands

Follow the exporter to confirm it's scraping:

```bash
docker compose logs -f exporter
```

Rebuild the exporter image after editing its Python code:

```bash
docker compose up -d --build exporter
```

Restart a single service without touching the others:

```bash
docker compose restart grafana
```

Check the health of every container at a glance:

```bash
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
```

Stop the stack, keeping all data volumes:

```bash
docker compose down
```

## What gets tracked

The exporter writes InfluxDB line protocol to the configured bucket, tagged so Grafana can group and filter it:

- **Summary** (`mc_stats_summary`) — lifetime totals per player: blocks mined, mobs killed, playtime hours, deaths, jumps, damage dealt/taken, plus more technical counters (mob/player kills, items dropped, damage absorbed/blocked/resisted, times left game), tick counters as hours (total world time, sneak time, time since death/rest), and movement distance by type (walk, sprint, crouch, swim, fall, climb, on/under water, creative fly, elytra, minecart, boat, horse) in meters.
- **Breakdowns** (`mc_stats_block`, `mc_stats_mob`) — per-block-mined and per-mob-killed counts.
- **Interactions** (`mc_stats_station`, `mc_stats_automation`, `mc_stats_container`, `mc_stats_other`) — crafting/utility-station usage (furnace, anvil, grindstone, brewing stand, cartography table, lectern, beacon, campfire, …), redstone/automation, container opens (barrel, ender chest, shulker box), and enchanting/trading/breeding/fishing/bells/raids.
- **Recent rates** (`mc_stats_derived`) — blocks mined and items picked up per hour, computed from the delta between scrapes rather than a lifetime average.
- **Server health** (`mc_server_health`) — per-scrape server telemetry parsed from the log: overload events, worst lag (ms behind), ticks skipped, server (re)starts, and last startup duration. No mods needed — it reads vanilla's own "Can't keep up" warnings.
- **Sessions** (`mc_session`, `mc_session_stats`) — each completed play session plus a running summary: server age, current daily streak, longest session, and total sessions.

## How it works

Every `EXPORTER_SCRAPE_INTERVAL_SECONDS` (default 60s), the exporter runs one cycle:

1. **Read new log lines** — seeks past the last-read offset in `logs/latest.log` (handling rotation/truncation). The same batch is matched for `joined the game` / `left the game` and scanned for server-health signals (overload warnings, `Starting minecraft server`, `Done (Xs)!`). Vanilla logs carry only a time-of-day, so events are timestamped with the wall-clock time they were observed, accurate within one interval.
2. **Close sessions** — pairs each leave with its open join into a completed session and refreshes the session summary. An in-progress session still counts toward today's streak.
3. **Parse stats** — reads each `world/stats/<uuid>.json`, resolves the UUID to a name via `usercache.json`, and emits the summary plus the per-block, per-mob, station, automation, container and economy breakdowns.
4. **Compute rates** — after a second scrape, divides the block/item delta by the playtime delta for per-hour rates that reflect recent activity.
5. **Write to InfluxDB** — POSTs all points to the HTTP v2 write API, retrying transient errors with backoff and failing fast on `4xx`; a failed write is logged and the loop continues instead of crashing.
6. **Persist state** — writes the log offset, open sessions, session history and per-player snapshots to `exporter-state/exporter_state.json` (atomic replace) so everything survives restarts.

## Development

The parsing logic lives in pure functions with no file or network I/O, tested against fixtures, so no live server is needed:

```bash
cd exporter
python -m unittest discover -s tests -v
```

Project layout:

```
craft-metrics/
  docker-compose.yml
  .env.example
  exporter/
    Dockerfile
    main.py            # polling loop, filesystem + InfluxDB I/O
    parsing.py         # pure functions: stats.json + log lines -> line protocol
    influx.py          # InfluxDB v2 line-protocol write client with retries
    requirements.txt
    tests/             # unittest suite + fixtures (sample_stats.json, sample_latest.log)
  grafana/
    provisioning/
      datasources/     # auto-configured InfluxDB datasource
      dashboards/      # auto-loaded "Craft Metrics" dashboard
```

## Troubleshooting

- **Grafana dashboard shows "No data"** — the exporter only writes after a scrape interval and once the world exists; check `docker compose logs exporter` and make sure you've logged into the server at least once.
- **`failed to write to InfluxDB: ... 401`** — `INFLUX_TOKEN` doesn't match `INFLUXDB_INIT_ADMIN_TOKEN`; InfluxDB only reads init variables on first setup, so wipe `influxdb-data/` (or update the token in InfluxDB directly) after changing it.
- **Panels stay empty after renaming the bucket** — update `grafana/provisioning/dashboards/craft-metrics.json` to match `INFLUXDB_INIT_BUCKET`.
- **Exporter container is `unhealthy`** — it reports healthy only while `exporter_state.json` is refreshed within three scrape intervals; a stuck loop or unreachable InfluxDB will trip it, so check its logs.

## Security

- Grafana (`3000`) and InfluxDB (`8086`) bind to `127.0.0.1` only — they are **not** reachable from other machines by default. Keep it that way; neither should ever be exposed directly to the internet.
- The Minecraft port (`25565`) is bound to all interfaces so you can actually play. To reach the server or dashboards remotely, put the host behind a VPN (e.g. [Tailscale](https://tailscale.com/) or WireGuard) rather than opening router/firewall ports.
- `.env` holds your InfluxDB admin token and Grafana/InfluxDB passwords. It's gitignored — never commit it, and never reuse the generated secrets elsewhere.
- Rotate `INFLUXDB_INIT_ADMIN_TOKEN` and the admin passwords if you suspect `.env` leaked. Since InfluxDB only reads init variables on first setup, change them directly in InfluxDB (or wipe `influxdb-data/` and re-init) for a rotation to take effect.

## Disclaimer

For personal, single-player use on servers you run yourself. Not affiliated with or endorsed by Mojang, Microsoft, InfluxData or Grafana Labs. "Minecraft" is a trademark of Mojang Synergies AB.

## License

[MIT](LICENSE)
</content>
