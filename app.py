import collections, contextlib, csv, io, json, os, re, shutil, sys, tempfile, threading, time, traceback
import duckdb, fmsave
import numpy as np
from nations import NATIONS
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from datadir import FILES, data_dir, list_saves, replace_data, resolve_save
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from urllib.parse import urlsplit
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", HERE)  # bundled read-only resources (static/) when frozen
try:  # written by the release CI from the git tag; absent when running from source
    VERSION = open(os.path.join(RES, "VERSION")).read().strip()
except OSError:
    VERSION = "dev"

STATUS = "empty"  # empty | loading | ready
_ingest_lock = threading.Lock()  # one load at a time (startup load or a save load)
LAST_PING, BYE_AT = time.monotonic(), None
OPEN_PATHS = {"/", "/api/status", "/api/saves", "/api/load-save", "/api/upload", "/api/ping", "/api/bye"}


def need_data(request: Request):
    if STATUS != "ready" and request.url.path not in OPEN_PATHS:
        raise HTTPException(409, "no_save" if STATUS == "empty" else STATUS)


@contextlib.asynccontextmanager
async def lifespan(_):
    threading.Thread(target=_load_data_dir, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan, dependencies=[Depends(need_data)])
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
MAX_UPLOAD = 4 * 1024**3  # largest honest .fm save is a few hundred MB


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Refuse DNS-rebinding (foreign Host), cross-site POSTs (foreign Origin) and oversized uploads."""
    host = request.headers.get("host", "").rsplit(":", 1)[0]
    origin = request.headers.get("origin")
    if host not in LOCAL_HOSTS or (origin is not None and urlsplit(origin).hostname not in LOCAL_HOSTS):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
        return JSONResponse({"detail": "too large"}, status_code=413)
    return await call_next(request)


_db = duckdb.connect()  # in-memory
_local = threading.local()


def con():
    """Per-thread cursor: FastAPI runs sync endpoints on a thread pool, and threads sharing one
    DuckDB connection can read each other's results."""
    if not hasattr(_local, "cur"):
        _local.cur = _db.cursor()
    return _local.cur

# Position groups -> positions_* columns (1-20 suitability, >=15 = accomplished or natural).
POS = {
    "GK": ["gk"], "CB": ["dc", "sw"], "FB": ["dl", "dr", "wbl", "wbr"], "DM": ["dm"],
    "CM": ["mc"], "W": ["ml", "mr", "aml", "amr"], "AM": ["amc"], "ST": ["stc"],
}
# Role -> (position group, {attribute: weight}). Key attributes weight 2, preferable 1. Score 0-100.
ROLES = {
    "sweeper_keeper": ("GK", dict(reflexes=2, one_on_ones=2, rushing_out=2, command_of_area=2, kicking=1, passing=1,
                                  first_touch=1, composure=2, anticipation=1, decisions=1, handling=1)),
    "ball_playing_def": ("CB", dict(passing=2, composure=2, marking=2, tackling=2, positioning=2, heading=1, strength=1,
                                    jumping_reach=1, vision=1, technique=1, decisions=1, anticipation=1)),
    "inverted_wingback": ("FB", dict(passing=2, first_touch=1, technique=1, decisions=2, composure=1, tackling=1,
                                     positioning=1, teamwork=2, anticipation=1, vision=1, stamina=1, work_rate=1)),
    "attacking_fullback": ("FB", dict(crossing=2, dribbling=1, stamina=2, work_rate=2, pace=1, acceleration=2,
                                      tackling=1, teamwork=1, off_the_ball=1, marking=1, positioning=1)),
    "anchor": ("DM", dict(tackling=2, marking=2, positioning=2, anticipation=2, concentration=2, decisions=1,
                          teamwork=1, strength=1, work_rate=1, composure=1)),
    "deep_playmaker": ("DM", dict(passing=2, vision=2, technique=2, first_touch=2, composure=2, decisions=2,
                                  teamwork=1, anticipation=1, off_the_ball=1)),
    "box_to_box": ("CM", dict(stamina=2, work_rate=2, passing=2, tackling=1, off_the_ball=2, teamwork=2,
                              determination=1, first_touch=1, finishing=1, long_shots=1, anticipation=1)),
    "inside_forward": ("W", dict(dribbling=2, finishing=2, acceleration=2, pace=1, technique=1, off_the_ball=2,
                                 first_touch=2, flair=1, composure=1, agility=1, anticipation=1)),
    "winger": ("W", dict(crossing=2, dribbling=2, acceleration=2, pace=2, technique=1, agility=2, off_the_ball=1,
                         first_touch=1, work_rate=1, flair=1)),
    "advanced_playmaker": ("AM", dict(passing=2, vision=2, technique=2, first_touch=2, flair=2, decisions=2,
                                      composure=1, dribbling=1, off_the_ball=1, agility=1)),
    "pressing_forward": ("ST", dict(work_rate=2, stamina=2, aggression=2, acceleration=2, anticipation=1,
                                    finishing=1, off_the_ball=1, teamwork=2, determination=1, composure=1, bravery=1)),
    "advanced_forward": ("ST", dict(finishing=2, off_the_ball=2, composure=2, acceleration=2, first_touch=2,
                                    dribbling=1, pace=1, anticipation=1, technique=1, decisions=1)),
    "target_man": ("ST", dict(heading=2, jumping_reach=2, strength=2, bravery=2, balance=1, finishing=1,
                              first_touch=1, composure=1, teamwork=1, aggression=1)),
}


def _role_sql():
    cols, fits = [], []
    for role, (grp, w) in ROLES.items():
        expr = " + ".join(f"{v}*attributes_{a}" for a, v in w.items())
        cols.append(f"round(({expr}) * 5.0 / {sum(w.values())}, 1) AS role_{role}")
        pos = ", ".join(f"positions_{p}" for p in POS[grp])
        fits.append((role, f"CASE WHEN greatest({pos}, 0) >= 15 THEN role_{role} END"))
    best = "greatest(" + ", ".join(f for _, f in fits) + ")"
    name = "CASE " + " ".join(f"WHEN {f} = best_role_score THEN '{r}'" for r, f in fits) + " END"
    return ", ".join(cols), best, name


NEW_UID = 2_000_000_000  # men's clubs predate FM's women's database; women's sections got uids above this


def club_genders(clubs, groups):
    """Label clubs 'Men'/'Women'. The save has no gender field, so infer it.

    Seeds: old uids are men's clubs; a new-uid club sharing name and nation with an old one is
    that club's women's section. Clubs that meet (same league table, or a fixture) are joined
    into components, since men's and women's teams never play each other; each component takes
    its seeds' majority label. Unlabelled affiliates then inherit from their parent club.
    """
    team_club = {t.team_id: c.uid for c in clubs for t in (c.teams or ())}
    same = collections.defaultdict(list)
    for c in clubs:
        same[(c.name, c.nation_id)].append(c.uid)
    # Strong seeds: a name+nation shared by several clubs is a men's club (lowest uid) and its women's
    # section(s). Weak seeds: any other old uid is probably men's, but some women's clubs have old uids
    # too (e.g. PSV women), so weak seeds only decide components that have no strong seed.
    strong = {u: "Men" if u == min(us) else "Women" for us in same.values() if len(us) > 1 for u in us}
    weak = {c.uid: "Men" for c in clubs if c.uid < NEW_UID and c.uid not in strong}
    root = {}

    def find(x):
        root.setdefault(x, x)
        while root[x] != x:
            root[x] = root[root[x]]; x = root[x]
        return x

    for g in groups:
        cs = [team_club[t] for t in g if t in team_club]
        for c in cs[1:]:
            root[find(c)] = find(cs[0])
    votes = collections.defaultdict(collections.Counter)
    for c, g in strong.items():
        votes[find(c)][g] += 1
    for c, g in weak.items():
        if not votes[find(c)]:
            votes[find(c)]["weak:" + g] += 1
    for v in votes.values():  # drop weak votes where strong ones exist
        if any(not k.startswith("weak:") for k in v):
            for k in [k for k in v if k.startswith("weak:")]:
                del v[k]
    lab = {c: votes[find(c)].most_common(1)[0][0].removeprefix("weak:") for c in root if votes[find(c)]}
    lab.update({u: g for u, g in weak.items() if u not in lab})
    lab.update(strong)  # a strong seed never loses to its component's majority
    for c in clubs:
        if c.uid not in lab and c.parent_club_uid in lab:
            lab[c.uid] = lab[c.parent_club_uid]
    return lab


# PlayerMatchStats.position is a one-bit mask the reader leaves undecoded; mapping verified against
# players' natural positions over ~400k appearances.
MATCH_POS = {1: "GK", 2: "SW", 4: "DR", 8: "DL", 16: "DC", 32: "WBR", 64: "WBL", 128: "DM", 256: "MR",
             512: "ML", 1024: "MC", 2048: "AMR", 4096: "AML", 8192: "AMC", 16384: "ST"}
PITCH_ORDER = ["GK", "SW", "DR", "DC", "DL", "WBR", "WBL", "DM", "MR", "MC", "ML", "AMR", "AMC", "AML", "ST"]


def infer_formation(matches, last=5):
    """The save's tactics are not readable in this build, so rebuild the XI shape from the minutes
    the squad played in each position over the last `last` matches (90 minutes = one slot)."""
    by_date = collections.defaultdict(collections.Counter)
    for m in matches:
        if m.position is not None and m.position.raw in MATCH_POS and m.minutes:
            by_date[m.date][MATCH_POS[m.position.raw]] += m.minutes
    dates = sorted(by_date)[-last:]
    if not dates:
        return ["GK", "DR", "DC", "DC", "DL", "DM", "DM", "AMR", "AMC", "AML", "ST"]
    share = collections.Counter()
    for d in dates:
        share.update({p: v / 90 / len(dates) for p, v in by_date[d].items()})
    # Largest-remainder rounding to exactly 11 slots, keeper fixed at one.
    share.pop("GK", None)
    slots = {p: int(v) for p, v in share.items()}
    for p, _ in sorted(share.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)[:max(10 - sum(slots.values()), 0)]:
        slots[p] += 1
    out = ["GK"] + [p for p in PITCH_ORDER for _ in range(slots.get(p, 0))]
    return out[:11]


def club_leagues(tables):
    """club_uid -> league id, from (table_key, club_count, club_uids) classification tables.
    The caller passes only competitions with promotion or relegation (real leagues, not cups or playoffs). A club can
    still sit in several (a league plus a regional group), so its league is the biggest domestic-sized one (8-24 clubs).
    Clubs in no such table (the large majority: only simulated leagues have tables) get no league."""
    best = {}
    for comp, n, clubs in tables:
        if 8 <= n <= 24:
            for c in clubs:
                if c is not None and (c not in best or n > best[c][0]):
                    best[c] = (n, comp)
    return {c: comp for c, (n, comp) in best.items()}


def extract(save_path, out_dir, players=True):
    """Write all_world_players.csv (optional), club_gender.csv, club_league.csv and season_stats.csv from a save."""
    E = fmsave.export
    with fmsave.open(save_path) as s:
        if players:
            E.write_csv(E.flat_rows(s.players(), fmsave.Player), E.column_names(fmsave.Player),
                        os.path.join(out_dir, "all_world_players.csv"))
        S = fmsave.PlayerSeasonStats
        overall = (r for r in s.player_season_stats() if r.kind == fmsave.SeasonStatsKind.OVERALL)
        E.write_csv(E.flat_rows(overall, S), E.column_names(S), os.path.join(out_dir, "season_stats.csv"))
        tables = list(s.league_tables())
        rules = {r.competition_id: r for r in s.competition_rules()}
        # competition_id is NOT unique to a league (one id covers the Australian league and a Croatian one, regional
        # groups of one league, one-club tables...), so a league is the table itself, keyed by its position.
        nation = {c.uid: c.nation_id for c in s.clubs()}
        # a domestic league has clubs from one nation; continental tournaments that promote/relegate (women's
        # champions league) span several
        leagues = club_leagues((i, t.club_count, [r.club_uid for r in t.rows]) for i, t in enumerate(tables)
                               if (r := rules.get(t.competition_id)) and (r.promotion_places or r.relegation_places)
                               and len({nation.get(r.club_uid) for r in t.rows} - {None}) <= 1)
        groups = [[r.team_id for r in t.rows] for t in tables]
        groups += [[f.home_team_id, f.away_team_id] for f in s.fixtures()]
        lab = club_genders(list(s.clubs()), groups)
        club = next(iter(s.managed_clubs()), None)
        if club:
            squad = {p.uid for p in s.players() if p.club_uid == club.club_uid}
            formation = infer_formation(m for m in s.player_match_stats() if m.player_uid in squad)
            with open(os.path.join(out_dir, "club.json"), "w") as f:
                json.dump({"club_uid": club.club_uid, "club_name": club.club_name, "formation": formation}, f)
    with open(os.path.join(out_dir, "club_gender.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["club_uid", "gender"]); w.writerows(lab.items())
    with open(os.path.join(out_dir, "club_league.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["club_uid", "league_id"]); w.writerows(leagues.items())


GAME_DATE = CLUB = None
# Season totals (OVERALL rows summed over every team the player turned out for this season).
STAT_SUMS = ["starts", "substitute_appearances", "minutes", "goals", "assists", "expected_goals", "expected_assists",
             "shots", "shots_on_target", "passes_attempted", "passes_completed", "progressive_passes", "key_passes",
             "clear_cut_chances_created", "crosses_attempted", "crosses_completed", "dribbles", "tackles_attempted",
             "tackles_completed", "interceptions", "possession_won", "pressures_completed", "aerial_challenges_attempted",
             "headers_won", "clearances", "blocks", "distance_km", "yellow_cards", "red_cards", "player_of_the_match",
             "clean_sheets", "goals_allowed", "shots_on_target_faced", "saves_held", "saves_parried", "saves_tipped", "expected_goals_prevented"]


def _stats_sql(path):
    sums = ", ".join(f"sum({c}) AS s_{c}" for c in STAT_SUMS)
    p90 = lambda e: f"round(({e}) * 90.0 / nullif(s_minutes, 0), 2)"
    return f"""
        WITH t AS (SELECT player_uid, {sums},
                          sum(average_rating * rated_appearances) / nullif(sum(rated_appearances), 0) AS rating,
                          -- level of the teams he actually played for (B teams are separate clubs), minutes-weighted
                          sum(minutes * r.rep) / nullif(sum(minutes) FILTER (WHERE r.rep IS NOT NULL), 0) AS s_league_rep,
                          -- the league where he played most minutes
                          arg_max(cl.league_id, minutes) FILTER (WHERE cl.league_id IS NOT NULL) AS s_league_id
                   FROM read_csv_auto('{path}', sample_size=-1)
                        LEFT JOIN (SELECT club_uid, max(club_reputation) AS rep FROM raw GROUP BY 1) r USING (club_uid)
                        LEFT JOIN cl USING (club_uid)
                   GROUP BY player_uid),
             ls AS (SELECT cl.league_id, avg(r.rep) AS league_strength  -- league level = mean reputation of its clubs
                    FROM cl JOIN (SELECT club_uid, max(club_reputation) AS rep FROM raw GROUP BY 1) r USING (club_uid)
                    GROUP BY 1)
        SELECT t.*, ls.league_strength, coalesce(s_starts, 0) + coalesce(s_substitute_appearances, 0) AS s_apps,
               round(rating, 2) AS s_avg_rating,
               round(100.0 * s_passes_completed / nullif(s_passes_attempted, 0), 1) AS s_pass_pct,
               round(100.0 * s_tackles_completed / nullif(s_tackles_attempted, 0), 1) AS s_tackle_pct,
               round(100.0 * s_headers_won / nullif(s_aerial_challenges_attempted, 0), 1) AS s_header_pct,
               {p90('s_goals')} AS s_goals_p90, {p90('s_assists')} AS s_assists_p90,
               {p90('s_expected_goals')} AS s_xg_p90, {p90('s_key_passes')} AS s_key_passes_p90,
               {p90('s_tackles_completed + s_interceptions')} AS s_def_actions_p90
        FROM t LEFT JOIN ls ON ls.league_id = t.s_league_id"""


# Moneyball performance index: see add_stat_score. Metrics below are per-90 output for each position group.
_p90 = lambda c: f"{c} * 90.0 / nullif(s_minutes, 0)"
STAT_METRICS = {
    "GK": [_p90("s_expected_goals_prevented"), "(coalesce(s_saves_held, 0) + coalesce(s_saves_parried, 0) + coalesce(s_saves_tipped, 0)) / nullif(s_shots_on_target_faced, 0)",
           _p90("s_clean_sheets"), "-" + _p90("s_goals_allowed"), "s_avg_rating", "s_avg_rating"],
    "CB": [_p90("s_headers_won"), "s_header_pct", _p90("s_tackles_completed"), _p90("s_interceptions"), _p90("s_clearances"),
           _p90("s_blocks"), "s_pass_pct", _p90("s_progressive_passes"), "s_avg_rating", "s_avg_rating"],
    "FB": [_p90("s_tackles_completed"), _p90("s_interceptions"), _p90("s_crosses_completed"), _p90("s_key_passes"),
           _p90("s_progressive_passes"), _p90("s_dribbles"), _p90("s_expected_assists"), "s_avg_rating", "s_avg_rating"],
    "DM": [_p90("s_tackles_completed"), _p90("s_interceptions"), _p90("s_possession_won"), "s_pass_pct",
           _p90("s_progressive_passes"), _p90("s_pressures_completed"), "s_avg_rating", "s_avg_rating"],
    "CM": [_p90("s_progressive_passes"), _p90("s_key_passes"), _p90("s_expected_assists"), "s_pass_pct", _p90("s_possession_won"),
           _p90("s_tackles_completed"), _p90("s_distance_km"), "s_avg_rating", "s_avg_rating"],
    "W": [_p90("s_dribbles"), _p90("s_expected_assists"), _p90("s_key_passes"), _p90("s_expected_goals"), _p90("s_goals"),
          _p90("s_crosses_completed"), "s_avg_rating", "s_avg_rating"],
    "AM": [_p90("s_key_passes"), _p90("s_expected_assists"), _p90("s_expected_goals"), _p90("s_goals"), _p90("s_dribbles"),
           _p90("s_clear_cut_chances_created"), "s_avg_rating", "s_avg_rating"],
    "ST": [_p90("s_goals"), _p90("s_expected_goals"), _p90("s_shots_on_target"), _p90("s_headers_won"), _p90("s_assists"),
           "s_avg_rating", "s_avg_rating"],
}
NAT_GROUP = {"GK": "GK", "SW": "CB", "DC": "CB", "DL": "FB", "DR": "FB", "WBL": "FB", "WBR": "FB", "DM": "DM", "MC": "CM",
             "ML": "W", "MR": "W", "AML": "W", "AMR": "W", "AMC": "AM", "STC": "ST", "ST": "ST"}


MIN_PEERS = 30  # smallest league (per position group) worth ranking players inside


def add_stat_score():
    """stat_score = 100 x perf x (0.4 + 0.6 x level). perf = per-90 percentile among same-position players of the
    same league, so a title-chasing side and a mid-table side are judged against the same opponents; leagues with
    under MIN_PEERS such players (or no known league) rank against all unplaced players instead. level = the league's
    percentile by mean club reputation; without a league, the reputation of the teams he played for."""
    grp = "CASE split_part(natural_positions, ';', 1) " + " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in NAT_GROUP.items()) + " END"
    parts = " UNION ALL ".join(
        f"SELECT uid, ({' + '.join(f'percent_rank() OVER (PARTITION BY gender, lg ORDER BY coalesce({m}, 0))' for m in ms)}) / {len(ms)} AS perf "
        f"FROM q WHERE stat_group = '{g}' AND s_minutes >= 600" for g, ms in STAT_METRICS.items())
    con().execute(f"""
        CREATE OR REPLACE TABLE players AS
        WITH p AS (SELECT *, {grp} AS stat_group FROM players),
             q AS (SELECT *, CASE WHEN count(*) FILTER (WHERE s_minutes >= 600) OVER (PARTITION BY gender, stat_group, s_league_id) >= {MIN_PEERS}
                                  THEN s_league_id END AS lg FROM p),
             s AS ({parts}),
             lv AS (SELECT s_league_id, percent_rank() OVER (ORDER BY league_strength) AS lvl
                    FROM (SELECT DISTINCT s_league_id, league_strength FROM players WHERE league_strength IS NOT NULL)),
             r AS (SELECT s.uid, s.perf,
                          coalesce(lv.lvl, percent_rank() OVER (PARTITION BY p.gender ORDER BY coalesce(p.s_league_rep, 0))) AS lvl
                   FROM s JOIN p ON p.uid = s.uid LEFT JOIN lv ON lv.s_league_id = p.s_league_id)
        SELECT p.*, round(100 * r.perf * (0.4 + 0.6 * r.lvl), 1) AS stat_score
        FROM p LEFT JOIN r USING (uid)""")


# ---- Fair price model ----
# The game's transfer value is driven by market context as much as by quality: age, the level of the
# club/league, the player's reputation, contract length and squad status. So fair prices come from a
# regression per gender: ln(fee) and ln(weekly wage) on that visible context plus the quality signals
# a lens (or data mode) may see. Quality x league-strength terms let a 7.3 rating in Ligue 1 count for
# more than a 7.3 in a regional league. Fit on players the game values (transfer_value > 0).
CONTEXT = ["age", "age * age AS age2",
           # value collapses after ~29 whatever the reputation: hinge terms let the model bend there
           "greatest(age - 29, 0) AS age_late", "greatest(age - 29, 0) * greatest(age - 29, 0) AS age_late2",
           "greatest(age - 29, 0) * coalesce(reputation_current, 0) / 1000.0 AS age_late_rep", "ln(coalesce(club_reputation, 0) + 1) AS crep", "coalesce(reputation_current, 0) / 1000.0 AS prep",
           "least(greatest(coalesce(months_left, 0), 0), 72) / 12.0 AS yrs", "coalesce(s_minutes, 0) / 1000.0 AS mins"] + [
           f"(contract_squad_status = '{s}')::INT AS st_{s}" for s in
           ("star_player", "important_player", "regular_starter", "squad_player", "fringe_player", "youngster", "breakthrough_prospect")]
# The player's personal reputation is a hidden number the game never shows the manager: only Full mode may price on it.
REP_FEATURES = {"age_late_rep", "prep"}
LENS_FEATURES = {
    "ability": ["ability_current AS ca", "ability_potential AS pa", "ability_potential * (age <= 23)::INT AS pa_young",
                "ability_current * ln(coalesce(club_reputation, 0) + 1) AS ca_crep"],
    "attributes": ["best_role_score AS brs", "best_role_score * best_role_score AS brs2",
                   "best_role_score * ln(coalesce(club_reputation, 0) + 1) AS brs_crep"],
    "form": ["CASE WHEN s_minutes >= 900 THEN s_avg_rating END AS rat",
             "CASE WHEN s_minutes >= 900 THEN s_avg_rating * ln(coalesce(s_league_rep, 0) + 1) END AS rat_crep"],
    "stats": ["CASE WHEN s_minutes >= 900 THEN stat_score END AS ss",
              "CASE WHEN s_minutes >= 900 THEN stat_score * ln(coalesce(s_league_rep, 0) + 1) END AS ss_crep"],
}
# Which lenses each data mode may use.
MODES = {"full": ["ability", "attributes", "form", "stats"], "fair": ["attributes", "form", "stats"], "moneyball": ["form", "stats"]}
MONEYBALL_MIN = 900  # minutes this season below which Moneyball gives no fair price
WAGE_WEEKS = 156  # contract_wage is weekly; a signing is judged on fee + 3 years of wages
MODEL_FIT = {}    # (gender, model) -> R² of ln(fee), for /api/meta


def _alias(e):
    return e.rsplit(" AS ", 1)[-1] if " AS " in e else e


def _fit_predict(X, y, train):
    """Ridge-regularised least squares on standardised features; returns predictions for every row and R²."""
    mu, sd = X[train].mean(0), X[train].std(0) + 1e-9
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    A = Z[train]
    beta = np.linalg.solve(A.T @ A + 1.0 * np.eye(A.shape[1]), A.T @ y[train])
    raw = Z @ beta
    # Calibrate by quantile mapping: exp() of a linear model with interaction terms overshoots wildly
    # at the elite end (£3B Saka). The k-th highest raw prediction gets the k-th highest real value,
    # so fair prices keep the real market's spread, elite end included.
    pred = np.interp(raw, np.sort(raw[train]), np.sort(y[train]))
    resid = y[train] - pred[train]
    return pred, 1 - resid.var() / y[train].var()


def add_deals():
    """Adds per lens and per mode: fair_fee_*, fair_package_* (fair fee + 3y of fair wage) and deal_* /
    deal_score_* = % the actual package (fee + 3y wages) sits below the fair one. Players the game has no
    value for (transfer_value NULL: the club won't sell) get no actual package and so no deal."""
    has_stats = "s_minutes" in {r[0] for r in con().execute("DESCRIBE players").fetchall()}
    lenses = [k for k in LENS_FEATURES if k in ("ability", "attributes") or has_stats]
    ctx = CONTEXT if has_stats else [c for c in CONTEXT if "s_minutes" not in c]
    cols = ctx + [f for k in lenses for f in LENS_FEATURES[k]]
    cur = con().execute(f"""SELECT uid, gender, CASE WHEN transfer_value > 0 THEN ln(transfer_value) END AS y_fee,
                                   CASE WHEN contract_wage > 0 THEN ln(contract_wage) END AS y_wage, {", ".join(cols)}
                            FROM players""")
    data = cur.fetchnumpy()
    n = len(data["uid"])
    num = lambda c: np.asarray(data[c], dtype=float) if not hasattr(data[c], "mask") else np.where(data[c].mask, np.nan, data[c].data).astype(float)
    ctx_X = np.column_stack([num(_alias(c)) for c in ctx])
    ctx_open = ctx_X[:, [i for i, c in enumerate(ctx) if _alias(c) not in REP_FEATURES]]  # what Fair play / Moneyball may see
    lens_X = {k: np.column_stack([num(_alias(f)) for f in LENS_FEATURES[k]]) for k in lenses}
    gender = np.array([g if isinstance(g, str) else "" for g in data["gender"]])
    y = {"fee": num("y_fee"), "wage": num("y_wage")}
    out = {"uid": data["uid"]}

    def run(name, X, usable):
        """Fit fee and wage models per gender on rows where X is usable; NaN elsewhere."""
        for tgt in ("fee", "wage"):
            pred = np.full(n, np.nan)
            for g in ("Men", "Women", ""):
                rows = (gender == g) & usable
                train = rows & ~np.isnan(y[tgt])
                if train.sum() < 200:
                    continue
                p, r2 = _fit_predict(X[rows], y[tgt][rows], train[rows])
                pred[rows] = p
                if tgt == "fee":
                    MODEL_FIT[(g or "Unknown", name)] = round(float(r2), 3)
            out[f"{tgt}_{name}"] = np.exp(pred)

    ctx_ok = ~np.isnan(ctx_X).any(1)
    for k in lenses:  # one lens alone: only rows that have that lens's quality signal
        run(k, np.column_stack([ctx_X if k == "ability" else ctx_open, lens_X[k]]), ctx_ok & ~np.isnan(lens_X[k]).any(1))
    for m, ks in MODES.items():  # a mode: every lens it may see; missing signals -> 0 plus a "has" flag
        parts = [ctx_X if m == "full" else ctx_open]
        for k in (k for k in ks if k in lenses):
            has = ~np.isnan(lens_X[k]).any(1)
            parts += [np.where(has[:, None], lens_X[k], 0), has[:, None].astype(float)]
        run(m, np.column_stack(parts), ctx_ok)

    names = list(out)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        np.savetxt(f, np.column_stack([out[c] for c in names]), delimiter=",", header=",".join(names), comments="",
                   fmt=["%d"] + ["%.6g"] * (len(names) - 1))  # uid first, exact
    model_cols = lenses + list(MODES)
    deal = lambda f: f"CASE WHEN {f} > 0 AND package_cost IS NOT NULL THEN greatest(-999, round(100 * (1 - package_cost / ({f})))) END"
    con().execute(f"""
        CREATE OR REPLACE TABLE players AS
        WITH m AS (SELECT * REPLACE (CAST(uid AS BIGINT) AS uid) FROM read_csv_auto('{f.name}', nullstr='nan')),
             p AS (SELECT players.*,
                          -- no game value = not for sale: no fee to pay, so no package (free agents pay wages only)
                          CASE WHEN transfer_value IS NOT NULL OR free_agent
                               THEN coalesce(transfer_value, 0) + {WAGE_WEEKS} * coalesce(contract_wage, 0) END AS package_cost,
                          {", ".join(f"round(m.fee_{c}, -3) AS fair_fee_{c}, round(m.wage_{c}, -1) AS fair_wage_{c}, "
                                     f"round(m.fee_{c}, -3) + {WAGE_WEEKS} * round(m.wage_{c}, -1) AS fair_package_{c}" for c in model_cols)}
                   FROM players LEFT JOIN m USING (uid))
        SELECT *, {", ".join(deal(f"fair_package_{k}") + f" AS deal_{k}" for k in lenses)},
                  {", ".join(deal(f"fair_package_{m}") + f" AS deal_score_{m}" for m in MODES)}
        FROM p""")
    os.unlink(f.name)
    # Moneyball prices on numbers only: under 900 min this season there are none to price on, so no fair price at all.
    if has_stats:
        con().execute(f"""UPDATE players SET {", ".join(f"{p}_moneyball = NULL" for p in ("fair_fee", "fair_wage", "fair_package", "deal_score"))}
                          WHERE coalesce(s_minutes, 0) < {MONEYBALL_MIN}""")


def load(d=None):
    global GAME_DATE
    d = str(d or data_dir())
    roles, best, name = _role_sql()
    j = lambda f: os.path.join(d, f)
    con().execute(f"CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM read_csv_auto('{j('all_world_players.csv')}', sample_size=-1)")
    has_g, has_s = os.path.exists(j("club_gender.csv")), os.path.exists(j("season_stats.csv"))
    con().execute("CREATE OR REPLACE TEMP TABLE cg AS " + (f"SELECT * FROM read_csv_auto('{j('club_gender.csv')}')" if has_g
               else "SELECT NULL::BIGINT AS club_uid, NULL::VARCHAR AS gender LIMIT 0"))
    has_l = os.path.exists(j("club_league.csv"))  # absent in data extracted by older versions: team-level scoring
    con().execute("CREATE OR REPLACE TEMP TABLE cl AS " + (f"SELECT * FROM read_csv_auto('{j('club_league.csv')}')" if has_l
               else "SELECT NULL::BIGINT AS club_uid, NULL::BIGINT AS league_id LIMIT 0"))
    con().execute("CREATE OR REPLACE TEMP TABLE st AS " + (_stats_sql(j("season_stats.csv")) if has_s
               else "SELECT NULL::BIGINT AS player_uid LIMIT 0"))
    con().execute("CREATE OR REPLACE TEMP TABLE nat (id INT, name VARCHAR, code VARCHAR)")
    con().executemany("INSERT INTO nat VALUES (?, ?, ?)", [(k, n, c) for k, (n, c) in NATIONS.items()])
    # Save date is not in the CSV; latest contract start is a close proxy.
    GAME_DATE = con().execute("SELECT max(contract_start) FROM raw").fetchone()[0]
    con().execute(f"""
        CREATE OR REPLACE TABLE players AS
        WITH a AS (SELECT *, {roles} FROM raw),
             b AS (SELECT *, {best} AS best_role_score FROM a)
        SELECT b.*, {name} AS best_role,
               ability_potential - ability_current AS pa_gap,
               date_diff('month', DATE '{GAME_DATE}', contract_end) AS months_left,
               (b.club_uid IS NULL) AS free_agent,
               -- current club's label, else the most common label among clubs in the contract chain
               coalesce(cg.gender, (SELECT mode(g2.gender) FROM unnest(string_split(CAST(b.contract_chain_club_uids AS VARCHAR), ';')) x(u)
                                    JOIN cg g2 ON g2.club_uid = try_cast(x.u AS BIGINT))) AS gender,
               n1.name AS nationality, n1.code AS nationality_code, n3.name AS club_country,
               (SELECT string_agg(n2.name, ', ') FROM unnest(string_split(CAST(b.second_nation_ids AS VARCHAR), ';')) x(u)
                JOIN nat n2 ON n2.id = try_cast(x.u AS INT)) AS second_nationalities,
               st.* EXCLUDE (player_uid)
        FROM b LEFT JOIN cg ON cg.club_uid = b.club_uid LEFT JOIN st ON st.player_uid = b.uid
             LEFT JOIN nat n1 ON n1.id = b.nation_id LEFT JOIN nat n3 ON n3.id = b.club_nation_id""")
    con().execute("DROP TABLE raw; DROP TABLE cg; DROP TABLE st; DROP TABLE nat; DROP TABLE cl")
    if has_s:
        add_stat_score()
    add_deals()
    global COLS, CLUB, MODE_HIDDEN, VISIBLE
    CLUB = json.load(open(j("club.json"))) if os.path.exists(j("club.json")) else None
    COLS = {r[0] for r in con().execute("DESCRIBE players").fetchall()}
    fair = {c for c in COLS if c.startswith(HIDDEN_PREFIXES) or c in HIDDEN_EXACT}
    MODE_HIDDEN = {"full": set(), "fair": fair,
                   "moneyball": fair | {c for c in COLS if c.startswith(MONEYBALL_PREFIXES) or c in MONEYBALL_EXACT}}
    VISIBLE = {m: (COLS - h - INTERNAL) | set(PER_MODE) for m, h in MODE_HIDDEN.items()}


# Fair play: what the game never shows the manager (CA/PA, hidden attributes, personality numbers).
# Moneyball: additionally no attributes at all, only what happened on the pitch. Hidden columns are
# stripped from every response and never used to filter or rank.
HIDDEN_PREFIXES = ("ability_", "raw_", "personality_")
PER_MODE = ("deal_score", "fair_package", "fair_fee", "fair_wage")  # each mode exposes its own as these names
INTERNAL = {f"{p}_{m}" for m in MODES for p in PER_MODE}
HIDDEN_EXACT = {"pa_gap", "deal_ability", "fair_package_ability", "fair_fee_ability", "fair_wage_ability", "trait_bits", "attributes_consistency",
                "attributes_important_matches", "attributes_injury_proneness", "attributes_dirtiness",
                "attributes_versatility"}
MONEYBALL_PREFIXES = ("attributes_", "role_")
MONEYBALL_EXACT = {"best_role", "best_role_score", "deal_attributes", "fair_package_attributes", "fair_fee_attributes",
                   "fair_wage_attributes", "left_foot", "right_foot"}
MODE_HIDDEN = VISIBLE = {}


def _mode(mode, fair_play=False):
    mode = "fair" if fair_play and mode == "full" else mode
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {list(MODES)}")
    return mode


def uses_hidden(sql, mode):
    return any(re.search(rf"\b{c}\b", sql) for c in MODE_HIDDEN[mode])


def source(mode):
    """Table expression for a request: hidden columns gone, the mode's own deal blend exposed as
    deal_score / fair_package."""
    drop = ", ".join(sorted(MODE_HIDDEN[mode] | INTERNAL))
    return f"(SELECT * EXCLUDE ({drop}), {', '.join(f'{p}_{mode} AS {p}' for p in PER_MODE)} FROM players)"


if __name__ == "__main__":  # python app.py <save.fm>: write the data files into the data dir
    extract(sys.argv[1], str(data_dir()))
    sys.exit()


def _load_data_dir():
    """Startup: load the data dir in the background if a previous save was extracted there."""
    global STATUS
    if not (data_dir() / FILES[0]).exists():
        return
    with _ingest_lock:
        STATUS = "loading"
        try:
            load()
            STATUS = "ready"
        except Exception:
            traceback.print_exc()
            STATUS = "empty"

# Community-favourite searches. SQL is server-defined (trusted); `view` picks the column set shown.
# A preset is offered in every data mode whose visible columns its filter uses (see meta()).
PRESETS = {
    "wonderkids": ("Talent", "Wonderkids", "Age ≤ 19, PA ≥ 150", "age <= 19 AND ability_potential >= 150", "ability_potential DESC", "overview"),
    "hidden_gems": ("Talent", "Hidden gems", "Age ≤ 21, PA ≥ 135, value ≤ £750k", "age <= 21 AND ability_potential >= 135 AND transfer_value <= 750000", "ability_potential DESC", "overview"),
    "biggest_gap": ("Talent", "Biggest growth gap", "Age ≤ 21, largest PA − CA", "age <= 21 AND ability_potential >= 120", "pa_gap DESC", "overview"),
    "loan_targets": ("Talent", "Loan targets", "Age ≤ 22, CA ≥ 100, not a starter", "age <= 22 AND ability_current >= 100 AND contract_squad_status IN ('youngster','breakthrough_prospect','future_prospect','fringe_player','squad_player') AND NOT coalesce(on_loan, false)", "ability_current DESC", "overview"),
    "model_pros": ("Talent", "Model professionals", "Age ≤ 23, PA ≥ 130, Prof ≥ 18, Det ≥ 16", "age <= 23 AND ability_potential >= 130 AND personality_professionalism >= 18 AND attributes_determination >= 16", "ability_potential DESC", "hidden"),
    "young_talent": ("Talent", "Promising youngsters", "Age ≤ 20, role score ≥ 55 (no hidden info)", "age <= 20 AND best_role_score >= 55", "best_role_score DESC", "overview"),
    "young_performers": ("Talent", "Young performers", "Age ≤ 21, performance index ≥ 60, 900+ min", "age <= 21 AND s_minutes >= 900 AND stat_score >= 60", "stat_score DESC", "season"),
    "free_agents": ("Bargains", "Free agents", "No club, CA ≥ 90", "free_agent AND ability_current >= 90", "ability_current DESC", "contract"),
    "free_agents_open": ("Bargains", "Free agents (no CA)", "No club, played this season or role score ≥ 50", "free_agent AND (s_minutes >= 450 OR best_role_score >= 50)", "best_role_score DESC", "contract"),
    "bosman": ("Bargains", "Expiring contracts", "≤ 6 months left, CA ≥ 110", "months_left BETWEEN 0 AND 6 AND ability_current >= 110", "ability_current DESC", "contract"),
    "bosman_open": ("Bargains", "Expiring & playing", "≤ 6 months left, 900+ min, performance ≥ 50", "months_left BETWEEN 0 AND 6 AND s_minutes >= 900 AND stat_score >= 50", "stat_score DESC", "contract"),
    "vets": ("Bargains", "Bargain veterans", "Age 29–33, CA ≥ 130, value ≤ £1.5M", "age BETWEEN 29 AND 33 AND ability_current >= 130 AND transfer_value <= 1500000", "ability_current DESC", "overview"),
    "champ_upgrade": ("Bargains", "Championship upgrades", "CA 120–145, age ≤ 28, value ≤ £2M", "ability_current BETWEEN 120 AND 145 AND age <= 28 AND transfer_value <= 2000000", "ability_current DESC", "overview"),
    "ready_stars": ("Elite", "Ready-made stars", "Age ≤ 24, CA ≥ 150", "age <= 24 AND ability_current >= 150", "ability_current DESC", "overview"),
    "big_game": ("Elite", "Big-game players", "Cons ≥ 16, Imp. matches ≥ 16, CA ≥ 120", "attributes_consistency >= 16 AND attributes_important_matches >= 16 AND ability_current >= 120", "ability_current DESC", "hidden"),
    "iron_men": ("Elite", "Iron men", "Inj. prone ≤ 4, Nat. fitness ≥ 16, CA ≥ 110", "attributes_injury_proneness <= 4 AND attributes_natural_fitness >= 16 AND ability_current >= 110", "ability_current DESC", "hidden"),
    "speed": ("Profiles", "Speed merchants", "Pace & Acceleration ≥ 17", "attributes_pace >= 17 AND attributes_acceleration >= 17", "ability_current DESC", "physical"),
    "target_men": ("Profiles", "Aerial monsters", "Height ≥ 190, Head & Jump ≥ 15", "height_cm >= 190 AND attributes_heading >= 15 AND attributes_jumping_reach >= 15", "ability_current DESC", "physical"),
    "set_piece": ("Profiles", "Set-piece specialists", "Free kicks & Corners ≥ 16", "attributes_free_kick_taking >= 16 AND attributes_corners >= 16", "ability_current DESC", "technical"),
    "enforcers": ("Profiles", "Midfield enforcers", "Tackling, Aggression, Work rate ≥ 16", "attributes_tackling >= 16 AND attributes_aggression >= 16 AND attributes_work_rate >= 16", "ability_current DESC", "mental"),
    "top_performers": ("Form", "Top performers", "Performance index (stats only), 900+ min", "s_minutes >= 900", "stat_score DESC", "season"),
    "top_rated": ("Form", "Top rated", "Avg rating, 900+ min this season", "s_minutes >= 900", "s_avg_rating DESC", "season"),
    "scorers": ("Form", "In-form scorers", "Goals per 90, 900+ min", "s_minutes >= 900", "s_goals_p90 DESC", "season"),
    "creators": ("Form", "Chance creators", "Key passes per 90, 900+ min", "s_minutes >= 900", "s_key_passes_p90 DESC", "season"),
    "xg_under": ("Form", "Unlucky finishers", "xG − goals ≥ 2, 900+ min", "s_minutes >= 900 AND s_expected_goals - s_goals >= 2", "s_expected_goals - s_goals DESC", "season"),
    "ball_winners": ("Form", "Ball winners", "Tackles + interceptions per 90, 900+ min", "s_minutes >= 900", "s_def_actions_p90 DESC", "season"),
    "young_breakout": ("Form", "Young breakouts", "Age ≤ 21, 900+ min, rating ≥ 7.0", "age <= 21 AND s_minutes >= 900 AND s_avg_rating >= 7.0", "s_avg_rating DESC", "season"),
    # Deal Finder lenses: % under the fair fee + 3y wages package, weighted by quality so £5k nobodies don't win on ratio alone.
    "deals_ability": ("Deals", "Best deals · Ability", "CA/PA vs fee + 3y wages", "deal_ability IS NOT NULL AND package_cost > 0 AND transfer_value IS NOT NULL AND (ability_current >= 90 OR ability_potential >= 130)",
                      "deal_ability * (ability_current + ability_potential) DESC", "deals"),
    "deals_attributes": ("Deals", "Best deals · Attributes", "Role score vs fee + 3y wages", "deal_attributes IS NOT NULL AND package_cost > 0 AND transfer_value IS NOT NULL AND best_role_score >= 55",
                         "deal_attributes * best_role_score DESC", "deals"),
    "deals_form": ("Deals", "Best deals · Form", "Season rating vs fee + 3y wages, 900+ min", "deal_form IS NOT NULL AND package_cost > 0 AND transfer_value IS NOT NULL AND stat_score >= 40",
                   "deal_form * s_avg_rating DESC", "deals"),
    "deals_stats": ("Deals", "Best deals · Performance", "Stats-only performance index vs fee + 3y wages, 900+ min", "deal_stats IS NOT NULL AND package_cost > 0 AND transfer_value IS NOT NULL AND stat_score >= 40",
                    "deal_stats * stat_score DESC", "deals"),
    # fair_package is the mode's own valuation, so ln(fair_package) is a mode-neutral quality weight.
    "deals_blend": ("Deals", "Best deals · Blend", "Every lens your data mode allows vs fee + 3y wages", "deal_score IS NOT NULL AND package_cost > 0 AND transfer_value IS NOT NULL AND fair_package >= 250000",
                    "deal_score * ln(fair_package) DESC", "deals"),
}
for r, (grp, _) in ROLES.items():
    pos = ", ".join(f"positions_{p}" for p in POS[grp])
    PRESETS[f"role_{r}"] = ("Roles", r.replace("_", " ").title(), f"Best rated {grp}s for this role",
                            f"greatest({pos}, 0) >= 15 AND role_{r} >= 50", f"role_{r} DESC", "roles")
DEFAULT_SORT = {"full": "ability_current DESC", "fair": "best_role_score DESC NULLS LAST", "moneyball": "stat_score DESC NULLS LAST"}


class Query(BaseModel):
    start: int = 0
    end: int = 100
    sort: list[dict] = []        # AG Grid sortModel
    filters: dict = {}           # AG Grid filterModel
    preset: str | None = None
    search: str = ""
    positions: list[str] = []
    age: list[int | None] = [None, None]
    ca_min: int | None = None
    pa_min: int | None = None
    value_max: int | None = None
    shortlist: list[int] | None = None
    hide_hidden_risks: bool = False
    gender: str | None = None    # "Men" | "Women" | "Unknown"
    wage_max: int | None = None  # weekly
    nations: list[str] = []      # country names; matches first or second nationality
    mode: str = "full"           # "full" | "fair" (no CA/PA/hidden) | "moneyball" (stats only)
    fair_play: bool = False      # legacy alias for mode="fair"


OPS = {"equals": "=", "notEqual": "<>", "greaterThan": ">", "lessThan": "<",
       "greaterThanOrEqual": ">=", "lessThanOrEqual": "<="}


def _col(c, mode="full"):
    if c not in VISIBLE[mode]:
        raise HTTPException(400, f"column {c} is not available in {mode} mode")
    return f'"{c}"'


def build(q: Query):
    mode = q.mode = _mode(q.mode, q.fair_play)
    where, args = [], []
    for col, f in q.filters.items():
        c, t, ft = _col(col, mode), f.get("type"), f.get("filterType")
        if t == "blank":
            where.append(f"{c} IS NULL"); continue
        if t == "notBlank":
            where.append(f"{c} IS NOT NULL"); continue
        if ft == "text":
            neg = "NOT " if t in ("notContains", "notEqual") else ""
            pat = {"equals": "{}", "notEqual": "{}", "startsWith": "{}%", "endsWith": "%{}"}.get(t, "%{}%")
            where.append(f"{neg}{c} ILIKE ?"); args.append(pat.format(f["filter"]))
            continue
        lo, hi = (f.get("dateFrom"), f.get("dateTo")) if ft == "date" else (f.get("filter"), f.get("filterTo"))
        if ft == "date":
            lo, hi = lo and lo[:10], hi and hi[:10]
        if t == "inRange":
            where.append(f"{c} BETWEEN ? AND ?"); args += [lo, hi]
        elif t in OPS:
            where.append(f"{c} {OPS[t]} ?"); args.append(lo)
    if q.preset:
        if q.preset not in PRESETS:
            raise HTTPException(400, "unknown preset")
        if uses_hidden(PRESETS[q.preset][3], mode):
            raise HTTPException(400, f"preset needs data hidden in {mode} mode")
        where.append(f"({PRESETS[q.preset][3]})")
    if q.search.strip():
        where.append("(name ILIKE ? OR club_name ILIKE ?)"); args += [f"%{q.search.strip()}%"] * 2
    if q.positions:
        cols = [f"positions_{p}" for g in q.positions for p in POS.get(g, [])]
        if cols:
            where.append(f"greatest({', '.join(cols)}, 0) >= 15")
    full = mode == "full"
    for cond, val in (("age >= ?", q.age[0] if q.age else None), ("age <= ?", q.age[1] if len(q.age) > 1 else None),
                      ("ability_current >= ?", q.ca_min if full else None), ("ability_potential >= ?", q.pa_min if full else None),
                      ("transfer_value <= ?", q.value_max), ("contract_wage <= ?", q.wage_max)):
        if val is not None:
            where.append(cond); args.append(val)
    if q.shortlist is not None:
        where.append("list_contains(?, uid)"); args.append(q.shortlist)
    if q.nations:
        where.append("(list_contains(?, nationality) OR list_has_any(?, string_split(coalesce(second_nationalities, ''), ', ')))")
        args += [q.nations, q.nations]
    if q.gender in ("Men", "Women"):
        where.append("gender = ?"); args.append(q.gender)
    elif q.gender == "Unknown":
        where.append("gender IS NULL")
    if q.hide_hidden_risks and full:
        where.append("attributes_injury_proneness <= 12 AND attributes_consistency >= 8 AND personality_professionalism >= 8")
    w = "WHERE " + " AND ".join(where) if where else ""
    order = [f'{_col(s["colId"], mode)} {"DESC" if s["sort"] == "desc" else "ASC"} NULLS LAST' for s in q.sort]
    if not order and q.preset and not uses_hidden(PRESETS[q.preset][4], mode):
        order = [PRESETS[q.preset][4] + " NULLS LAST"]
    return w, args, ("ORDER BY " + ", ".join(order or [DEFAULT_SORT[mode]]))


@app.post("/api/players")
def players(q: Query):
    w, args, order = build(q)
    src = source(q.mode)
    total = con().execute(f"SELECT count(*) FROM {src} {w}", args).fetchone()[0]
    cur = con().execute(f"SELECT * FROM {src} {w} {order} LIMIT ? OFFSET ?", args + [max(q.end - q.start, 0), q.start])
    names = [d[0] for d in cur.description]
    return {"total": total, "rows": [dict(zip(names, r)) for r in cur.fetchall()]}


@app.post("/api/export")
def export(q: Query):
    w, args, order = build(q)
    cur = con().execute(f"SELECT * FROM {source(q.mode)} {w} {order} LIMIT 20000", args)
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(d[0] for d in cur.description)
    out.writerows(cur.fetchall())
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=fm26_players.csv"})


def _player(uid, mode):
    cur = con().execute(f"SELECT * FROM {source(mode)} WHERE uid = ?", [uid])
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "not found")
    return dict(zip([d[0] for d in cur.description], row))


@app.get("/api/player/{uid}")
def player(uid: int, mode: str = "full", fair_play: bool = False):
    return _player(uid, _mode(mode, fair_play))


@app.get("/api/meta")
def meta():
    return {"columns": sorted(VISIBLE["full"]), "game_date": GAME_DATE, "positions": list(POS),
            "modes": {m: {"lenses": ls, "hidden_columns": sorted(MODE_HIDDEN[m])} for m, ls in MODES.items()},
            "hidden_columns": sorted(MODE_HIDDEN["fair"]),  # legacy: fair-play list
            "roles": {r: g for r, (g, _) in ROLES.items()},
            # Key attributes per role, heaviest first (for showing why a role score is what it is).
            "role_attributes": {r: [a for a, _ in sorted(w.items(), key=lambda kv: -kv[1])] for r, (_, w) in ROLES.items()},
            "stat_groups": list(STAT_METRICS), "wage_weeks": WAGE_WEEKS,
            "model_fit_r2": {f"{g}/{m}": r2 for (g, m), r2 in MODEL_FIT.items()},
            "nations": [{"name": n, "code": c, "players": k} for n, c, k in con().execute(
                "SELECT nationality, any_value(nationality_code), count(*) FROM players WHERE nationality IS NOT NULL GROUP BY 1 ORDER BY 3 DESC").fetchall()],
            "presets": [{"id": k, "group": g, "label": l, "desc": d, "view": v, "uses_hidden": uses_hidden(w, "fair"),
                         "modes": [m for m in MODES if not uses_hidden(w, m)]}
                        for k, (g, l, d, w, _, v) in PRESETS.items()]}


def ingest(src):
    """Extract a .fm save and make it the only loaded data. One at a time; failures keep the old data."""
    global STATUS
    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(409, "busy")
    try:
        dd = data_dir()
        tmp = tempfile.mkdtemp(dir=dd)
        try:
            extract(str(src), tmp)
            replace_data(tmp, dd)
        except fmsave.FmsaveError as e:
            raise HTTPException(400, str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        STATUS = "loading"
        try:
            load(dd)
        except Exception:
            STATUS = "empty"
            raise
        STATUS = "ready"
        return {"rows": con().execute("SELECT count(*) FROM players").fetchone()[0], "game_date": GAME_DATE}
    finally:
        _ingest_lock.release()


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):  # fallback picker: the browser sends the file bytes
    if not (file.filename or "").lower().endswith(".fm"):
        raise HTTPException(400, "need .fm save")
    with tempfile.TemporaryDirectory(dir=data_dir()) as d:
        src = os.path.join(d, "s.fm")
        with open(src, "wb") as f:
            shutil.copyfileobj(file.file, f)
        return ingest(src)


class SaveReq(BaseModel):
    name: str


@app.post("/api/load-save")
def load_save(q: SaveReq):
    try:
        src = resolve_save(q.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ingest(src)


@app.get("/api/status")
def status():
    return {"app": "fm26-scout", "status": STATUS, "version": VERSION}


@app.get("/api/saves")
def saves():
    s = list_saves()
    return {"folder_found": s is not None, "saves": s or []}


@app.post("/api/ping")
def ping():
    global LAST_PING, BYE_AT
    LAST_PING, BYE_AT = time.monotonic(), None
    return {"ok": True}


@app.post("/api/bye")  # sent by the page on pagehide; a reload pings again within the grace period
def bye():
    global BYE_AT
    BYE_AT = time.monotonic() + 15
    return {"ok": True}


def idle_expired(now=None, hard=600):
    now = time.monotonic() if now is None else now
    return (BYE_AT is not None and now > BYE_AT) or now - LAST_PING > hard


# ---- My Club: squad power ranking per formation slot, and transfer targets ----
SLOT_GROUP = {"GK": "GK", "SW": "CB", "DC": "CB", "DL": "FB", "DR": "FB", "WBL": "FB", "WBR": "FB", "DM": "DM",
              "MC": "CM", "ML": "W", "MR": "W", "AML": "W", "AMR": "W", "AMC": "AM", "ST": "ST"}
SHAPES = {"4-2-3-1": "GK DR DC DC DL DM DM AMR AMC AML ST", "4-3-3": "GK DR DC DC DL DM MC MC AMR AML ST",
          "4-4-2": "GK DR DC DC DL MR MC MC ML ST ST", "3-5-2": "GK DC DC DC WBR DM MC MC WBL ST ST",
          "3-4-3": "GK DC DC DC WBR MC MC WBL AMR AML ST", "5-3-2": "GK DC DC DC WBR WBL DM MC MC ST ST"}


def fit_sql(pos, mode="full"):
    """0-100 fit at a slot, only if the player can play there (>= 15/20). Attributes modes use the best
    role score of the slot's group; moneyball uses the stats-only performance index."""
    col = "stc" if pos == "ST" else pos.lower()
    if mode == "moneyball":
        return f"CASE WHEN positions_{col} >= 15 THEN stat_score END"
    roles = [f"role_{r}" for r, (g, _) in ROLES.items() if g == SLOT_GROUP[pos]]
    return f"CASE WHEN positions_{col} >= 15 THEN greatest({', '.join(roles)}) END"


def _club():
    if not CLUB:
        raise HTTPException(404, "no managed club in this save (re-upload the .fm save)")
    return CLUB


def _formation(f):
    f = f or _club()["formation"]
    if len(f) != 11 or any(p not in SLOT_GROUP for p in f):
        raise HTTPException(400, "formation needs 11 valid positions")
    return f


def squad_report(formation, mode="full"):
    club = _club()
    pos_set = sorted(set(formation))
    cur = con().execute(f"SELECT uid, name, age, ability_current AS ca, ability_potential AS pa, transfer_value, contract_wage, "
                        f"{', '.join(f'{fit_sql(p, mode)} AS fit_{p}' for p in pos_set)} FROM players WHERE club_uid = ?", [club["club_uid"]])
    names = [d[0] for d in cur.description]
    squad = [dict(zip(names, r)) for r in cur.fetchall()]

    def card(p, pos):
        if not p:
            return None
        c = {"uid": p["uid"], "name": p["name"], "age": p["age"], "fit": p[f"fit_{pos}"], "wage": p["contract_wage"]}
        return {**c, "ca": p["ca"], "pa": p["pa"]} if mode == "full" else c
    ranked = {p: sorted((s for s in squad if s[f"fit_{p}"] is not None), key=lambda s: -s[f"fit_{p}"]) for p in pos_set}
    # Greedy XI: scarcest positions pick first so a versatile player isn't spent where others could play.
    taken, starters = set(), {}
    for i in sorted(range(11), key=lambda i: len(ranked[formation[i]])):
        pos = formation[i]
        starters[i] = next((s for s in ranked[pos] if s["uid"] not in taken), None)
        if starters[i]:
            taken.add(starters[i]["uid"])
    fits = [starters[i][f"fit_{formation[i]}"] if starters[i] else 0 for i in range(11)]
    team_fit = sum(fits) / 11
    slots = []
    for i, pos in enumerate(formation):
        st = starters[i]
        backup = next((s for s in ranked[pos] if s["uid"] not in taken), None)
        sf, bf = fits[i], backup[f"fit_{pos}"] if backup else 0
        need = round((team_fit - sf) + 0.3 * (sf - bf), 1)
        slots.append({"slot_index": i, "pos": pos, "starter": card(st, pos), "backup": card(backup, pos),
                      "depth": len(ranked[pos]), "need": need,
                      "grade": "weak" if need >= 4 else "strong" if need <= 1 else "ok"})
    values = [s["transfer_value"] or 0 for s in squad]
    wages = sorted((s["contract_wage"] or 0 for s in squad), reverse=True)
    xi_wages = sorted((starters[i]["contract_wage"] or 0 for i in starters if starters[i]), reverse=True)
    gender = con().execute("SELECT mode(gender) FROM players WHERE club_uid = ?", [club["club_uid"]]).fetchone()[0]
    shape = next((k for k, v in SHAPES.items() if sorted(v.split()) == sorted(formation)), None)
    return {"club_uid": club["club_uid"], "club_name": club["club_name"], "formation": formation, "formation_name": shape,
            "mode": mode, "shapes": {k: v.split() for k, v in SHAPES.items()}, "slots": slots, "team_fit": round(team_fit, 1),
            "gender": gender, "default_budget": int(round(2 * max(values, default=0), -4)),
            # A signing can earn up to ~25% above the current top earner before he breaks the wage structure.
            "top_wage": wages[0] if wages else 0, "default_wage_max": int(round(1.25 * (wages[0] if wages else 0), -2)),
            "xi_median_wage": xi_wages[len(xi_wages) // 2] if xi_wages else 0}


@app.get("/api/club")
def club(formation: str | None = None, mode: str = "full", fair_play: bool = False):
    return squad_report(_formation(formation.split(",") if formation else None), _mode(mode, fair_play))


class TargetQuery(BaseModel):
    budget: int | None = None    # max transfer fee
    wage_max: int | None = None  # max weekly wage he currently earns (what we'd have to match)
    age_max: int | None = None
    gender: str | None = None
    formation: list[str] | None = None
    slot_index: int | None = None  # only this pitch slot (0-10)
    mode: str = "full"
    fair_play: bool = False


def targets(q: TargetQuery, rep, slots, per_pos, min_rating, extra_where=""):
    """Top 20 outside players who beat the current starter at the given slots, scored by
    upgrade x slot need, plus how far under the fair fee + wages package they are, minus a penalty
    for wages above the club's structure (a well-paid player rarely takes a pay cut to move)."""
    club, mode = _club(), q.mode
    wage_ref = max(rep["xi_median_wage"], 1)
    rows = []
    for s in slots:
        pos, sf = s["pos"], (s["starter"] or {}).get("fit") or 0
        col = "stc" if pos == "ST" else pos.lower()
        fit = fit_sql(pos, mode)
        where = [f"positions_{col} >= {min_rating}", f"({fit}) > ?", "coalesce(club_uid, -1) <> ?",
                 "(transfer_value > 0 OR free_agent)"]
        args = [sf + 2, club["club_uid"]]
        for cond, val in (("transfer_value <= ?", q.budget), ("coalesce(contract_wage, 0) <= ?", q.wage_max),
                          ("age <= ?", q.age_max)):
            if val is not None:
                where.append(cond); args.append(val)
        if q.gender in ("Men", "Women"):
            where.append("gender = ?"); args.append(q.gender)
        weight = 1 + max(s["need"], 0) / 10
        # wage penalty: 0 at/below our XI's median wage, growing with each multiple above it.
        score = (f"(({fit}) - {sf}) * {weight} "
                 f"+ 0.15 * CASE WHEN free_agent THEN 60 ELSE greatest(coalesce(deal_score, 0), 0) END "
                 f"- 4 * greatest(ln(greatest(coalesce(contract_wage, 0), 1) / {wage_ref}), 0)")
        # Fit/score are computed on the full table (moneyball fit uses positions_*), then rows come back mode-filtered.
        cur = con().execute(f"""SELECT v.*, t.fit, t.score FROM {source(mode)} v JOIN (
                                   SELECT uid, {fit} AS fit, {score} AS score FROM (SELECT p.*, d.deal_score FROM players p
                                       JOIN (SELECT uid, deal_score_{mode} AS deal_score FROM players) d USING (uid))
                                   WHERE {' AND '.join(where)} {extra_where} ORDER BY score DESC LIMIT {per_pos}) t USING (uid)""", args)
        names = [d[0] for d in cur.description]
        for r in cur.fetchall():
            row = dict(zip(names, r))
            up = round(row["fit"] - sf, 1)
            deal, wage = row.get("deal_score"), row.get("contract_wage") or 0
            bits = [f"{pos}: +{up} fit over {(s['starter'] or {}).get('name', 'empty slot')}"]
            if row["free_agent"]:
                bits.append("free agent")
            elif deal and deal > 0:
                bits.append(f"{deal:.0f}% under fair fee + wages")
            if wage > 1.5 * wage_ref:
                bits.append(f"wage £{wage / 1000:.1f}k/w is {wage / wage_ref:.1f}× your XI median")
            row.update(target_pos=pos, target_slot_index=s["slot_index"], upgrade=up, replaces=s["starter"],
                       reason=" · ".join(bits))
            rows.append(row)
    best = {}
    for r in sorted(rows, key=lambda r: -r["score"]):
        best.setdefault(r["uid"], r)  # one entry per player, at his best-scoring slot
    return {"rows": list(best.values())[:20]}


def _prepare(q: TargetQuery):
    q.mode = _mode(q.mode, q.fair_play)
    rep = squad_report(_formation(q.formation), q.mode)
    if q.budget is None:
        q.budget = rep["default_budget"] or None
    if q.wage_max is None:
        q.wage_max = rep["default_wage_max"] or None
    if q.gender is None:
        q.gender = rep["gender"]
    if q.age_max is None:
        q.age_max = 31  # resale value and decline: veterans otherwise top every "cheap upgrade" list
    if q.slot_index is not None and not 0 <= q.slot_index < 11:
        raise HTTPException(400, "slot_index must be 0-10")
    return q, rep


def _one_slot_per_pos(slots, key):
    return list({s["pos"]: s for s in sorted(slots, key=key)}.values())


@app.post("/api/club/needs")
def club_needs(q: TargetQuery):
    q, rep = _prepare(q)
    if q.slot_index is not None:
        return targets(q, rep, [rep["slots"][q.slot_index]], per_pos=20, min_rating=15)
    weak = sorted(_one_slot_per_pos(rep["slots"], lambda s: s["need"]), key=lambda s: -s["need"])
    weak = [s for s in weak if s["need"] > 0][:4] or weak[:2]
    return targets(q, rep, weak, per_pos=8, min_rating=15)


@app.post("/api/club/tactic")
def club_tactic(q: TargetQuery):
    q, rep = _prepare(q)
    # Plug-and-play: natural in the slot (>= 18/20) and already at the XI's level. Only full mode may
    # use CA for that; the other modes rely on the fit-over-starter requirement alone.
    xi_ca = [s["starter"]["ca"] for s in rep["slots"] if s["starter"] and "ca" in s["starter"]]
    extra = f"AND ability_current >= {int(sum(xi_ca) / len(xi_ca)) - 5}" if xi_ca else ""
    if q.slot_index is not None:
        return targets(q, rep, [rep["slots"][q.slot_index]], per_pos=20, min_rating=18, extra_where=extra)
    return targets(q, rep, _one_slot_per_pos(rep["slots"], lambda s: s["need"]), per_pos=4, min_rating=18, extra_where=extra)


# ---- Offers: is this fee fair, and would the club accept it? ----
# ponytail: asking-price heuristic (status x contract multipliers on transfer value); FM's AI also
# weighs club finances, player morale and interest from other clubs, which the save export lacks.
STATUS_MULT = {"star_player": 1.5, "important_player": 1.35, "regular_starter": 1.2, "squad_player": 1.0,
               "fringe_player": 0.9, "youngster": 1.1, "breakthrough_prospect": 1.15, "future_prospect": 1.2}


class OfferQuery(BaseModel):
    uid: int
    fee: int
    wage: int | None = None      # weekly wage offered; defaults to his current wage
    mode: str = "full"
    fair_play: bool = False


@app.post("/api/offer")
def offer(q: OfferQuery):
    mode = _mode(q.mode, q.fair_play)
    p = _player(q.uid, mode)
    lens_ids = [k for k in MODES[mode] if f"fair_fee_{k}" in COLS]
    row = con().execute(f"SELECT {', '.join(f'fair_fee_{k}, fair_wage_{k}' for k in lens_ids + [mode])} FROM players WHERE uid = ?",
                        [q.uid]).fetchone()
    vals = dict(zip([f"{a}_{k}" for k in lens_ids + [mode] for a in ("fee", "wage")], row))
    cur_wage = p.get("contract_wage") or 0
    wage = q.wage if q.wage is not None else cur_wage
    lenses = [{"lens": k, "fair_fee": vals[f"fee_{k}"], "fair_wage": vals[f"wage_{k}"],
               "fair_package": vals[f"fee_{k}"] + WAGE_WEEKS * vals[f"wage_{k}"]}
              for k in lens_ids if vals[f"fee_{k}"] is not None]
    fair_fee, fair_wage = vals[f"fee_{mode}"], vals[f"wage_{mode}"]
    value = p.get("transfer_value")  # None = the game puts no price on him: his club won't sell
    months = p.get("months_left")
    notes = []
    if fair_fee is None:
        verdict, ratio = "unknown", None
        notes.append("Fair fee can't be computed in Moneyball under 900 minutes played this season." if mode == "moneyball"
                     else f"Not enough data to value this player in {mode} mode.")
    else:
        ratio = q.fee / fair_fee if fair_fee else None
        verdict = ("unknown" if ratio is None else "steal" if ratio < 0.7 else "good" if ratio < 0.9
                   else "fair" if ratio <= 1.1 else "overpay" if ratio <= 1.4 else "rip-off")
    if fair_wage and wage > 1.3 * fair_wage:
        notes.append(f"The wage (£{wage / 1000:.1f}k/w) is well above a fair £{fair_wage / 1000:.1f}k/w for this player.")
    if p.get("free_agent"):
        asking, acceptance = 0, "likely"
        notes.append("Free agent: no fee needed, only wages.")
    elif value is None:
        asking, acceptance = None, "not for sale"
        notes.append("No game valuation: his club does not want to sell. Any bid is likely to be rejected outright.")
    else:
        contract = 1.0 if months is None else 0.6 if months <= 6 else 0.85 if months <= 12 else 1.1 if months >= 36 else 1.0
        asking = round(value * STATUS_MULT.get(p.get("contract_squad_status"), 1.2) * contract, -3)
        # Acceptance is the selling club's view, independent of whether the fee is fair for you.
        acceptance = "likely" if q.fee >= asking else "possible" if q.fee >= 0.8 * asking else "unlikely"
    if months is not None and months <= 6 and not p.get("free_agent"):
        notes.append(f"Contract ends in {months} months: a pre-contract or a cut-price fee is realistic.")
    if p.get("on_loan"):
        notes.append(f"On loan from {p.get('loan_parent_club_name')}; negotiate with the parent club.")
    if CLUB and wage > 1.25 * squad_report(_formation(None), mode)["top_wage"]:
        notes.append("The wage would break your wage structure (over 1.25× your top earner).")
    if q.wage is not None and q.wage < cur_wage:
        notes.append("You offer less than his current wage: players rarely accept a pay cut.")
    # The mode's fee comes from one joint model, so it can sit outside the single-lens fees: include it.
    fees = [l["fair_fee"] for l in lenses] + ([fair_fee] if fair_fee is not None else [])
    return {"uid": q.uid, "name": p["name"], "mode": mode, "fee": q.fee, "wage": wage, "current_wage": cur_wage,
            "transfer_value": value, "for_sale": value is not None or bool(p.get("free_agent")),
            "package": q.fee + WAGE_WEEKS * wage, "fair_fee": fair_fee, "fair_wage": fair_wage,
            "fair_package": fair_fee + WAGE_WEEKS * fair_wage if fair_fee is not None else None,
            "fair_fee_range": [min(fees), max(fees)] if fees else None, "lenses": lenses,
            "ratio": round(ratio, 2) if ratio is not None else None, "verdict": verdict,
            "asking_estimate": asking, "acceptance": acceptance, "notes": notes}


@app.get("/")
def index():
    return FileResponse(os.path.join(RES, "static", "index.html"))
