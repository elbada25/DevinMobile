import sqlite3, os, sys

db_path = os.path.join(os.environ.get("APPDATA", r"C:\Users\EduardoBadaRuano\AppData\Roaming"), "devin", "cli", "sessions.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in cur.fetchall()]
print("Tablas:", tables)

for t in tables:
    try:
        cur.execute(f'SELECT sql FROM sqlite_master WHERE name="{t}"')
        row = cur.fetchone()
        print(f"\n--- {t} ---")
        print(row[0][:800] if row else "(no schema)")
    except Exception as e:
        print(f"\n--- {t} --- ERROR: {e}")

conn.close()
