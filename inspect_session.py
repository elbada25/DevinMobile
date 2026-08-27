import sqlite3, os, json

db_path = os.path.join(os.environ.get("APPDATA", r"C:\Users\EduardoBadaRuano\AppData\Roaming"), "devin", "cli", "sessions.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Ver la sesion regal-adasaurus
sid = "regal-adasaurus"
cur.execute("SELECT id, backend_type, model, agent_mode, main_chain_id, title FROM sessions WHERE id=?", (sid,))
row = cur.fetchone()
print("Sesion:", row)

# Ver los ultimos message_nodes de esa sesion
cur.execute("SELECT row_id, node_id, parent_node_id, substr(chat_message, 1, 200), created_at FROM message_nodes WHERE session_id=? ORDER BY node_id DESC LIMIT 5", (sid,))
print("\nUltimos message_nodes:")
for r in cur.fetchall():
    print(f"  row={r[0]} node={r[1]} parent={r[2]} msg={r[3][:120]}... ts={r[4]}")

# Ver el formato de chat_message (uno completo)
cur.execute("SELECT chat_message FROM message_nodes WHERE session_id=? ORDER BY node_id DESC LIMIT 1", (sid,))
msg = cur.fetchone()
if msg:
    print("\nchat_message (primeros 1000 chars):")
    print(msg[0][:1000])

# Ver prompt_history
cur.execute("SELECT id, substr(content,1,100), timestamp, is_shell FROM prompt_history WHERE session_id=? ORDER BY id DESC LIMIT 3", (sid,))
print("\nprompt_history:")
for r in cur.fetchall():
    print(f"  id={r[0]} content={r[1]}... ts={r[2]} shell={r[3]}")

# Ver app_state
cur.execute("SELECT key, substr(value, 1, 100) FROM app_state")
print("\napp_state:")
for r in cur.fetchall():
    print(f"  {r[0]} = {r[1]}...")

conn.close()
