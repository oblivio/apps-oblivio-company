# MyCircles Easy Integration Guide

This guide shows how to easily integrate Game Portal into your mycircles website. Every circle gets its own persistent lobby for Blackjack or Dominoes, and players can choose game style and AI player count when joining.

## Quick Start

### 1. Join Circle Lobby with Game Settings

When a user clicks "Play Blackjack" or "Play Dominoes" in a circle, use this endpoint:

**Endpoint:** `POST /experiments/game_portal/backend/mycircles/join/{circle_id}/{game_type}`

**Request Body:**
```json
{
  "player_id": "user123",
  "game_mode": "classic",  // Optional: "classic" or "boricua" for dominoes, "best_of_5" or "best_of_10" for blackjack
  "ai_count": 2            // Optional: Number of AI players (0-3)
}
```

**Example:**
```javascript
// Join circle lobby with game settings
async function joinCircleGame(circleId, gameType, userId, gameMode = null, aiCount = null) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/mycircles/join/${circleId}/${gameType}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        player_id: userId,
        game_mode: gameMode,  // Optional
        ai_count: aiCount    // Optional
      })
    }
  );
  
  if (!response.ok) {
    throw new Error('Failed to join game');
  }
  
  const result = await response.json();
  
  // Redirect to game portal
  window.location.href = result.redirect_url;
  
  return result;
}

// Usage examples:
// Join Blackjack with default settings
await joinCircleGame('circle123', 'blackjack', 'user123');

// Join Dominoes with Boricua style and 2 AI players
await joinCircleGame('circle123', 'dominoes', 'user123', 'boricua', 2);

// Join Blackjack with best_of_10 (5 wins) and 1 AI player
await joinCircleGame('circle123', 'blackjack', 'user123', 'best_of_10', 1);
```

### 2. Get Lobby Status (Display Player Count)

Display "X players waiting" in your UI:

**Endpoint:** `GET /experiments/game_portal/backend/mycircles/lobby/{circle_id}/{game_type}`

**Example:**
```javascript
// Get lobby status for display
async function getLobbyStatus(circleId, gameType) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/mycircles/lobby/${circleId}/${gameType}`
  );
  
  if (!response.ok) {
    return null;
  }
  
  const lobby = await response.json();
  return lobby;
}

// Usage: Display player count
const lobby = await getLobbyStatus('circle123', 'blackjack');
if (lobby) {
  console.log(`${lobby.player_count}/${lobby.max_players} players waiting`);
  console.log(`Game style: ${lobby.game_mode}`);
  console.log(`AI players: ${lobby.ai_count || 0}`);
}
```

### 3. Update Lobby Settings (Host Only)

Allow the host to change game style and AI player count:

**Endpoint:** `POST /experiments/game_portal/backend/mycircles/lobby/{circle_id}/{game_type}/settings`

**Request Body:**
```json
{
  "player_id": "user123",  // Must be the host
  "game_mode": "boricua",  // Optional: New game style
  "ai_count": 3            // Optional: New AI player count (0-3)
}
```

**Example:**
```javascript
// Update lobby settings (host only)
async function updateLobbySettings(circleId, gameType, userId, gameMode = null, aiCount = null) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/mycircles/lobby/${circleId}/${gameType}/settings`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        player_id: userId,
        game_mode: gameMode,
        ai_count: aiCount
      })
    }
  );
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to update settings');
  }
  
  return await response.json();
}

// Usage: Host changes game style to Boricua
await updateLobbySettings('circle123', 'dominoes', 'user123', 'boricua', null);
```

## Game Styles

### Dominoes
- **`"classic"`**: Classic dominoes (Best of 5 hands, 2-4 players)
- **`"boricua"`**: Boricua style (First to 500 points, 2v2 teams, requires 4 players)

### Blackjack
- **`"best_of_5"`**: Best of 5 rounds (3 wins needed, 2-4 players)
- **`"best_of_10"`**: Best of 10 rounds (5 wins needed, 2-4 players)

## Complete Integration Example

Here's a complete example for a mycircles circle page:

```javascript
class CircleGameIntegration {
  constructor(circleId, userId) {
    this.circleId = circleId;
    this.userId = userId;
    this.baseUrl = 'https://apps.oblivio-company.com/experiments/game_portal/backend';
  }
  
  // Join game with user-selected settings
  async joinGame(gameType, gameMode, aiCount) {
    const response = await fetch(
      `${this.baseUrl}/mycircles/join/${this.circleId}/${gameType}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          player_id: this.userId,
          game_mode: gameMode,
          ai_count: aiCount
        })
      }
    );
    
    if (!response.ok) {
      throw new Error('Failed to join game');
    }
    
    const result = await response.json();
    window.location.href = result.redirect_url;
    return result;
  }
  
  // Get lobby status for display
  async getLobbyStatus(gameType) {
    const response = await fetch(
      `${this.baseUrl}/mycircles/lobby/${this.circleId}/${gameType}`
    );
    
    if (!response.ok) {
      return null;
    }
    
    return await response.json();
  }
  
  // Update lobby settings (host only)
  async updateSettings(gameType, gameMode, aiCount) {
    const response = await fetch(
      `${this.baseUrl}/mycircles/lobby/${this.circleId}/${gameType}/settings`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          player_id: this.userId,
          game_mode: gameMode,
          ai_count: aiCount
        })
      }
    );
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to update settings');
    }
    
    return await response.json();
  }
  
  // Poll lobby status for real-time updates
  startPolling(gameType, callback, intervalMs = 2000) {
    const poll = async () => {
      const lobby = await this.getLobbyStatus(gameType);
      if (lobby) {
        callback(lobby);
      }
    };
    
    // Poll immediately
    poll();
    
    // Then poll every intervalMs
    return setInterval(poll, intervalMs);
  }
}

// Usage in your mycircles page:
const gameIntegration = new CircleGameIntegration('circle123', 'user123');

// Show game buttons with lobby status
async function renderGameButtons() {
  const blackjackLobby = await gameIntegration.getLobbyStatus('blackjack');
  const dominoesLobby = await gameIntegration.getLobbyStatus('dominoes');
  
  // Display player counts
  document.getElementById('blackjack-count').textContent = 
    `${blackjackLobby?.player_count || 0}/${blackjackLobby?.max_players || 4}`;
  document.getElementById('dominoes-count').textContent = 
    `${dominoesLobby?.player_count || 0}/${dominoesLobby?.max_players || 4}`;
}

// Handle join button click with settings form
document.getElementById('join-blackjack').addEventListener('click', async () => {
  // Show settings form
  const gameMode = document.getElementById('game-mode').value; // 'best_of_5' or 'best_of_10'
  const aiCount = parseInt(document.getElementById('ai-count').value); // 0-3
  
  await gameIntegration.joinGame('blackjack', gameMode, aiCount);
});

// Poll for real-time updates
gameIntegration.startPolling('blackjack', (lobby) => {
  updateLobbyDisplay(lobby);
});
```

## Key Features

✅ **One Lobby Per Circle**: Each circle automatically has one persistent lobby per game type  
✅ **Persistent Lobbies**: Lobbies never get deleted, even when empty (shows 0/4 players)  
✅ **Automatic Host**: First player to join becomes host, can change game settings  
✅ **Game Style Selection**: Choose Classic/Boricua for dominoes, Best of 5/10 for blackjack  
✅ **AI Player Control**: Set 0-3 AI players when joining or updating settings  
✅ **Real-time Updates**: Poll lobby status to show live player counts  
✅ **Easy Integration**: Simple REST API, works from any website  

## API Reference

### POST `/mycircles/join/{circle_id}/{game_type}`
Join a circle's lobby with optional game settings.

**Request:**
```json
{
  "player_id": "user123",
  "game_mode": "classic",  // Optional
  "ai_count": 2            // Optional
}
```

**Response:**
```json
{
  "game_id": "LOBBY_ABC123",
  "player_id": "user123",
  "game_type": "dominoes",
  "game_mode": "classic",
  "redirect_url": "https://apps.oblivio-company.com/experiments/game_portal/?game=LOBBY_ABC123",
  "players": ["user123"],
  "player_count": 1,
  "status": "waiting",
  "min_players": 2,
  "max_players": 4
}
```

### GET `/mycircles/lobby/{circle_id}/{game_type}`
Get lobby status without joining.

**Response:**
```json
{
  "game_id": "LOBBY_ABC123",
  "game_type": "dominoes",
  "game_mode": "classic",
  "players": ["user123", "user456"],
  "player_count": 2,
  "status": "waiting",
  "min_players": 2,
  "max_players": 4,
  "can_join": true
}
```

### POST `/mycircles/lobby/{circle_id}/{game_type}/settings`
Update lobby settings (host only).

**Request:**
```json
{
  "player_id": "user123",
  "game_mode": "boricua",  // Optional
  "ai_count": 3            // Optional
}
```

**Response:**
```json
{
  "game_id": "LOBBY_ABC123",
  "game_type": "dominoes",
  "game_mode": "boricua",
  "ai_count": 3,
  "players": ["user123"],
  "player_count": 1,
  "status": "waiting",
  "min_players": 4,
  "max_players": 4,
  "message": "Lobby settings updated successfully"
}
```

## Notes

- **Game Modes**: 
  - Dominoes: `"classic"` (2-4 players) or `"boricua"` (requires 4 players, 2v2 teams)
  - Blackjack: `"best_of_5"` (3 wins needed) or `"best_of_10"` (5 wins needed)
  
- **AI Players**: Can be set to 0-3. If game requires minimum players, AI will be auto-added when starting.

- **Host Privileges**: Only the host can update lobby settings. First player to join becomes host.

- **Persistent Lobbies**: Lobbies persist forever - they never get deleted, even when empty.

