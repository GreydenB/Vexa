import subprocess
import os
def open_application(app_name: str):
    apps = {
        "spotify": r"C:\Users\greyb\AppData\Roaming\Spotify\Spotify.exe",
        "discord": r"C:\Users\greyb\AppData\Local\Discord\Update.exe",
        "minecraft launcher": r"C:\XboxGames\Minecraft Launcher\Content\Minecraft Launcher - Shortcut.lnk"
    }

    app_name = app_name.lower()

    if app_name not in apps:
        return f"I don't know how to open {app_name} yet."

    os.startfile(apps[app_name])
    

    return f"Opened {app_name}."
