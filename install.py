import os
import sys
import platform
import secrets
import subprocess
import getpass
import glob
import socket
import shutil
from pathlib import Path


if os.name == 'nt':
    os.system('color')
    os.system('chcp 65001 >nul')
    sys.stdout.reconfigure(encoding='utf-8')

try:
    import readline
except ImportError:
    readline = None


# ANSI Color Codes
class C:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header():
    """
    Prints a clean ASCII Art header for the installer.
    """
    width = 58

    print(f"\n{C.BLUE}╭" + "─" * width + f"╮{C.END}")
    print(f"{C.BLUE}│" + " " * width + f"│{C.END}")

    ascii_lines = [
        "     █████╗ ███████╗██████╗         ██████╗ ██████╗    ",
        "    ██╔══██╗██╔════╝╚════██╗        ██╔══██╗██╔══██╗   ",
        "    ███████║█████╗   █████╔╝  █████╗██║  ██║██████╔╝   ",
        "    ██╔══██║██╔══╝   ╚═══██╗  ╚════╝██║  ██║██╔══██╗   ",
        "    ██║  ██║██║     ██████╔╝        ██████╔╝██████╔╝   ",
        "    ╚═╝  ╚═╝╚═╝     ╚═════╝         ╚═════╝ ╚═════╝    "
    ]

    for line in ascii_lines:
        print(f"{C.BLUE}│{C.BOLD}{line.center(width)}{C.END}{C.BLUE}│{C.END}")

    print(f"{C.BLUE}│" + " " * width + f"│{C.END}")
    print(f"{C.BLUE}│" + "Installation & Configuration Wizard".center(width) + f"│{C.END}")
    print(f"{C.BLUE}╰" + "─" * width + f"╯{C.END}\n")


def print_step(title: str):
    """Prints a visually distinct section header."""
    print(f"\n{C.BLUE} ❖ {C.BOLD}{title}{C.END}")
    print(f"{C.BLUE} └" + "─" * 40 + f"{C.END}")


def check_docker_installed():
    """Checks if Docker is installed and the daemon is running."""
    if not shutil.which("docker"):
        print(f"\n{C.RED}{C.BOLD}[!] Docker is not installed or not found in your system PATH.{C.END}")
        if platform.system() == "Windows":
            print(f"    Please download and install Docker Desktop for Windows:")
            print(f"    https://docs.docker.com/desktop/install/windows-install/\n")
        else:
            print(f"    Please install Docker (e.g., via 'curl -fsSL https://get.docker.com | sudo sh').\n")
        sys.exit(1)

    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except subprocess.CalledProcessError:
        print(f"\n{C.RED}{C.BOLD}[!] Docker is installed, but the Docker Engine is not running.{C.END}")
        if platform.system() == "Windows":
            print(f"    Please start the 'Docker Desktop' application from your start menu.\n")
        else:
            print(f"    Please start the docker service (e.g., 'sudo systemctl start docker').\n")
        sys.exit(1)


def check_existing_containers():
    """Checks for existing AF3-DB containers and aborts if found to prevent password conflicts."""
    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True, text=True
        )
        existing = [name for name in ["af3db_postgres", "af3db_fastapi", "af3db_caddy"] if name in res.stdout]
        if existing:
            print(f"\n{C.RED}{C.BOLD}[!] CRITICAL: Existing AF3-DB containers detected: {', '.join(existing)}{C.END}")
            print(f"    Running the installer over an existing setup causes database password conflicts.")
            print(f"    {C.YELLOW}Please run 'python uninstall.py' first to safely clean up before reinstalling.{C.END}\n")
            sys.exit(1)
    except Exception:
        pass


def get_local_ip() -> str:
    """Attempts to find the local network IP address of the host machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_machine_hostname() -> str:
    """Attempts to fetch the network hostname of the machine."""
    try:
        return socket.gethostname().lower()
    except Exception:
        return ""


def is_port_in_use(port: int) -> bool:
    """Checks if a given port is currently in use on the local machine."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def path_completer(text: str, state: int) -> str:
    """Provides path autocompletion matches for readline."""
    text = os.path.expanduser(text)
    matches = glob.glob(text + '*')
    return (matches + [None])[state]


def prompt(text: str, default: str = "") -> str:
    """Helper to ask a question with a default value."""
    if readline:
        readline.set_completer(None)
    prompt_str = f"   {text} [{default}]: " if default else f"   {text}: "
    val = input(prompt_str).strip()
    return val if val else default


def prompt_choice(text: str, choices: list[str], default: str) -> str:
    """Helper to ask a multiple choice question."""
    if readline:
        readline.set_completer(None)
    options = "/".join(choices)
    while True:
        val = input(f"   {text} [{options}] [{default}]: ").strip().upper()
        if not val:
            return default
        if val in choices:
            return val
        print(f"   {C.RED}[!] Please enter one of: {', '.join(choices)}{C.END}")


def prompt_path(text: str, default: str = "") -> str:
    """Helper to ask for a path with tab autocompletion."""
    if readline:
        readline.set_completer_delims(' \t\n;')
        readline.parse_and_bind("tab: complete")
        readline.set_completer(path_completer)

    prompt_str = f"   {text} [{default}]: " if default else f"   {text}: "
    val = input(prompt_str).strip()
    if readline:
        readline.set_completer(None)
    return os.path.expanduser(val) if val else default


def prompt_bool(text: str, default: bool = True) -> bool:
    """Helper to ask a Yes/No question."""
    if readline:
        readline.set_completer(None)
    options = "[Y/n]" if default else "[y/N]"
    while True:
        val = input(f"   {text} {options}: ").strip().lower()
        if not val:
            return default
        if val in ['y', 'yes']:
            return True
        if val in ['n', 'no']:
            return False
        print("   Please answer 'y' or 'n'.")


def prompt_password(text: str) -> str:
    """Helper to ask for a password twice securely without echoing."""
    if readline:
        readline.set_completer(None)
    while True:
        pass1 = getpass.getpass(f"   {text}: ")
        pass2 = getpass.getpass("   Confirm Password: ")
        if not pass1:
            print(f"   {C.RED}Password cannot be empty. Please try again.{C.END}")
            continue
        if pass1 == pass2:
            return pass1
        print(f"   {C.RED}Passwords do not match. Please try again.{C.END}\n")


def copy_project_files(src_dir: Path, dest_dir: Path):
    """Copies all relevant project files from the current directory to the destination."""
    print(f"   Copying project files to {dest_dir.absolute()}...")
    if not dest_dir.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
    ignore_patterns = {".git", ".env", "venv", "__pycache__", "af3_data", ".idea", ".pytest_cache"}

    def ignore_func(dir_path, contents):
        return [c for c in contents if c in ignore_patterns or c.endswith(".pyc")]

    for item in src_dir.iterdir():
        if item.name in ignore_patterns:
            continue
        target_path = dest_dir / item.name
        if item.is_dir():
            if not target_path.exists():
                shutil.copytree(item, target_path, ignore=ignore_func)
            else:
                for root, dirs, files in os.walk(item):
                    dirs[:] = [d for d in dirs if d not in ignore_patterns]
                    rel_path = Path(root).relative_to(item)
                    target_root = target_path / rel_path
                    target_root.mkdir(parents=True, exist_ok=True)
                    for f in files:
                        if f.endswith(".pyc") or f in ignore_patterns:
                            continue
                        shutil.copy2(Path(root) / f, target_root / f)
        elif item.is_file():
            shutil.copy2(item, target_path)
    print(f"   {C.GREEN}✓ Code successfully copied.{C.END}")


def setup_local_cli(host_script: Path, wrapper_name: str):
    """Sets up the CLI for the current user only on Linux/Mac."""
    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    target_link = local_bin / wrapper_name
    if target_link.exists() or target_link.is_symlink():
        target_link.unlink()
    os.symlink(host_script.absolute(), target_link)
    print(f"   {C.GREEN}✓ CLI linked to {target_link} (Current User only).{C.END}")
    print("     Make sure ~/.local/bin is in your PATH.")


def add_windows_path(target_dir: Path) -> bool:
    """Safely adds a directory to the Windows User PATH via Registry."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
        try:
            current_path, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_path = ""

        target_str = str(target_dir.absolute())

        # Check if already in path
        if target_str.lower() not in current_path.lower():
            new_path = f"{current_path};{target_str}" if current_path else target_str
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
            # Notify Windows about the environment change
            try:
                import ctypes
                HWND_BROADCAST = 0xFFFF
                WM_SETTINGCHANGE = 0x1A
                SMTO_ABORTIFHUNG = 0x0002
                ctypes.windll.user32.SendMessageTimeoutW(
                    HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None
                )
            except Exception:
                pass
            return True
        return False
    except Exception as e:
        print(f"   {C.RED}[!] Failed to update Windows PATH automatically: {e}{C.END}")
        return False


def setup_cli_wrapper(app_dir: Path):
    """Links the static af_router.py script to the global system PATH securely."""
    os_name = platform.system()
    wrapper_name = "af3-db.bat" if os_name == "Windows" else "af3-db"

    print_step("CLI Integration")
    print("   This will create a unified command 'af3-db' to manage the server and database.")
    host_script = app_dir / "af_router.py"
    if not host_script.exists():
        print(f"   {C.RED}[!] Error: Could not find {host_script.name} in the project folder.{C.END}")
        return

    try:
        if os_name == "Windows":
            exec_path = app_dir / wrapper_name
            with open(exec_path, "w") as f:
                f.write(f'@echo off\npython "{host_script.absolute()}" %*\n')

            print(f"   {C.BLUE}Attempting to add {app_dir.absolute()} to your Windows PATH...{C.END}")
            added = add_windows_path(app_dir)
            if added:
                print(f"   {C.GREEN}✓ Successfully added CLI to Windows PATH!{C.END}")
                print(
                    f"   {C.YELLOW}[!] IMPORTANT: You must close and reopen PowerShell/CMD for the 'af3-db' command to work.{C.END}")
            else:
                print(f"   {C.GREEN}✓ CLI path is already in your Windows PATH.{C.END}")
        else:
            os.chmod(host_script, 0o755)
            choice = prompt_choice("Install CLI for [A]ll users (requires sudo) or [M]e only?", ["A", "M"], "A")
            if choice == "A":
                target_link = Path("/usr/local/bin") / wrapper_name
                sudoers_file = Path("/etc/sudoers.d/af3db")
                print(f"\n   {C.BLUE}[i] Setting up passwordless, secure system-wide access for all users.{C.END}")
                print("       This requires temporary sudo privileges. You may be prompted for your password.")
                try:
                    sudoers_content = f"ALL ALL=(root) NOPASSWD: {host_script.absolute()}\n"
                    with open("/tmp/af3db_sudoers", "w") as f:
                        f.write(sudoers_content)
                    subprocess.run(["sudo", "cp", "/tmp/af3db_sudoers", str(sudoers_file)], check=True)
                    subprocess.run(["sudo", "chmod", "0440", str(sudoers_file)], check=True)
                    os.remove("/tmp/af3db_sudoers")

                    wrapper_content = f'#!/bin/bash\nsudo {host_script.absolute()} "$@"\n'
                    with open("/tmp/af3db_wrapper", "w") as f:
                        f.write(wrapper_content)
                    subprocess.run(["sudo", "cp", "/tmp/af3db_wrapper", str(target_link)], check=True)
                    subprocess.run(["sudo", "chmod", "0755", str(target_link)], check=True)
                    os.remove("/tmp/af3db_wrapper")
                except subprocess.CalledProcessError as e:
                    print(f"   {C.RED}[!] Failed to setup global access: {e}{C.END}")
                    print("       Falling back to local installation for your user...")
                    setup_local_cli(host_script, wrapper_name)
            else:
                setup_local_cli(host_script, wrapper_name)
    except Exception as e:
        print(f"   {C.RED}Could not automatically link CLI: {e}{C.END}")


def generate_caddyfile(app_dir: Path, use_https: bool):
    """Generates the Caddyfile based on HTTP/HTTPS preference."""
    caddyfile_path = app_dir / "Caddyfile"
    if use_https:
        content = "{$DOMAIN_NAME:localhost} {\n    reverse_proxy web:8000\n}\n"
    else:
        content = ":{$APP_PORT:80} {\n    reverse_proxy web:8000\n}\n"
    with open(caddyfile_path, "w") as f:
        f.write(content)


def print_success_summary(use_https: bool, port: str, base_dir: Path, app_dir: Path):
    """Prints the final summary with access links and important paths."""
    protocol = "https" if use_https else "http"
    local_ip = get_local_ip()
    hostname = get_machine_hostname()

    print(f"\n   {C.GREEN}" + "═" * 50)
    print("   🎉 DOCKER CONTAINERS STARTED SUCCESSFULLY!")
    print("   " + "═" * 50 + f"{C.END}\n")
    print(f"   {C.BOLD}🌐 HOW TO ACCESS YOUR DATABASE:{C.END}")
    print("   -------------------------------")
    print(f"   Local access:       {C.BLUE}{protocol}://localhost:{port}{C.END}")

    if local_ip != "127.0.0.1":
        print(f"   Network IP:         {C.BLUE}{protocol}://{local_ip}:{port}{C.END}")
    if hostname and hostname != "localhost":
        print(f"   Network Hostname:   {C.BLUE}{protocol}://{hostname}:{port}{C.END}")
        print(f"                       {C.BLUE}{protocol}://{hostname}.local:{port}{C.END}")

    print("\n   (Share the Network links with colleagues in the same network)")

    if use_https:
        print(f"\n   {C.YELLOW}[!] HTTPS Note:{C.END}")
        print("       Because you are using local HTTPS, your browser will show a 'Not Secure'")
        print("       warning. This is normal for local networks. You can safely bypass it.")
    else:
        print(f"\n   {C.YELLOW}[!] HTTP Note:{C.END}")
        print("       You opted for plain HTTP. Ensure you are operating in a trusted network.")

    print(f"\n   {C.BOLD}📁 IMPORTANT LOCATIONS:{C.END}")
    print("   -----------------------")
    print(f"   Base Directory:     {base_dir.absolute()}")
    print(f"   Application Code:   {app_dir.absolute()}")
    print(f"   Config File (.env): {app_dir.absolute()}/.env")

    print(f"\n   {C.BOLD}🛠️  COMMAND LINE INTERFACE:{C.END}")
    print("   --------------------------")
    print("   You can now use the CLI to interact with your server and database.")
    print(f"   Try running: {C.BLUE}af3-db help{C.END}")
    if platform.system() == "Windows":
        print(f"   {C.YELLOW}(Remember to restart your PowerShell/CMD window first!){C.END}")
    print("\n")


def start_docker(use_https: bool, port: str, base_dir: Path, app_dir: Path):
    """Executes docker compose up -d --build from the NEW app directory."""
    print_step("Start Application")
    if prompt_bool("Do you want to build and start the Docker containers now?", True):
        try:
            subprocess.run(["docker", "compose", "up", "-d", "--build"], cwd=app_dir, check=True)
            print_success_summary(use_https, port, base_dir, app_dir)
        except subprocess.CalledProcessError:
            print(f"\n   {C.RED}[!] Docker failed to start. This often happens if old containers{C.END}")
            print(f"   {C.RED}    from a previous installation are still blocking the network or names.{C.END}")
            if prompt_bool("Do you want to safely remove the old AF3-DB containers and retry?", True):
                print(f"   {C.YELLOW}Cleaning up old containers safely...{C.END}")
                subprocess.run(["docker", "rm", "-f", "af3db_postgres", "af3db_fastapi", "af3db_caddy"],
                               capture_output=True)
                print(f"   {C.BLUE}Retrying container build...{C.END}")
                try:
                    subprocess.run(["docker", "compose", "up", "-d", "--build"], cwd=app_dir, check=True)
                    print_success_summary(use_https, port, base_dir, app_dir)
                except subprocess.CalledProcessError as e2:
                    print(f"\n   {C.RED}[!] Docker failed again. Error: {e2}{C.END}")
                    print(
                        f"   You can investigate later by running: docker compose up -d --build in {app_dir.absolute()}")
            else:
                print(
                    f"   You can start it manually later by going to {app_dir.absolute()} and running: docker compose up -d --build")
    else:
        print("\n   You can start the application later by running:")
        print(f"   {C.BLUE}af3-db start{C.END}")


def main():
    """Main installer flow."""
    check_docker_installed()
    check_existing_containers()
    print_header()
    original_source_dir = Path(os.getcwd())

    print_step("Installation Directory")
    print("   Choose a base folder. The application code, database, and storage will be placed inside.")
    default_base = str((Path.home() / "af3_database").absolute())

    while True:
        base_dir_str = prompt_path("Enter base installation path", default_base)
        base_dir = Path(base_dir_str).resolve()

        protected_roots = {Path(p).resolve() for p in
                           ["/", "/opt", "/home", "/usr", "/etc", "/var", "/root", "/bin", "/sbin", "/boot"]}


        if platform.system() == "Windows" and len(base_dir.parts) <= 1:
            print(
                f"\n   {C.RED}{C.BOLD}[!] CRITICAL: You cannot install directly into a drive root like '{base_dir.absolute()}'.{C.END}")
            print("       Please specify a dedicated subfolder, e.g., 'C:\\af3_database'.\n")
            continue

        if platform.system() != "Windows" and (base_dir in protected_roots or len(base_dir.parts) <= 1):
            print(
                f"\n   {C.RED}{C.BOLD}[!] CRITICAL: You cannot install directly into a system root directory like '{base_dir.absolute()}'.{C.END}")
            print(f"       {C.RED}This prevents accidental deletion of system files during wipes.{C.END}")
            print("       Please specify a dedicated subfolder, e.g., '/opt/af3_database'.\n")
            continue

        try:
            is_home = base_dir.is_relative_to(Path.home().parent)
        except AttributeError:
            is_home = str(base_dir).startswith(str(Path.home().parent))

        if is_home and platform.system() != "Windows":
            print(f"\n   {C.YELLOW}[!] WARNING: You selected a path inside a personal home directory.{C.END}")
            print("       If other users need to use the 'af3-db' CLI on this server,")
            print("       Linux permissions will permanently block their access.")
            print("       For a multi-user setup, a global path like '/opt/af3_database' is strongly recommended.")
            if not prompt_bool("Are you sure you want to keep this personal path?", False):
                default_base = "/opt/af3_database"
                print("")
                continue

        if base_dir.exists() and any(base_dir.iterdir()):
            print(
                f"\n   {C.YELLOW}[!] WARNING: The directory '{base_dir.absolute()}' already exists and is not empty.{C.END}")
            looks_like_af3 = (base_dir / "code").exists() or (base_dir / "postgres_data").exists()

            if not looks_like_af3:
                print("       This directory does NOT look like a previous AF3-DB installation.")
                print("       To protect your files, the installer will NOT wipe this folder.")
                print("       Please choose a different, empty path.\n")
                continue

            print("       Installing here can mix old and new files and cause permission conflicts.")
            if prompt_bool("Do you want to completely WIPE the old AF3-DB files in this directory?", False):
                print(f"   {C.YELLOW}Wiping old installation files safely...{C.END}")
                try:
                    code_dir = base_dir / "code"
                    if (code_dir / "docker-compose.yml").exists():
                        subprocess.run(["docker", "compose", "down"], cwd=code_dir, capture_output=True)
                    subprocess.run(["docker", "rm", "-f", "af3db_postgres", "af3db_fastapi", "af3db_caddy"],
                                   capture_output=True)
                    af3_subfolders = ["code", "storage", "postgres_data", "caddy_data", "caddy_config"]
                    for subfolder in af3_subfolders:
                        target = base_dir / subfolder
                        if target.exists():
                            if platform.system() != "Windows":
                                subprocess.run(["sudo", "rm", "-rf", str(target)], check=True)
                            else:
                                shutil.rmtree(target, ignore_errors=True)
                except subprocess.CalledProcessError as e:
                    print(f"   {C.RED}[!] Permission denied or error during targeted wipe: {e}{C.END}")
                    sys.exit(1)
            else:
                print("   Please choose a different, empty path.\n")
                continue
        break

    app_code_dir = base_dir / "code"
    storage_path = base_dir / "storage"
    postgres_path = base_dir / "postgres_data"
    caddy_data_path = base_dir / "caddy_data"
    caddy_config_path = base_dir / "caddy_config"

    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        if platform.system() != "Windows":
            print(f"\n   {C.BLUE}[i] Permission denied for {base_dir.absolute()}.{C.END}")
            print("       Requesting temporary administrator (sudo) privileges to create the folder...")
            try:
                subprocess.run(["sudo", "mkdir", "-p", str(base_dir.absolute())], check=True)
                current_user = os.getuid()
                current_group = os.getgid()
                subprocess.run(["sudo", "chown", "-R", f"{current_user}:{current_group}", str(base_dir.absolute())],
                               check=True)
                print(f"   {C.GREEN}✓ Folder created and permissions granted to your user.{C.END}")
            except subprocess.CalledProcessError:
                print(f"   {C.RED}[!] Failed to create directory using sudo. Aborting.{C.END}")
                sys.exit(1)
        else:
            print(
                f"   {C.RED}[!] Permission denied to create {base_dir.absolute()}. Please run as Administrator.{C.END}")
            sys.exit(1)

    for p in [app_code_dir, storage_path, postgres_path, caddy_data_path, caddy_config_path]:
        p.mkdir(parents=True, exist_ok=True)

    print_step("Deploying Codebase")
    if original_source_dir.absolute() != app_code_dir.absolute():
        copy_project_files(original_source_dir, app_code_dir)
    else:
        print("   Source and destination are the same. Skipping copy.")

    # Ensure static files exist in storage for downloads
    static_storage = storage_path / "static"
    static_storage.mkdir(parents=True, exist_ok=True)
    source_terms = original_source_dir / "app/static/AF3-TERMS_OF_USE.md"
    if source_terms.exists():
        shutil.copy2(source_terms, static_storage / "TERMS_OF_USE.md")


    print_step("Network & Protocol")
    print("   If you enable HTTPS locally, your browser will show a 'Not Secure' warning.")
    print("   This is completely normal. Selecting 'no' (Plain HTTP) avoids the warning,")
    print("   but should ONLY be used in safe, trusted local networks.")
    use_https = prompt_bool("Enable HTTPS (SSL)?", False)
    domain = "localhost"
    default_port = "443" if use_https else "3000"

    while True:
        port_str = prompt("Enter port for the Web Interface", default_port)
        try:
            port = int(port_str)
            if is_port_in_use(port):
                print(f"   {C.RED}[!] Port {port} is currently in use by another application.{C.END}")
                print("       Please choose a different port.")
            else:
                break
        except ValueError:
            print(f"   {C.RED}[!] Please enter a valid number.{C.END}")

    generate_caddyfile(app_code_dir, use_https)
    autostart = prompt_bool("Start AF3-DB automatically when the server boots?", True)
    restart_policy = "unless-stopped" if autostart else "no"

    print_step("Public 'About' Page Details")
    hoster_name = prompt("Hoster Name / Institution", "Local Administrator")
    hoster_email = prompt("Contact Email", "")
    hoster_desc = prompt("Short Description of this instance", "")

    print_step("Security")
    admin_user = prompt("Enter Admin Username", "admin")
    admin_pass = prompt_password("Enter Admin Password")
    db_pass = secrets.token_urlsafe(24)

    env_content = f"""# Auto-generated by AF3-DB Installer
# System
INSTALL_DIR={base_dir.absolute()}
STORAGE_PATH={storage_path.absolute()}
DOMAIN_NAME={domain}
APP_PORT={port}
RESTART_POLICY={restart_policy}

# Security
ADMIN_USERNAME={admin_user}
ADMIN_PASSWORD={admin_pass}

# Biophysical Thresholds (in Angstroms)
# Max heavy-atom distance for Hydrogen Bonds (default: 3.5)
THRESHOLD_H_BOND=3.5
# Max distance for Salt Bridges (default: 4.0)
THRESHOLD_SALT_BRIDGE=4.0
# Max distance to define an Interface Residue (default: 4.0)
THRESHOLD_INTERFACE=4.0

# Database (Please do not change this manually)
POSTGRES_DB=af3db
POSTGRES_USER=af3db
POSTGRES_PASSWORD={db_pass}

# Public Info
HOSTER_NAME={hoster_name}
HOSTER_EMAIL={hoster_email}
HOSTER_DESCRIPTION={hoster_desc}
"""
    env_path = app_code_dir / ".env"
    with open(env_path, "w") as f:
        f.write(env_content)

    setup_cli_wrapper(app_code_dir)

    print(f"\n   {C.GREEN}" + "═" * 50)
    print("   ✓ Configuration and deployment saved successfully!")
    print("   " + "═" * 50 + f"{C.END}")

    start_docker(use_https, str(port), base_dir, app_code_dir)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n   {C.RED}[!] Installation aborted by user.{C.END}")
        sys.exit(1)