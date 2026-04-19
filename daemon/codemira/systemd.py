import os
import subprocess
import sys

UNIT_NAME = "codemira-daemon"
UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
LOG_DIR = os.path.expanduser("~/.local/state/codemira/logs")


def _collect_env_vars() -> dict[str, str]:
    api_key = os.environ.get("CODEMIRA_EXTRACTION_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "CODEMIRA_EXTRACTION_API_KEY is not set. Export it in your shell before running "
            "`python -m codemira.systemd install` — systemd user units do not inherit "
            "your shell environment at runtime, so the key must be baked into the unit file."
        )
    env: dict[str, str] = {}
    opencode_db = os.environ.get("OPENCODE_DB")
    if opencode_db:
        env["OPENCODE_DB"] = opencode_db
    for k, v in os.environ.items():
        if k.startswith("CODEMIRA_"):
            env[k] = v
    return env


def _escape_env_value(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")


def _render_env_lines(env: dict[str, str]) -> str:
    lines = []
    for k, v in env.items():
        lines.append(f'Environment="{k}={_escape_env_value(v)}"')
    return "\n".join(lines)


def install():
    python_path = sys.executable
    unit_path = os.path.join(UNIT_DIR, f"{UNIT_NAME}.service")
    os.makedirs(UNIT_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    env = _collect_env_vars()
    env_lines = _render_env_lines(env)
    unit = f"""[Unit]
Description=CodeMira daemon

[Service]
Type=simple
ExecStart={python_path} -m codemira.daemon
Restart=always
RestartSec=5
StandardOutput=append:{LOG_DIR}/daemon.log
StandardError=append:{LOG_DIR}/daemon-error.log
{env_lines}

[Install]
WantedBy=default.target
"""
    with open(unit_path, "w") as f:
        f.write(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", f"{UNIT_NAME}.service"], check=True)
    subprocess.run(["systemctl", "--user", "restart", f"{UNIT_NAME}.service"], check=True)
    print(f"Installed {UNIT_NAME}. Logs: {LOG_DIR}/daemon.log")
    print("For autostart without an active login session, run: loginctl enable-linger $USER")


def uninstall():
    unit_path = os.path.join(UNIT_DIR, f"{UNIT_NAME}.service")
    if os.path.exists(unit_path):
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{UNIT_NAME}.service"], check=False)
        os.remove(unit_path)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        print(f"Uninstalled {UNIT_NAME}")
    else:
        print(f"{UNIT_NAME} is not installed")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("install", "uninstall"):
        print("usage: python -m codemira.systemd [install|uninstall]", file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] == "install":
        install()
    else:
        uninstall()


if __name__ == "__main__":
    main()
