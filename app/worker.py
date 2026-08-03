"""Background worker entrypoint — placeholder for Phase 10 background jobs."""
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vexarium.worker")

async def main():
    logger.info("VEXARIUM worker started (placeholder — no jobs registered yet)")
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
