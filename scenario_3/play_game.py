#!/usr/bin/env python3
"""
Script to play the Berghain Challenge game by making accept/reject decisions
for each person until the venue is full (1000 people) or 20,000 rejections occur.
"""

import re
import requests
import json
import sys
import os
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

# Configuration
BASE_URL = "https://berghain.challenges.listenlabs.ai"
MAX_VENUE_CAPACITY = 1000 + 1
MAX_REJECTIONS = 20000

# Scenario directory for file paths
SCENARIO_DIR = os.path.dirname(os.path.abspath(__file__))


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


def load_game_info(game_id: Optional[str] = None, filename: str = None) -> Optional[Dict[str, Any]]:
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
    if filename is None:
        filename = os.path.join(SCENARIO_DIR, "game_info.json")
    
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
    attribute_statistics: Optional[Dict[str, Any]] = None,
    well_connected_encountered: int = 0
) -> bool:
    """
    Decision logic: determine whether to accept or reject a person.
    
    Strategy for Scenario 3 (6 constraints):
    - underground_veteran: min 500 (67.9% frequency - common)
    - international: min 650 (57.4% frequency - common)
    - fashion_forward: min 550 (69.1% frequency - very common)
    - queer_friendly: min 250 (4.6% frequency - very rare, accept almost all)
    - vinyl_collector: min 200 (4.5% frequency - very rare, accept almost all)
    - german_speaker: min 800 (45.7% frequency - moderate)
    
    Correlation-based insights:
    - international ↔ german_speaker: -0.717 (very strong negative) - mutually exclusive, need separate people
    - fashion_forward ↔ german_speaker: -0.352 (moderate negative) - somewhat mutually exclusive
    - queer_friendly ↔ vinyl_collector: +0.48 (strong positive) - accepting one helps get the other
    - underground_veteran ↔ fashion_forward: -0.17 (weak negative)
    
    Strategy:
    - Always accept rare attributes (queer_friendly, vinyl_collector) when below target
    - Prioritize people with multiple needed attributes
    - Account for negative correlations (international vs german_speaker)
    - Track progress on each constraint and accept based on needs
    - Be more selective as venue fills up
    
    Args:
        person_attributes: Dictionary of attribute IDs to boolean values
        constraints: List of constraint dictionaries with 'attribute' and 'minCount'
        admitted_count: Current number of admitted people
        attribute_counts: Dictionary tracking count of each attribute in admitted people
        total_admitted: Total number of people admitted so far
        attribute_statistics: Optional dictionary with 'relativeFrequencies' and 'correlations'
        well_connected_encountered: Not used in scenario 3 (kept for compatibility)
    
    Returns:
        True to accept, False to reject
    """
    # ============================================================================
    # TUNABLE PARAMETERS
    # ============================================================================
    
    # Venue fill ratio thresholds
    VENUE_FILL_CRITICAL = 0.99      # Very late game
    VENUE_FILL_LATE = 0.97          # Late game
    VENUE_FILL_MID = 0.9            # Mid game
    VENUE_FILL_EARLY = 0.8          # Early game
    
    # Deficit thresholds
    DEFICIT_VERY_HIGH = 200
    DEFICIT_HIGH = 100
    DEFICIT_MODERATE = 50
    DEFICIT_LOW = 20
    
    # Over-target multipliers
    OVER_TARGET_RARE = 1.2          # Allow 20% over for rare attributes
    OVER_TARGET_COMMON = 1.1       # Allow 10% over for common attributes
    
    # Default constraint minimums
    DEFAULT_UNDERGROUND_MIN = 500
    DEFAULT_INTERNATIONAL_MIN = 650
    DEFAULT_FASHION_MIN = 550
    DEFAULT_QUEER_MIN = 250
    DEFAULT_VINYL_MIN = 200
    DEFAULT_GERMAN_MIN = 800
    
    # ============================================================================
    # END OF TUNABLE PARAMETERS
    # ============================================================================
    
    # If venue is full, reject
    if admitted_count >= MAX_VENUE_CAPACITY:
        return False
    
    # Extract attribute values
    is_underground = person_attributes.get("underground_veteran", False)
    is_international = person_attributes.get("international", False)
    is_fashion = person_attributes.get("fashion_forward", False)
    is_queer = person_attributes.get("queer_friendly", False)
    is_vinyl = person_attributes.get("vinyl_collector", False)
    is_german = person_attributes.get("german_speaker", False)
    
    # Get constraint requirements
    constraint_mins = {}
    for constraint in constraints:
        constraint_mins[constraint['attribute']] = constraint['minCount']
    
    underground_min = constraint_mins.get("underground_veteran", DEFAULT_UNDERGROUND_MIN)
    international_min = constraint_mins.get("international", DEFAULT_INTERNATIONAL_MIN)
    fashion_min = constraint_mins.get("fashion_forward", DEFAULT_FASHION_MIN)
    queer_min = constraint_mins.get("queer_friendly", DEFAULT_QUEER_MIN)
    vinyl_min = constraint_mins.get("vinyl_collector", DEFAULT_VINYL_MIN)
    german_min = constraint_mins.get("german_speaker", DEFAULT_GERMAN_MIN)
    
    # Get current counts
    underground_count = attribute_counts.get("underground_veteran", 0)
    international_count = attribute_counts.get("international", 0)
    fashion_count = attribute_counts.get("fashion_forward", 0)
    queer_count = attribute_counts.get("queer_friendly", 0)
    vinyl_count = attribute_counts.get("vinyl_collector", 0)
    german_count = attribute_counts.get("german_speaker", 0)
    
    # Calculate progress towards constraints
    underground_progress = underground_count / underground_min if underground_min > 0 else 1.0
    international_progress = international_count / international_min if international_min > 0 else 1.0
    fashion_progress = fashion_count / fashion_min if fashion_min > 0 else 1.0
    queer_progress = queer_count / queer_min if queer_min > 0 else 1.0
    vinyl_progress = vinyl_count / vinyl_min if vinyl_min > 0 else 1.0
    german_progress = german_count / german_min if german_min > 0 else 1.0
    
    # Calculate deficits
    underground_deficit = underground_min - underground_count
    international_deficit = international_min - international_count
    fashion_deficit = fashion_min - fashion_count
    queer_deficit = queer_min - queer_count
    vinyl_deficit = vinyl_min - vinyl_count
    german_deficit = german_min - german_count
    
    # Calculate venue fill ratio
    venue_fill_ratio = admitted_count / MAX_VENUE_CAPACITY
    remaining_capacity = MAX_VENUE_CAPACITY - admitted_count
    
    # Extract correlations if available
    correlations = {}
    if attribute_statistics and "correlations" in attribute_statistics:
        correlations = attribute_statistics["correlations"]
    
    # Key correlation: international ↔ german_speaker: -0.717 (very strong negative)
    int_german_corr = correlations.get("international", {}).get("german_speaker", -0.717)
    
    # HARD RULES

    if queer_count < 130:
        if is_queer:
            return True
        else:
            return False
    
    if vinyl_count < 130:
        if is_vinyl:
            return True
        else:
            return False

    # STRATEGY 1: Always accept rare attributes (queer_friendly, vinyl_collector) when below target
    # These are very rare (4.5-4.6%), so we can't afford to reject them
    if is_queer and queer_count < queer_min * OVER_TARGET_RARE:
        return True
    if is_vinyl and vinyl_count < vinyl_min * OVER_TARGET_RARE:
        return True
    
    # STRATEGY 2: Prioritize people with multiple needed attributes
    needed_attributes = 0
    critical_attributes = 0
    
    if is_underground and underground_count < underground_min:
        needed_attributes += 1
        if underground_deficit > DEFICIT_MODERATE:
            critical_attributes += 1
    if is_international and international_count < international_min:
        needed_attributes += 1
        if international_deficit > DEFICIT_MODERATE:
            critical_attributes += 1
    if is_fashion and fashion_count < fashion_min:
        needed_attributes += 1
        if fashion_deficit > DEFICIT_MODERATE:
            critical_attributes += 1
    if is_queer and queer_count < queer_min:
        needed_attributes += 1
        critical_attributes += 1  # Always critical (rare)
    if is_vinyl and vinyl_count < vinyl_min:
        needed_attributes += 1
        critical_attributes += 1  # Always critical (rare)
    if is_german and german_count < german_min:
        needed_attributes += 1
        if german_deficit > DEFICIT_MODERATE:
            critical_attributes += 1
    
    # Accept if person has 3+ needed attributes (very valuable)
    if needed_attributes >= 3:
        return True
    
    # Accept if person has 2+ critical attributes (high deficit)
    if critical_attributes >= 2:
        return True
    
    # STRATEGY 3: Handle rare attributes with special care
    # queer_friendly and vinyl_collector are very rare, accept liberally
    if is_queer:
        if queer_count < queer_min:
            return True
        # Even if we have enough, accept if not too far over and venue has space
        if queer_progress < OVER_TARGET_RARE and venue_fill_ratio < VENUE_FILL_LATE:
            return True
    
    if is_vinyl:
        if vinyl_count < vinyl_min:
            return True
        # Even if we have enough, accept if not too far over and venue has space
        if vinyl_progress < OVER_TARGET_RARE and venue_fill_ratio < VENUE_FILL_LATE:
            return True
    
    # STRATEGY 4: Handle negative correlation between international and german_speaker
    # Since they're mutually exclusive (-0.717 correlation), people with both are rare but valuable
    # They count for both attributes, so accept if we need either
    if is_international and is_german:
        # Person has both (rare due to negative correlation, but valuable)
        # Accept if we need either attribute
        if international_count < international_min or german_count < german_min:
            return True
        # If we have enough of both, still accept if not too far over and venue has space
        if international_progress < OVER_TARGET_COMMON and german_progress < OVER_TARGET_COMMON:
            if venue_fill_ratio < VENUE_FILL_LATE:
                return True
        # Reject only if both are well over target and venue is getting full
        return False
    
    # STRATEGY 5: Handle individual attributes based on deficits
    # german_speaker (needs 800, moderate frequency)
    if is_german and not is_international:  # Avoid double-counting with international
        if german_count < german_min:
            # Accept if we need german speakers
            if german_deficit > DEFICIT_LOW:
                return True
            # Accept if venue is not too full
            if venue_fill_ratio < VENUE_FILL_MID:
                return True
        else:
            # We have enough, but accept if not too far over and venue has space
            if german_progress < OVER_TARGET_COMMON and venue_fill_ratio < VENUE_FILL_LATE:
                return True
    
    # international (needs 650, common frequency)
    if is_international and not is_german:  # Avoid double-counting
        if international_count < international_min:
            if international_deficit > DEFICIT_LOW:
                return True
            if venue_fill_ratio < VENUE_FILL_MID:
                return True
        else:
            if international_progress < OVER_TARGET_COMMON and venue_fill_ratio < VENUE_FILL_LATE:
                return True
    
    # underground_veteran (needs 500, very common)
    if is_underground:
        if underground_count < underground_min:
            if underground_deficit > DEFICIT_LOW:
                return True
            if venue_fill_ratio < VENUE_FILL_MID:
                return True
        else:
            if underground_progress < OVER_TARGET_COMMON and venue_fill_ratio < VENUE_FILL_LATE:
                return True
    
    # fashion_forward (needs 550, very common)
    if is_fashion:
        if fashion_count < fashion_min:
            if fashion_deficit > DEFICIT_LOW:
                return True
            if venue_fill_ratio < VENUE_FILL_MID:
                return True
        else:
            if fashion_progress < OVER_TARGET_COMMON and venue_fill_ratio < VENUE_FILL_LATE:
                return True
    
    # STRATEGY 6: Accept people with at least one needed attribute early in the game
    if needed_attributes >= 1:
        if venue_fill_ratio < VENUE_FILL_EARLY:
            return True
        # Later in game, only accept if deficit is significant
        if venue_fill_ratio < VENUE_FILL_MID:
            # Check if any critical deficit exists
            max_deficit = max(underground_deficit, international_deficit, fashion_deficit, 
                            queer_deficit, vinyl_deficit, german_deficit)
            if max_deficit > DEFICIT_MODERATE:
                return True
    
    # STRATEGY 7: Late game - be more selective
    if venue_fill_ratio >= VENUE_FILL_LATE:
        # Only accept if we have significant deficits
        max_deficit = max(underground_deficit, international_deficit, fashion_deficit,
                        queer_deficit, vinyl_deficit, german_deficit)
        if max_deficit > DEFICIT_HIGH and needed_attributes >= 1:
            return True
        # Always accept rare attributes even in late game if we're below target
        if (is_queer and queer_count < queer_min) or (is_vinyl and vinyl_count < vinyl_min):
            return True
    
    # Default: reject if none of the above conditions are met
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
    
    # Track well_connected encounters (for skip-every-second strategy)
    well_connected_encountered = 0
    
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
        
        # Track well_connected encounters (before decision, to count all encounters)
        if attributes.get("well_connected", False):
            well_connected_encountered += 1
        
        # Make decision for current person
        # Check if decision_strategy accepts attribute_statistics parameter
        import inspect
        sig = inspect.signature(decision_strategy)
        has_attr_stats = 'attribute_statistics' in sig.parameters
        has_well_conn_counter = 'well_connected_encountered' in sig.parameters
        
        if has_attr_stats and has_well_conn_counter:
            accept = decision_strategy(
                attributes,
                constraints,
                admitted_count,
                attribute_counts,
                admitted_count + rejected_count,
                attribute_statistics,
                well_connected_encountered
            )
        elif has_attr_stats:
            accept = decision_strategy(
                attributes,
                constraints,
                admitted_count,
                attribute_counts,
                admitted_count + rejected_count,
                attribute_statistics
            )
        elif has_well_conn_counter:
            accept = decision_strategy(
                attributes,
                constraints,
                admitted_count,
                attribute_counts,
                admitted_count + rejected_count,
                None,
                well_connected_encountered
            )
        else:
            # Fallback for strategies that don't use attribute_statistics or counter
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


def save_game_attempt(game_id: str, results: Dict[str, Any], filename: str = None):
    """
    Save a game attempt, keyed by gameId.
    Each attempt includes a timestamp and attempt number.
    
    Args:
        game_id: The game ID
        results: Dictionary containing game results
        filename: Output filename
    """
    if filename is None:
        filename = os.path.join(SCENARIO_DIR, "game_attempts.json")
    
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
        history_file = os.path.join(SCENARIO_DIR, f"decision_history_{game_id}_attempt_{attempt_number}.json")
        with open(history_file, 'w') as f:
            json.dump(results["decisionHistory"], f, indent=2)
        print(f"Decision history saved to {history_file}")


def load_game_attempts(game_id: Optional[str] = None, filename: str = None) -> Dict[str, Any]:
    """
    Load game attempts from file.
    
    Args:
        game_id: Optional gameId to load specific game's attempts
        filename: Path to the attempts JSON file
    
    Returns:
        Dictionary of attempts (keyed by gameId) or specific game's attempts
    """
    if filename is None:
        filename = os.path.join(SCENARIO_DIR, "game_attempts.json")
    
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


def calculate_actual_statistics(decision_history: list) -> Dict[str, Any]:
    """
    Calculate actual relativeFrequencies and correlations from admitted people.
    
    Args:
        decision_history: List of decision records with 'attributes' and 'decision' fields
    
    Returns:
        Dictionary with 'relativeFrequencies' and 'correlations'
    """
    admitted_decisions = [d for d in decision_history if d.get("decision") == "accepted"]
    total_admitted = len(admitted_decisions)
    
    if total_admitted == 0:
        return {
            "relativeFrequencies": {},
            "correlations": {}
        }
    
    # Get all attribute names
    all_attributes = set()
    for decision in admitted_decisions:
        all_attributes.update(decision.get("attributes", {}).keys())
    all_attributes = sorted(list(all_attributes))
    
    # Calculate relative frequencies
    relative_frequencies = {}
    for attr in all_attributes:
        count = sum(1 for d in admitted_decisions if d.get("attributes", {}).get(attr, False))
        relative_frequencies[attr] = count / total_admitted if total_admitted > 0 else 0.0
    
    # Calculate correlations
    correlations = {}
    for attr1 in all_attributes:
        correlations[attr1] = {}
        for attr2 in all_attributes:
            if attr1 == attr2:
                correlations[attr1][attr2] = 1.0
            else:
                # Calculate correlation coefficient
                # For binary variables: corr = (P(both) - P(A)*P(B)) / sqrt(P(A)*(1-P(A))*P(B)*(1-P(B)))
                count_both = sum(1 for d in admitted_decisions 
                                if d.get("attributes", {}).get(attr1, False) and 
                                   d.get("attributes", {}).get(attr2, False))
                p_both = count_both / total_admitted if total_admitted > 0 else 0.0
                p_a = relative_frequencies.get(attr1, 0.0)
                p_b = relative_frequencies.get(attr2, 0.0)
                
                denominator = (p_a * (1 - p_a) * p_b * (1 - p_b)) ** 0.5
                if denominator > 0:
                    correlation = (p_both - p_a * p_b) / denominator
                else:
                    correlation = 0.0
                
                correlations[attr1][attr2] = correlation
    
    return {
        "relativeFrequencies": relative_frequencies,
        "correlations": correlations
    }


def evaluate_attribute_statistics(
    actual_stats: Dict[str, Any],
    target_stats: Dict[str, Any],
    tolerance: float = 0.01
) -> Dict[str, Any]:
    """
    Evaluate if actual statistics meet target criteria.
    
    Args:
        actual_stats: Dictionary with actual 'relativeFrequencies' and 'correlations'
        target_stats: Dictionary with target 'relativeFrequencies' and 'correlations'
        tolerance: Tolerance for comparison (default 0.01 = 1%)
    
    Returns:
        Dictionary with evaluation results similar to constraintStatus structure
    """
    evaluation = {
        "relativeFrequencies": {},
        "correlations": {},
        "allMet": True
    }
    
    # Evaluate relative frequencies
    target_freqs = target_stats.get("relativeFrequencies", {})
    actual_freqs = actual_stats.get("relativeFrequencies", {})
    
    for attr, target_freq in target_freqs.items():
        actual_freq = actual_freqs.get(attr, 0.0)
        diff = abs(actual_freq - target_freq)
        met = diff <= tolerance
        evaluation["relativeFrequencies"][attr] = {
            "actual": actual_freq,
            "target": target_freq,
            "difference": diff,
            "met": met
        }
        if not met:
            evaluation["allMet"] = False
    
    # Evaluate correlations
    target_corrs = target_stats.get("correlations", {})
    actual_corrs = actual_stats.get("correlations", {})
    
    for attr1, attr2_dict in target_corrs.items():
        if attr1 not in evaluation["correlations"]:
            evaluation["correlations"][attr1] = {}
        
        for attr2, target_corr in attr2_dict.items():
            actual_corr = actual_corrs.get(attr1, {}).get(attr2, 0.0)
            diff = abs(actual_corr - target_corr)
            met = diff <= tolerance
            evaluation["correlations"][attr1][attr2] = {
                "actual": actual_corr,
                "target": target_corr,
                "difference": diff,
                "met": met
            }
            if not met:
                evaluation["allMet"] = False
    
    return evaluation


def save_leaderboard_entry(
    game_id: str,
    results: Dict[str, Any],
    constraints: Optional[list] = None,
    attribute_statistics: Optional[Dict[str, Any]] = None,
    filename: str = None
):
    """
    Save a leaderboard entry for a game attempt.
    
    Args:
        game_id: The game ID
        results: Dictionary containing game results
        constraints: List of constraints (optional)
        attribute_statistics: Optional dictionary with 'relativeFrequencies' and 'correlations' (target values)
        filename: Output filename for leaderboard
    """
    if filename is None:
        filename = os.path.join(SCENARIO_DIR, "leaderboard.json")
    
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
    
    # Calculate and evaluate attribute statistics
    attribute_statistics_status = None
    if attribute_statistics:
        # Calculate actual statistics from decision history
        decision_history = results.get("decisionHistory", [])
        actual_stats = calculate_actual_statistics(decision_history)
        
        # Evaluate against target statistics
        attribute_statistics_status = evaluate_attribute_statistics(actual_stats, attribute_statistics)
    
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
    
    # Add attribute statistics (target values)
    if attribute_statistics:
        entry["attributeStatistics"] = attribute_statistics
    
    # Add attribute statistics evaluation
    if attribute_statistics_status:
        entry["attributeStatisticsStatus"] = attribute_statistics_status
    
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
    filename: str = None
):
    """
    Display the leaderboard of game attempts.
    
    Args:
        game_id: Optional gameId to filter by specific game
        limit: Optional limit on number of entries to display
        filename: Path to the leaderboard JSON file
    """
    if filename is None:
        filename = os.path.join(SCENARIO_DIR, "leaderboard.json")
    
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


def get_latest_game_id(filename: str = None) -> Optional[str]:
    """
    Get the latest gameId from game_info.json.
    Returns the most recently created game (last key in the dict).
    This ensures newly created games are always used.
    
    Args:
        filename: Path to the game info JSON file
    
    Returns:
        The latest gameId or None if no games exist
    """
    if filename is None:
        filename = os.path.join(SCENARIO_DIR, "game_info.json")
    
    try:
        with open(filename, 'r') as f:
            all_games = json.load(f)
            
        # Check if it's old format (single game)
        if isinstance(all_games, dict):
            if "gameId" in all_games and isinstance(all_games["gameId"], str):
                return all_games["gameId"]
            
            # New format: get the last gameId (most recently created)
            # In Python 3.7+, dicts maintain insertion order
            if all_games:
                return list(all_games.keys())[-1]
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        pass
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
    save_leaderboard_entry(game_id, results, constraints, attribute_statistics)
    
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

