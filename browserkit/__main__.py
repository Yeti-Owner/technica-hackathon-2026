import asyncio
import json
import queue
import threading

from playwright.async_api import Error
from .controller import BrowserController
from .voice_control import VoiceControl
from .voice import Microphone

HELP = """Commands:
  home | go URL | back | forward | reload
  scroll [pixels] | next | prev | enter | type TEXT | click EXACT LINK NAME
  text | links | images | image INDEX [FRAME] | shot
  tabs | tab INDEX | help | quit
  mic on | mic off | mic status | mic reset
  mic list | mic use NUMBER | mic default
Image and tab indices start at 0. Output files go to artifacts/."""


async def main():
    commands = queue.Queue()

    def read_commands():
        while True:
            try:
                line = input("browser> ")
            except EOFError:
                line = "quit"
            commands.put(line)
            if line.strip() == "quit":
                return

    async with BrowserController() as b:
        voice = VoiceControl(b)
        voice.start()
        try:
            await command_loop(b, commands, read_commands, voice)
        finally:
            await voice.close()


async def command_loop(b, commands, read_commands, voice):
    print(HELP)
    # Daemon input thread allows closing the browser to exit immediately.
    threading.Thread(target=read_commands, daemon=True).start()
    while b.browser.is_connected() and b.context.pages:
        if b.page.is_closed():
            b.page = b.context.pages[-1]
        try:
            line = commands.get_nowait().strip()
        except queue.Empty:
            await asyncio.sleep(0.1)
            continue
        command, _, arg = line.partition(" ")
        try:
            if command == "quit": break
            elif command == "help": print(HELP)
            elif command == "home": await b.home()
            elif command == "go": await b.goto(arg)
            elif command == "back": await b.page.go_back()
            elif command == "forward": await b.page.go_forward()
            elif command == "reload": await b.page.reload()
            elif command == "scroll": await b.scroll(int(arg or 600))
            elif command == "next": await b.next_focus()
            elif command == "prev": await b.previous_focus()
            elif command == "enter": await b.activate()
            elif command == "type": await b.page.keyboard.insert_text(arg)
            elif command == "click": await b.click_link(arg)
            elif command == "text": print(await b.text())
            elif command == "links": print(json.dumps(await b.links(), indent=2))
            elif command == "images": print(json.dumps(await b.images(), indent=2))
            elif command == "image":
                parts = arg.split()
                index, frame = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                path = f"artifacts/image-{frame}-{index}.png"
                await b.capture_image(index, path, frame)
                print(path)
            elif command == "shot":
                from pathlib import Path
                Path("artifacts").mkdir(exist_ok=True)
                await b.page.screenshot(path="artifacts/page.png", full_page=True)
                print("artifacts/page.png")
            elif command == "tabs": print([p.url for p in b.context.pages])
            elif command == "tab": await b.select_tab(int(arg))
            elif command == "mic":
                if arg == "off": voice.pause()
                elif arg == "on":
                    if not voice.enabled: voice.start()
                elif arg == "reset": voice.reset_learning()
                elif arg == 'list':
                    for index, name in Microphone.devices(): print(f'{index}: {name}')
                elif arg.startswith('use '): voice.select_device(int(arg[4:].strip()))
                elif arg == 'default': voice.select_device(None)
                elif arg not in ("", "status"): print("Use mic on/off/status/reset/list, mic use NUMBER, or mic default.")
                print(voice.status())
            elif command: print("Unknown command. Type help.")
        except (Error, ValueError, IndexError) as error:
            print(f"Could not complete command: {error}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
