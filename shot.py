"""Capture a real terminal shot of glassbox. Run from anywhere."""
import asyncio

from app import Glassbox


async def main():
    app = Glassbox()
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause(4.0)  # let poller post real data
        app.save_screenshot("screenshot.svg")
        print("saved screenshot.svg")

asyncio.run(main())
