import pandas as pd

df = pd.read_csv("all_world_players.csv", nrows=5)
gender_cols = [c for c in df.columns if any(k in c.lower() for k in ['gender', 'female', 'sex', 'is_female', 'gender_id'])]
print("Colonnes de genre trouvées :", gender_cols)