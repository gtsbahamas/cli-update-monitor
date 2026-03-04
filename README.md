# CLI Update Monitor

Checks your CLIs for updates 3x daily. Writes status to a JSON file so your AI coding assistant (or any session startup script) can warn you before a stale CLI blocks a deployment.

## The Problem

You're mid-deploy. Everything's ready. `vercel deploy` fails because your CLI is 3 versions behind. Context lost, momentum killed.

## The Fix

A Python script on a macOS LaunchAgent. Runs at 8am, 2pm, 8pm. Checks installed versions against latest. Writes results to `~/.claude/cli-updates.json`.

```
⚠️ CRITICAL: vercel needs update (50.25.6 → 50.26.0)
⚠️ CRITICAL: agent-browser needs update (0.15.1 → 0.16.1)
📦 Updates available: gh, fly
```

Session starts, you see the warning, update in 10 seconds, keep moving.

## Quick Install (macOS)

```bash
git clone https://github.com/tywells/cli-update-monitor.git
cd cli-update-monitor
chmod +x install.sh
./install.sh
```

That's it. The install script:
- Copies the checker to `~/.claude/monitors/`
- Creates the LaunchAgent plist with your home directory
- Loads it immediately
- Runs the first check

## Manual Install

```bash
# 1. Create directory
mkdir -p ~/.claude/monitors

# 2. Copy files
cp cli-updates.py ~/.claude/monitors/
cp cli-config.json ~/.claude/monitors/
chmod +x ~/.claude/monitors/cli-updates.py

# 3. Create LaunchAgent (replace REPLACE_WITH_HOME with your home dir)
sed "s|REPLACE_WITH_HOME|$HOME|g" com.claude.cli-monitor.plist > ~/Library/LaunchAgents/com.claude.cli-monitor.plist

# 4. Load it
launchctl load ~/Library/LaunchAgents/com.claude.cli-monitor.plist

# 5. Test
python3 ~/.claude/monitors/cli-updates.py --verbose
```

## Configuration

Edit `~/.claude/monitors/cli-config.json`:

```json
{
  "clis": ["vercel", "gh", "supabase", "fly"],
  "custom": {}
}
```

**Built-in CLIs:** vercel, gh, supabase, fly, railway, wrangler

**Add a custom CLI:**

```json
{
  "clis": ["vercel", "gh", "my-tool"],
  "custom": {
    "my-tool": {
      "version_cmd": ["my-tool", "--version"],
      "latest_cmd": ["npm", "show", "my-tool", "version"],
      "update_cmd": "npm install -g my-tool",
      "parse_version": "first_line",
      "critical": true
    }
  }
}
```

Or via CLI:
```bash
python3 ~/.claude/monitors/cli-updates.py --add my-tool
```

## Usage

```bash
# Manual check
python3 ~/.claude/monitors/cli-updates.py --verbose

# List monitored CLIs
python3 ~/.claude/monitors/cli-updates.py --list

# Add a CLI
python3 ~/.claude/monitors/cli-updates.py --add <name>
```

## Output

Status is written to `~/.claude/cli-updates.json`:

```json
{
  "checked_at": "2026-03-04T02:00:04Z",
  "clis": {
    "vercel": {
      "installed": "50.25.6",
      "latest": "50.26.0",
      "needs_update": true,
      "critical": true
    }
  },
  "needs_update": ["vercel"],
  "critical_updates": ["vercel"]
}
```

Read this file on session start. If `critical_updates` is non-empty, update before you start working.

## How I Use It

I read `cli-updates.json` at the start of every Claude Code session. Critical CLIs (vercel, supabase) get an immediate warning. Non-critical (gh, fly) are noted but don't block anything.

The script is ~170 lines of Python. No dependencies. LaunchAgent handles scheduling. JSON file is the contract between the monitor and whatever reads it.

## Requirements

- macOS (uses LaunchAgent for scheduling)
- Python 3.8+
- `brew` (for checking gh/supabase/fly latest versions)
- `npm` (for checking vercel/wrangler/railway latest versions)

## Linux / Windows

The Python script works anywhere. You just need a different scheduler:

- **Linux:** cron job — `0 8,14,20 * * * python3 ~/.claude/monitors/cli-updates.py`
- **Windows:** Task Scheduler — point it at the script

## License

MIT
