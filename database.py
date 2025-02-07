# database.py
import sqlite3

conn = sqlite3.connect("bot_data.db")
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_data (
        user_id INTEGER PRIMARY KEY,
        title TEXT,
        body TEXT,
        category TEXT,
        tags TEXT,
        image TEXT,
        schedule_datetime TEXT
    )
""")
conn.commit()

def save_user_data(user_id, data):
    cursor.execute("""
        INSERT OR REPLACE INTO user_data (user_id, title, body, category, tags, image, schedule_datetime)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, data.get("title"), data.get("body"), data.get("category"),
          ",".join(data.get("tags", [])), data.get("image"), data.get("schedule_datetime")))
    conn.commit()

def load_user_data(user_id):
    cursor.execute("SELECT * FROM user_data WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        return {
            "title": row[1],
            "body": row[2],
            "category": row[3],
            "tags": row[4].split(",") if row[4] else [],
            "image": row[5],
            "schedule_datetime": row[6]
        }
    return {}

def delete_user_data(user_id):
    cursor.execute("DELETE FROM user_data WHERE user_id = ?", (user_id,))
    conn.commit()