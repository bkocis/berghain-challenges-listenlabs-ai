#!/usr/bin/env python3
"""
Script to play the Berghain Challenge game by making accept/reject decisions
for each person until the venue is full (1000 people) or 20,000 rejections occur.
"""

import requests
import json
import sys
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


def load_game_info(filename: str = "game_info.json") -> Optional[Dict[str, Any]]:
    """
    Load game information from a JSON file.
    
    Args:
        filename: Path to the game info JSON file
    
    Returns:
        Dictionary containing game information or None if file doesn't exist
    """
    try:
        with open(filename, 'r') as f:
            return json.load(f)
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
    - Young BUT not well_dressed: Accept early/mid game, more selective later
    
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
    
    # Strategy 4: Young BUT not well_dressed
    if is_young and not is_well_dressed:
        # If we already have enough young people, reject
        if young_count >= young_min:
            return False
        
        # Early game: accept if we need young people
        if venue_fill_ratio < 0.5:
            return True
        
        # Mid game: be selective, accept if below requirement
        if venue_fill_ratio < 0.75:
            # Accept if we're below 95% of requirement
            if young_progress < 0.95:
                return True
            return False
        
        # Late game: very selective, only accept if critically below requirement
        if young_progress < 0.90:
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
    
    return {
        "status": final_status,
        "admittedCount": final_admitted,
        "rejectedCount": final_rejected,
        "attributeCounts": attribute_counts,
        "decisionHistory": decision_history,
        "initialResponse": initial_response if 'initial_response' in locals() else None
    }


def main():
    """Main function to run the game."""
    # Try to load game info from file
    game_info = load_game_info()
    
    if game_info:
        game_id = game_info.get("gameId")
        constraints = game_info.get("constraints", [])
        attribute_statistics = game_info.get("attributeStatistics")
        print(f"Loaded game info from file")
        print(f"Game ID: {game_id}")
    else:
        # Get game ID from command line argument
        if len(sys.argv) < 2:
            print("Usage: python play_game.py <game_id>")
            print("Or create a game_info.json file using create_game.py")
            sys.exit(1)
        
        game_id = sys.argv[1]
        constraints = []
        attribute_statistics = None
        print(f"Using game ID from command line: {game_id}")
    
    if not game_id:
        print("Error: No game ID provided")
        sys.exit(1)
    
    # Play the game
    results = play_game(game_id, constraints, attribute_statistics=attribute_statistics)
    
    # Save results
    output_file = "game_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_file}")
    
    # Save decision history separately for easier analysis
    if "decisionHistory" in results:
        history_file = "decision_history.json"
        with open(history_file, 'w') as f:
            json.dump(results["decisionHistory"], f, indent=2)
        print(f"Decision history saved to {history_file}")
        print(f"Total decisions recorded: {len(results['decisionHistory'])}")


if __name__ == "__main__":
    main()

