import asyncio
import db

async def main():
    print("Initializing database...")
    await db.init_db()
    print("Database initialized successfully!")

    print("Registering room...")
    await db.register_middleman_room(-10012345678, "Test Deal Room")
    print("Room registered!")

    room = await db.get_middleman_room(-10012345678)
    print("Fetched room:", dict(room))

    print("Claiming room for deal_123...")
    claimed = await db.claim_middleman_room("deal_123")
    print("Claimed:", claimed)

    print("Claiming again for deal_123 (idempotency check)...")
    reclaimed = await db.claim_middleman_room("deal_123")
    print("Reclaimed:", reclaimed)

    print("Releasing room...")
    await db.set_middleman_room_status(-10012345678, "available", "")
    print("Released!")

    room_after = await db.get_middleman_room(-10012345678)
    print("Room after release:", dict(room_after))
    print("ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(main())
