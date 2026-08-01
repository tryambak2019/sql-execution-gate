import asyncio
from google.adk.runners import InMemoryRunner
from app.agent import app  # Import the app

async def main():
    runner = InMemoryRunner(app=app)
    response = await runner.run_debug("Your query here")
    session_id = runner.session.id
    await runner.session_service.delete_session(session_id)
    await runner.close()

if __name__ == "__main__":
    asyncio.run(main())