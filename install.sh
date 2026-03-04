#!/bin/bash
# CLI Update Monitor - Quick Install
# Works on macOS with LaunchAgent scheduling

set -e

HOME_DIR="$HOME"
MONITOR_DIR="$HOME_DIR/.claude/monitors"
PLIST_DIR="$HOME_DIR/Library/LaunchAgents"
PLIST_NAME="com.claude.cli-monitor.plist"
STATUS_FILE="$HOME_DIR/.claude/cli-updates.json"

echo "Installing CLI Update Monitor..."

# Create directories
mkdir -p "$MONITOR_DIR"
mkdir -p "$HOME_DIR/.claude"

# Copy files
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/cli-updates.py" "$MONITOR_DIR/cli-updates.py"
chmod +x "$MONITOR_DIR/cli-updates.py"

# Only copy config if it doesn't exist (don't overwrite user customizations)
if [ ! -f "$MONITOR_DIR/cli-config.json" ]; then
    cp "$SCRIPT_DIR/cli-config.json" "$MONITOR_DIR/cli-config.json"
    echo "Created default config at $MONITOR_DIR/cli-config.json"
else
    echo "Config already exists, skipping (edit manually if needed)"
fi

# Create plist with correct home directory
sed "s|REPLACE_WITH_HOME|$HOME_DIR|g" "$SCRIPT_DIR/com.claude.cli-monitor.plist" > "$PLIST_DIR/$PLIST_NAME"

# Unload if already loaded (ignore errors)
launchctl unload "$PLIST_DIR/$PLIST_NAME" 2>/dev/null || true

# Load the LaunchAgent
launchctl load "$PLIST_DIR/$PLIST_NAME"

echo ""
echo "Installed:"
echo "  Script:  $MONITOR_DIR/cli-updates.py"
echo "  Config:  $MONITOR_DIR/cli-config.json"
echo "  Plist:   $PLIST_DIR/$PLIST_NAME"
echo "  Status:  $STATUS_FILE"
echo ""
echo "Running first check..."
python3 "$MONITOR_DIR/cli-updates.py" --verbose
echo ""
echo "Done. Monitor will run at 8am, 2pm, and 8pm daily."
echo "Edit $MONITOR_DIR/cli-config.json to add/remove CLIs."
