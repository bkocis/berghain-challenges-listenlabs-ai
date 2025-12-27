#!/usr/bin/env python3
"""
Script to play the Berghain Challenge game by making accept/reject decisions
for each person until the venue is full (1000 people) or 20,000 rejections occur.
"""

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
    
    Strategy for Scenario 2 (4 constraints):
    - techno_lover: min 650 (62.65% frequency)
    - well_connected: min 450 (47% frequency)
    - creative: min 300 (6.23% frequency - rarest, accept almost all)
    - berlin_local: min 750 (39.8% frequency)
    
    Correlation-based insights:
    - techno_lover ↔ berlin_local: -0.65 (strong negative) - rare to have both, prioritize separately
    - well_connected ↔ berlin_local: +0.57 (strong positive) - accepting well_connected helps get berlin_local
    - techno_lover ↔ well_connected: -0.47 (moderate negative) - somewhat mutually exclusive
    - creative: weakly correlated with all (0.09-0.14) - independent, can find in any combination
    
    Strategy:
    - Always accept creative people (too rare to reject)
    - Prioritize rare combinations (techno_lover + berlin_local) - extremely valuable
    - Leverage positive correlations (well_connected helps berlin_local)
    - Account for negative correlations (techno_lover won't help berlin_local, need separate people)
    - Track progress on each constraint and accept based on needs
    - Be more selective as venue fills up
    
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
    # ============================================================================
    # TUNABLE PARAMETERS - Adjust these values to fine-tune decision strategy
    # ============================================================================
    
    # Venue fill ratio thresholds (0.0 to 1.0) - when to accept/reject based on capacity
    VENUE_FILL_CRITICAL = 0.99      # Very late game - accept until 99% full
    VENUE_FILL_LATE = 0.97          # Late game - accept until 97% full
    VENUE_FILL_MID_LATE = 0.95      # Mid-late game - accept until 95% full
    VENUE_FILL_MID = 0.9            # Mid game - accept until 90% full
    VENUE_FILL_MID_EARLY = 0.85     # Mid-early game - accept until 85% full
    VENUE_FILL_EARLY = 0.8          # Early game - accept until 80% full
    VENUE_FILL_MODERATE = 0.75      # Moderate fill - accept until 75% full
    VENUE_FILL_LOW = 0.7            # Low fill - accept until 70% full
    VENUE_FILL_VERY_LOW = 0.65      # Very low fill - accept until 65% full
    VENUE_FILL_MINIMAL = 0.6        # Minimal fill - accept until 60% full
    VENUE_FILL_VERY_MINIMAL = 0.5  # Very minimal fill - accept until 50% full
    VENUE_FILL_EMPTY = 0.05         # Empty venue - accept if under 5% full
    VENUE_FILL_VERY_EMPTY = 0.02    # Very empty - accept if under 2% full
    VENUE_FILL_EXTREMELY_EMPTY = 0.005  # Extremely empty - accept if under 0.5% full
    VENUE_FILL_ULTRA_EMPTY = 0.002      # Ultra empty - accept if under 0.2% full
    
    # Deficit thresholds - when to adjust behavior based on how far behind we are
    DEFICIT_VERY_HIGH = 200         # Very high deficit threshold
    DEFICIT_HIGH = 150              # High deficit threshold
    DEFICIT_MODERATE_HIGH = 100     # Moderate-high deficit threshold
    DEFICIT_MODERATE = 50           # Moderate deficit threshold
    DEFICIT_LOW = 20                # Low deficit threshold
    DEFICIT_VERY_LOW = 15           # Very low deficit threshold
    DEFICIT_MINIMAL = 10            # Minimal deficit threshold
    DEFICIT_TINY = 8                # Tiny deficit threshold
    DEFICIT_MICRO = 5               # Micro deficit threshold
    
    # Over-target multipliers - how much over target to allow before rejecting
    OVER_TARGET_HIGH = 1.1          # Allow 20% over target (for creative)
    OVER_TARGET_MODERATE = 1.1      # Allow 10% over target (for most attributes)
    
    # Well_connected skip strategy parameters
    WELL_CONNECTED_SKIP_MODULO = 15  # Accept every Nth well_connected person (more restrictive)
    WELL_CONNECTED_CRITICAL_RATIO = 0.10  # Accept skipped ones if under this % of target (more restrictive)
    
    # Multi-attribute thresholds
    MIN_ATTRIBUTES_FOR_STRATEGY_2 = 3  # Minimum attributes for Strategy 2
    MIN_NEEDED_ATTRIBUTES = 2          # Minimum needed attributes for Strategy 3
    MIN_CRITICAL_ATTRIBUTES = 2         # Minimum critical attributes needed
    
    # Default correlation values (fallbacks if not in attribute_statistics)
    DEFAULT_TECH_BERLIN_CORR = -0.65    # techno_lover ↔ berlin_local correlation
    DEFAULT_WELL_BERLIN_CORR = 0.57     # well_connected ↔ berlin_local correlation
    DEFAULT_TECH_WELL_CORR = -0.47      # techno_lover ↔ well_connected correlation
    
    # Default constraint minimums (fallbacks if not in constraints)
    DEFAULT_TECHNO_MIN = 650
    DEFAULT_WELL_CONNECTED_MIN = 450
    DEFAULT_CREATIVE_MIN = 300
    DEFAULT_BERLIN_LOCAL_MIN = 750
    
    # ============================================================================
    # END OF TUNABLE PARAMETERS
    # ============================================================================
    
    # If venue is full, reject
    if admitted_count >= MAX_VENUE_CAPACITY:
        return False
    
    # Extract attribute values for scenario 2
    is_techno_lover = person_attributes.get("techno_lover", False)
    is_well_connected = person_attributes.get("well_connected", False)
    is_creative = person_attributes.get("creative", False)
    is_berlin_local = person_attributes.get("berlin_local", False)
    
    # Get constraint requirements from constraints list
    constraint_mins = {}
    for constraint in constraints:
        constraint_mins[constraint['attribute']] = constraint['minCount']
    
    techno_min = constraint_mins.get("techno_lover", DEFAULT_TECHNO_MIN)
    well_connected_min = constraint_mins.get("well_connected", DEFAULT_WELL_CONNECTED_MIN)
    creative_min = constraint_mins.get("creative", DEFAULT_CREATIVE_MIN)
    berlin_local_min = constraint_mins.get("berlin_local", DEFAULT_BERLIN_LOCAL_MIN)
    
    # Get current counts
    techno_count = attribute_counts.get("techno_lover", 0)
    well_connected_count = attribute_counts.get("well_connected", 0)
    creative_count = attribute_counts.get("creative", 0)
    berlin_local_count = attribute_counts.get("berlin_local", 0)
    
    # Extract correlation information if available
    correlations = {}
    if attribute_statistics and "correlations" in attribute_statistics:
        correlations = attribute_statistics["correlations"]
    
    # Key correlations for decision making:
    # techno_lover ↔ berlin_local: -0.65 (strong negative - rare to have both)
    tech_berlin_corr = correlations.get("techno_lover", {}).get("berlin_local", DEFAULT_TECH_BERLIN_CORR)
    # well_connected ↔ berlin_local: +0.57 (strong positive - helps get berlin_local)
    well_berlin_corr = correlations.get("well_connected", {}).get("berlin_local", DEFAULT_WELL_BERLIN_CORR)
    # techno_lover ↔ well_connected: -0.47 (moderate negative)
    tech_well_corr = correlations.get("techno_lover", {}).get("well_connected", DEFAULT_TECH_WELL_CORR)
    
    # Calculate progress towards constraints (0.0 to 1.0+)
    techno_progress = techno_count / techno_min if techno_min > 0 else 1.0
    well_connected_progress = well_connected_count / well_connected_min if well_connected_min > 0 else 1.0
    creative_progress = creative_count / creative_min if creative_min > 0 else 1.0
    berlin_local_progress = berlin_local_count / berlin_local_min if berlin_local_min > 0 else 1.0
    
    # Calculate how full the venue is (0.0 to 1.0)
    venue_fill_ratio = admitted_count / MAX_VENUE_CAPACITY
    
    # Calculate remaining capacity
    remaining_capacity = MAX_VENUE_CAPACITY - admitted_count
    
    # Calculate deficits (how many more we need)
    techno_deficit = techno_min - techno_count
    well_connected_deficit = well_connected_min - well_connected_count
    creative_deficit = creative_min - creative_count
    berlin_deficit = berlin_local_min - berlin_local_count
    
    # Count how many attributes this person has that we need
    needed_attributes = 0
    if is_techno_lover and techno_count < techno_min:
        needed_attributes += 1
    if is_well_connected and well_connected_count < well_connected_min:
        needed_attributes += 1
    if is_creative and creative_count < creative_min:
        needed_attributes += 1
    if is_berlin_local and berlin_local_count < berlin_local_min:
        needed_attributes += 1
    
    # Count total attributes this person has (for multi-attribute scoring)
    total_attributes = sum([is_techno_lover, is_well_connected, is_creative, is_berlin_local])
    
    # CORRELATION-BASED PRIORITY: Check for rare valuable combinations
    # techno_lover + berlin_local is extremely rare (correlation -0.65)
    # This combination is HIGHLY valuable since we need both but they rarely co-occur
    has_rare_tech_berlin_combo = is_techno_lover and is_berlin_local
    # well_connected + berlin_local is common (correlation +0.57) - still valuable
    has_well_berlin_combo = is_well_connected and is_berlin_local
    
    # Strategy 1: Always accept creative people (too rare - only 6.23% frequency)
    # This is CRITICAL - we need at least 300 people with creative attribute
    # (these 300 can also have other attributes like well_connected, techno_lover, etc.)
    # Since only 6.23% of population has creative, we MUST accept ALL creative people
    # until we have 300, even if venue is full. We'll reject others to make room if needed
    if is_creative:
        # ALWAYS accept until we reach the minimum (300)
        # This is the rarest attribute and we need at least 300 people with it
        # NEVER reject a creative person until we have 300, even if venue is 100% full
        if creative_count < creative_min:
            return True
        
        # If we already have enough creative (300+), be VERY selective
        # Only accept if we're still significantly behind on other critical constraints
        # and venue has plenty of room
        if creative_count >= creative_min:
            # Check if we still need berlin_local or techno_lover
            berlin_deficit = berlin_local_min - berlin_local_count
            techno_deficit = techno_min - techno_count
            
            if berlin_local_count < berlin_local_min or techno_count < techno_min:
                # Still need other critical attributes - but be selective
                # Only accept if we're significantly behind (not just a few short)
                if berlin_deficit > DEFICIT_MODERATE or techno_deficit > DEFICIT_MODERATE:
                    # Significantly behind - accept if venue has room
                    if venue_fill_ratio < VENUE_FILL_MID:
                        return True
                elif berlin_deficit > DEFICIT_LOW or techno_deficit > DEFICIT_LOW:
                    # Moderately behind - accept if venue has plenty of room
                    if venue_fill_ratio < VENUE_FILL_MID_EARLY:
                        return True
            # Only accept if venue is not too full and we're not way over target
            if venue_fill_ratio < VENUE_FILL_MID and creative_count < creative_min * 1.03:
                return True
            return False
        return False
    
    # Strategy 2: Accept people with multiple attributes (even if some are already met)
    # People with 3+ attributes are valuable, but prioritize those with critical attributes we need
    if total_attributes >= MIN_ATTRIBUTES_FOR_STRATEGY_2:
        # CORRELATION-BASED: Prioritize rare techno_lover + berlin_local combination
        # This is extremely rare (correlation -0.65) and highly valuable
        if has_rare_tech_berlin_combo:
            # This is a RARE and VALUABLE combination - almost always accept
            # Even if we're over on one, the other is likely still needed
            if techno_count < techno_min or berlin_local_count < berlin_local_min:
                # Need at least one - always accept
                return True
            # Both met, but still valuable - accept unless venue is 99%+ full
            if venue_fill_ratio < VENUE_FILL_CRITICAL:
                return True
            return False
        
        # Check if they have critical attributes we still need
        has_needed_creative = is_creative and creative_count < creative_min
        has_needed_berlin = is_berlin_local and berlin_local_count < berlin_local_min
        has_needed_techno = is_techno_lover and techno_count < techno_min
        
        # If they have critical attributes we need, accept more freely
        if has_needed_creative or has_needed_berlin or has_needed_techno:
            # Accept if venue is not 99%+ full
            if venue_fill_ratio < VENUE_FILL_CRITICAL:
                return True
            # Very late game: only reject if we're way over on ALL their critical attributes
            all_critical_over = True
            if has_needed_techno and techno_count < techno_min * OVER_TARGET_MODERATE:
                all_critical_over = False
            if has_needed_berlin and berlin_local_count < berlin_local_min * OVER_TARGET_MODERATE:
                all_critical_over = False
            if has_needed_creative and creative_count < creative_min * OVER_TARGET_MODERATE:
                all_critical_over = False
            if not all_critical_over:
                return True
            return False
        
        # They have 3+ attributes but none are critical attributes we need
        # Reject almost all - only accept if venue is very empty (very early game)
        # Check creative deficit to be even more selective
        creative_deficit = creative_min - creative_count
        
        # If we're behind on creative, reject all to save space
        if creative_deficit > DEFICIT_MODERATE:
            return False
        
        # Unless they have well_connected - then reject even more aggressively
        if is_well_connected:
            # If they have well_connected and we're already over, reject
            if well_connected_count >= well_connected_min:
                return False
            # Only accept if venue is extremely empty
            if venue_fill_ratio < VENUE_FILL_VERY_EMPTY:
                return True
            return False
        
        # Only accept if venue is very empty (early game)
        if venue_fill_ratio < VENUE_FILL_EMPTY:
            return True
        return False
    
    # Strategy 3: Accept people with 2+ needed attributes
    # These are valuable, prioritize those with critical attributes we need
    if needed_attributes >= MIN_NEEDED_ATTRIBUTES:
        # CORRELATION-BASED: Prioritize rare techno_lover + berlin_local combination
        # This is extremely rare (correlation -0.65) - if we need both, ALWAYS accept
        if has_rare_tech_berlin_combo:
            if techno_count < techno_min and berlin_local_count < berlin_local_min:
                # Need BOTH - this is extremely valuable, always accept
                return True
            # Need at least one - still very valuable
            if techno_count < techno_min or berlin_local_count < berlin_local_min:
                if venue_fill_ratio < VENUE_FILL_CRITICAL:
                    return True
                return False
        
        # CORRELATION-BASED: well_connected + berlin_local is common (correlation +0.57)
        # Still valuable, but less rare than tech+berlin
        if has_well_berlin_combo:
            if berlin_local_count < berlin_local_min:
                # Need berlin_local - well_connected helps (positive correlation)
                # Accept more freely since this combo is more common
                if venue_fill_ratio < VENUE_FILL_MID_LATE:
                    return True
                # Late game: only if we still need berlin_local
                if berlin_local_count < berlin_local_min * OVER_TARGET_MODERATE:
                    return True
                return False
        
        # Check which critical attributes we need
        has_needed_creative = is_creative and creative_count < creative_min
        has_needed_berlin = is_berlin_local and berlin_local_count < berlin_local_min
        has_needed_techno = is_techno_lover and techno_count < techno_min
        critical_needed = sum([has_needed_creative, has_needed_berlin, has_needed_techno])
        
        if critical_needed >= MIN_CRITICAL_ATTRIBUTES:
            # Both are critical - accept until venue is 99%+ full
            if venue_fill_ratio < VENUE_FILL_CRITICAL:
                return True
            return False
        
        # If at least one is critical, accept but be more selective if we're behind on creative
        if critical_needed >= 1:
            # Check creative deficit
            creative_deficit = creative_min - creative_count
            
            # If we're far behind on creative, be more selective to save space
            if creative_deficit > DEFICIT_MODERATE_HIGH:
                if venue_fill_ratio < VENUE_FILL_LOW:
                    return True
                return False
            # If we're moderately behind on creative, be somewhat selective
            if creative_deficit > DEFICIT_MODERATE:
                if venue_fill_ratio < VENUE_FILL_MID_EARLY:
                    return True
                return False
            # Otherwise, accept more freely
            if venue_fill_ratio < VENUE_FILL_MID_LATE:
                return True
            # Late game: only accept if we're not way over on their critical attributes
            if has_needed_techno and techno_count >= techno_min * OVER_TARGET_MODERATE:
                return False
            if has_needed_berlin and berlin_local_count >= berlin_local_min * OVER_TARGET_MODERATE:
                return False
            if has_needed_creative and creative_count >= creative_min * OVER_TARGET_MODERATE:
                return False
            return True
        
        # Neither is critical (e.g., well_connected + something else)
        # Reject almost all - only accept if venue is very empty (very early game)
        # If it includes well_connected and we're over, reject even more aggressively
        if is_well_connected and well_connected_count >= well_connected_min:
            return False
        
        if venue_fill_ratio < VENUE_FILL_EMPTY:
            return True
        return False
    
    # Strategy 4: Handle individual attributes based on needs
    # Priority order: berlin_local > techno_lover > well_connected (since well_connected is already met)
    
    # berlin_local (need 750, only 39.8% frequency - need to be VERY aggressive)
    # CORRELATION INSIGHT: techno_lover ↔ berlin_local: -0.65 (strong negative)
    # This means techno_lover people WON'T help us get berlin_local
    # We MUST be very aggressive about accepting berlin_local separately
    # This is CRITICAL - we need at least 750 people with berlin_local attribute
    # (these 750 can also have other attributes like well_connected, techno_lover, etc.)
    # Since only 39.8% of population has berlin_local, we need to be aggressive
    # BUT: be more selective - ALWAYS prioritize creative first, then berlin_local
    if is_berlin_local:
        # ALWAYS accept until we reach the minimum (750)
        # Since techno_lover and berlin_local are negatively correlated,
        # we can't rely on techno_lover people to also have berlin_local
        # We need to accept berlin_local people aggressively, but ALWAYS prioritize creative
        if berlin_local_count < berlin_local_min:
            # Account for creative deficit (ALWAYS prioritize creative first)
            if creative_deficit > DEFICIT_MODERATE_HIGH:
                # Very far behind on creative - only accept berlin_local if venue is very empty
                # This keeps venue from filling up, allowing us to search longer for creative
                if venue_fill_ratio < VENUE_FILL_VERY_LOW:
                    return True
                return False
            if creative_deficit > DEFICIT_MODERATE:
                # Moderately behind on creative - be selective with berlin_local
                if venue_fill_ratio < VENUE_FILL_LOW:
                    return True
                return False
            if creative_deficit > DEFICIT_LOW:
                # Somewhat behind on creative - still be somewhat selective
                if venue_fill_ratio < VENUE_FILL_MODERATE:
                    return True
                return False
            if creative_deficit > DEFICIT_MINIMAL:
                # Slightly behind on creative - be somewhat selective
                if venue_fill_ratio < VENUE_FILL_MID_EARLY:
                    return True
                return False
            # Creative is very close to target - accept berlin_local more freely
            if venue_fill_ratio < VENUE_FILL_MID:
                return True
            return False
        
        # If we already have enough berlin_local (750+), be VERY selective
        # Only accept if we still need other critical attributes (creative, techno_lover)
        if berlin_local_count >= berlin_local_min:
            # Check if we still need creative or techno_lover
            creative_deficit = creative_min - creative_count
            techno_deficit = techno_min - techno_count
            
            if creative_count < creative_min or techno_count < techno_min:
                # Still need other critical attributes - but be selective
                # Only accept if we're significantly behind (not just a few short)
                if creative_deficit > DEFICIT_MODERATE or techno_deficit > DEFICIT_MODERATE:
                    # Significantly behind - accept if venue has room
                    if venue_fill_ratio < VENUE_FILL_MID:
                        return True
                elif creative_deficit > DEFICIT_LOW or techno_deficit > DEFICIT_LOW:
                    # Moderately behind - accept if venue has plenty of room
                    if venue_fill_ratio < VENUE_FILL_MID_EARLY:
                        return True
            # Only accept if venue is not too full and we're not way over target
            if venue_fill_ratio < VENUE_FILL_MID and berlin_local_count < berlin_local_min * 1.03:
                return True
            return False
        return False
    
    # techno_lover (need 650, 62.65% frequency - need to be aggressive)
    # CORRELATION INSIGHT: techno_lover ↔ berlin_local: -0.65 (strong negative)
    # This means techno_lover people are LESS likely to also have berlin_local
    # We need to be careful not to fill venue with techno_lover if we still need berlin_local
    # But also need to prioritize creative people
    if is_techno_lover:
        # ALWAYS accept until we reach the minimum (650)
        if techno_count < techno_min:
            # CORRELATION-BASED: Since techno_lover and berlin_local are negatively correlated,
            # if we're far behind on berlin_local, be more selective with techno_lover
            # This ensures we leave room for berlin_local people (who won't have techno_lover)
            if berlin_deficit > DEFICIT_VERY_HIGH:
                # Very far behind on berlin_local - be selective to save space
                # We need to accept berlin_local people separately (they won't have techno_lover)
                if venue_fill_ratio < VENUE_FILL_MINIMAL:
                    return True
                return False
            
            # If we're far behind on creative, be VERY selective to save space
            if creative_deficit > DEFICIT_MODERATE_HIGH:
                # Only accept if venue is not too full (early/mid game)
                # This keeps venue from filling up, allowing us to search longer
                if venue_fill_ratio < VENUE_FILL_VERY_MINIMAL:
                    return True
                return False
            # If we're moderately behind on creative, be selective
            if creative_deficit > DEFICIT_MODERATE:
                # Accept if venue is not too full
                if venue_fill_ratio < VENUE_FILL_VERY_LOW:
                    return True
                return False
            # If we're far behind on berlin_local, be somewhat selective
            if berlin_deficit > DEFICIT_HIGH:
                # Accept if venue is not too full
                if venue_fill_ratio < VENUE_FILL_MODERATE:
                    return True
                return False
            # Otherwise, accept more freely - we need techno_lovers too
            if venue_fill_ratio < VENUE_FILL_MID_EARLY:
                return True
            return False
        
        # If we already have enough techno_lover (650+), be selective
        # Only accept if we're close to meeting critical constraints
        if creative_deficit < DEFICIT_MODERATE and berlin_deficit < DEFICIT_MODERATE_HIGH:
            # And venue is not too full
            if venue_fill_ratio < VENUE_FILL_MID:
                return True
        return False
    
    # well_connected (need 450, 47% frequency)
    # CORRELATION INSIGHT: well_connected ↔ berlin_local: +0.57 (strong positive)
    # This means well_connected people are MORE likely to also have berlin_local
    # Accepting well_connected can help us get berlin_local too
    # However, well_connected ↔ techno_lover: -0.47 (moderate negative)
    if is_well_connected:
        # EARLY GAME RESTRICTION: Reject well_connected aggressively when creative count is low
        # This allows us to accumulate creatives before filling venue with well_connected
        # Creative is the rarest attribute (6.23% frequency) - we MUST prioritize it early
        if creative_count < creative_min:
            # Still accumulating creatives - be EXTREMELY restrictive on well_connected
            # Reject almost all well_connected people to maximize space for creatives
            creative_progress = creative_count / creative_min if creative_min > 0 else 1.0
            
            # Only accept well_connected if ALL of these conditions are met:
            # 1. We're extremely far behind on well_connected (less than 10% of target)
            # 2. AND venue is extremely empty (less than 20% full)
            # 3. AND we have at least 80% of creative target
            if well_connected_count < well_connected_min * 0.1:
                # Extremely far behind on well_connected - only accept if venue is extremely empty
                # and we have at least 80% of creative target
                if venue_fill_ratio < VENUE_FILL_VERY_MINIMAL and creative_progress >= 0.8:
                    # Continue to normal logic below - but will be checked again
                    pass
                else:
                    # Reject to save space for creatives
                    return False
            else:
                # Not extremely behind on well_connected - ALWAYS reject to save space for creatives
                # No exceptions - we need to prioritize creative accumulation
                return False
        
        # EARLY REJECTION: If we're already over target, reject immediately
        # This prevents accepting well_connected when we're already over (e.g., 667 vs 450 needed)
        if well_connected_count >= well_connected_min:
            # Already have enough well_connected - be VERY restrictive
            # Only accept if they have other critical attributes AND we're not behind on creative
            # Check if they also have berlin_local (which we still need) and venue has room
            # BUT: ALWAYS prioritize creative and berlin_local over well_connected
            if is_berlin_local and berlin_local_count < berlin_local_min:
                # They have berlin_local which we need - but be very selective
                # Only accept if:
                # 1. We're not behind on creative
                # 2. We're not way over on well_connected (max 3% over)
                # 3. Venue has room
                if creative_deficit > DEFICIT_MINIMAL:
                    # Behind on creative - reject to save space
                    return False
                if well_connected_count < well_connected_min * 1.03 and venue_fill_ratio < VENUE_FILL_MID:
                    return True
            # Reject all other well_connected people when we're already over target
            return False
        
        # STRATEGY: Skip most, accept every Nth well_connected person to slow venue filling
        # This allows more time to search for rare creative people
        # well_connected_encountered tracks total encounters (not just admitted)
        if well_connected_encountered > 0 and well_connected_encountered % WELL_CONNECTED_SKIP_MODULO == 0:
            # This is every Nth well_connected person (15th, 30th, 45th...)
            # Only accept if we still need well_connected or if it helps with other constraints
            # BUT: ALWAYS prioritize creative first, especially early in the game
            if well_connected_count < well_connected_min:
                # Still need well_connected - but ALWAYS prioritize creative first
                # Early game: if creative count is low, be EXTREMELY restrictive
                if creative_count < creative_min:
                    # Still accumulating creatives - be EXTREMELY restrictive
                    # Only accept if venue is extremely empty and we have high creative progress
                    creative_progress = creative_count / creative_min if creative_min > 0 else 1.0
                    if venue_fill_ratio < VENUE_FILL_VERY_MINIMAL and creative_progress >= 0.85:
                        # Venue is very empty (< 30% full) and we have 85%+ of creative target
                        # Continue to normal logic below - but will be checked again
                        pass
                    else:
                        # Reject to save space for creatives
                        return False
                
                # If we're behind on creative, reject to save space
                if creative_deficit > DEFICIT_MINIMAL:
                    # Behind on creative - reject to save space
                    return False
                # Also check if we're close to target - if so, be more selective
                if well_connected_count >= well_connected_min * 0.9:
                    # Close to target (90%+) - be more selective, only accept if venue has room
                    if venue_fill_ratio < VENUE_FILL_MID_EARLY:
                        return True
                    return False
                # Continue to normal logic below
                pass
            elif berlin_deficit > DEFICIT_MODERATE_HIGH and venue_fill_ratio < VENUE_FILL_MID:
                # Need berlin_local and well_connected helps (positive correlation)
                # But only if we're not behind on creative
                if creative_deficit > DEFICIT_MINIMAL:
                    return False
                # Also check if we're still accumulating creatives
                if creative_count < creative_min:
                    # Still accumulating creatives - be EXTREMELY restrictive
                    creative_progress = creative_count / creative_min if creative_min > 0 else 1.0
                    if creative_progress < 0.85:
                        # Less than 85% of creative target - reject to save space
                        return False
                    # Even if 85%+, only accept if venue is very empty
                    if venue_fill_ratio >= VENUE_FILL_VERY_MINIMAL:
                        return False
                return True
            else:
                # Already have enough well_connected and don't need berlin_local urgently
                # Reject to save space for creative people
                return False
        elif well_connected_encountered > 0:
            # This is not every Nth well_connected person (1st-14th, 16th-29th, etc.)
            # Skip/reject to slow venue filling and allow more search time for creatives
            # Only make exception if we're critically behind on well_connected
            if well_connected_count < well_connected_min * WELL_CONNECTED_CRITICAL_RATIO:
                # Very far behind (less than 10% of target) - accept even skipped ones
                # BUT: only if we're not behind on creative AND we have high creative progress
                if creative_deficit > DEFICIT_MINIMAL:
                    return False
                # Early game: if creative count is low, be EXTREMELY restrictive
                if creative_count < creative_min:
                    creative_progress = creative_count / creative_min if creative_min > 0 else 1.0
                    if creative_progress < 0.85:
                        # Less than 85% of creative target - reject to save space
                        return False
                    # Even if 85%+, only accept if venue is very empty
                    if venue_fill_ratio >= VENUE_FILL_VERY_MINIMAL:
                        return False
                pass  # Continue to normal logic below
            else:
                # Skip this one to save space and allow longer search for creatives
                return False
        
        # CORRELATION-BASED: If we need berlin_local, well_connected people are more likely to have it
        # This makes well_connected more valuable when we need berlin_local
        # BUT: ALWAYS prioritize creative first, especially early in the game
        if well_connected_count < well_connected_min:
            # Need well_connected - but ALWAYS prioritize creative first
            # Early game: if creative count is low, be EXTREMELY restrictive
            if creative_count < creative_min:
                # Still accumulating creatives - be EXTREMELY restrictive
                creative_progress = creative_count / creative_min if creative_min > 0 else 1.0
                # Only accept if we have at least 85% of creative target AND venue is very empty
                if creative_progress < 0.85 or venue_fill_ratio >= VENUE_FILL_VERY_MINIMAL:
                    # Reject to save space for creatives
                    return False
            
            if creative_deficit > DEFICIT_MINIMAL:
                # Behind on creative - reject to save space
                return False
            
            # Creative is close to target - now consider well_connected
            # But be more selective if we're close to well_connected target
            if well_connected_count >= well_connected_min * 0.9:
                # Close to target (90%+) - be very selective
                if berlin_deficit > DEFICIT_MODERATE_HIGH:
                    # Far behind on berlin_local - accept if venue has room
                    if venue_fill_ratio < VENUE_FILL_MID:
                        return True
                return False
            
            if berlin_deficit > DEFICIT_MODERATE_HIGH:
                # Far behind on berlin_local - well_connected helps (positive correlation)
                # Accept if venue has room
                if venue_fill_ratio < VENUE_FILL_MID_EARLY:
                    return True
                return False
            
            # Not far behind on berlin_local - be very selective
            # Only accept if we're close on all critical constraints
            if creative_deficit < DEFICIT_MINIMAL and berlin_deficit < DEFICIT_LOW and techno_deficit < DEFICIT_VERY_LOW:
                # And venue is not too full
                if venue_fill_ratio < VENUE_FILL_EARLY:
                    return True
            return False
        
        # This section should not be reached if we're already over target (handled by early rejection above)
        # But keep as fallback for edge cases
        # Already have enough well_connected - reject almost all
        # Only accept if they have berlin_local (which we need) and we're not way over
        if well_connected_count >= well_connected_min:
            if is_berlin_local and berlin_local_count < berlin_local_min:
                # They have berlin_local which we need - only accept if not way over on well_connected
                # AND if we're not behind on creative
                if creative_deficit > DEFICIT_MINIMAL:
                    return False
                if well_connected_count < well_connected_min * 1.03 and venue_fill_ratio < VENUE_FILL_MID:
                    return True
            return False
        
        # Fallback: reject all other cases
        return False
    
    # Strategy 5: Person has no needed attributes
    # Reject if they don't help with any constraint
    # Be EXTREMELY aggressive about rejecting to make room for critical attributes
    # Especially reject to save space for creative people and allow us to search longer
    if needed_attributes == 0:
        # Calculate how far behind we are on creative (most critical)
        creative_deficit = creative_min - creative_count
        
        # If we're behind on creative, reject almost all non-creative people
        # to save space for creative people and allow us to process more people
        if creative_deficit > DEFICIT_MODERATE:
            # Reject all - we need to save every spot for creative people
            # This keeps venue from filling up, allowing us to search longer
            return False
        
        # If we're moderately behind on creative, be very selective
        if creative_deficit > DEFICIT_LOW:
            # Only accept if venue is extremely empty (very early game)
            if venue_fill_ratio < VENUE_FILL_ULTRA_EMPTY:
                return True
            return False
        
        # If we're close on creative, still be very selective
        # Only accept in extremely early game if venue is very empty
        if venue_fill_ratio < VENUE_FILL_EXTREMELY_EMPTY:
            return True
        # Reject everyone else - we need the space for critical attributes
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

