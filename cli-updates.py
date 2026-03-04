#!/usr/bin/env python3
"""CLI Update Monitor - Checks for outdated CLI tools and persists status.

Runs via LaunchAgent 3x daily. Writes status to ~/.claude/cli-updates.json
so Claude can warn about needed updates on session start.

Usage:
    cli-updates.py              # Check all configured CLIs
    cli-updates.py --verbose    # Show detailed output
    cli-updates.py --add <cli>  # Add a CLI to monitor (auto-detects or prompts)
    cli-updates.py --list       # List monitored CLIs
"""

import json
import re
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

# Common version output patterns for auto-detection
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)")


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


def extract_semver(text: str) -> Optional[str]:
    """Extract first semver-like version (X.Y.Z) from any text."""
    if not text:
        return None
    match = VERSION_PATTERN.search(text)
    return match.group(1) if match else None


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

    elif parse_type == "auto":
        # Generic: extract first semver from output
        return extract_semver(output)

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
        # Get CLI config: custom > default > auto-detect
        cli_config = config.get("custom", {}).get(cli_name) or DEFAULT_CLIS.get(cli_name)
        if not cli_config:
            # Try auto-detect before skipping
            cli_config = auto_detect_cli(cli_name, verbose)
            if cli_config:
                # Save the detected config for future runs
                if "custom" not in config:
                    config["custom"] = {}
                config["custom"][cli_name] = cli_config
                save_config(config)
                log(f"{cli_name}: auto-detected config saved", verbose)
            else:
                log(f"{cli_name}: no configuration found and auto-detect failed, skipping", verbose)
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


def auto_detect_cli(name: str, verbose: bool = False) -> Optional[dict]:
    """Try to auto-detect how to check a CLI's version and latest release.

    Attempts common patterns:
    1. <name> --version (most CLIs)
    2. <name> -v
    3. <name> version
    Then checks npm and brew for latest version.
    """
    log(f"{name}: attempting auto-detection...", verbose)

    # Step 1: Find a working version command
    version_cmd = None
    version_output = None
    for cmd_variant in [
        [name, "--version"],
        [name, "-v"],
        [name, "version"],
        [name, "-V"],
    ]:
        output = run_cmd(cmd_variant, timeout=10)
        if output and extract_semver(output):
            version_cmd = cmd_variant
            version_output = output
            log(f"{name}: version command found: {' '.join(cmd_variant)} -> {extract_semver(output)}", verbose)
            break

    if not version_cmd:
        log(f"{name}: could not find a working version command", verbose)
        return None

    # Step 2: Find a working latest-version source
    latest_cmd = None
    parse_latest_type = None

    # Try npm first (most JS/Node CLIs)
    npm_output = run_cmd(["npm", "show", name, "version"], timeout=15)
    if npm_output and extract_semver(npm_output):
        latest_cmd = ["npm", "show", name, "version"]
        parse_latest_type = None  # default (plain text)
        log(f"{name}: latest version source: npm -> {npm_output.strip()}", verbose)
    else:
        # Try brew
        brew_output = run_cmd(["brew", "info", name, "--json"], timeout=15)
        if brew_output:
            try:
                data = json.loads(brew_output)
                if isinstance(data, list) and len(data) > 0:
                    stable = data[0].get("versions", {}).get("stable")
                    if stable:
                        latest_cmd = ["brew", "info", name, "--json"]
                        parse_latest_type = "brew_json"
                        log(f"{name}: latest version source: brew -> {stable}", verbose)
            except json.JSONDecodeError:
                pass

    if not latest_cmd:
        # Try pip (Python CLIs)
        pip_output = run_cmd(["pip", "index", "versions", name], timeout=15)
        if pip_output and extract_semver(pip_output):
            latest_cmd = ["pip", "index", "versions", name]
            parse_latest_type = None
            log(f"{name}: latest version source: pip -> {extract_semver(pip_output)}", verbose)

    if not latest_cmd:
        log(f"{name}: could not find a latest version source (tried npm, brew, pip)", verbose)
        return None

    # Step 3: Determine update command
    if latest_cmd[0] == "npm":
        update_cmd = f"npm install -g {name}"
    elif latest_cmd[0] == "brew":
        update_cmd = f"brew upgrade {name}"
    elif latest_cmd[0] == "pip":
        update_cmd = f"pip install --upgrade {name}"
    else:
        update_cmd = f"# update {name} manually"

    config = {
        "version_cmd": version_cmd,
        "latest_cmd": latest_cmd,
        "update_cmd": update_cmd,
        "parse_version": "auto",
        "critical": False,
    }
    if parse_latest_type:
        config["parse_latest"] = parse_latest_type

    log(f"{name}: auto-detection successful", verbose)
    return config


def add_cli(name: str, verbose: bool = False):
    """Add a CLI to the monitor list. Auto-detects config or prompts interactively."""
    config = load_config()

    if name in config.get("clis", []):
        print(f"{name} is already being monitored")
        return

    # Check if it's a built-in default
    if name in DEFAULT_CLIS:
        config["clis"].append(name)
        save_config(config)
        print(f"Added {name} (built-in config)")
        return

    # Try auto-detection
    print(f"Detecting {name}...")
    detected = auto_detect_cli(name, verbose=verbose)

    if detected:
        installed = extract_semver(run_cmd(detected["version_cmd"]) or "")
        latest_raw = run_cmd(detected["latest_cmd"])
        latest = extract_semver(latest_raw) if latest_raw else None

        print(f"\nAuto-detected config for {name}:")
        print(f"  Version command:  {' '.join(detected['version_cmd'])}")
        print(f"  Installed:        {installed or 'unknown'}")
        print(f"  Latest source:    {' '.join(detected['latest_cmd'])}")
        print(f"  Latest version:   {latest or 'unknown'}")
        print(f"  Update command:   {detected['update_cmd']}")
        print(f"  Critical:         {detected['critical']}")

        response = input("\nUse this config? [Y/n/c(ustomize)] ").strip().lower()

        if response in ("", "y", "yes"):
            if "custom" not in config:
                config["custom"] = {}
            config["custom"][name] = detected
            config["clis"].append(name)
            save_config(config)
            print(f"Added {name} with auto-detected config")
            return

        elif response in ("c", "customize"):
            detected = interactive_config(name, detected)
            if detected:
                if "custom" not in config:
                    config["custom"] = {}
                config["custom"][name] = detected
                config["clis"].append(name)
                save_config(config)
                print(f"Added {name} with custom config")
            return

        else:
            print("Cancelled")
            return

    # Auto-detect failed — fall through to interactive
    print(f"Could not auto-detect {name}. Entering interactive setup.\n")
    custom = interactive_config(name)
    if custom:
        if "custom" not in config:
            config["custom"] = {}
        config["custom"][name] = custom
        config["clis"].append(name)
        save_config(config)
        print(f"Added {name} with custom config")


def interactive_config(name: str, defaults: Optional[dict] = None) -> Optional[dict]:
    """Interactively configure a CLI monitor entry."""
    defaults = defaults or {}

    print(f"Configure monitoring for: {name}")
    print("(Press Enter to accept defaults shown in brackets)\n")

    # Version command
    default_vcmd = " ".join(defaults.get("version_cmd", [name, "--version"]))
    vcmd = input(f"  Version command [{default_vcmd}]: ").strip()
    if not vcmd:
        vcmd = default_vcmd
    version_cmd = vcmd.split()

    # Verify it works
    test_output = run_cmd(version_cmd, timeout=10)
    if test_output:
        detected_ver = extract_semver(test_output)
        print(f"    -> Output: {test_output.split(chr(10))[0]}")
        print(f"    -> Detected version: {detected_ver or 'could not parse'}")
    else:
        print(f"    -> WARNING: command failed. Continuing anyway.")

    # Latest version command
    default_lcmd = " ".join(defaults.get("latest_cmd", ["npm", "show", name, "version"]))
    lcmd = input(f"  Latest version command [{default_lcmd}]: ").strip()
    if not lcmd:
        lcmd = default_lcmd
    latest_cmd = lcmd.split()

    # Determine parse_latest type
    parse_latest_type = defaults.get("parse_latest")
    if latest_cmd[0] == "brew" and "--json" in latest_cmd:
        parse_latest_type = "brew_json"

    # Update command
    default_ucmd = defaults.get("update_cmd", f"npm install -g {name}")
    ucmd = input(f"  Update command [{default_ucmd}]: ").strip()
    if not ucmd:
        ucmd = default_ucmd

    # Critical flag
    crit = input(f"  Critical (blocks deploys)? [y/N] ").strip().lower()
    critical = crit in ("y", "yes")

    config = {
        "version_cmd": version_cmd,
        "latest_cmd": latest_cmd,
        "update_cmd": ucmd,
        "parse_version": "auto",
        "critical": critical,
    }
    if parse_latest_type:
        config["parse_latest"] = parse_latest_type

    return config


def list_clis():
    """List all monitored CLIs with source info."""
    config = load_config()
    custom = config.get("custom", {})
    print("Monitored CLIs:")
    for cli in config.get("clis", []):
        if cli in DEFAULT_CLIS:
            source = "built-in"
            critical = " [CRITICAL]" if DEFAULT_CLIS[cli].get("critical") else ""
        elif cli in custom:
            source = "custom"
            critical = " [CRITICAL]" if custom[cli].get("critical") else ""
        else:
            source = "unconfigured"
            critical = ""
        print(f"  - {cli} ({source}){critical}")


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
