"""Pure functions that turn Minecraft stats.json and server log lines into InfluxDB line protocol.

No file or network I/O here on purpose, so this module can be unit tested without a live server.
"""

import re
from datetime import timedelta

STATION_STATS = [
    "interact_with_furnace",
    "interact_with_blast_furnace",
    "interact_with_smoker",
    "interact_with_crafting_table",
    "interact_with_smithing_table",
    "interact_with_stonecutter",
    "interact_with_loom",
    "interact_with_anvil",
    "interact_with_grindstone",
    "interact_with_brewingstand",
    "interact_with_cartography_table",
    "interact_with_lectern",
    "interact_with_beacon",
    "interact_with_campfire",
]

AUTOMATION_STATS = [
    "inspect_hopper",
    "inspect_dropper",
    "inspect_dispenser",
    "trigger_trapped_chest",
    "target_hit",
]

# Container opens live under minecraft:custom with no shared prefix, so they get their own group.
CONTAINER_STATS = [
    "open_barrel",
    "open_enderchest",
    "open_shulker_box",
]

OTHER_STATS = [
    "enchant_item",
    "traded_with_villager",
    "talked_to_villager",
    "animals_bred",
    "fish_caught",
    "sleep_in_bed",
    "bell_ring",
    "raid_trigger",
    "raid_win",
]

# Vanilla stores distances in centimeters; map to the summary field name we export (in meters).
DISTANCE_FIELDS = {
    "distance_walk_m": "walk_one_cm",
    "distance_sprint_m": "sprint_one_cm",
    "distance_crouch_m": "crouch_one_cm",
    "distance_swim_m": "swim_one_cm",
    "distance_fall_m": "fall_one_cm",
    "distance_climb_m": "climb_one_cm",
    "distance_walk_on_water_m": "walk_on_water_one_cm",
    "distance_walk_under_water_m": "walk_under_water_one_cm",
    "distance_fly_m": "fly_one_cm",
    "distance_elytra_m": "aviate_one_cm",
    "distance_minecart_m": "minecart_one_cm",
    "distance_boat_m": "boat_one_cm",
    "distance_horse_m": "horse_one_cm",
}

# Cumulative custom counters exported verbatim as integers.
COUNTER_FIELDS = {
    "mob_kills": "mob_kills",
    "player_kills": "player_kills",
    "items_dropped": "drop",
    "leave_game": "leave_game",
    "damage_absorbed": "damage_absorbed",
    "damage_blocked_by_shield": "damage_blocked_by_shield",
    "damage_resisted": "damage_resisted",
    "damage_dealt_absorbed": "damage_dealt_absorbed",
}

# Custom tick counters exported as hours.
TIME_FIELDS = {
    "total_world_time_hours": "total_world_time",
    "sneak_time_hours": "sneak_time",
    "time_since_death_hours": "time_since_death",
    "time_since_rest_hours": "time_since_rest",
}

JOIN_RE = re.compile(r"\]: (\S+) joined the game")
LEAVE_RE = re.compile(r"\]: (\S+) left the game")

# Server-health signals in latest.log. The overload warning is vanilla's built-in lag indicator.
OVERLOAD_RE = re.compile(r"Running (\d+)ms behind, skipping (\d+) tick")
SERVER_START_RE = re.compile(r"Starting minecraft server version (\S+)")
STARTUP_DONE_RE = re.compile(r"Done \(([\d.]+)s\)!")


def strip_namespace(key):
    return key.split(":", 1)[-1]


def escape_tag_value(value):
    return str(value).replace(" ", "_").replace(",", "_").replace("=", "_")


def build_line(measurement, tags, fields, timestamp_ns):
    tag_str = "".join(f",{k}={escape_tag_value(v)}" for k, v in tags.items())
    field_str = ",".join(
        f"{k}={v}i" if isinstance(v, int) else f"{k}={v}" for k, v in fields.items()
    )
    return f"{measurement}{tag_str} {field_str} {timestamp_ns}"


def parse_stats(stats_json, player, previous, timestamp_ns):
    """Build InfluxDB lines from one player's stats.json content.

    `previous` is the snapshot dict returned by the prior call for this player (or None on the
    first run). Derived per-hour rates are computed from the delta against that snapshot, not
    from cumulative totals, so they reflect recent activity rather than a lifetime average.

    Returns (lines, snapshot) — persist `snapshot` and pass it back in as `previous` next time.
    """
    stats = stats_json.get("stats", {})
    mined = stats.get("minecraft:mined", {})
    killed = stats.get("minecraft:killed", {})
    custom = stats.get("minecraft:custom", {})
    picked_up = stats.get("minecraft:picked_up", {})

    tags = {"player": player}
    lines = []

    blocks_mined_total = sum(mined.values())
    mobs_killed_total = sum(killed.values())
    items_picked_total = sum(picked_up.values())
    playtime_ticks = custom.get("minecraft:play_time", custom.get("minecraft:play_one_minute", 0))
    playtime_hours = playtime_ticks / 20 / 3600

    summary_fields = {
        "blocks_mined_total": blocks_mined_total,
        "mobs_killed_total": mobs_killed_total,
        "playtime_hours": round(playtime_hours, 4),
        "deaths": custom.get("minecraft:deaths", 0),
        "jumps": custom.get("minecraft:jump", 0),
        "damage_dealt": custom.get("minecraft:damage_dealt", 0),
        "damage_taken": custom.get("minecraft:damage_taken", 0),
    }
    for field_name, stat_key in COUNTER_FIELDS.items():
        summary_fields[field_name] = custom.get(f"minecraft:{stat_key}", 0)
    for field_name, stat_key in TIME_FIELDS.items():
        summary_fields[field_name] = round(custom.get(f"minecraft:{stat_key}", 0) / 20 / 3600, 4)
    for field_name, stat_key in DISTANCE_FIELDS.items():
        summary_fields[field_name] = round(custom.get(f"minecraft:{stat_key}", 0) / 100, 2)

    lines.append(build_line("mc_stats_summary", tags, summary_fields, timestamp_ns))

    for block_key, count in mined.items():
        block_tags = dict(tags, block=strip_namespace(block_key))
        lines.append(build_line("mc_stats_block", block_tags, {"count": count}, timestamp_ns))

    for mob_key, count in killed.items():
        mob_tags = dict(tags, mob=strip_namespace(mob_key))
        lines.append(build_line("mc_stats_mob", mob_tags, {"count": count}, timestamp_ns))

    for stat_name in STATION_STATS:
        count = custom.get(f"minecraft:{stat_name}", 0)
        if count:
            station_tags = dict(tags, station=stat_name.replace("interact_with_", ""))
            lines.append(build_line("mc_stats_station", station_tags, {"count": count}, timestamp_ns))

    for stat_name in AUTOMATION_STATS:
        count = custom.get(f"minecraft:{stat_name}", 0)
        if count:
            action_tags = dict(tags, action=stat_name)
            lines.append(build_line("mc_stats_automation", action_tags, {"count": count}, timestamp_ns))

    for stat_name in CONTAINER_STATS:
        count = custom.get(f"minecraft:{stat_name}", 0)
        if count:
            container_tags = dict(tags, container=stat_name.replace("open_", ""))
            lines.append(build_line("mc_stats_container", container_tags, {"count": count}, timestamp_ns))

    for stat_name in OTHER_STATS:
        count = custom.get(f"minecraft:{stat_name}", 0)
        if count:
            action_tags = dict(tags, action=stat_name)
            lines.append(build_line("mc_stats_other", action_tags, {"count": count}, timestamp_ns))

    snapshot = {
        "blocks_mined_total": blocks_mined_total,
        "items_picked_total": items_picked_total,
        "playtime_hours": playtime_hours,
    }

    if previous:
        hours_delta = snapshot["playtime_hours"] - previous["playtime_hours"]
        if hours_delta > 0:
            blocks_delta = max(snapshot["blocks_mined_total"] - previous["blocks_mined_total"], 0)
            items_delta = max(snapshot["items_picked_total"] - previous["items_picked_total"], 0)
            derived_fields = {
                "blocks_mined_per_hour": round(blocks_delta / hours_delta, 2),
                "items_picked_per_hour": round(items_delta / hours_delta, 2),
            }
            lines.append(build_line("mc_stats_derived", tags, derived_fields, timestamp_ns))

    return lines, snapshot


def parse_log_lines(lines, now, open_sessions):
    """Detect join/leave events in a batch of raw server log lines.

    Vanilla logs only carry a time-of-day (no date), so we timestamp each event with `now`
    (the wall-clock time we observed it) rather than trying to parse an unreliable date.
    `open_sessions` (player -> join datetime) is mutated in place so state survives across calls.

    Returns a list of completed sessions: {"player", "join", "leave", "duration_seconds"}.
    """
    completed = []
    for line in lines:
        join_match = JOIN_RE.search(line)
        if join_match:
            open_sessions[join_match.group(1)] = now
            continue
        leave_match = LEAVE_RE.search(line)
        if leave_match:
            player = leave_match.group(1)
            join_time = open_sessions.pop(player, None)
            if join_time is None:
                continue  # exporter started mid-session, no join event was ever seen
            completed.append({
                "player": player,
                "join": join_time,
                "leave": now,
                "duration_seconds": (now - join_time).total_seconds(),
            })
    return completed


def session_to_line(session, timestamp_ns):
    tags = {"player": session["player"]}
    fields = {
        "join_time": int(session["join"].timestamp()),
        "leave_time": int(session["leave"].timestamp()),
        "duration_seconds": int(session["duration_seconds"]),
    }
    return build_line("mc_session", tags, fields, timestamp_ns)


def compute_session_stats(all_sessions, first_seen, now, extra_played_dates=None):
    """Summarize session history: server age, current streak, longest session, total sessions.

    `extra_played_dates` lets an in-progress (not yet completed) session count toward today's
    streak, so the streak doesn't look broken while the player is still connected.
    """
    total_sessions = len(all_sessions)
    longest_session_seconds = int(max((s["duration_seconds"] for s in all_sessions), default=0))
    server_age_days = max((now - first_seen).days, 0)

    played_dates = {s["join"].date() for s in all_sessions}
    if extra_played_dates:
        played_dates |= set(extra_played_dates)
    played_dates = sorted(played_dates, reverse=True)

    current_streak_days = 0
    if played_dates:
        today = now.date()
        if played_dates[0] in (today, today - timedelta(days=1)):
            current_streak_days = 1
            expected = played_dates[0] - timedelta(days=1)
            for d in played_dates[1:]:
                if d == expected:
                    current_streak_days += 1
                    expected -= timedelta(days=1)
                else:
                    break

    return {
        "server_age_days": server_age_days,
        "current_streak_days": current_streak_days,
        "longest_session_seconds": longest_session_seconds,
        "total_sessions": total_sessions,
    }


def session_stats_to_line(stats, timestamp_ns):
    return build_line("mc_session_stats", {}, dict(stats), timestamp_ns)


def parse_server_health(lines):
    """Summarize server-health signals in a batch of raw log lines.

    Vanilla's own "Can't keep up! ... Running Nms behind, skipping M tick(s)" warning is the
    only lag indicator available without a mod, so we count those events and track the worst
    lag / total ticks skipped in this batch. Server (re)starts and the last startup duration
    come from the "Starting minecraft server" / "Done (Xs)!" lines.

    Emitted every cycle (zeros when healthy) so Grafana gets a continuous lag timeseries.
    """
    overload_events = 0
    max_ms_behind = 0
    ticks_skipped = 0
    server_starts = 0
    startup_seconds = None

    for line in lines:
        overload = OVERLOAD_RE.search(line)
        if overload:
            overload_events += 1
            max_ms_behind = max(max_ms_behind, int(overload.group(1)))
            ticks_skipped += int(overload.group(2))
            continue
        if SERVER_START_RE.search(line):
            server_starts += 1
            continue
        done = STARTUP_DONE_RE.search(line)
        if done:
            startup_seconds = float(done.group(1))

    return {
        "overload_events": overload_events,
        "max_ms_behind": max_ms_behind,
        "ticks_skipped": ticks_skipped,
        "server_starts": server_starts,
        "startup_seconds": startup_seconds,  # None unless a "Done" line appeared this batch
    }


def server_health_to_line(health, timestamp_ns):
    fields = {
        "overload_events": health["overload_events"],
        "max_ms_behind": health["max_ms_behind"],
        "ticks_skipped": health["ticks_skipped"],
        "server_starts": health["server_starts"],
    }
    if health["startup_seconds"] is not None:
        fields["startup_seconds"] = health["startup_seconds"]
    return build_line("mc_server_health", {}, fields, timestamp_ns)
