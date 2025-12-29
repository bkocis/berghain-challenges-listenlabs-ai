#!/usr/bin/env python3
"""
Script to create a new game and store game information including:
- Player ID
- Scenario ID
- Game ID
- Constraints
- Attribute statistics (relative frequencies and correlations)
"""

import requests
import json
import ssl
import urllib3
import os
from typing import Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager

# Disable SSL warnings (since we're handling SSL issues)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration
BASE_URL = "https://berghain.challenges.listenlabs.ai"  # Update this if needed
PLAYER_ID = "88539b16-3002-47ff-a234-10d9474cbb9c"  # Your unique player ID
SCENARIO_ID = 3  # Choose 1, 2, or 3

# Scenario directory for file paths
SCENARIO_DIR = os.path.dirname(os.path.abspath(__file__))


class SSLAdapter(HTTPAdapter):
    """Custom HTTPAdapter with more permissive SSL settings."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)


def create_new_game(base_url: str, player_id: str, scenario_id: int) -> Dict[str, Any]:
    """
    Create a new game by calling the /new-game endpoint.
    
    Args:
        base_url: Base URL of the API
        player_id: Unique player identifier
        scenario_id: Scenario number (1, 2, or 3)
    
    Returns:
        Dictionary containing the API response
    """
    endpoint = f"{base_url}/new-game"
    params = {
        "scenario": scenario_id,
        "playerId": player_id
    }
    
    print(f"Creating new game...")
    print(f"Endpoint: {endpoint}")
    print(f"Parameters: {params}")
    
    # Create a session with custom SSL adapter and retry logic
    session = requests.Session()
    
    # Configure retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    
    # Mount the custom SSL adapter
    adapter = SSLAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    
    try:
        response = session.get(endpoint, params=params, timeout=30, verify=False)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error making request: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")
        raise
    finally:
        session.close()


def store_game_info(response_data: Dict[str, Any], player_id: str, scenario_id: int) -> Dict[str, Any]:
    """
    Extract and organize game information from the API response.
    
    Args:
        response_data: JSON response from the API
        player_id: Player ID used for the request
        scenario_id: Scenario ID used for the request
    
    Returns:
        Dictionary containing all stored game information
    """
    game_info = {
        "playerId": player_id,
        "scenarioId": scenario_id,
        "gameId": response_data.get("gameId"),
        "constraints": response_data.get("constraints", []),
        "attributeStatistics": {
            "relativeFrequencies": response_data.get("attributeStatistics", {}).get("relativeFrequencies", {}),
            "correlations": response_data.get("attributeStatistics", {}).get("correlations", {})
        }
    }
    
    return game_info


def save_to_file(game_info: Dict[str, Any], filename: str = None):
    """
    Save game information to a JSON file, keyed by gameId.
    Supports multiple games - each game is stored under its gameId.
    Automatically migrates old format (single game) to new format (dict keyed by gameId).
    
    Args:
        game_info: Dictionary containing game information
        filename: Output filename
    """
    if filename is None:
        filename = os.path.join(SCENARIO_DIR, "game_info.json")
    
    # Load existing games if file exists
    all_games = {}
    try:
        with open(filename, 'r') as f:
            existing_data = json.load(f)
            
            # Check if it's old format (single game object)
            if isinstance(existing_data, dict) and "gameId" in existing_data and isinstance(existing_data["gameId"], str):
                # Migrate old format to new format
                old_game_id = existing_data["gameId"]
                all_games[old_game_id] = existing_data
                print(f"Migrated old format game to new format (gameId: {old_game_id})")
            else:
                # Already in new format
                all_games = existing_data
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        # If file exists but is invalid JSON, start fresh
        print(f"Warning: {filename} exists but is invalid JSON. Starting fresh.")
        all_games = {}
    
    # Store this game under its gameId
    game_id = game_info.get("gameId")
    if game_id:
        all_games[game_id] = game_info
        with open(filename, 'w') as f:
            json.dump(all_games, f, indent=2)
        print(f"\nGame information saved to {filename} (gameId: {game_id})")
        print(f"Total games stored: {len(all_games)}")
    else:
        print("Warning: No gameId found in game_info, cannot save properly")


def print_game_info(game_info: Dict[str, Any]):
    """
    Print game information in a readable format.
    
    Args:
        game_info: Dictionary containing game information
    """
    print("\n" + "="*60)
    print("GAME INFORMATION")
    print("="*60)
    print(f"Player ID: {game_info['playerId']}")
    print(f"Scenario ID: {game_info['scenarioId']}")
    print(f"Game ID: {game_info['gameId']}")
    
    print("\nConstraints:")
    for constraint in game_info['constraints']:
        print(f"  - {constraint['attribute']}: minimum {constraint['minCount']} required")
    
    print("\nAttribute Statistics:")
    print("\nRelative Frequencies:")
    for attr_id, freq in game_info['attributeStatistics']['relativeFrequencies'].items():
        print(f"  - {attr_id}: {freq:.4f} ({freq*100:.2f}%)")
    
    print("\nCorrelations:")
    correlations = game_info['attributeStatistics']['correlations']
    for attr1, attr2_dict in correlations.items():
        for attr2, corr_value in attr2_dict.items():
            print(f"  - {attr1} <-> {attr2}: {corr_value:.4f}")
    
    print("="*60)


def main():
    """Main function to create a game and store the information."""
    # Create new game
    response_data = create_new_game(BASE_URL, PLAYER_ID, SCENARIO_ID)
    
    # Store game information
    game_info = store_game_info(response_data, PLAYER_ID, SCENARIO_ID)
    
    # Print information
    print_game_info(game_info)
    
    # Save to file (will be stored keyed by gameId)
    save_to_file(game_info)
    
    # Check if this is a retry of an existing game
    game_id = game_info.get("gameId")
    if game_id:
        # Load all games to check
        try:
            game_info_file = os.path.join(SCENARIO_DIR, "game_info.json")
            with open(game_info_file, 'r') as f:
                all_games = json.load(f)
                if isinstance(all_games, dict) and game_id in all_games:
                    existing_attempts = len(all_games.get(game_id, {}).get("attempts", []))
                    if existing_attempts > 0:
                        print(f"\nNote: This gameId already exists with {existing_attempts} previous attempt(s)")
        except:
            pass
    
    # Return game_info for programmatic use
    return game_info


if __name__ == "__main__":
    game_info = main()
    
    # Example: Access stored values
    print("\n" + "="*60)
    print("ACCESSING STORED VALUES:")
    print("="*60)
    print(f"Player ID: {game_info['playerId']}")
    print(f"Scenario ID: {game_info['scenarioId']}")
    print(f"Game ID: {game_info['gameId']}")
    print(f"Number of constraints: {len(game_info['constraints'])}")
    print(f"Number of attributes: {len(game_info['attributeStatistics']['relativeFrequencies'])}")
