#!/usr/bin/env python3
"""
Script to play the Berghain Challenge game by making accept/reject decisions
for each person until the venue is full (1000 people) or 20,000 rejections occur.
"""

import requests
import json
import sys
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

# Configuration
BASE_URL = "https://berghain.challenges.listenlabs.ai"
MAX_VENUE_CAPACITY = 1000
MAX_REJECTIONS = 20000


def decide_and_next(
    base_url: str,
    game_id: str,
    person_index: int,
    accept: Optional[bool] = None
) -> Dict[str, Any]:
    """
    Make a decision for a person and get the next person.
    
    Args:
        base_url: Base URL of the API
        game_id: UUID of the game
        person_index: Index of the current person (0 for first person)
        accept: True to accept, False to reject. Optional for first person (personIndex=0)
    
    Returns:
        Dictionary containing the API response with status, counts, and next person
    """
    endpoint = f"{base_url}/decide-and-next"
    params = {
        "gameId": game_id,
        "personIndex": person_index
    }
    
    # For personIndex > 0, accept parameter is required
    if person_index > 0:
        if accept is None:
            raise ValueError(f"accept parameter is required for personIndex > 0")
        params["accept"] = str(accept).lower()
    elif accept is not None:
        # Optional for personIndex=0, but can be included
        params["accept"] = str(accept).lower()
    
    try:
        response = requests.post(endpoint, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error making request: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")
        raise


def load_game_info(game_id: Optional[str] = None, filename: str = "game_info.json") -> Optional[Dict[str, Any]]:
    """
    Load game information from a JSON file.
    If game_id is provided, returns that specific game's info.
    Otherwise, returns the first game found (for backward compatibility).
    
    Supports both old format (single game object) and new format (dict keyed by gameId).
    
    Args:
        game_id: Optional gameId to load specific game
        filename: Path to the game info JSON file
    
    Returns:
        Dictionary containing game information or None if file doesn't exist
    """
    try:
        with open(filename, 'r') as f:
            all_games = json.load(f)
            
        # Check if it's the old format (single game object with "gameId" as a field)
        # Old format: {"gameId": "uuid", "playerId": "...", ...}
        # New format: {"uuid": {"gameId": "uuid", ...}, ...}
        if isinstance(all_games, dict):
            # If it has "gameId" as a key and the value is a string (not a dict), it's old format
            if "gameId" in all_games and isinstance(all_games["gameId"], str):
                # Old format - return it directly (or check if game_id matches)
                if game_id is None or all_games.get("gameId") == game_id:
                    return all_games
                return None
            
            # New format: dict keyed by gameId
            if game_id:
                return all_games.get(game_id)
            # Return first game if no game_id specified (backward compatibility)
            if all_games:
                return next(iter(all_games.values()))
        
        return None
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON file: {e}")
        return None


def should_accept_person(
    person_attributes: Dict[str, bool],
    constraints: list,
    admitted_count: int,
    attribute_counts: Dict[str, int],
    total_admitted: int,
    attribute_statistics: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Decision logic: determine whether to accept or reject a person.
    
    Strategy:
    - Young AND well_dressed: Always accept (preferred type)
    - Not young AND not well_dressed: Reject
    - Not young BUT well_dressed: Accept early, more selective later
    - Young (regardless of well_dressed): Accept more liberally, especially as game progresses
    
    Args:
        person_attributes: Dictionary of attribute IDs to boolean values
        constraints: List of constraint dictionaries with 'attribute' and 'minCount'
        admitted_count: Current number of admitted people
        attribute_counts: Dictionary tracking count of each attribute in admitted people
        total_admitted: Total number of people admitted so far
        attribute_statistics: Optional dictionary with 'relativeFrequencies' and 'correlations'
    
    Returns:
        True to accept, False to reject
    """
    # If venue is full, reject
    if admitted_count >= MAX_VENUE_CAPACITY:
        return False
    
    # Extract attribute values
    is_young = person_attributes.get("young", False)
    is_well_dressed = person_attributes.get("well_dressed", False)
    
    # Get constraint requirements
    young_min = 600
    well_dressed_min = 600
    for constraint in constraints:
        if constraint['attribute'] == 'young':
            young_min = constraint['minCount']
        elif constraint['attribute'] == 'well_dressed':
            well_dressed_min = constraint['minCount']
    
    # Get current counts
    young_count = attribute_counts.get("young", 0)
    well_dressed_count = attribute_counts.get("well_dressed", 0)
    
    # Calculate progress towards constraints
    young_progress = young_count / young_min if young_min > 0 else 1.0
    well_dressed_progress = well_dressed_count / well_dressed_min if well_dressed_min > 0 else 1.0
    
    # Calculate how full the venue is (0.0 to 1.0)
    venue_fill_ratio = admitted_count / MAX_VENUE_CAPACITY
    
    # Strategy 1: Young AND well_dressed - Always accept (preferred)
    if is_young and is_well_dressed:
        return True
    
    # Strategy 2: Not young AND not well_dressed - Reject
    if not is_young and not is_well_dressed:
        return False
    
    # Strategy 3: Not young BUT well_dressed
    if not is_young and is_well_dressed:
        # If we already have enough well_dressed people, reject
        if well_dressed_count >= well_dressed_min:
            return False
        
        # Early game: accept if we need well_dressed people
        if venue_fill_ratio < 0.6:
            return True
        
        # Mid game: be more selective, only accept if significantly below requirement
        if venue_fill_ratio < 0.8:
            # Accept if we're below 90% of requirement
            if well_dressed_progress < 0.9:
                return True
            return False
        
        # Late game: very selective, only accept if we're critically below requirement
        # Since we don't know how many people are coming, be conservative
        if well_dressed_progress < 0.85:
            return True
        return False
    
    # Strategy 4: Young (regardless of well_dressed status)
    # Be kinder as the game progresses - accept young people more liberally
    if is_young:
        # If we already have enough young people, still accept if venue is not too full
        # (be kinder as game progresses)
        if young_count >= young_min:
            # Early to mid game: still accept some young people even if we have enough
            if venue_fill_ratio < 0.7:
                return True
            # Late game: only reject if venue is very full
            if venue_fill_ratio < 0.9:
                return True
            return False
        
        # Early game: accept young people liberally
        if venue_fill_ratio < 0.4:
            return True
        
        # Mid game: accept more liberally as game progresses
        if venue_fill_ratio < 0.7:
            # Accept if we're below requirement, or even slightly above
            if young_progress < 1.1:
                return True
            return False
        
        # Late game: be even kinder - accept young people unless we're way over requirement
        if venue_fill_ratio < 0.9:
            # Accept if we're not way over requirement (up to 120% of requirement)
            if young_progress < 1.2:
                return True
            return False
        
        # Very late game: still accept young people unless we're significantly over
        if young_progress < 1.3:
            return True
        return False
    
    # Default: reject (shouldn't reach here with the above logic)
    return False


def play_game(
    game_id: str,
    constraints: Optional[list] = None,
    decision_strategy=None,
    attribute_statistics: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Play the game by iterating through all people and making decisions.
    
    Args:
        game_id: UUID of the game
        constraints: List of constraints (optional, for decision strategy)
        decision_strategy: Optional function to use for decision making
        attribute_statistics: Optional dictionary with 'relativeFrequencies' and 'correlations'
    
    Returns:
        Dictionary containing final game results
    """
    if decision_strategy is None:
        decision_strategy = should_accept_person
    
    if constraints is None:
        constraints = []
    
    # Load attribute statistics from game_info.json if not provided
    if attribute_statistics is None:
        game_info = load_game_info()
        if game_info:
            attribute_statistics = game_info.get("attributeStatistics")
    
    # Track game state
    person_index = 0
    admitted_count = 0
    rejected_count = 0
    attribute_counts = {}  # Track how many of each attribute we have
    status = "running"
    
    # Track all decisions: person attributes and accept/reject decisions
    decision_history = []
    
    print(f"Starting game: {game_id}")
    print(f"Constraints: {constraints}")
    if attribute_statistics:
        print(f"Attribute statistics loaded: {list(attribute_statistics.get('relativeFrequencies', {}).keys())}")
    print("-" * 60)
    
    # Get first person (personIndex=0, no decision yet)
    response = decide_and_next(BASE_URL, game_id, person_index)
    status = response.get("status", "running")
    
    # Store initial response for reference
    initial_response = response.copy()
    
    while status == "running":
        # Extract current person info
        if response.get("nextPerson") is None:
            status = response.get("status", "unknown")
            break
        
        current_person = response["nextPerson"]
        person_index = current_person["personIndex"]
        attributes = current_person["attributes"]
        
        # Update counts from response (from previous decision)
        admitted_count = response.get("admittedCount", admitted_count)
        rejected_count = response.get("rejectedCount", rejected_count)
        
        # Make decision for current person
        # Check if decision_strategy accepts attribute_statistics parameter
        import inspect
        sig = inspect.signature(decision_strategy)
        if 'attribute_statistics' in sig.parameters:
            accept = decision_strategy(
                attributes,
                constraints,
                admitted_count,
                attribute_counts,
                admitted_count + rejected_count,
                attribute_statistics
            )
        else:
            # Fallback for strategies that don't use attribute_statistics
            accept = decision_strategy(
                attributes,
                constraints,
                admitted_count,
                attribute_counts,
                admitted_count + rejected_count
            )
        
        # Update attribute counts if accepting (before making the API call)
        if accept:
            for attr, value in attributes.items():
                if value:
                    attribute_counts[attr] = attribute_counts.get(attr, 0) + 1
        
        # Record decision in history (before submitting to API)
        decision_record = {
            "personIndex": person_index,
            "attributes": attributes.copy(),  # Store a copy of the attributes
            "decision": "accepted" if accept else "rejected",
            "admittedCountBefore": admitted_count,
            "rejectedCountBefore": rejected_count
        }
        
        # Print decision
        decision_str = "ACCEPT" if accept else "REJECT"
        attr_str = ", ".join([f"{k}={v}" for k, v in attributes.items()])
        print(f"Person {person_index}: {decision_str} | Attributes: {attr_str} | "
              f"Admitted: {admitted_count}, Rejected: {rejected_count}")
        
        # Submit decision and get next person
        response = decide_and_next(BASE_URL, game_id, person_index, accept)
        status = response.get("status", "running")
        
        # Capture the full API response
        decision_record["apiResponse"] = response.copy()
        
        # Update counts after decision
        admitted_count = response.get("admittedCount", admitted_count)
        rejected_count = response.get("rejectedCount", rejected_count)
        
        # Add updated counts to the record
        decision_record["admittedCountAfter"] = admitted_count
        decision_record["rejectedCountAfter"] = rejected_count
        
        # Append to history after we have the full response
        decision_history.append(decision_record)
        
        # Check stopping conditions
        if status == "completed":
            print(f"\n✓ Game completed!")
            break
        
        if status == "failed":
            reason = response.get("reason", "Unknown reason")
            print(f"\n✗ Game failed: {reason}")
            break
        
        if admitted_count >= MAX_VENUE_CAPACITY:
            print(f"\n✓ Venue is full! ({admitted_count} people admitted)")
            break
        
        if rejected_count >= MAX_REJECTIONS:
            print(f"\n✗ Maximum rejections reached! ({rejected_count} rejections)")
            break
    
    # Final status
    final_status = response.get("status", status)
    final_admitted = response.get("admittedCount", admitted_count)
    final_rejected = response.get("rejectedCount", rejected_count)
    
    print("\n" + "=" * 60)
    print("GAME FINISHED")
    print("=" * 60)
    print(f"Status: {final_status}")
    print(f"Admitted: {final_admitted}")
    print(f"Rejected: {final_rejected}")
    print(f"Total processed: {final_admitted + final_rejected}")
    
    # Check if constraints were met
    if constraints:
        print("\nConstraint Status:")
        for constraint in constraints:
            attr = constraint['attribute']
            min_count = constraint['minCount']
            current_count = attribute_counts.get(attr, 0)
            met = "✓" if current_count >= min_count else "✗"
            print(f"  {met} {attr}: {current_count}/{min_count} (min required)")
    
    # Print total decisions recorded
    total_decisions = len(decision_history)
    print(f"\nTotal decisions recorded: {total_decisions}")
    
    return {
        "status": final_status,
        "admittedCount": final_admitted,
        "rejectedCount": final_rejected,
        "attributeCounts": attribute_counts,
        "decisionHistory": decision_history,
        "initialResponse": initial_response if 'initial_response' in locals() else None
    }


def save_game_attempt(game_id: str, results: Dict[str, Any], filename: str = "game_attempts.json"):
    """
    Save a game attempt, keyed by gameId.
    Each attempt includes a timestamp and attempt number.
    
    Args:
        game_id: The game ID
        results: Dictionary containing game results
        filename: Output filename
    """
    # Load existing attempts if file exists
    all_attempts = {}
    try:
        with open(filename, 'r') as f:
            all_attempts = json.load(f)
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        print(f"Warning: {filename} exists but is invalid JSON. Starting fresh.")
        all_attempts = {}
    
    # Initialize game entry if it doesn't exist
    if game_id not in all_attempts:
        all_attempts[game_id] = {
            "gameId": game_id,
            "attempts": []
        }
    
    # Create attempt record
    attempt_number = len(all_attempts[game_id]["attempts"]) + 1
    timestamp = datetime.now().isoformat()
    
    attempt_record = {
        "attemptNumber": attempt_number,
        "timestamp": timestamp,
        "status": results.get("status"),
        "admittedCount": results.get("admittedCount"),
        "rejectedCount": results.get("rejectedCount"),
        "attributeCounts": results.get("attributeCounts", {}),
        "decisionHistory": results.get("decisionHistory", []),
        "initialResponse": results.get("initialResponse")
    }
    
    # Add attempt to game's attempts list
    all_attempts[game_id]["attempts"].append(attempt_record)
    
    # Save to file
    with open(filename, 'w') as f:
        json.dump(all_attempts, f, indent=2)
    
    print(f"\nAttempt #{attempt_number} saved for gameId: {game_id}")
    print(f"Total attempts for this game: {attempt_number}")
    
    # Also save decision history separately for this attempt (for easy access)
    if "decisionHistory" in results:
        history_file = f"decision_history_{game_id}_attempt_{attempt_number}.json"
        with open(history_file, 'w') as f:
            json.dump(results["decisionHistory"], f, indent=2)
        print(f"Decision history saved to {history_file}")


def load_game_attempts(game_id: Optional[str] = None, filename: str = "game_attempts.json") -> Dict[str, Any]:
    """
    Load game attempts from file.
    
    Args:
        game_id: Optional gameId to load specific game's attempts
        filename: Path to the attempts JSON file
    
    Returns:
        Dictionary of attempts (keyed by gameId) or specific game's attempts
    """
    try:
        with open(filename, 'r') as f:
            all_attempts = json.load(f)
        
        if game_id:
            return all_attempts.get(game_id, {"gameId": game_id, "attempts": []})
        
        return all_attempts
    except FileNotFoundError:
        return {} if not game_id else {"gameId": game_id, "attempts": []}
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON file: {e}")
        return {} if not game_id else {"gameId": game_id, "attempts": []}


def save_leaderboard_entry(
    game_id: str,
    results: Dict[str, Any],
    constraints: Optional[list] = None,
    filename: str = "leaderboard.json"
):
    """
    Save a leaderboard entry for a game attempt.
    
    Args:
        game_id: The game ID
        results: Dictionary containing game results
        constraints: List of constraints (optional)
        filename: Output filename for leaderboard
    """
    # Load existing leaderboard if file exists
    leaderboard = []
    try:
        with open(filename, 'r') as f:
            leaderboard = json.load(f)
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        print(f"Warning: {filename} exists but is invalid JSON. Starting fresh.")
        leaderboard = []
    
    # Calculate constraint status
    constraint_status = {}
    attribute_counts = results.get("attributeCounts", {})
    if constraints:
        for constraint in constraints:
            attr = constraint['attribute']
            min_count = constraint['minCount']
            current_count = attribute_counts.get(attr, 0)
            constraint_status[attr] = {
                "count": current_count,
                "minRequired": min_count,
                "met": current_count >= min_count
            }
    
    # Create leaderboard entry
    timestamp = datetime.now()
    entry = {
        "gameId": game_id,
        "date": timestamp.isoformat(),
        "dateReadable": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "status": results.get("status"),
        "admitted": results.get("admittedCount", 0),
        "rejected": results.get("rejectedCount", 0),
        "totalProcessed": results.get("admittedCount", 0) + results.get("rejectedCount", 0),
        "constraintStatus": constraint_status,
        "totalDecisionsRecorded": len(results.get("decisionHistory", []))
    }
    
    # Add to leaderboard
    leaderboard.append(entry)
    
    # Sort by date (most recent first)
    leaderboard.sort(key=lambda x: x["date"], reverse=True)
    
    # Save to file
    with open(filename, 'w') as f:
        json.dump(leaderboard, f, indent=2)
    
    print(f"\nLeaderboard entry saved to {filename}")


def display_leaderboard(
    game_id: Optional[str] = None,
    limit: Optional[int] = None,
    filename: str = "leaderboard.json"
):
    """
    Display the leaderboard of game attempts.
    
    Args:
        game_id: Optional gameId to filter by specific game
        limit: Optional limit on number of entries to display
        filename: Path to the leaderboard JSON file
    """
    try:
        with open(filename, 'r') as f:
            leaderboard = json.load(f)
    except FileNotFoundError:
        print(f"No leaderboard found at {filename}")
        return
    except json.JSONDecodeError as e:
        print(f"Error parsing leaderboard file: {e}")
        return
    
    # Filter by game_id if provided
    if game_id:
        leaderboard = [entry for entry in leaderboard if entry.get("gameId") == game_id]
    
    if not leaderboard:
        filter_msg = f" for gameId: {game_id}" if game_id else ""
        print(f"\nNo leaderboard entries found{filter_msg}")
        return
    
    # Limit entries if specified
    if limit:
        leaderboard = leaderboard[:limit]
    
    print("\n" + "=" * 100)
    print("LEADERBOARD - Game Attempts")
    print("=" * 100)
    
    # Print header
    header = f"{'Date':<20} {'Game ID':<38} {'Status':<12} {'Admitted':<10} {'Rejected':<10} {'Total':<10} {'Decisions':<10}"
    print(header)
    print("-" * 100)
    
    # Print each entry
    for entry in leaderboard:
        date_str = entry.get("dateReadable", entry.get("date", "Unknown"))[:19]
        game_id_str = entry.get("gameId", "Unknown")[:36]
        status = entry.get("status", "unknown")
        admitted = entry.get("admitted", 0)
        rejected = entry.get("rejected", 0)
        total = entry.get("totalProcessed", 0)
        decisions = entry.get("totalDecisionsRecorded", 0)
        
        row = f"{date_str:<20} {game_id_str:<38} {status:<12} {admitted:<10} {rejected:<10} {total:<10} {decisions:<10}"
        print(row)
        
        # Print constraint status if available
        constraint_status = entry.get("constraintStatus", {})
        if constraint_status:
            constraint_lines = []
            for attr, info in constraint_status.items():
                count = info.get("count", 0)
                min_req = info.get("minRequired", 0)
                met = "✓" if info.get("met", False) else "✗"
                constraint_lines.append(f"  {met} {attr}: {count}/{min_req} (min required)")
            
            if constraint_lines:
                print("    Constraint Status:")
                for line in constraint_lines:
                    print(line)
    
    print("=" * 100)
    print(f"Total entries: {len(leaderboard)}")


def get_latest_game_id(filename: str = "game_info.json") -> Optional[str]:
    """
    Get the latest gameId from game_info.json.
    First tries to find the gameId with the most recent attempt.
    If no attempts exist, returns the last gameId in game_info.json.
    
    Args:
        filename: Path to the game info JSON file
    
    Returns:
        The latest gameId or None if no games exist
    """
    # First, try to find the gameId with the most recent attempt
    try:
        all_attempts = load_game_attempts()
        if all_attempts:
            latest_game_id = None
            latest_timestamp = None
            
            for game_id, game_data in all_attempts.items():
                attempts = game_data.get("attempts", [])
                if attempts:
                    # Get the most recent attempt for this game
                    latest_attempt = max(attempts, key=lambda x: x.get("timestamp", ""))
                    attempt_timestamp = latest_attempt.get("timestamp", "")
                    
                    if latest_timestamp is None or attempt_timestamp > latest_timestamp:
                        latest_timestamp = attempt_timestamp
                        latest_game_id = game_id
            
            if latest_game_id:
                return latest_game_id
    except Exception:
        pass
    
    # If no attempts found, get the last gameId from game_info.json
    try:
        with open(filename, 'r') as f:
            all_games = json.load(f)
            
        # Check if it's old format (single game)
        if isinstance(all_games, dict):
            if "gameId" in all_games and isinstance(all_games["gameId"], str):
                return all_games["gameId"]
            
            # New format: get the last gameId
            if all_games:
                # Get the last key (in Python 3.7+, dicts maintain insertion order)
                return list(all_games.keys())[-1]
    except Exception:
        pass
    
    return None


def display_attempt_summary(game_id: str):
    """
    Display a summary of all attempts for a game, showing progress across attempts.
    
    Args:
        game_id: The game ID to display attempts for
    """
    attempts_data = load_game_attempts(game_id)
    
    if not attempts_data or not attempts_data.get("attempts"):
        print(f"\nNo previous attempts found for gameId: {game_id}")
        return
    
    attempts = attempts_data["attempts"]
    
    print("\n" + "=" * 80)
    print(f"ATTEMPT PROGRESS SUMMARY - GameId: {game_id}")
    print("=" * 80)
    
    # Load game info to get constraints
    game_info = load_game_info(game_id)
    constraints = game_info.get("constraints", []) if game_info else []
    
    # Print header
    header = f"{'Attempt':<10} {'Status':<12} {'Admitted':<12} {'Rejected':<12}"
    if constraints:
        for constraint in constraints:
            attr = constraint['attribute']
            header += f" {attr.capitalize():<12}"
    print(header)
    print("-" * 80)
    
    # Print each attempt
    for attempt in attempts:
        attempt_num = attempt.get("attemptNumber", "?")
        status = attempt.get("status", "unknown")
        admitted = attempt.get("admittedCount", 0)
        rejected = attempt.get("rejectedCount", 0)
        attr_counts = attempt.get("attributeCounts", {})
        
        row = f"{attempt_num:<10} {status:<12} {admitted:<12} {rejected:<12}"
        if constraints:
            for constraint in constraints:
                attr = constraint['attribute']
                min_count = constraint['minCount']
                count = attr_counts.get(attr, 0)
                met = "✓" if count >= min_count else "✗"
                row += f" {count}/{min_count} {met:<3}"
        
        print(row)
    
    print("=" * 80)
    
    # Show improvement trends
    if len(attempts) > 1:
        print("\nProgress Trends:")
        prev_admitted = None
        for attempt in attempts:
            attempt_num = attempt.get("attemptNumber", "?")
            admitted = attempt.get("admittedCount", 0)
            status = attempt.get("status", "unknown")
            
            if prev_admitted is not None:
                diff = admitted - prev_admitted
                trend = "↑" if diff > 0 else "↓" if diff < 0 else "→"
                print(f"  Attempt {attempt_num}: {admitted} admitted ({status}) {trend} {abs(diff)} from previous")
            else:
                print(f"  Attempt {attempt_num}: {admitted} admitted ({status})")
            
            prev_admitted = admitted


def main():
    """Main function to run the game."""
    # Check if gameId is provided as command line argument
    if len(sys.argv) >= 2:
        game_id = sys.argv[1]
        # Try to load game info for this specific gameId
        game_info = load_game_info(game_id)
        if game_info:
            constraints = game_info.get("constraints", [])
            attribute_statistics = game_info.get("attributeStatistics")
            print(f"Loaded game info for gameId: {game_id}")
        else:
            constraints = []
            attribute_statistics = None
            print(f"Using game ID from command line: {game_id}")
            print("Note: No game info found for this gameId. Constraints and statistics not available.")
    else:
        # No gameId provided - try to get the latest one
        game_id = get_latest_game_id()
        
        if game_id:
            game_info = load_game_info(game_id)
            if game_info:
                constraints = game_info.get("constraints", [])
                attribute_statistics = game_info.get("attributeStatistics")
                print(f"Using latest gameId: {game_id}")
                print(f"Loaded game info from file")
            else:
                constraints = []
                attribute_statistics = None
                print(f"Using latest gameId: {game_id}")
                print("Note: No game info found for this gameId. Constraints and statistics not available.")
        else:
            print("Error: No game ID provided and no games found in game_info.json")
            print("Usage: python play_game.py [<game_id>]")
            print("Or create a game_info.json file using create_game.py")
            sys.exit(1)
    
    if not game_id:
        print("Error: No game ID available")
        sys.exit(1)
    
    # Show previous attempts if any
    attempts_data = load_game_attempts(game_id)
    if attempts_data.get("attempts"):
        num_attempts = len(attempts_data["attempts"])
        print(f"\nPrevious attempts for this game: {num_attempts}")
        last_attempt = attempts_data["attempts"][-1]
        print(f"Last attempt: #{last_attempt.get('attemptNumber')} - "
              f"{last_attempt.get('admittedCount')} admitted, "
              f"Status: {last_attempt.get('status')}")
    
    # Play the game
    results = play_game(game_id, constraints, attribute_statistics=attribute_statistics)
    
    # Save results with attempt tracking
    save_game_attempt(game_id, results)
    
    # Save to leaderboard
    save_leaderboard_entry(game_id, results, constraints)
    
    # Display attempt summary
    display_attempt_summary(game_id)
    
    # Display leaderboard
    print("\n")
    display_leaderboard(game_id, limit=10)


if __name__ == "__main__":
    # Check if user wants to view progress only
    if len(sys.argv) > 1 and sys.argv[1] == "--view-progress":
        if len(sys.argv) < 3:
            print("Usage: python play_game.py --view-progress <game_id>")
            sys.exit(1)
        game_id = sys.argv[2]
        display_attempt_summary(game_id)
    # Check if user wants to view leaderboard
    elif len(sys.argv) > 1 and sys.argv[1] == "--leaderboard":
        game_id = sys.argv[2] if len(sys.argv) > 2 else None
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
        display_leaderboard(game_id, limit)
    else:
        main()

