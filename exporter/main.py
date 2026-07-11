"""Polling loop: reads Minecraft stats.json and server logs, writes metrics to InfluxDB.

Every step that touches the filesystem or the network is wrapped so one bad cycle (missing
file, corrupt JSON, InfluxDB unreachable) never crashes the loop.
"""

import glob
import json
import os
import time
from datetime import datetime, timezone

from influx import write_lines
from parsing import (
    compute_session_stats,
    parse_log_lines,
    parse_server_health,
    parse_stats,
    server_health_to_line,
    session_stats_to_line,
    session_to_line,
)

MC_DATA_DIR = os.environ.get("MC_DATA_DIR", "/mc-data")
STATE_DIR = os.environ.get("STATE_DIR", "/state")
STATE_FILE = os.path.join(STATE_DIR, "exporter_state.json")
LOG_FILE = os.path.join(MC_DATA_DIR, "logs", "latest.log")
STATS_GLOB = os.path.join(MC_DATA_DIR, "world", "stats", "*.json")
USERCACHE_FILE = os.path.join(MC_DATA_DIR, "usercache.json")

SCRAPE_INTERVAL_SECONDS = int(os.environ.get("SCRAPE_INTERVAL_SECONDS", "60"))
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_ORG = os.environ["INFLUX_ORG"]
INFLUX_BUCKET = os.environ["INFLUX_BUCKET"]
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f)
    os.replace(tmp_path, STATE_FILE)


def resolve_player_name(uuid):
    try:
        with open(USERCACHE_FILE) as f:
            entries = json.load(f)
        for entry in entries:
            if entry.get("uuid") == uuid:
                return entry.get("name", uuid[:8])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return uuid[:8]


def read_new_log_lines(state):
    if not os.path.exists(LOG_FILE):
        return []

    size = os.path.getsize(LOG_FILE)
    offset = state.get("log_offset", 0)
    if size < offset:
        offset = 0  # log was rotated/truncated by a server restart

    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        new_lines = f.readlines()
        state["log_offset"] = f.tell()
    return new_lines


def process_sessions(state, new_lines):
    now = datetime.now(timezone.utc)
    timestamp_ns = int(now.timestamp() * 1e9)

    open_sessions = {
        player: datetime.fromisoformat(ts) for player, ts in state.get("open_sessions", {}).items()
    }
    completed = parse_log_lines(new_lines, now, open_sessions)
    state["open_sessions"] = {player: ts.isoformat() for player, ts in open_sessions.items()}

    if "first_seen" not in state:
        state["first_seen"] = now.isoformat()

    influx_lines = []
    all_sessions = state.setdefault("all_sessions", [])
    for session in completed:
        all_sessions.append({
            "player": session["player"],
            "join": session["join"].isoformat(),
            "leave": session["leave"].isoformat(),
            "duration_seconds": session["duration_seconds"],
        })
        session_ns = int(session["leave"].timestamp() * 1e9)
        influx_lines.append(session_to_line(session, session_ns))

    history = [
        {"join": datetime.fromisoformat(s["join"]), "duration_seconds": s["duration_seconds"]}
        for s in all_sessions
    ]
    first_seen = datetime.fromisoformat(state["first_seen"])
    extra_played_dates = {ts.date() for ts in open_sessions.values()}
    stats = compute_session_stats(history, first_seen, now, extra_played_dates)
    influx_lines.append(session_stats_to_line(stats, timestamp_ns))

    return influx_lines


def process_stats(state):
    influx_lines = []
    snapshots = state.setdefault("stats_snapshots", {})
    timestamp_ns = int(time.time() * 1e9)

    for stats_path in glob.glob(STATS_GLOB):
        uuid = os.path.splitext(os.path.basename(stats_path))[0]
        try:
            with open(stats_path) as f:
                stats_json = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[exporter] skipping unreadable stats file {stats_path}: {exc}")
            continue

        player = resolve_player_name(uuid)
        previous = snapshots.get(player)
        lines, snapshot = parse_stats(stats_json, player, previous, timestamp_ns)
        snapshots[player] = snapshot
        influx_lines.extend(lines)

    return influx_lines


def process_server_health(new_lines):
    timestamp_ns = int(time.time() * 1e9)
    health = parse_server_health(new_lines)
    return [server_health_to_line(health, timestamp_ns)]


def run_cycle(state):
    influx_lines = []

    try:
        new_lines = read_new_log_lines(state)
    except Exception as exc:
        print(f"[exporter] reading log lines failed: {exc}")
        new_lines = []

    try:
        influx_lines.extend(process_sessions(state, new_lines))
    except Exception as exc:
        print(f"[exporter] session processing failed: {exc}")

    try:
        influx_lines.extend(process_server_health(new_lines))
    except Exception as exc:
        print(f"[exporter] server health processing failed: {exc}")

    try:
        influx_lines.extend(process_stats(state))
    except Exception as exc:
        print(f"[exporter] stats processing failed: {exc}")

    if influx_lines:
        try:
            write_lines(influx_lines, INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET, INFLUX_TOKEN)
        except Exception as exc:
            print(f"[exporter] failed to write to InfluxDB: {exc}")

    save_state(state)


def main():
    state = load_state()
    print(f"[exporter] starting, scrape interval={SCRAPE_INTERVAL_SECONDS}s")
    while True:
        run_cycle(state)
        time.sleep(SCRAPE_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
