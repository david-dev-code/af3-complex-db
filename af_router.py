#!/usr/bin/env python3
"""
AF3-DB Host Router
--------------------------------------------
Provides a unified CLI for server management and database interactions.
Intercepts local commands (start/stop/logs) and forwards DB commands to the container.
Handles cross-boundary file uploads automatically using native Python.
"""

import os
import sys
import subprocess
import secrets
import shutil
from pathlib import Path


if os.name == 'nt':
    os.system('')


class C:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    END = '\033[0m'


PROJECT_DIR = Path(__file__).resolve().parent


def check_docker():
    """Ensures Docker is installed before running container commands."""
    if not shutil.which("docker"):
        print(f"\n{C.RED}{C.BOLD}❌ Error: Docker is not installed or not in your PATH.{C.END}")
        sys.exit(1)


def is_container_running() -> bool:
    """Checks if the FastAPI Docker container is currently up and running."""
    try:
        res = subprocess.run(
            ["docker", "ps", "-q", "-f", "name=af3db_fastapi"],
            capture_output=True, text=True
        )
        return bool(res.stdout.strip())
    except Exception:
        return False


def show_help():
    """Prints a beautifully formatted help menu instantly using native terminal colors."""
    print(f"\n{C.BLUE}{C.BOLD}╭──────────────────────────────────────────────────────────╮{C.END}")
    print(f"{C.BLUE}{C.BOLD}│         AF3-DB Unified Command Line Interface            │{C.END}")
    print(f"{C.BLUE}{C.BOLD}╰──────────────────────────────────────────────────────────╯{C.END}")

    print(f"\n{C.CYAN}[ SERVER COMMANDS ] (Executed Locally){C.END}")
    print(f"  {C.GREEN}start{C.END}             Start the Docker containers")
    print(f"  {C.GREEN}stop{C.END}              Stop the Docker containers")
    print(f"  {C.GREEN}config{C.END}            Open the .env configuration file")
    print(f"  {C.GREEN}logs{C.END}              View server logs (e.g., af3-db logs web -n 50)")

    print(f"\n{C.CYAN}[ DATABASE COMMANDS ] (Forwarded to Container){C.END}")
    print(f"  {C.GREEN}upload-folder{C.END}     Upload AF3 output folders or archives (.zip/.tar)")
    print(f"  {C.GREEN}delete-complex{C.END}    Delete one complex (e.g., AF-CP-00001)")
    print(f"  {C.GREEN}delete-collection{C.END} Delete a collection and its content")
    print(f"  {C.GREEN}purge-db{C.END}          Nuke DB and Storage completely")

    print(f"\n{C.DIM}Tip: Run 'af3-db <command> --help' for details on a specific database command.{C.END}\n")


def handle_upload(args):
    """Handles the path translation and docker copying logic for uploads."""
    path_arg_idx = -1
    path_str = None

    for i, arg in enumerate(args):
        if i > 0 and not arg.startswith("-"):
            path_str = arg
            path_arg_idx = i
            break

    if not path_str:
        subprocess.run(["docker", "exec", "-it", "af3db_fastapi", "python", "-m", "app.cli"] + args)
        sys.exit(1)

    host_path = Path(path_str).resolve()
    if not host_path.exists():
        print(f"\n{C.RED}{C.BOLD}❌ Error: Path '{host_path}' does not exist on your computer.{C.END}\n")
        sys.exit(1)

    storage_path = None
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        with open(env_file, "r") as f:
            for line in f:
                if line.startswith("STORAGE_PATH="):
                    storage_path = Path(line.strip().split("=", 1)[1]).resolve()
                    break

    requires_docker_cp = True
    container_target = ""


    allowed_native_paths = []
    if storage_path:
        allowed_native_paths.append(str(storage_path))

    for allowed in allowed_native_paths:
        if str(host_path).startswith(allowed):
            requires_docker_cp = False
            if allowed == str(storage_path):
                rel_path = host_path.relative_to(storage_path)
                container_target = f"/app/storage_root/{rel_path}"
            else:
                container_target = str(host_path)
            break

    is_symlink = "--symlink" in args or "-s" in args
    temp_id = None

    if requires_docker_cp:
        temp_id = secrets.token_hex(6)
        container_target = f"/tmp/upload_{temp_id}/{host_path.name}"

        if is_symlink:
            print(
                f"{C.MAGENTA}{C.BOLD}⚠ Warning: Ignoring '--symlink' because the files are being uploaded from outside the database directory.{C.END}")

        print(f"{C.BLUE}{C.BOLD}Copying files into the database container... (This might take a moment){C.END}")
        subprocess.run(["docker", "exec", "af3db_fastapi", "mkdir", "-p", f"/tmp/upload_{temp_id}"])

        cp_result = subprocess.run(["docker", "cp", str(host_path), f"af3db_fastapi:/tmp/upload_{temp_id}/"])
        if cp_result.returncode != 0:
            print(f"{C.RED}{C.BOLD}❌ Failed to copy files into the container.{C.END}")
            sys.exit(1)

    new_argv = []
    for i, arg in enumerate(args):
        if i == path_arg_idx:
            new_argv.append(container_target)
        elif requires_docker_cp and arg in ["--symlink", "-s"]:
            continue
        else:
            new_argv.append(arg)

    try:
        # Streaming the rich progress bars and UI directly from the container to the host
        subprocess.run(["docker", "exec", "-it", "af3db_fastapi", "python", "-m", "app.cli"] + new_argv)
    except KeyboardInterrupt:
        pass
    finally:
        if requires_docker_cp and temp_id:
            print(f"{C.DIM}Cleaning up temporary upload files...{C.END}")
            subprocess.run(["docker", "exec", "af3db_fastapi", "rm", "-rf", f"/tmp/upload_{temp_id}"])


def main():
    """Main execution entry point for the AF3-DB CLI Router."""
    args = sys.argv[1:]

    if not args or args[0] in ["-h", "--help", "help"]:
        show_help()
        sys.exit(0)

    cmd = args[0]

    if cmd in ["start", "stop", "config", "logs"] and any(h in args for h in ["-h", "--help"]):
        if cmd == "start":
            print(f"\n{C.BLUE}{C.BOLD}Usage:{C.END} af3-db start")
            print(f"Starts the AF3-DB PostgreSQL, FastAPI, and Caddy containers in the background.\n")
        elif cmd == "stop":
            print(f"\n{C.BLUE}{C.BOLD}Usage:{C.END} af3-db stop")
            print(f"Gracefully stops all running AF3-DB containers.\n")
        elif cmd == "config":
            print(f"\n{C.BLUE}{C.BOLD}Usage:{C.END} af3-db config")
            print(f"Opens the local .env configuration file in your default terminal text editor.\n")
        elif cmd == "logs":
            print(f"\n{C.BLUE}{C.BOLD}Usage:{C.END} af3-db logs [OPTIONS] [SERVICE...]")
            print(f"View log output from the AF3-DB containers.\n")
            print(f"{C.BOLD}Available Services:{C.END}")
            print(f"  {C.GREEN}web{C.END}         (FastAPI Python backend)")
            print(f"  {C.GREEN}postgres{C.END}    (Database)")
            print(f"  {C.GREEN}caddy{C.END}       (Web Server / Reverse Proxy)\n")
            print(f"{C.DIM}Example: af3-db logs web -n 50 -f{C.END}\n")
            print(f"{C.BOLD}Docker Compose Options:{C.END}")
            subprocess.run(["docker", "compose", "logs", "--help"], cwd=PROJECT_DIR)
        sys.exit(0)

    check_docker()

    if cmd == "start":
        print(f"{C.GREEN}{C.BOLD}Starting AF3-DB Server...{C.END}")
        subprocess.run(["docker", "compose", "up", "-d"], cwd=PROJECT_DIR)
        sys.exit(0)
    elif cmd == "stop":
        print(f"{C.MAGENTA}{C.BOLD}Stopping AF3-DB Server...{C.END}")
        subprocess.run(["docker", "compose", "stop"], cwd=PROJECT_DIR)
        sys.exit(0)
    elif cmd == "config":
        editor = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "nano")
        subprocess.run([editor, str(PROJECT_DIR / ".env")])
        print(f"{C.BLUE}Note: Run 'af3-db start' to apply changes.{C.END}")
        sys.exit(0)
    elif cmd == "logs":
        print(f"{C.BLUE}{C.BOLD}Fetching logs...{C.END}")
        subprocess.run(["docker", "compose", "logs"] + args[1:], cwd=PROJECT_DIR)
        sys.exit(0)

    if not is_container_running():
        print(f"\n{C.RED}{C.BOLD}❌ Command Failed: The database server is currently stopped.{C.END}")
        print(f"Please start it first by running: {C.BLUE}{C.BOLD}af3-db start{C.END}\n")
        sys.exit(1)

    if cmd == "upload-folder":
        handle_upload(args)
    else:
        try:
            subprocess.run(["docker", "exec", "-it", "af3db_fastapi", "python", "-m", "app.cli"] + args)
        except KeyboardInterrupt:
            pass
        sys.exit(0)


if __name__ == "__main__":
    main()