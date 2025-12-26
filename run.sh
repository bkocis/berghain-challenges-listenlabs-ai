#!/bin/bash

# Script to create a game, commit changes with gameID, and play the game
# Usage: ./run.sh <scenario_number>
# Example: ./run.sh 1

set -e  # Exit on error

# Check if scenario parameter is provided
if [ -z "$1" ]; then
    echo "Error: Scenario number is required"
    echo "Usage: $0 <scenario_number>"
    echo "Example: $0 1"
    exit 1
fi

SCENARIO=$1
SCENARIO_DIR="scenario_${SCENARIO}"

# Check if scenario directory exists
if [ ! -d "$SCENARIO_DIR" ]; then
    echo "Error: Scenario directory '$SCENARIO_DIR' does not exist"
    exit 1
fi

# Get the absolute path of the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Running game workflow for scenario $SCENARIO"
echo "=========================================="

# Step 1: Change to scenario directory
echo ""
echo "Step 1: Changing to scenario directory..."
cd "$SCENARIO_DIR"
echo "Current directory: $(pwd)"

# Step 2: Execute create_game.py
echo ""
echo "Step 2: Creating new game..."
python create_game.py

# Step 3: Extract gameID from game_info.json
echo ""
echo "Step 3: Extracting gameID..."
if [ ! -f "game_info.json" ]; then
    echo "Error: game_info.json not found after creating game"
    exit 1
fi

# Extract the most recent gameID from game_info.json
# The file is a JSON object keyed by gameID, so we get the last key
GAME_ID=$(python3 -c "
import json
import sys
try:
    with open('game_info.json', 'r') as f:
        data = json.load(f)
    if isinstance(data, dict):
        # Get the last gameID (most recent)
        game_ids = list(data.keys())
        if game_ids:
            print(game_ids[-1])
        else:
            sys.exit(1)
    else:
        sys.exit(1)
except Exception as e:
    sys.exit(1)
")

if [ -z "$GAME_ID" ]; then
    echo "Error: Could not extract gameID from game_info.json"
    exit 1
fi

echo "Extracted gameID: $GAME_ID"

# Step 4: Add code changes and commit with gameID as message
echo ""
echo "Step 4: Committing changes with gameID..."
cd "$SCRIPT_DIR"  # Go back to root for git operations
git add -A
git commit -m "$GAME_ID" || {
    echo "Warning: Git commit failed (maybe no changes to commit?)"
}

# Step 5: Execute play_game.py
echo ""
echo "Step 5: Playing game..."
cd "$SCENARIO_DIR"
python play_game.py

echo ""
echo "=========================================="
echo "Workflow completed successfully!"
echo "GameID: $GAME_ID"
echo "=========================================="

