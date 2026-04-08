import os
import sys
import shutil
import subprocess
import platform
from pathlib import Path


if os.name == 'nt':
    os.system('color')
    os.system('chcp 65001 >nul')
    sys.stdout.reconfigure(encoding='utf-8')


class C:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header():
    """Prints a clean ASCII Art header for the uninstaller."""
    width = 58

    print(f"\n{C.RED}╭" + "─" * width + f"╮{C.END}")
    print(f"{C.RED}│" + " " * width + f"│{C.END}")
    print(f"{C.RED}│{C.BOLD}" + "AF3-DB Uninstaller".center(width) + f"{C.END}{C.RED}│{C.END}")
    print(f"{C.RED}╰" + "─" * width + f"╯{C.END}\n")


def prompt_bool(text: str, default: bool = False) -> bool:
    """Prompts the user for a boolean yes/no answer."""
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


def prompt_path(text: str, default: str) -> Path:
    """Prompts the user for a directory path."""
    prompt_str = f"   {text} [{default}]: "
    val = input(prompt_str).strip()
    return Path(os.path.expanduser(val)).resolve() if val else Path(default).resolve()


def remove_containers(base_dir: Path):
    """Stops and removes AF3-DB Docker containers."""
    print(f"\n{C.BLUE} ❖ {C.BOLD}Removing Docker Containers{C.END}")
    print(f"{C.BLUE} └" + "─" * 40 + f"{C.END}")

    code_dir = base_dir / "code"
    if (code_dir / "docker-compose.yml").exists():
        print(f"   Stopping containers gracefully via compose...")
        subprocess.run(["docker", "compose", "down", "-v"], cwd=code_dir, capture_output=True)

    print(f"   Ensuring containers are completely removed...")
    subprocess.run(["docker", "rm", "-f", "af3db_postgres", "af3db_fastapi", "af3db_caddy"], capture_output=True)
    print(f"   {C.GREEN}✓ Containers removed.{C.END}")


def remove_windows_path(target_dir: Path) -> bool:
    """Safely removes a directory from the Windows User PATH via Registry."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
        try:
            current_path, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return False

        target_str = str(target_dir.absolute()).lower()
        path_parts = current_path.split(';')

        new_parts = [p for p in path_parts if p and p.lower() != target_str]
        new_path = ';'.join(new_parts)

        if len(new_parts) != len([p for p in path_parts if p]):
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


def remove_cli(app_dir: Path):
    """Removes the AF3-DB CLI wrappers from system paths and registry."""
    print(f"\n{C.BLUE} ❖ {C.BOLD}Removing CLI Commands{C.END}")
    print(f"{C.BLUE} └" + "─" * 40 + f"{C.END}")

    wrapper_name = "af3-db.bat" if platform.system() == "Windows" else "af3-db"

    if platform.system() == "Windows":
        if remove_windows_path(app_dir):
            print(f"   {C.GREEN}✓ Removed CLI path from Windows PATH registry.{C.END}")
        else:
            print(f"   {C.YELLOW}i CLI path was not found in Windows PATH registry.{C.END}")
    else:
        local_bin = Path.home() / ".local" / "bin" / wrapper_name
        if local_bin.exists() or local_bin.is_symlink():
            local_bin.unlink()
            print(f"   {C.GREEN}✓ Removed local CLI wrapper ({local_bin}).{C.END}")

        global_bin = Path("/usr/local/bin") / wrapper_name
        sudoers_file = Path("/etc/sudoers.d/af3db")

        if global_bin.exists() or sudoers_file.exists():
            print(f"   {C.YELLOW}Found global CLI. Requesting sudo to remove...{C.END}")
            try:
                if global_bin.exists():
                    subprocess.run(["sudo", "rm", "-f", str(global_bin)], check=True)
                if sudoers_file.exists():
                    subprocess.run(["sudo", "rm", "-f", str(sudoers_file)], check=True)
                print(f"   {C.GREEN}✓ Removed global CLI wrapper.{C.END}")
            except subprocess.CalledProcessError:
                print(f"   {C.RED}✗ Could not remove global CLI. Please delete manually.{C.END}")


def remove_directory(target: Path):
    """Removes a directory securely across OS platforms."""
    if not target.exists():
        return
    try:
        shutil.rmtree(target)
        print(f"   {C.GREEN}✓ Removed {target.name}{C.END}")
    except PermissionError:
        if platform.system() != "Windows":
            print(f"   {C.YELLOW}Permission denied for {target.name}. Requesting sudo...{C.END}")
            try:
                subprocess.run(["sudo", "rm", "-rf", str(target)], check=True)
                print(f"   {C.GREEN}✓ Removed {target.name} via sudo{C.END}")
            except subprocess.CalledProcessError:
                print(f"   {C.RED}✗ Failed to remove {target.name}.{C.END}")
        else:
            print(f"   {C.RED}✗ Could not remove {target.name}. Please delete manually as Administrator.{C.END}")
    except Exception as e:
        print(f"   {C.RED}✗ Error removing {target.name}: {e}{C.END}")


def main():
    """Main execution flow for the uninstall process."""
    print_header()

    default_base = str((Path.home() / "af3_database").absolute())
    if platform.system() == "Windows" and Path("C:\\af3_database").exists():
        default_base = "C:\\af3_database"

    base_dir = prompt_path("Enter the base installation path you want to remove", default_base)

    if not base_dir.exists():
        print(f"\n   {C.RED}Directory {base_dir} does not exist. Aborting.{C.END}")
        sys.exit(1)

    is_named_correctly = "af3" in base_dir.name.lower()
    has_code = (base_dir / "code").exists()
    has_postgres = (base_dir / "postgres_data").exists()
    has_storage = (base_dir / "storage").exists()

    looks_like_af3 = is_named_correctly and has_code and has_postgres and has_storage

    if not looks_like_af3:
        print(f"\n   {C.RED}SAFETY ABORT: {base_dir} does not look like an AF3-DB installation.{C.END}")
        print(f"   {C.YELLOW}To prevent accidental deletion of important files, the uninstaller requires:{C.END}")
        print(f"   - The base folder name must contain 'af3'")
        print(f"   - It must contain the subfolders: 'code', 'postgres_data', and 'storage'")
        sys.exit(1)

    print(f"\n   {C.YELLOW}{C.BOLD}WARNING: You are about to uninstall AF3-DB from {base_dir}{C.END}")

    if not prompt_bool("Do you want to proceed?", False):
        print(f"   {C.GREEN}Uninstallation aborted.{C.END}")
        sys.exit(0)

    app_code_dir = base_dir / "code"

    remove_containers(base_dir)
    remove_cli(app_code_dir)

    print(f"\n{C.BLUE} ❖ {C.BOLD}Removing Application Files{C.END}")
    print(f"{C.BLUE} └" + "─" * 40 + f"{C.END}")

    remove_directory(base_dir / "code")
    remove_directory(base_dir / "caddy_data")
    remove_directory(base_dir / "caddy_config")

    print(f"\n{C.RED} ❖ {C.BOLD}DANGER ZONE: User Data{C.END}")
    print(f"{C.RED} └" + "─" * 40 + f"{C.END}")
    print(f"   This includes your PostgreSQL database and all uploaded AF3 storage files.")

    if prompt_bool(f"{C.RED}{C.BOLD}Do you want to PERMANENTLY DELETE all your database entries and files?{C.END}",
                   False):
        remove_directory(base_dir / "postgres_data")
        remove_directory(base_dir / "storage")
        print(f"   {C.GREEN}✓ User data deleted.{C.END}")

        if not any(base_dir.iterdir()):
            try:
                base_dir.rmdir()
                print(f"   {C.GREEN}✓ Removed empty base directory {base_dir.name}.{C.END}")
            except Exception:
                pass
    else:
        print(f"   {C.YELLOW}i User data (postgres_data, storage) was kept safely in {base_dir}{C.END}")

    print(f"\n   {C.GREEN}{C.BOLD}✓ Uninstallation complete.{C.END}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n   {C.RED}[!] Uninstallation aborted by user.{C.END}")
        sys.exit(1)