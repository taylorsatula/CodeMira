import os
import subprocess
import sys
from xml.sax.saxutils import escape

PLIST_NAME = "com.codemira.daemon"
PLIST_DIR = os.path.expanduser("~/Library/LaunchAgents")
LOG_DIR = os.path.expanduser("~/Library/Logs/codemira")


def _collect_env_vars() -> dict[str, str]:
    api_key = os.environ.get("CODEMIRA_EXTRACTION_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "CODEMIRA_EXTRACTION_API_KEY is not set. Export it in your shell before running "
            "`python -m codemira.launchd install` — launchd agents do not inherit "
            "your shell environment at runtime, so the key must be baked into the plist."
        )
    env: dict[str, str] = {}
    opencode_db = os.environ.get("OPENCODE_DB")
    if opencode_db:
        env["OPENCODE_DB"] = opencode_db
    for k, v in os.environ.items():
        if k.startswith("CODEMIRA_"):
            env[k] = v
    return env


def _render_env_dict(env: dict[str, str]) -> str:
    lines = []
    for k, v in env.items():
        lines.append(f"        <key>{escape(k)}</key>")
        lines.append(f"        <string>{escape(v)}</string>")
    return "\n".join(lines)


def install():
    python_path = sys.executable
    plist_path = os.path.join(PLIST_DIR, f"{PLIST_NAME}.plist")
    os.makedirs(PLIST_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    env = _collect_env_vars()
    env_xml = _render_env_dict(env)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{escape(PLIST_NAME)}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{escape(python_path)}</string>
        <string>-m</string><string>codemira.daemon</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{escape(LOG_DIR)}/daemon.log</string>
    <key>StandardErrorPath</key><string>{escape(LOG_DIR)}/daemon-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
{env_xml}
    </dict>
</dict>
</plist>"""
    with open(plist_path, "w") as f:
        f.write(plist)
    subprocess.run(["launchctl", "unload", plist_path], check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", plist_path], check=True)
    print(f"Installed {PLIST_NAME}. Logs: {LOG_DIR}/daemon.log")


def uninstall():
    plist_path = os.path.join(PLIST_DIR, f"{PLIST_NAME}.plist")
    if os.path.exists(plist_path):
        subprocess.run(["launchctl", "unload", plist_path], check=False)
        os.remove(plist_path)
        print(f"Uninstalled {PLIST_NAME}")
    else:
        print(f"{PLIST_NAME} is not installed")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("install", "uninstall"):
        print("usage: python -m codemira.launchd [install|uninstall]", file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] == "install":
        install()
    else:
        uninstall()


if __name__ == "__main__":
    main()
