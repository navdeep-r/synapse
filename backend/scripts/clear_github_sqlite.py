import sqlite3

conn = sqlite3.connect('var/synapse.db')
cursor = conn.cursor()

github_tables = [
    'github_repositories',
    'github_file_state',
    'github_commits',
    'github_sync_runs',
    'github_webhook_events'
]

for table in github_tables:
    cursor.execute(f"DELETE FROM {table};")

cursor.execute("DELETE FROM sources WHERE id LIKE 'github:repo:%';")
cursor.execute("DELETE FROM chunks;")
cursor.execute("DELETE FROM documents;")
cursor.execute("DELETE FROM episode_receipts;")

conn.commit()
conn.close()
print("Successfully cleared GitHub repository data from SQLite.")
