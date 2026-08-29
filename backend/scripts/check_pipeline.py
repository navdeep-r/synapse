import asyncio
from app.config import get_settings
from app.db import Database

async def main():
    settings = get_settings()
    db = Database(settings)
    await db.connect()
    
    jobs = await db.fetch_all("SELECT * FROM pipeline_jobs ORDER BY created_at DESC LIMIT 5")
    print("Recent pipeline jobs:")
    for j in jobs:
        print(dict(j))
        
    dead_letters = await db.fetch_all("SELECT * FROM dead_letters ORDER BY created_at DESC LIMIT 5")
    print("\nRecent dead letters:")
    for d in dead_letters:
        d_dict = dict(d)
        d_dict.pop('payload_b64', None) # Don't print the huge payload
        print(d_dict)

if __name__ == "__main__":
    asyncio.run(main())
