import sqlite3, os, json

db_path = os.path.join(os.environ.get("APPDATA", r"C:\Users\EduardoBadaRuano\AppData\Roaming"), "devin", "cli", "sessions.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

sid = "regal-adasaurus"

# Ver un mensaje de role "user" para entender el formato
cur.execute("SELECT node_id, chat_message FROM message_nodes WHERE session_id=? AND chat_message LIKE '%\"role\":\"user\"%' ORDER BY node_id DESC LIMIT 2", (sid,))
print("Mensajes de usuario:")
for r in cur.fetchall():
    msg = json.loads(r[1])
    print(f"  node={r[0]} role={msg.get('role')} content={str(msg.get('content',''))[:200]}")
    print(f"  keys={list(msg.keys())}")
    print()

# Ver el main_chain_id y el ultimo node
cur.execute("SELECT main_chain_id FROM sessions WHERE id=?", (sid,))
main_chain = cur.fetchone()[0]
print(f"main_chain_id: {main_chain}")

# Ver el ultimo node del chain
cur.execute("SELECT node_id, parent_node_id, chat_message FROM message_nodes WHERE session_id=? ORDER BY node_id DESC LIMIT 1", (sid,))
r = cur.fetchone()
print(f"ultimo node: {r[0]} parent: {r[1]}")
msg = json.loads(r[2])
print(f"  role: {msg.get('role')}")

# Ver si hay algo en app_state relacionado con sesiones activas
cur.execute("SELECT key, value FROM app_state")
print("\napp_state completo:")
for r in cur.fetchall():
    print(f"  {r[0]} = {r[1][:200]}")

conn.close()
