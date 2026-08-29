import sqlite3

try:
    conn = sqlite3.connect('var/synapse.db', timeout=5.0)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    for table in tables:
        table_name = table[0]
        if table_name != 'sqlite_sequence':
            cursor.execute(f"DELETE FROM {table_name};")
    conn.commit()
    conn.close()
    print('SQLite tables cleared successfully.')
except Exception as e:
    print('Failed to clear SQLite tables:', e)
