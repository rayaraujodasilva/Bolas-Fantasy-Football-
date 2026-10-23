import html
import io
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Fantasy Hub", layout="wide")
st.title("Fantasy Hub")

cover = Path(__file__).parent / "cover.jpg"
if cover.exists():
    st.image(str(cover), width=700)

SLEEPER = "https://api.sleeper.app/v1"
SLEEPER_PROJ = "https://api.sleeper.app/projections/nfl"

# Paste your league ID between the quotes so it loads automatically.
LEAGUE_ID = ""

# Last week to count for rest-of-season projections.
END_WEEK = 17

TEAM_COLORS = {
    "ARI": "#97233F", "ATL": "#A71930", "BAL": "#6B4FD8", "BUF": "#2F6BFF",
    "CAR": "#0085CA", "CHI": "#C83803", "CIN": "#FB4F14", "CLE": "#FF3C00",
    "DAL": "#3A6FD8", "DEN": "#FB4F14", "DET": "#0076B6", "GB": "#FFB612",
    "HOU": "#A71930", "IND": "#3F8FD6", "JAX": "#00859B", "KC": "#E31837",
    "LV": "#A5ACAF", "LAC": "#0080C6", "LAR": "#3C6BD6", "MIA": "#008E97",
    "MIN": "#7C4DC4", "NE": "#C60C30", "NO": "#D3BC8D", "NYG": "#4A73E0",
    "NYJ": "#1F8F6A", "PHI": "#06A79E", "PIT": "#FFB612", "SF": "#AA0000",
    "SEA": "#69BE28", "TB": "#D50A0A", "TEN": "#4B92DB", "WAS": "#FFB612",
}

POS_COLORS = {
    "QB": "#FF2A6D", "RB": "#00CEB8", "WR": "#58A7FF",
    "TE": "#FFAE58", "K": "#BD66FF", "DEF": "#7988A1",
}

# Nicer short names for lineup slots
SLOT_NAMES = {
    "SUPER_FLEX": "SF", "FLEX": "FLX", "REC_FLEX": "FLX", "WRRB_FLEX": "FLX",
    "IDP_FLEX": "IDP",
}

# ESPN spells Washington differently than Sleeper does
ESPN_LOGO_CODES = {"WAS": "wsh"}

# nflverse (free NFL data on GitHub): targets, snaps, schedule, and an ID translation table
STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
SNAPS_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
IDS_URL = "https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv"

# Sleeper calls the Rams "LAR", nflverse calls them "LA"
TEAM_TO_NFLVERSE = {"LAR": "LA"}
TEAM_FROM_NFLVERSE = {"LA": "LAR"}

POS_GRADED = ["QB", "RB", "WR", "TE"]
GRADE_COLORS = {
    "A+": "#22c55e", "A": "#4ade80", "B": "#a3e635",
    "C": "#facc15", "D": "#fb923c", "F": "#f87171",
}
GRADE_DOTS = {"A+": "🟢", "A": "🟢", "B": "🟡", "C": "🟡", "D": "🟠", "F": "🔴"}


# ---------- Data pulls (cached so we don't hammer the APIs) ----------

@st.cache_data(ttl=86400)  # 24 hrs. Sleeper asks you not to pull this file more than once a day.
def get_all_players():
    """Big dict of every NFL player. Keys are player IDs like '4881'."""
    r = requests.get(f"{SLEEPER}/players/nfl", timeout=30)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def get_league(league_id):
    league = requests.get(f"{SLEEPER}/league/{league_id}", timeout=10)
    league.raise_for_status()
    rosters = requests.get(f"{SLEEPER}/league/{league_id}/rosters", timeout=10)
    rosters.raise_for_status()
    users = requests.get(f"{SLEEPER}/league/{league_id}/users", timeout=10)
    users.raise_for_status()
    return league.json(), rosters.json(), users.json()


@st.cache_data(ttl=600)
def get_state():
    """What season and week it is right now."""
    r = requests.get(f"{SLEEPER}/state/nfl", timeout=10)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=3600)
def get_values(is_dynasty, num_qbs, num_teams, ppr):
    """Trade values from FantasyCalc. Returns {sleeper_id: value}."""
    url = "https://api.fantasycalc.com/values/current"
    params = {
        "isDynasty": str(is_dynasty).lower(),
        "numQbs": num_qbs,
        "numTeams": num_teams,
        "ppr": ppr,
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    out = {}
    for item in r.json():
        sid = item["player"].get("sleeperId")
        if sid:
            out[str(sid)] = max(item["value"], 0)
    return out


@st.cache_data(ttl=3600)
def get_recent_avg(n, score_key):
    """Average fantasy points over the last n completed weeks. Returns {player_id: avg}."""
    state = get_state()
    season = state["season"]
    last_week = int(state["week"]) - 1  # current week isn't finished yet

    scores = {}
    for wk in range(max(1, last_week - n + 1), last_week + 1):
        r = requests.get(f"{SLEEPER}/stats/nfl/regular/{season}/{wk}", timeout=15)
        r.raise_for_status()
        for pid, stats in r.json().items():
            pts = stats.get(score_key)
            if pts is not None:
                scores.setdefault(pid, []).append(pts)

    return {pid: sum(v) / len(v) for pid, v in scores.items()}


@st.cache_data(ttl=3600)
def get_ros(season, start_week, end_week, score_key):
    """
    Rest-of-season projection = add up Sleeper's weekly projections for every
    week still to play. Returns totals, week-by-week points, and how many weeks had data.
    """
    weekly = {}  # {player_id: {week: points}}
    weeks_loaded = 0
    positions = ["QB", "RB", "WR", "TE", "K", "DEF"]
    for wk in range(start_week, end_week + 1):
        params = [("season_type", "regular")] + [("position[]", p) for p in positions]
        try:
            r = requests.get(f"{SLEEPER_PROJ}/{season}/{wk}", params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue  # skip a week that fails, keep going

        # The response can come back as a list or as a dict, so handle both.
        if isinstance(data, dict):
            pairs = [(pid, v) for pid, v in data.items()]
        else:
            pairs = [(item.get("player_id"), item) for item in data if isinstance(item, dict)]

        found_any = False
        for pid, item in pairs:
            if not pid or not isinstance(item, dict):
                continue
            stats = item.get("stats") if isinstance(item.get("stats"), dict) else item
            pts = stats.get(score_key)
            if pts is not None:
                weekly.setdefault(str(pid), {})[wk] = pts
                found_any = True
        if found_any:
            weeks_loaded += 1

    totals = {pid: sum(w.values()) for pid, w in weekly.items()}
    return {"totals": totals, "weekly": weekly, "weeks_loaded": weeks_loaded}


def _read_csv(url, usecols=None):
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content), usecols=usecols, low_memory=False)


def rank_to_grade(rank, total):
    """Rank 1 = the defense that gives up the MOST points to that position (best matchup)."""
    pct = rank / total
    if pct <= 0.125:
        return "A+"
    if pct <= 0.25:
        return "A"
    if pct <= 0.45:
        return "B"
    if pct <= 0.65:
        return "C"
    if pct <= 0.85:
        return "D"
    return "F"


def _num(x):
    return None if pd.isna(x) else float(x)


@st.cache_data(ttl=86400)
def get_id_map():
    """Translate between ID systems. {sleeper_id: {"gsis": ..., "pfr": ..., "yahoo": ...}}"""
    df = _read_csv(IDS_URL, usecols=["sleeper_id", "gsis_id", "pfr_id", "yahoo_id"])
    df = df[df["sleeper_id"].notna()]
    out = {}
    for sid, gsis, pfr, yahoo in zip(df["sleeper_id"], df["gsis_id"], df["pfr_id"], df["yahoo_id"]):
        out[str(int(sid))] = {
            "gsis": gsis if isinstance(gsis, str) else None,
            "pfr": pfr if isinstance(pfr, str) else None,
            "yahoo": str(int(yahoo)) if pd.notna(yahoo) else None,
        }
    return out


@st.cache_data(ttl=21600)
def get_nfl_data(season, week, rec_value, n):
    """
    Targets, snaps, and matchup grades from nflverse.
    usage:    {gsis_id: last n games of targets/carries}
    snaps:    {pfr_id: last n games of snap %}
    matchups: {(defense, position): points allowed + rank + grade}
    opp:      {team: this week's opponent}
    """
    out = {"usage": {}, "snaps": {}, "matchups": {}, "opp": {}, "weeks": []}

    stats = _read_csv(STATS_URL.format(season=season), usecols=[
        "player_id", "position", "opponent_team", "week", "season_type",
        "targets", "target_share", "receptions", "carries", "fantasy_points",
    ])
    stats = stats[stats["season_type"] == "REG"]
    weeks = sorted(stats["week"].unique())
    recent_weeks = weeks[-n:]
    out["weeks"] = [int(w) for w in recent_weeks]

    # --- Targets / carries over the last n games ---
    recent = stats[stats["week"].isin(recent_weeks)].sort_values("week")
    for gsis, g in recent.groupby("player_id"):
        out["usage"][gsis] = {
            "targets": g["targets"].fillna(0).astype(float).tolist(),
            "avg_targets": float(g["targets"].fillna(0).mean()),
            "avg_share": float(g["target_share"].fillna(0).mean()) * 100,
            "avg_carries": float(g["carries"].fillna(0).mean()),
        }

    # --- Matchup grades: how many fantasy points each defense gives up to each position ---
    skill = stats[stats["position"].isin(POS_GRADED)].copy()
    skill["fp"] = skill["fantasy_points"].fillna(0) + rec_value * skill["receptions"].fillna(0)
    per_game = skill.groupby(["week", "opponent_team", "position"])["fp"].sum().reset_index()
    allowed = per_game.groupby(["opponent_team", "position"])["fp"].mean().reset_index()
    for pos, g in allowed.groupby("position"):
        g = g.sort_values("fp", ascending=False).reset_index(drop=True)
        for i, row in g.iterrows():
            out["matchups"][(row["opponent_team"], pos)] = {
                "avg": float(row["fp"]),
                "rank": i + 1,
                "grade": rank_to_grade(i + 1, len(g)),
            }

    # --- Snap % over the last n games (separate file, so don't fail everything if it's missing) ---
    try:
        snaps = _read_csv(SNAPS_URL.format(season=season), usecols=[
            "game_type", "week", "pfr_player_id", "offense_pct",
        ])
        snaps = snaps[snaps["game_type"] == "REG"]
        snap_weeks = sorted(snaps["week"].unique())[-n:]
        recent_snaps = snaps[snaps["week"].isin(snap_weeks)].sort_values("week")
        for pfr, g in recent_snaps.groupby("pfr_player_id"):
            pct = (g["offense_pct"].fillna(0) * 100).round(0).astype(float)
            out["snaps"][pfr] = {"pct": pct.tolist(), "avg": float(pct.mean())}
    except Exception:
        pass

    # --- This week's opponents ---
    try:
        games = _read_csv(GAMES_URL, usecols=[
            "season", "game_type", "week", "away_team", "home_team", "roof", "temp", "wind",
        ])
        games = games[
            (games["season"] == int(season)) & (games["game_type"] == "REG") & (games["week"] == int(week))
        ]
        for _, r in games.iterrows():
            info = {
                "roof": r["roof"] if isinstance(r["roof"], str) else "",
                "temp": _num(r["temp"]),
                "wind": _num(r["wind"]),
            }
            out["opp"][r["home_team"]] = {"opp": r["away_team"], "where": "vs", **info}
            out["opp"][r["away_team"]] = {"opp": r["home_team"], "where": "@", **info}
    except Exception:
        pass

    return out


# ---------- Helpers ----------

def player_name(players, pid):
    p = players.get(pid)
    if not p:
        return f"Unknown ({pid})"
    if p.get("full_name"):
        return p["full_name"]
    # Team defenses don't have a full_name
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid


def team_logo_url(team):
    code = ESPN_LOGO_CODES.get(team, team.lower())
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{code}.png"


def headshot_url(players, pid):
    p = players.get(pid, {})
    espn_id = p.get("espn_id")
    if espn_id:
        return f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"
    return f"https://sleepercdn.com/content/nfl/players/thumb/{pid}.jpg"


def photo_url(players, pid):
    """Headshot for players, team logo for defenses."""
    if players.get(pid, {}).get("position") == "DEF":
        return team_logo_url(pid)
    return headshot_url(players, pid)


def matchup_info(nfl, players, pid):
    """This week's opponent and matchup grade for one player (None if we have no schedule data)."""
    p = players.get(pid, {})
    pos = p.get("position")
    team = p.get("team")
    if not team or pos not in POS_GRADED or not nfl["opp"]:
        return None
    game = nfl["opp"].get(TEAM_TO_NFLVERSE.get(team, team))
    if not game:
        return {"bye": True}
    m = nfl["matchups"].get((game["opp"], pos)) or {}
    return {
        "opp": TEAM_FROM_NFLVERSE.get(game["opp"], game["opp"]),
        "where": game["where"],
        "roof": game["roof"],
        "grade": m.get("grade"),
        "rank": m.get("rank"),
        "avg": m.get("avg"),
    }


def matchup_chip_html(info):
    if not info:
        return ""
    sep = '<span style="color:#4a5a78;">|</span>'
    if info.get("bye"):
        return sep + '<span style="font-size:12px;color:#9fb3d1;font-weight:600;">BYE</span>'
    chip = sep + (
        f'<span style="font-size:12px;color:#9fb3d1;">{info["where"]} {html.escape(info["opp"])}</span>'
    )
    if info.get("grade"):
        chip += (
            f'<span style="background:{GRADE_COLORS[info["grade"]]};color:#0b1220;font-weight:700;'
            f'font-size:11px;padding:1px 7px;border-radius:5px;">{info["grade"]}</span>'
        )
    return chip


def card_html(players, values, pid, slot=None, dim=False, extra=""):
    """One player card: headshot, position tag, name, team logo, value."""
    p = players.get(pid, {})
    pos = p.get("position") or "?"
    team = p.get("team") or ""
    team_color = TEAM_COLORS.get(team, "#5b6475")
    pos_color = POS_COLORS.get(pos, "#7988A1")
    name = html.escape(player_name(players, pid))
    val = values.get(pid)
    val_txt = f"{val:,.0f}" if val else "&ndash;"

    # Team defenses get the team logo instead of a face
    if pos == "DEF":
        photo_src = team_logo_url(pid)
        photo_fit = "contain"
    else:
        photo_src = headshot_url(players, pid)
        photo_fit = "cover"

    bg = "#161e2c" if dim else "#1f2b40"
    opacity = "0.75" if dim else "1"
    border = "3px" if dim else "5px"

    slot_html = ""
    if slot:
        label = html.escape(SLOT_NAMES.get(slot, slot))
        slot_html = (
            f'<span style="background:#2a3650;color:#9fb3d1;font-weight:700;font-size:11px;'
            f'padding:4px 0;border-radius:6px;min-width:36px;text-align:center;">{label}</span>'
        )

    team_html = ""
    if team:
        team_html = (
            f'<img src="{team_logo_url(team)}" width="16" height="16" '
            f'style="object-fit:contain;" alt="">'
            f'<span style="font-size:12px;font-weight:600;color:{team_color};">{html.escape(team)}</span>'
        )

    return (
        f'<div style="display:flex;align-items:center;gap:12px;padding:8px 14px;margin-bottom:6px;'
        f'background:{bg};border-left:{border} solid {team_color};border-radius:8px;opacity:{opacity};">'
        f'{slot_html}'
        f'<img src="{photo_src}" width="46" height="46" alt="" '
        f'style="border-radius:50%;object-fit:{photo_fit};background:{team_color}44;flex-shrink:0;">'
        f'<span style="background:{pos_color};color:#0b1220;font-weight:700;font-size:12px;'
        f'padding:3px 8px;border-radius:6px;min-width:34px;text-align:center;">{html.escape(pos)}</span>'
        f'<div style="flex:1;"><div style="font-weight:600;color:#ffffff;">{name}</div>'
        f'<div style="display:flex;align-items:center;gap:6px;margin-top:2px;">{team_html}{extra}</div></div>'
        f'<div style="font-weight:700;font-size:18px;color:#e6edf2;">{val_txt}</div></div>'
    )


def empty_slot_html(slot):
    label = html.escape(SLOT_NAMES.get(slot, slot)) if slot else "?"
    return (
        f'<div style="display:flex;align-items:center;gap:12px;padding:12px 14px;margin-bottom:6px;'
        f'border:1px dashed #3a4a66;border-radius:8px;color:#6f819f;">'
        f'<span style="font-weight:700;font-size:11px;min-width:36px;text-align:center;">{label}</span>'
        f'<span>Empty slot</span></div>'
    )


def render_lineup(roster, slot_labels, players, values, chip_fn=None):
    chip = chip_fn if chip_fn else (lambda pid: "")
    starters = roster.get("starters") or []
    reserve = roster.get("reserve") or []
    taxi = roster.get("taxi") or []
    everyone = roster.get("players") or []

    used = set(starters) | set(reserve) | set(taxi)
    bench = [pid for pid in everyone if pid not in used]
    bench.sort(key=lambda pid: values.get(pid) or 0, reverse=True)

    starter_total = sum(values.get(pid) or 0 for pid in starters if pid != "0")
    bench_total = sum(values.get(pid) or 0 for pid in bench)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total roster value", f"{starter_total + bench_total:,.0f}")
    m2.metric("Starters value", f"{starter_total:,.0f}")
    m3.metric("Bench value", f"{bench_total:,.0f}")

    st.markdown("##### Starters")
    cards = []
    for i, pid in enumerate(starters):
        slot = slot_labels[i] if i < len(slot_labels) else None
        if pid == "0":
            cards.append(empty_slot_html(slot))
        else:
            cards.append(card_html(players, values, pid, slot=slot, extra=chip(pid)))
    if cards:
        st.markdown("".join(cards), unsafe_allow_html=True)
    else:
        st.info("No starters set for this team.")

    if bench:
        st.markdown("##### Bench")
        st.markdown(
            "".join(card_html(players, values, pid, dim=True, extra=chip(pid)) for pid in bench),
            unsafe_allow_html=True,
        )

    if reserve:
        st.markdown("##### IR")
        st.markdown(
            "".join(card_html(players, values, pid, dim=True, extra=chip(pid)) for pid in reserve),
            unsafe_allow_html=True,
        )

    if taxi:
        st.markdown("##### Taxi")
        st.markdown(
            "".join(card_html(players, values, pid, dim=True, extra=chip(pid)) for pid in taxi),
            unsafe_allow_html=True,
        )


def render_ros(roster, players, values, ros, avgs, key):
    starters = set(pid for pid in (roster.get("starters") or []) if pid != "0")
    reserve = set(roster.get("reserve") or [])
    taxi = set(roster.get("taxi") or [])
    rows = []
    for pid in roster.get("players") or []:
        p = players.get(pid, {})
        proj = ros["totals"].get(pid)
        rows.append({
            "pid": pid,
            "Photo": photo_url(players, pid),
            "Player": player_name(players, pid),
            "Pos": p.get("position", ""),
            "Team": p.get("team", ""),
            "Status": (
                "Starter" if pid in starters
                else "IR" if pid in reserve
                else "Taxi" if pid in taxi
                else "Bench"
            ),
            "ROS proj pts": round(proj, 1) if proj is not None else None,
            "Avg last 4 wks": round(avgs[pid], 1) if pid in avgs else None,
            "Value": values.get(pid),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("This roster is empty.")
        return

    df["ROS proj pts"] = pd.to_numeric(df["ROS proj pts"], errors="coerce")
    df["Avg last 4 wks"] = pd.to_numeric(df["Avg last 4 wks"], errors="coerce")
    df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
    df = df.sort_values("ROS proj pts", ascending=False, na_position="last")

    starter_ros = df.loc[df["Status"] == "Starter", "ROS proj pts"].sum()
    c1, c2 = st.columns(2)
    c1.metric("Starters' projected ROS pts", f"{starter_ros:,.0f}")
    c2.metric("Whole roster projected ROS pts", f"{df['ROS proj pts'].sum():,.0f}")

    # ----- Line chart: projected points week by week -----
    st.markdown("##### Projected points by week")
    weekly = ros.get("weekly", {})
    chartable = df[df["ROS proj pts"].notna()]
    if chartable.empty:
        st.info("No weekly projections to chart yet.")
    else:
        names = chartable["Player"].tolist()  # already sorted best to worst
        name_to_pid = dict(zip(chartable["Player"], chartable["pid"]))
        picked = st.multiselect(
            "Players to chart", names, default=names[:5], key=f"ros_pick_{key}"
        )
        view = st.radio(
            "View", ["Weekly", "Cumulative"], horizontal=True, key=f"ros_view_{key}"
        )
        if picked:
            weeks = sorted({wk for n in picked for wk in weekly.get(name_to_pid[n], {})})
            chart_df = pd.DataFrame(
                {n: [weekly.get(name_to_pid[n], {}).get(wk) for wk in weeks] for n in picked},
                index=[f"Wk {wk:02d}" for wk in weeks],  # zero-padded so weeks stay in order
            ).astype(float)
            if view == "Cumulative":
                chart_df = chart_df.fillna(0).cumsum()
            st.line_chart(chart_df)
        else:
            st.info("Pick at least one player to chart.")

    # ----- Full table -----
    st.markdown("##### Full roster")
    max_proj = float(df["ROS proj pts"].max()) if df["ROS proj pts"].notna().any() else 1.0
    st.dataframe(
        df.drop(columns=["pid"]),
        hide_index=True,
        column_config={
            "Photo": st.column_config.ImageColumn(" ", width="small"),
            "ROS proj pts": st.column_config.ProgressColumn(
                "ROS proj pts", min_value=0, max_value=max(max_proj, 1.0), format="%.1f"
            ),
            "Avg last 4 wks": st.column_config.NumberColumn("Avg last 4 wks", format="%.1f"),
            "Value": st.column_config.NumberColumn("Value", format="%d"),
        },
    )


def render_usage(roster, players, values, nfl, id_map, key):
    starters = [pid for pid in (roster.get("starters") or []) if pid != "0"]
    starter_set = set(starters)
    reserve = set(roster.get("reserve") or [])
    taxi = set(roster.get("taxi") or [])
    others = [pid for pid in (roster.get("players") or []) if pid not in starter_set]
    others.sort(key=lambda pid: values.get(pid) or 0, reverse=True)

    rows = []
    for pid in starters + others:
        p = players.get(pid, {})
        pos = p.get("position", "")
        if pos not in POS_GRADED:
            continue
        ids = id_map.get(pid, {})
        gsis = ids.get("gsis") or p.get("gsis_id")
        use = nfl["usage"].get(gsis) if gsis else None
        snap = nfl["snaps"].get(ids.get("pfr")) if ids.get("pfr") else None
        info = matchup_info(nfl, players, pid)

        if info is None:
            opp_txt, grade_txt, allowed = "", "", None
        elif info.get("bye"):
            opp_txt, grade_txt, allowed = "BYE", "", None
        else:
            opp_txt = f'{info["where"]} {info["opp"]}'
            grade = info.get("grade")
            grade_txt = f"{GRADE_DOTS[grade]} {grade}" if grade else ""
            allowed = round(info["avg"], 1) if info.get("avg") is not None else None

        rows.append({
            "Photo": photo_url(players, pid),
            "Player": player_name(players, pid),
            "Pos": pos,
            "Team": p.get("team", ""),
            "Status": (
                "Starter" if pid in starter_set
                else "IR" if pid in reserve
                else "Taxi" if pid in taxi
                else "Bench"
            ),
            "Opp": opp_txt,
            "Matchup": grade_txt,
            "Pts allowed to pos": allowed,
            "Avg targets": round(use["avg_targets"], 1) if use else None,
            "Target share %": round(use["avg_share"], 1) if use else None,
            "Avg carries": round(use["avg_carries"], 1) if use else None,
            "Avg snap %": round(snap["avg"]) if snap else None,
            "Targets by game": use["targets"] if use else None,
            "Snap % by game": snap["pct"] if snap else None,
        })

    if not rows:
        st.info("No QB/RB/WR/TE on this roster to show.")
        return

    df = pd.DataFrame(rows)

    graded = df[(df["Status"] == "Starter") & (df["Matchup"] != "")]
    good = graded["Matchup"].str.contains(r"A\+|A$|B$", regex=True).sum() if not graded.empty else 0
    c1, c2 = st.columns(2)
    c1.metric("Starters with a good matchup (A or B)", f"{good} of {len(graded)}")
    if nfl["weeks"]:
        c2.metric("Games used for usage stats", f"Weeks {nfl['weeks'][0]}-{nfl['weeks'][-1]}")

    st.caption(
        "Matchup grade = how many fantasy points that defense has allowed to the player's position "
        "this season, ranked among all 32 teams (A+ = gives up the most). It's a proxy, "
        "not true receiver-vs-cornerback coverage."
    )
    st.dataframe(
        df,
        hide_index=True,
        column_config={
            "Photo": st.column_config.ImageColumn(" ", width="small"),
            "Avg targets": st.column_config.NumberColumn(format="%.1f"),
            "Target share %": st.column_config.NumberColumn(format="%.1f"),
            "Avg carries": st.column_config.NumberColumn(format="%.1f"),
            "Avg snap %": st.column_config.NumberColumn(format="%d"),
            "Targets by game": st.column_config.LineChartColumn("Targets by game", y_min=0),
            "Snap % by game": st.column_config.LineChartColumn("Snap % by game", y_min=0, y_max=100),
        },
    )


# ---------- UI ----------

league_id = st.text_input(
    "Sleeper League ID",
    value=LEAGUE_ID,
    help="Paste the league ID from the Sleeper URL.",
).strip()

if not league_id:
    st.info("Enter your Sleeper league ID to load the league data.")
    st.stop()

try:
    league, rosters, users = get_league(league_id)
except Exception as e:
    st.error(f"Couldn't load that league. Check the ID and try again. ({e})")
    st.stop()

with st.spinner("Loading players..."):
    try:
        players = get_all_players()
    except Exception as e:
        st.error(f"Couldn't load the player database. ({e})")
        st.stop()

# Figure out league settings so the numbers match YOUR league
num_teams = int(league.get("total_rosters", 12) or 12)
scoring_settings = league.get("scoring_settings") or {}
ppr = float(scoring_settings.get("rec", 0) or 0)
num_qbs = 2 if "SUPER_FLEX" in (league.get("roster_positions") or []) else 1
is_dynasty = bool(league.get("settings", {}).get("type") == 2)

# Which of Sleeper's point columns matches this league's PPR
if ppr >= 1:
    score_key = "pts_ppr"
elif ppr > 0:
    score_key = "pts_half_ppr"
else:
    score_key = "pts_std"

# Lineup slot names in order, with bench slots removed (matches roster["starters"])
slot_labels = [s for s in (league.get("roster_positions") or []) if s != "BN"]

try:
    values = get_values(is_dynasty, num_qbs, num_teams, ppr)
except Exception as e:
    values = {}
    st.warning(f"Couldn't pull player values: {e}")

# Season/week, recent points, rest-of-season projections
season = None
start_week = 1
try:
    state = get_state()
    season = state.get("season")
    start_week = max(int(state.get("week") or 1), 1)
except Exception as e:
    st.warning(f"Couldn't load the NFL week. ({e})")

try:
    avgs = get_recent_avg(4, score_key)
except Exception as e:
    avgs = {}
    st.warning(f"Couldn't load recent player points. ({e})")

ros = {"totals": {}, "weeks_loaded": 0}
if season:
    with st.spinner("Loading rest-of-season projections (first load is slow)..."):
        try:
            ros = get_ros(season, start_week, END_WEEK, score_key)
        except Exception as e:
            st.warning(f"Couldn't load projections. ({e})")

# Targets, snaps, matchup grades (nflverse) + the ID translation table
nfl = {"usage": {}, "snaps": {}, "matchups": {}, "opp": {}, "weeks": []}
id_map = {}
if season:
    with st.spinner("Loading targets, snaps and matchups (first load is slow)..."):
        try:
            id_map = get_id_map()
        except Exception as e:
            st.warning(f"Couldn't load the player ID table. ({e})")
        try:
            nfl = get_nfl_data(int(season), start_week, ppr, 4)
        except Exception as e:
            st.warning(f"Couldn't load targets/snaps/matchups. ({e})")

chip_fn = lambda pid: matchup_chip_html(matchup_info(nfl, players, pid))

# Map owner_id -> team name
team_names = {}
for u in users:
    team_names[u["user_id"]] = (
        u.get("metadata", {}).get("team_name") or u.get("display_name", "Unknown")
    )

st.subheader(league.get("name", "Your league"))

if not rosters:
    st.info("This league doesn't have any rosters yet.")
    st.stop()

# One tab per team, and inside each: Lineup + ROS
team_labels = [team_names.get(r.get("owner_id"), f"Team {r.get('roster_id', 'Unknown')}") for r in rosters]
tabs = st.tabs(team_labels)

for tab, roster in zip(tabs, rosters):
    with tab:
        lineup_tab, ros_tab, usage_tab = st.tabs(["Lineup", "ROS Projections", "Usage & Matchups"])
        with lineup_tab:
            if roster.get("players"):
                render_lineup(roster, slot_labels, players, values, chip_fn)
            else:
                st.info("This roster is empty.")
        with ros_tab:
            weeks_left = max(END_WEEK - start_week + 1, 0)
            st.caption(
                f"Sleeper projections found for {ros['weeks_loaded']} of {weeks_left} "
                f"remaining weeks (week {start_week} through {END_WEEK})."
            )
            if ros["weeks_loaded"] == 0:
                st.info("No projections came back, so only recent points and values are shown.")
            render_ros(roster, players, values, ros, avgs, roster.get("roster_id"))
        with usage_tab:
            render_usage(roster, players, values, nfl, id_map, roster.get("roster_id"))

# ---------- Trade Checker ----------
st.divider()
st.header("Trade Checker")

team_players = {}  # team -> {dropdown label: trade value}
team_pids = {}     # team -> {dropdown label: player id}
for roster, label in zip(rosters, team_labels):
    options = {}
    pids = {}
    for pid in roster.get("players") or []:
        p = players.get(pid, {})
        tag = f"{player_name(players, pid)} ({p.get('position', '')})"
        options[tag] = values.get(pid) or 0
        pids[tag] = pid
    team_players[label] = options
    team_pids[label] = pids

all_teams = list(team_players.keys())
if len(all_teams) < 2:
    st.info("At least two teams are needed to check a trade.")
else:
    c1, c2 = st.columns(2)

    with c1:
        my_team = st.selectbox("Your team", all_teams, key="my_team")
        give = st.multiselect(
            "You give", list(team_players[my_team].keys()), key=f"give_{my_team}"
        )
        if give:
            st.markdown(
                "".join(card_html(players, values, team_pids[my_team][t]) for t in give),
                unsafe_allow_html=True,
            )

    partners = [team for team in all_teams if team != my_team]
    with c2:
        their_team = st.selectbox("Trade partner", partners, key="their_team")
        get = st.multiselect(
            "You get", list(team_players[their_team].keys()), key=f"get_{their_team}"
        )
        if get:
            st.markdown(
                "".join(card_html(players, values, team_pids[their_team][t]) for t in get),
                unsafe_allow_html=True,
            )

    give_total = sum(team_players[my_team][t] for t in give)
    get_total = sum(team_players[their_team][t] for t in get)
    diff = get_total - give_total

    # Always show the gap as a positive number; the label says which way it goes.
    m1, m2, m3 = st.columns(3)
    m1.metric("You give", f"{give_total:,.0f}")
    m2.metric("You get", f"{get_total:,.0f}")
    if diff > 0:
        m3.metric("Value gained", f"{diff:,.0f}")
    elif diff < 0:
        m3.metric("Value lost", f"{abs(diff):,.0f}")
    else:
        m3.metric("Value gap", "0")

    if give and get:
        biggest = max(give_total, get_total)
        if biggest > 0 and abs(diff) <= 0.10 * biggest:
            st.info("Pretty even trade.")
        elif diff > 0:
            st.success("You win this one on value.")
        else:
            st.error("You're giving up more value than you get.")

    if give or get:
        rows = []
        for side, picks, team in (("Give", give, my_team), ("Get", get, their_team)):
            for tag in picks:
                pid = team_pids[team][tag]
                proj = ros["totals"].get(pid)
                rows.append({
                    "Side": side,
                    "Player": tag,
                    "Value": team_players[team][tag],
                    "Last 4 wks avg pts": round(avgs.get(pid, 0), 1),
                    "ROS proj pts": round(proj, 1) if proj is not None else None,
                })
        st.dataframe(pd.DataFrame(rows), hide_index=True)