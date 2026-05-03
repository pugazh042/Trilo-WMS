import sqlite3
conn = sqlite3.connect('db.sqlite3')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
tables = cur.fetchall()
for table in tables:
    try:
        cur.execute(f"DROP TABLE {table[0]}")
    except Exception as e:
        print(e)
conn.commit()
conn.close()
print("All tables dropped.")
