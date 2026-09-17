"""Capture a real terminal shot of glassbox. Run from anywhere."""
import asyncio
import re

from app import Glassbox

FONT = "Iosevka Nerd Font"


def use_font(path):
    # textual/rich hardcode Fira Code in exported SVGs with no font
    # option, so swap it for Iosevka Nerd Font after capture.
    svg = open(path, encoding="utf-8").read()
    svg = re.sub(r'src:\s*local\([^)]*\),[^;]*?;',
                 f'src: local("{FONT}");', svg, flags=re.DOTALL)
    svg = svg.replace('"Fira Code"', f'"{FONT}"')
    svg = svg.replace("font-family: Fira Code, monospace;",
                      f'font-family: "{FONT}", monospace;')
    open(path, "w", encoding="utf-8").write(svg)


async def main():
    app = Glassbox()
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause(4.0)  # let poller post real data
        app.save_screenshot("screenshot.svg")
        use_font("screenshot.svg")
        print("saved screenshot.svg")


asyncio.run(main())
