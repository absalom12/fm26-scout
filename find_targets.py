import pandas as pd
import numpy as np

# File paths
squad_path = "/Users/thomaslopez/Football_Data/fm26data/bristol_rovers_squad.csv"
all_players_path = "/Users/thomaslopez/Football_Data/fm26data/all_world_players.csv"

# Load datasets
squad = pd.read_csv(squad_path, low_memory=False)
world = pd.read_csv(all_players_path, low_memory=False)

print(f"Total raw database entries: {len(world)}")

# 1. HARDENED MEN-ONLY FILTERING VIA FM26 UID STRUCTURE
# Men's entities predate FM26's women's database; women's sections receive UIDs >= 2,000,000,000.
NEW_UID_THRESHOLD = 2_000_000_000

# Filter out players assigned to women's clubs or possessing women's database UIDs
if "club_uid" in world.columns:
    world["club_uid"] = pd.to_numeric(world["club_uid"], errors="coerce").fillna(0)
    world_men = world[world["club_uid"] < NEW_UID_THRESHOLD].copy()
elif "club_id" in world.columns:
    world["club_id"] = pd.to_numeric(world["club_id"], errors="coerce").fillna(0)
    world_men = world[world["club_id"] < NEW_UID_THRESHOLD].copy()
else:
    # Backup player UID check
    world["uid"] = pd.to_numeric(world["uid"], errors="coerce").fillna(0)
    world_men = world[world["uid"] < NEW_UID_THRESHOLD].copy()

# Double check is_female / gender flag if explicit in output
if "is_female" in world_men.columns:
    world_men = world_men[world_men["is_female"] == False]

print(f"Filtered Men's Database: {len(world_men)} records")

# 2. TACTICAL ROLES & WEIGHTINGS (0 Hidden CA/PA Cheating)
TACTICAL_ROLES = {
    "GK": {
        "pos": "GK",
        "primary": ["attributes_handling", "attributes_reflexes", "attributes_command_of_area"],
        "secondary": ["attributes_aerial_reach", "attributes_positioning"]
    },
    "BPD": {  # Ball-Playing Defender
        "pos": "DC",
        "primary": ["attributes_tackling", "attributes_marking", "attributes_heading", "attributes_passing"],
        "secondary": ["attributes_jumping_reach", "attributes_positioning", "attributes_composure"]
    },
    "IWB": {  # Inverted Wing-Back
        "pos": "DL;DR;WBL;WBR",
        "primary": ["attributes_tackling", "attributes_passing", "attributes_work_rate", "attributes_stamina"],
        "secondary": ["attributes_positioning", "attributes_first_touch", "attributes_pace"]
    },
    "B2B": {  # Box-To-Box Midfielder
        "pos": "DM;MC",
        "primary": ["attributes_passing", "attributes_tackling", "attributes_work_rate", "attributes_stamina"],
        "secondary": ["attributes_off_the_ball", "attributes_first_touch", "attributes_pace"]
    },
    "IF": {   # Inside Forward / Winger
        "pos": "AML;AMR",
        "primary": ["attributes_dribbling", "attributes_pace", "attributes_acceleration", "attributes_technique"],
        "secondary": ["attributes_finishing", "attributes_passing", "attributes_off_the_ball"]
    },
    "PF": {   # Pressing Forward
        "pos": "STC",
        "primary": ["attributes_work_rate", "attributes_stamina", "attributes_pace", "attributes_acceleration"],
        "secondary": ["attributes_finishing", "attributes_off_the_ball", "attributes_composure"]
    }
}

def calculate_role_score(df_row, role_def):
    primaries = [c for c in role_def["primary"] if c in df_row]
    secondaries = [c for c in role_def["secondary"] if c in df_row]
    p_vals = [float(df_row[c]) for c in primaries if pd.notnull(df_row[c])]
    s_vals = [float(df_row[c]) for c in secondaries if pd.notnull(df_row[c])]
    
    p_score = np.mean(p_vals) if p_vals else 10.0
    s_score = np.mean(s_vals) if s_vals else 10.0
    return (p_score * 0.65) + (s_score * 0.35)

# 3. BRISTOL ROVERS SQUAD NEED BENCHMARKS
squad_benchmarks = {}
squad_counts = {}

for role_code, role_def in TACTICAL_ROLES.items():
    pos_targets = role_def["pos"].split(";")
    pattern = "|".join(pos_targets)
    squad_matching = squad[squad["natural_positions"].astype(str).str.contains(pattern, na=False)]
    squad_counts[role_code] = len(squad_matching)
    
    if len(squad_matching) > 0:
        scores = [calculate_role_score(row, role_def) for _, row in squad_matching.iterrows()]
        squad_benchmarks[role_code] = round(np.mean(scores), 2)
    else:
        squad_benchmarks[role_code] = 7.5

# 4. SCOUTING ENGINE
world_men["transfer_value_num"] = pd.to_numeric(world_men["transfer_value"], errors="coerce").fillna(0)

# Targets: Age 16-21, Value <= £2.5M, not Bristol Rovers
prospects = world_men[
    (world_men["age"] >= 16) & (world_men["age"] <= 21) &
    (world_men["club_name"] != "Bristol Rovers") &
    (world_men["transfer_value_num"] >= 50000) &
    (world_men["transfer_value_num"] <= 2500000)
].copy()

results = []

for _, player in prospects.iterrows():
    pos_str = str(player["natural_positions"])
    best_role = None
    best_player_score = 0
    
    for role_code, role_def in TACTICAL_ROLES.items():
        pos_targets = role_def["pos"].split(";")
        if any(pt in pos_str for pt in pos_targets):
            r_score = calculate_role_score(player, role_def)
            if r_score > best_player_score:
                best_player_score = r_score
                best_role = role_code
                
    if not best_role:
        continue
        
    rovers_benchmark = squad_benchmarks[best_role]
    squad_depth = squad_counts[best_role]
    
    urgency_mult = 1.4 if squad_depth == 0 else (1.2 if best_player_score > rovers_benchmark else 1.0)
    
    bargain_score = max(0, 30 - (player["transfer_value_num"] / 85000))
    youth_score = (22 - player["age"]) * 3.5
    det = float(player.get("attributes_determination", 10))
    wr = float(player.get("attributes_work_rate", 10))
    mental_boost = (det + wr) / 4
    
    quality_diff = (best_player_score - rovers_benchmark) * 6
    base_score = 35 + quality_diff + bargain_score + youth_score + mental_boost
    final_deal_score = round(max(0, base_score * urgency_mult), 1)
    
    results.append({
        "deal_score": final_deal_score,
        "name": player["name"],
        "age": player["age"],
        "role": best_role,
        "pos": player["natural_positions"],
        "player_attr": round(best_player_score, 1),
        "rovers_attr": rovers_benchmark,
        "value": player["transfer_value_num"],
        "club": player["club_name"]
    })

results_df = pd.DataFrame(results).sort_values(by="deal_score", ascending=False)

print("\n==========================================================================================")
print("     MONEYBALL MALE RECRUITMENT TARGETS FOR BRISTOL ROVERS (UID < 2,000,000,000)")
print("==========================================================================================")
cols = ["deal_score", "name", "age", "role", "player_attr", "rovers_attr", "value", "club"]
display_df = results_df[cols].head(25).copy()
display_df.columns = ["Deal Score", "Name", "Age", "Role", "Player Attr", "Rovers Attr", "Value (£)", "Club"]
print(display_df.to_string(index=False))