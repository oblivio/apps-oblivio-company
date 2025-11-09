# Game Portal Backend API Integration Guide

This guide explains how to integrate with the Game Portal Backend API from external applications like `mycircles.oblivio-company.com`.

## Overview

The Game Portal Backend API provides a CORS-enabled REST API that allows external applications under the `*.oblivio-company.com` domain to create and manage games. This API is accessible at:

**Base URL**: `https://<your-domain>/experiments/game_portal/backend`

## CORS Configuration

The backend API is configured to accept requests from any subdomain of `oblivio-company.com`:
- ✅ `https://mycircles.oblivio-company.com`
- ✅ `https://app.oblivio-company.com`
- ✅ `http://localhost.oblivio-company.com` (for development)
- ❌ `https://other-domain.com` (blocked)

## API Endpoints

### 1. Create a Game

Create a new game lobby.

**Endpoint**: `POST /game/create`

**Request Body**:
```json
{
  "player_id": "user123",
  "game_type": "blackjack" | "dominoes",
  "game_mode": "classic" | "boricua" | "best_of_5" | "best_of_10",
  "ai_count": 0
}
```

**Parameters**:
- `player_id` (string, required): Unique identifier for the player creating the game
- `game_type` (string, required): Type of game - `"blackjack"` or `"dominoes"`
- `game_mode` (string, optional): Game mode
  - For dominoes: `"classic"` (2 players) or `"boricua"` (4 players)
  - For blackjack: `"best_of_5"` or `"best_of_10"`
  - Default: `"classic"`
- `ai_count` (integer, optional): Number of AI players to add (0-3). Default: 0

**Response**:
```json
{
  "game_id": "ABC123",
  "player_id": "user123",
  "game_type": "blackjack",
  "game_mode": "classic",
  "players": ["user123", "AI_ABC123_0"],
  "ai_players": ["AI_ABC123_0"]
}
```

**Example**:
```javascript
const response = await fetch('https://your-domain.com/experiments/game_portal/backend/game/create', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    player_id: 'user123',
    game_type: 'blackjack',
    game_mode: 'classic',
    ai_count: 1
  })
});

const game = await response.json();
console.log('Game created:', game.game_id);
```

### 2. Join a Game

Join an existing game lobby or mid-game.

**Endpoint**: `POST /game/{game_id}/join`

**Path Parameters**:
- `game_id` (string, required): The game ID to join

**Request Body**:
```json
{
  "player_id": "user456",
  "replace_ai": "AI_ABC123_0",  // Optional: AI player ID to replace
  "as_spectator": false  // Optional: Join as spectator if game is in progress
}
```

**Parameters**:
- `player_id` (string, required): Unique identifier for the player joining
- `replace_ai` (string, optional): AI player ID to replace (for mid-game joining)
- `as_spectator` (boolean, optional): Join as spectator if game is in progress. Default: `false`

**Response**:
```json
{
  "game_id": "ABC123",
  "player_id": "user456",
  "game_type": "blackjack",
  "game_mode": "classic",
  "role": "player",  // or "spectator"
  "replaced_ai": "AI_ABC123_0"  // if replaced an AI player
}
```

**Example**:
```javascript
const gameId = 'ABC123';
const response = await fetch(`https://your-domain.com/experiments/game_portal/backend/game/${gameId}/join`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    player_id: 'user456',
    as_spectator: false
  })
});

const result = await response.json();
console.log('Joined game:', result);
```

### 3. Get Game Information

Get information about a game.

**Endpoint**: `GET /game/{game_id}`

**Path Parameters**:
- `game_id` (string, required): The game ID

**Response**:
```json
{
  "game_id": "ABC123",
  "game_type": "blackjack",
  "game_mode": "classic",
  "status": "waiting" | "in_progress" | "finished",
  "players": ["user123", "user456"],
  "ai_players": [],
  "spectators": [],
  "host_id": "user123",
  "min_players": 2,
  "max_players": 4
}
```

**Example**:
```javascript
const gameId = 'ABC123';
const response = await fetch(`https://your-domain.com/experiments/game_portal/backend/game/${gameId}`);
const game = await response.json();
console.log('Game status:', game.status);
```

### 4. Get Game State

Get the current game state (sanitized for a specific player).

**Endpoint**: `GET /game/{game_id}/state?player_id={player_id}`

**Path Parameters**:
- `game_id` (string, required): The game ID

**Query Parameters**:
- `player_id` (string, optional): Player ID to get sanitized state for

**Response** (with player_id):
```json
{
  "game_id": "ABC123",
  "game_type": "blackjack",
  "game_mode": "classic",
  "status": "in_progress",
  "game_state": {
    // Sanitized game state - only shows information visible to this player
    "current_turn_index": 0,
    "hands": {
      "user123": [{"rank": "A", "suit": "spades", "value": 11}, ...],
      "user456": {"value": 15, "status": "playing", "bet": 10}
    },
    // Other player's hands are hidden
  },
  "players": ["user123", "user456"],
  "ai_players": [],
  "spectators": []
}
```

**Response** (without player_id):
```json
{
  "game_id": "ABC123",
  "game_type": "blackjack",
  "game_mode": "classic",
  "status": "in_progress",
  "players": ["user123", "user456"],
  "ai_players": [],
  "spectators": []
}
```

**Example**:
```javascript
const gameId = 'ABC123';
const playerId = 'user123';
const response = await fetch(`https://your-domain.com/experiments/game_portal/backend/game/${gameId}/state?player_id=${playerId}`);
const gameState = await response.json();
console.log('Game state:', gameState.game_state);
```

### 5. Start a Game

Start a game (only the host can start).

**Endpoint**: `POST /game/{game_id}/start`

**Path Parameters**:
- `game_id` (string, required): The game ID

**Request Body**:
```json
{
  "player_id": "user123"
}
```

**Response**:
```json
{
  "success": true
}
```

**Example**:
```javascript
const gameId = 'ABC123';
const response = await fetch(`https://your-domain.com/experiments/game_portal/backend/game/${gameId}/start`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    player_id: 'user123'
  })
});

const result = await response.json();
console.log('Game started:', result);
```

### 6. Get Available AI Slots

Get list of AI players that can be replaced in an in-progress game.

**Endpoint**: `GET /game/{game_id}/ai-slots`

**Path Parameters**:
- `game_id` (string, required): The game ID

**Response**:
```json
{
  "ai_slots": ["AI_ABC123_0", "AI_ABC123_1"]
}
```

**Example**:
```javascript
const gameId = 'ABC123';
const response = await fetch(`https://your-domain.com/experiments/game_portal/backend/game/${gameId}/ai-slots`);
const { ai_slots } = await response.json();
console.log('Available AI slots:', ai_slots);
```

## Error Handling

All endpoints return standard HTTP status codes:

- `200 OK`: Request successful
- `400 Bad Request`: Invalid request parameters
- `404 Not Found`: Game not found
- `500 Internal Server Error`: Server error

Error responses follow this format:
```json
{
  "detail": "Error message here"
}
```

**Example Error Handling**:
```javascript
try {
  const response = await fetch('https://your-domain.com/experiments/game_portal/backend/game/create', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      player_id: 'user123',
      game_type: 'blackjack'
    })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Request failed');
  }

  const game = await response.json();
  console.log('Game created:', game);
} catch (error) {
  console.error('Error:', error.message);
}
```

## Integration Workflow for mycircles.oblivio-company.com

### Step 1: Create a Game

When a user wants to create a game in mycircles:

```javascript
async function createGame(userId, gameType = 'blackjack') {
  const response = await fetch('https://your-domain.com/experiments/game_portal/backend/game/create', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      player_id: userId,
      game_type: gameType,
      game_mode: 'classic',
      ai_count: 0  // Start with no AI, let friends join
    })
  });

  if (!response.ok) {
    throw new Error('Failed to create game');
  }

  const game = await response.json();
  return game;
}
```

### Step 2: Share Game Link

Generate a shareable link for the game:

```javascript
function getGameShareLink(gameId) {
  // Option 1: Link to your app's game page
  return `https://mycircles.oblivio-company.com/games/${gameId}`;
  
  // Option 2: Link directly to Game Portal
  return `https://your-domain.com/experiments/game_portal/?game=${gameId}`;
}
```

### Step 3: Join Game from Shared Link

When someone clicks the shared link:

```javascript
async function joinGame(gameId, userId) {
  const response = await fetch(`https://your-domain.com/experiments/game_portal/backend/game/${gameId}/join`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      player_id: userId,
      as_spectator: false
    })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to join game');
  }

  const result = await response.json();
  return result;
}
```

### Step 4: Poll Game State (Optional)

If you want to display game status in your app:

```javascript
async function pollGameState(gameId, playerId) {
  const response = await fetch(`https://your-domain.com/experiments/game_portal/backend/game/${gameId}/state?player_id=${playerId}`);
  
  if (!response.ok) {
    throw new Error('Failed to get game state');
  }

  const gameState = await response.json();
  return gameState;
}

// Poll every 2 seconds
setInterval(async () => {
  try {
    const state = await pollGameState(gameId, playerId);
    updateGameUI(state);
  } catch (error) {
    console.error('Error polling game state:', error);
  }
}, 2000);
```

### Step 5: Start Game

When the host is ready to start:

```javascript
async function startGame(gameId, hostId) {
  const response = await fetch(`https://your-domain.com/experiments/game_portal/backend/game/${gameId}/start`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      player_id: hostId
    })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to start game');
  }

  return await response.json();
}
```

## WebSocket Connection (Real-time Gameplay)

For real-time gameplay, you'll need to connect via WebSocket. The WebSocket endpoint is:

**WebSocket URL**: `wss://your-domain.com/experiments/game_portal/ws/game/{game_id}/{player_id}`

**Note**: WebSocket connections are not CORS-protected and require the user to be on the same domain or use a proxy. For external apps, you may need to:

1. Use the REST API to create/join games
2. Redirect users to the Game Portal UI for actual gameplay
3. Or implement a WebSocket proxy in your app

## Complete Integration Example

Here's a complete example for mycircles.oblivio-company.com:

```javascript
class GamePortalClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl || 'https://your-domain.com/experiments/game_portal/backend';
  }

  async createGame(userId, gameType = 'blackjack', gameMode = 'classic', aiCount = 0) {
    const response = await fetch(`${this.baseUrl}/game/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_id: userId, game_type: gameType, game_mode: gameMode, ai_count: aiCount })
    });
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  }

  async joinGame(gameId, userId, replaceAi = null, asSpectator = false) {
    const response = await fetch(`${this.baseUrl}/game/${gameId}/join`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_id: userId, replace_ai: replaceAi, as_spectator: asSpectator })
    });
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  }

  async getGame(gameId) {
    const response = await fetch(`${this.baseUrl}/game/${gameId}`);
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  }

  async getGameState(gameId, playerId = null) {
    const url = playerId 
      ? `${this.baseUrl}/game/${gameId}/state?player_id=${playerId}`
      : `${this.baseUrl}/game/${gameId}/state`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  }

  async startGame(gameId, playerId) {
    const response = await fetch(`${this.baseUrl}/game/${gameId}/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_id: playerId })
    });
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  }

  async getAISlots(gameId) {
    const response = await fetch(`${this.baseUrl}/game/${gameId}/ai-slots`);
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  }
}

// Usage
const client = new GamePortalClient();

// Create a game
const game = await client.createGame('user123', 'blackjack', 'classic', 0);
console.log('Game created:', game.game_id);

// Share link
const shareLink = `https://mycircles.oblivio-company.com/games/${game.game_id}`;

// Join game
const joinResult = await client.joinGame(game.game_id, 'user456');
console.log('Joined game:', joinResult);

// Get game state
const state = await client.getGameState(game.game_id, 'user123');
console.log('Game state:', state);

// Start game (host only)
if (game.host_id === 'user123') {
  await client.startGame(game.game_id, 'user123');
}
```

## Best Practices

1. **Player IDs**: Use unique, stable identifiers for players (e.g., user IDs from your database)
2. **Error Handling**: Always handle errors gracefully and provide user feedback
3. **Polling**: If polling game state, use reasonable intervals (2-5 seconds) to avoid rate limiting
4. **Game Links**: Store game IDs in your database and generate shareable links
5. **Security**: Validate player IDs on your side before making API calls
6. **Rate Limiting**: Be aware of rate limits and implement exponential backoff for retries

## Support

For issues or questions, contact the Game Portal team or check the main Game Portal documentation.

