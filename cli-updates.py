#!/usr/bin/env python3
"""CLI Update Monitor - Checks for outdated CLI tools and persists status.

Runs via LaunchAgent 3x daily. Writes status to ~/.claude/cli-updates.json
so Claude can warn about needed updates on session start.

Usage:
    cli-updates.py              # Check all configured CLIs
    cli-updates.py --verbose    # Show detailed output
    cli-updates.py --add <cli>  # Add a CLI to monitor
    cli-updates.py --list       # List monitored CLIs
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CONFIG_FILE = Path.home() / ".claude" / "monitors" / "cli-config.json"
STATUS_FILE = Path.home() / ".claude" / "cli-updates.json"
LOG_FILE = Path.home() / ".claude" / "monitors" / "cli-updates.log"

# Default CLIs to monitor with their check commands
DEFAULT_CLIS = {
    "vercel": {
        "version_cmd": ["vercel", "--version"],
        "latest_cmd": ["npm", "show", "vercel", "version"],
        "update_cmd": "npm install -g vercel",
        "parse_version": "first_line",  # Just take first line
        "critical": True,  # Blocks deployments
    },
    "gh": {
        "version_cmd": ["gh", "--version"],
        "latest_cmd": ["brew", "info", "gh", "--json"],
        "update_cmd": "brew upgrade gh",
        "parse_version": "gh_version",  # Parse "gh version X.Y.Z"
        "parse_latest": "brew_json",
        "critical": False,
    },
    "supabase": {
        "version_cmd": ["supabase", "--version"],
        "latest_cmd": ["brew", "info", "supabase", "--json"],
        "update_cmd": "brew upgrade supabase",
        "parse_version": "first_line",
        "parse_latest": "brew_json",
        "critical": True,
    },
    "fly": {
        "version_cmd": ["fly", "version"],
        "latest_cmd": ["brew", "info", "flyctl", "--json"],
        "update_cmd": "brew upgrade flyctl",
        "parse_version": "fly_version",  # Parse "fly v0.1.234"
        "parse_latest": "brew_json",
        "critical": False,
    },
    "railway": {
        "version_cmd": ["railway", "--version"],
        "latest_cmd": ["npm", "show", "@railway/cli", "version"],
        "update_cmd": "npm install -g @railway/cli",
        "parse_version": "railway_version",
        "critical": False,
    },
    "wrangler": {
        "version_cmd": ["wrangler", "--version"],
        "latest_cmd": ["npm", "show", "wrangler", "version"],
        "update_cmd": "npm install -g wrangler",
        "parse_version": "wrangler_version",
        "critical": False,
    },
}


def log(msg: str, verbose: bool = False):
    """Log to file and optionally stdout."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    if verbose:
        print(line)


def run_cmd(cmd: list[str], timeout: int = 30) -> Optional[str]:
    """Run a command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None


def parse_version(output: str, parse_type: str) -> Optional[str]:
    """Extract version number from command output."""
    if not output:
        return None

    if parse_type == "first_line":
        # Just return first line, strip common prefixes
        line = output.split("\n")[0].strip()
        # Remove common prefixes like "v" or "Vercel CLI"
        for prefix in ["Vercel CLI ", "v", "supabase version "]:
            if line.lower().startswith(prefix.lower()):
                line = line[len(prefix):]
        return line.strip()

    elif parse_type == "gh_version":
        # "gh version 2.40.1 (2024-01-01)"
        parts = output.split()
        if len(parts) >= 3:
            return parts[2]
        return None

    elif parse_type == "fly_version":
        # "fly v0.1.234 ..." or "flyctl v0.1.234"
        parts = output.split()
        for part in parts:
            if part.startswith("v"):
                return part[1:]
        return None

    elif parse_type == "railway_version":
        # "Railway CLI 3.5.0"
        parts = output.split()
        if len(parts) >= 3:
            return parts[-1]
        return None

    elif parse_type == "wrangler_version":
        # "wrangler 3.22.1" or similar
        parts = output.split()
        for part in parts:
            if part[0].isdigit():
                return part
        return None

    elif parse_type == "agent_browser_version":
        # "agent-browser 0.6.0" or similar
        parts = output.split()
        for part in parts:
            if part and part[0].isdigit():
                return part
        return None

    return output.strip()


def parse_latest(output: str, parse_type: Optional[str]) -> Optional[str]:
    """Extract latest version from various sources."""
    if not output:
        return None

    if parse_type == "brew_json":
        try:
            data = json.loads(output)
            if isinstance(data, list) and len(data) > 0:
                versions = data[0].get("versions", {})
                return versions.get("stable")
        except json.JSONDecodeError:
            return None

    # Default: just return trimmed output (npm show returns plain version)
    return output.strip()


def load_config() -> dict:
    """Load CLI configuration, creating default if needed."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            pass

    # Create default config
    config = {"clis": list(DEFAULT_CLIS.keys()), "custom": {}}
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    return config


def save_config(config: dict):
    """Save CLI configuration."""
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def check_cli(name: str, cli_config: dict, verbose: bool = False) -> dict:
    """Check a single CLI for updates."""
    result = {
        "name": name,
        "installed": None,
        "latest": None,
        "needs_update": False,
        "update_cmd": cli_config.get("update_cmd", ""),
        "critical": cli_config.get("critical", False),
        "error": None,
    }

    # Get installed version
    version_output = run_cmd(cli_config["version_cmd"])
    if version_output is None:
        result["error"] = "not_installed"
        log(f"{name}: not installed or not in PATH", verbose)
        return result

    installed = parse_version(version_output, cli_config.get("parse_version", "first_line"))
    result["installed"] = installed
    log(f"{name}: installed version {installed}", verbose)

    # Get latest version
    latest_output = run_cmd(cli_config["latest_cmd"])
    if latest_output is None:
        result["error"] = "cannot_check_latest"
        log(f"{name}: could not check latest version", verbose)
        return result

    latest = parse_latest(latest_output, cli_config.get("parse_latest"))
    result["latest"] = latest
    log(f"{name}: latest version {latest}", verbose)

    # Compare versions (simple string comparison - works for semver)
    if installed and latest and installed != latest:
        # Try to do a smarter comparison
        try:
            inst_parts = [int(x) for x in installed.split(".")[:3]]
            lat_parts = [int(x) for x in latest.split(".")[:3]]
            result["needs_update"] = inst_parts < lat_parts
        except (ValueError, IndexError):
            # Fall back to string comparison
            result["needs_update"] = installed != latest

    if result["needs_update"]:
        log(f"{name}: UPDATE AVAILABLE {installed} -> {latest}", verbose)

    return result


def check_all(verbose: bool = False) -> dict:
    """Check all configured CLIs and save status."""
    config = load_config()
    results = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "clis": {},
        "needs_update": [],
        "critical_updates": [],
    }

    for cli_name in config.get("clis", []):
        # Get CLI config (from defaults or custom)
        cli_config = config.get("custom", {}).get(cli_name) or DEFAULT_CLIS.get(cli_name)
        if not cli_config:
            log(f"{cli_name}: no configuration found, skipping", verbose)
            continue

        status = check_cli(cli_name, cli_config, verbose)
        results["clis"][cli_name] = status

        if status["needs_update"]:
            results["needs_update"].append(cli_name)
            if status["critical"]:
                results["critical_updates"].append(cli_name)

    # Save results
    STATUS_FILE.write_text(json.dumps(results, indent=2))
    log(f"Status saved to {STATUS_FILE}", verbose)

    # Summary
    if results["critical_updates"]:
        log(f"CRITICAL UPDATES NEEDED: {', '.join(results['critical_updates'])}", verbose)
    elif results["needs_update"]:
        log(f"Updates available: {', '.join(results['needs_update'])}", verbose)
    else:
        log("All CLIs up to date", verbose)

    return results


def add_cli(name: str, verbose: bool = False):
    """Add a CLI to the monitor list."""
    config = load_config()
    if name not in config["clis"]:
        config["clis"].append(name)
        save_config(config)
        log(f"Added {name} to monitor list", verbose)
        print(f"Added {name} to monitor list")
    else:
        print(f"{name} is already being monitored")


def list_clis():
    """List all monitored CLIs."""
    config = load_config()
    print("Monitored CLIs:")
    for cli in config.get("clis", []):
        info = DEFAULT_CLIS.get(cli, {})
        critical = " [CRITICAL]" if info.get("critical") else ""
        print(f"  - {cli}{critical}")


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args

    if "--add" in args:
        idx = args.index("--add")
        if idx + 1 < len(args):
            add_cli(args[idx + 1], verbose)
        else:
            print("Usage: cli-updates.py --add <cli-name>")
        return

    if "--list" in args:
        list_clis()
        return

    # Default: check all CLIs
    results = check_all(verbose)

    # Print summary to stdout
    if results["critical_updates"]:
        print(f"CRITICAL: {len(results['critical_updates'])} CLI(s) need updates: {', '.join(results['critical_updates'])}")
        sys.exit(1)
    elif results["needs_update"]:
        print(f"Updates available for: {', '.join(results['needs_update'])}")
    else:
        print("All CLIs up to date")


if __name__ == "__main__":
    main()
