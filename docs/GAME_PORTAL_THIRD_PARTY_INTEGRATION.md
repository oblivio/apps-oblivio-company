# Game Portal Third-Party Integration Guide

This guide explains how **any third-party website** can integrate with the Game Portal to create and display games. The API is designed to be flexible and work with any system - whether it's a social network, organization platform, event management system, or any other application.

## Overview

The Game Portal provides a **unified, flexible CORS-enabled backend API** that allows external sites to:
- Create persistent lobbies for any context (group, room, organization, etc.)
- Join players to lobbies with automatic redirect URLs
- Poll for game updates (players joined, game started, etc.)
- Display game information to users
- Update lobby settings (host only)
- Allow users to join games via shareable links

**Key Features:**
- ✅ Works with **any unique context identifier** (circle ID, group ID, room ID, organization ID, etc.)
- ✅ Persistent lobbies that always exist (even with 0 players)
- ✅ Automatic redirect URLs for seamless user experience
- ✅ Host-controlled settings (game mode, AI player count)
- ✅ Flexible and robust - designed for any third-party system

## API Endpoints

All backend API endpoints are accessible from `*.oblivio-company.com` domains and are mounted at:
```
https://apps.oblivio-company.com/experiments/game_portal/backend/
```

### 1. Auto-Create a Game

Create a new game and get a shareable link:

```javascript
// Create a blackjack game with 2 AI players
const response = await fetch(
  'https://apps.oblivio-company.com/experiments/game_portal/backend/new/blackjack?game_mode=best_of_5&ai_count=2',
  { method: 'GET' }
);

const gameData = await response.json();
// Returns: { game_id, player_id, game_type, game_mode, players, ai_players }

// Shareable link for users to join:
const shareLink = `https://apps.oblivio-company.com/experiments/game_portal?game=${gameData.game_id}`;
```

**Query Parameters:**
- `game_type`: `"blackjack"` or `"dominoes"` (required)
- `game_mode`: Optional - `"best_of_5"`, `"best_of_10"` for blackjack; `"classic"`, `"boricua"` for dominoes
- `ai_count`: Number of AI players (0-3, defaults to 0)
- `player_id`: Optional - if not provided, a placeholder will be created and automatically replaced when the user joins

**Note**: When a game is created without a `player_id`, a placeholder player is created. The placeholder is automatically replaced with the user's browser fingerprint when they visit the game link. Placeholders are filtered from the UI and never displayed to users.

### 2. Poll for Game Updates

Poll for real-time game updates to display on your site:

```javascript
async function pollGameUpdates(gameId) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/game/${gameId}/poll`,
    { method: 'GET' }
  );
  
  const updates = await response.json();
  
  // Updates include:
  // - status: "waiting" | "in_progress" | "round_finished" | "hand_finished" | "finished"
  // - players: Array of player objects with is_ai, is_spectator flags
  // - player_count: Number of active players
  // - can_start: Whether the game can be started
  // - is_started: Whether the game has started
  // - game_state_summary: Public game info (scores, current turn, etc.)
  
  return updates;
}

// Poll every 2 seconds
setInterval(() => {
  pollGameUpdates(gameId).then(updates => {
    updateGameDisplay(updates);
  });
}, 2000);
```

**Response Structure:**
```json
{
  "game_id": "ABC123",
  "game_type": "blackjack",
  "game_mode": "best_of_5",
  "status": "waiting",
  "host_id": "player123",
  "players": [
    { "player_id": "player123", "is_ai": false, "is_spectator": false },
    { "player_id": "AI_ABC123_0", "is_ai": true, "is_spectator": false }
  ],
  "player_count": 2,
  "spectator_count": 0,
  "min_players": 2,
  "max_players": 4,
  "can_start": true,
  "is_started": false,
  "is_finished": false,
  "game_state_summary": null  // Only populated when game is in progress
}
```

### 3. Display Game Information

Use the polling endpoint to create a live game widget:

```javascript
function createGameWidget(gameId) {
  const widget = document.createElement('div');
  widget.id = `game-widget-${gameId}`;
  widget.innerHTML = `
    <div class="game-status">Waiting for players...</div>
    <div class="players-list"></div>
    <a href="https://apps.oblivio-company.com/experiments/game_portal?game=${gameId}" 
       target="_blank" class="join-button">Join Game</a>
  `;
  
  // Poll for updates
  setInterval(async () => {
    const updates = await pollGameUpdates(gameId);
    updateWidget(updates);
  }, 2000);
  
  function updateWidget(updates) {
    const statusEl = widget.querySelector('.game-status');
    const playersEl = widget.querySelector('.players-list');
    
    // Update status
    if (updates.is_started) {
      statusEl.textContent = `Game in progress - Round ${updates.game_state_summary?.round_number || 1}`;
    } else if (updates.can_start) {
      statusEl.textContent = `Ready to start (${updates.player_count}/${updates.max_players} players)`;
    } else {
      statusEl.textContent = `Waiting for players (${updates.player_count}/${updates.min_players} needed)`;
    }
    
    // Update players list
    playersEl.innerHTML = updates.players.map(p => {
      const badge = p.is_ai ? '🤖 AI' : p.is_spectator ? '👀 Spectator' : '👤 Player';
      return `<div>${badge} ${p.player_id.substring(0, 8)}</div>`;
    }).join('');
  }
  
  return widget;
}
```

### 4. Unified Lobby API (Third-Party Integration)

The unified lobby API works with **any unique context identifier** - could be a circle ID, group ID, room ID, organization ID, or any string identifier from your system.

#### 4a. Get Lobby Status

Get lobby status without joining (perfect for displaying "X players waiting"):

```javascript
async function getLobbyStatus(contextId, gameType) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/lobby/${contextId}/${gameType}`
  );
  
  if (!response.ok) {
    return null;
  }
  
  const lobby = await response.json();
  // Returns: { game_id, game_type, game_mode, ai_count, players, player_count, 
  //            status, min_players, max_players, can_join }
  
  return lobby;
}

// Example: Get lobby status for any context
const lobby = await getLobbyStatus('my-group-123', 'blackjack');
console.log(`${lobby.player_count}/${lobby.max_players} players waiting`);
```

#### 4b. Create or Join Lobby

Create or join a lobby and optionally join a player:

```javascript
async function createOrJoinLobby(contextId, gameType, playerId, gameMode = null, aiCount = null) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/lobby/${contextId}/${gameType}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        player_id: playerId,
        game_mode: gameMode,  // Optional
        ai_count: aiCount     // Optional
      })
    }
  );
  
  if (!response.ok) {
    throw new Error('Failed to create/join lobby');
  }
  
  const result = await response.json();
  // Returns: { game_id, player_id, game_type, game_mode, action: "retrieved" | "joined",
  //            ai_count, players, ai_players, player_count, status, redirect_url }
  
  // If player_id was provided, redirect_url is included for seamless redirect
  if (result.redirect_url) {
    window.location.href = result.redirect_url;
  }
  
  return result;
}

// Example: Join a blackjack lobby for any context
const lobby = await createOrJoinLobby('my-group-123', 'blackjack', 'user456');
console.log(`Lobby ${lobby.action}:`, lobby.game_id);
```

**How it works:**
1. Each unique `context_id` + `game_type` combination gets one persistent lobby
2. Lobbies always exist (even with 0 players) - they never get deleted
3. If `player_id` is provided, the player joins the lobby
4. If `player_id` is provided, `redirect_url` is included in the response for seamless redirect
5. If player is host (or lobby is empty), `game_mode` and `ai_count` settings are applied

**Response fields:**
- `action`: `"retrieved"` if just getting lobby, `"joined"` if player joined
- `game_id`: The game ID to use for joining/polling
- `redirect_url`: Included if `player_id` was provided (for seamless redirect)
- `status`: Current game status (`"waiting"`, `"in_progress"`, etc.)
- `players`: Array of player IDs
- `ai_count`: Number of AI players
- `player_count`: Number of active players
- `can_join`: Whether more players can join

#### 4c. Update Lobby Settings (Host Only)

Update lobby settings (game mode, AI player count) - only the host can do this:

```javascript
async function updateLobbySettings(contextId, gameType, playerId, gameMode = null, aiCount = null) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/lobby/${contextId}/${gameType}/settings`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        player_id: playerId,  // Must be the host
        game_mode: gameMode,   // Optional
        ai_count: aiCount      // Optional
      })
    }
  );
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to update settings');
  }
  
  return await response.json();
}

// Example: Host updates lobby settings
await updateLobbySettings('my-group-123', 'dominoes', 'host-user', 'boricua', 2);
```

**Note:** Only the host can update lobby settings. If the player is not the host, returns a 403 error.

### 5. WebSocket Real-Time Updates

For real-time game state updates, use WebSocket connections. The Game Portal provides a WebSocket API for:
- Real-time game state synchronization
- Player join/leave notifications
- Game start/end notifications
- Move updates
- State changes

#### 5a. Get WebSocket URL

Get the WebSocket URL for connecting to a game:

```javascript
async function getWebSocketUrl(gameId, playerId) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/websocket/url/${gameId}/${playerId}`
  );
  
  if (!response.ok) {
    throw new Error('Failed to get WebSocket URL');
  }
  
  const data = await response.json();
  // Returns: { websocket_url, game_id, player_id, protocol: "websocket", message_format: "json" }
  
  return data.websocket_url;
}
```

#### 5b. Connect to WebSocket

Connect to the WebSocket for real-time updates:

```javascript
class GameWebSocket {
  constructor(gameId, playerId) {
    this.gameId = gameId;
    this.playerId = playerId;
    this.ws = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
  }
  
  async connect() {
    // Get WebSocket URL
    const wsUrl = await getWebSocketUrl(this.gameId, this.playerId);
    
    // Connect to WebSocket
    this.ws = new WebSocket(wsUrl);
    
    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.reconnectAttempts = 0;
      this.onConnected();
    };
    
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.handleMessage(data);
    };
    
    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      this.onError(error);
    };
    
    this.ws.onclose = () => {
      console.log('WebSocket disconnected');
      this.onDisconnected();
      // Auto-reconnect
      if (this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++;
        setTimeout(() => this.connect(), 1000 * this.reconnectAttempts);
      }
    };
  }
  
  handleMessage(data) {
    switch (data.type) {
      case 'connection_success':
        console.log('Connected to game:', data.game_id);
        this.onGameState(data.game_state, data.players, data.game_status);
        break;
        
      case 'player_joined':
      case 'player_connected':
      case 'player_disconnected':
        this.onPlayersUpdate(data.players || []);
        break;
        
      case 'game_started':
        console.log('Game started!');
        this.onGameState(data.game_state, data.players, data.game_status);
        break;
        
      case 'state_update':
        this.onGameState(data.game_state, data.players, data.game_status);
        break;
        
      case 'error':
        console.error('Game error:', data.message);
        this.onError(data.message);
        break;
        
      default:
        console.log('Unknown message type:', data.type);
    }
  }
  
  sendMessage(type, data = {}) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, ...data }));
    }
  }
  
  // Start game
  startGame() {
    this.sendMessage('start_game');
  }
  
  // Make a move
  makeMove(moveData) {
    this.sendMessage('make_move', { move_data: moveData });
  }
  
  // Ready for next round/hand
  readyForNext() {
    this.sendMessage('ready_for_next');
  }
  
  // Event handlers (override these)
  onConnected() {}
  onDisconnected() {}
  onError(error) {}
  onGameState(gameState, players, gameStatus) {}
  onPlayersUpdate(players) {}
  
  disconnect() {
    if (this.ws) {
      this.ws.close();
    }
  }
}

// Usage:
const gameWs = new GameWebSocket('ABC123', 'user456');

// Override event handlers
gameWs.onGameState = (gameState, players, gameStatus) => {
  console.log('Game state updated:', gameState);
  updateGameUI(gameState, players, gameStatus);
};

gameWs.onPlayersUpdate = (players) => {
  console.log('Players updated:', players);
  updatePlayersList(players);
};

// Connect
gameWs.connect();
```

#### 5c. WebSocket Message Types

**Incoming Messages (from server):**

- `connection_success`: Initial connection established
  ```json
  {
    "type": "connection_success",
    "game_state": { ... },
    "players": [ ... ],
    "game_type": "blackjack",
    "game_status": "waiting",
    "is_spectator": false,
    "is_host": true
  }
  ```

- `player_joined`: Player joined the game
  ```json
  {
    "type": "player_joined",
    "player_id": "user123",
    "players": [ ... ]
  }
  ```

- `player_connected`: Player connected via WebSocket
  ```json
  {
    "type": "player_connected",
    "player_id": "user123"
  }
  ```

- `player_disconnected`: Player disconnected
  ```json
  {
    "type": "player_disconnected",
    "player_id": "user123"
  }
  ```

- `game_started`: Game has started
  ```json
  {
    "type": "game_started",
    "game_state": { ... },
    "players": [ ... ],
    "game_status": "in_progress"
  }
  ```

- `state_update`: Game state updated (after moves, etc.)
  ```json
  {
    "type": "state_update",
    "game_state": { ... },
    "players": [ ... ],
    "game_status": "in_progress"
  }
  ```

- `error`: Error occurred
  ```json
  {
    "type": "error",
    "message": "Error message"
  }
  ```

**Outgoing Messages (to server):**

- `start_game`: Start the game
  ```json
  { "type": "start_game" }
  ```

- `make_move`: Make a move
  ```json
  {
    "type": "make_move",
    "move_data": { ... }
  }
  ```

- `ready_for_next`: Ready for next round/hand
  ```json
  { "type": "ready_for_next" }
  ```

### 6. Complete Integration Example with WebSocket

Here's a complete example for any third-party site with real-time WebSocket updates:

```javascript
// On your site (e.g., your-platform.com)

class GamePortalIntegration {
  constructor(contextId) {
    this.contextId = contextId;  // Your unique context identifier (group ID, room ID, etc.)
    this.baseUrl = 'https://apps.oblivio-company.com/experiments/game_portal/backend';
  }
  
  // Get lobby status for display
  async getLobbyStatus(gameType) {
    const response = await fetch(
      `${this.baseUrl}/lobby/${this.contextId}/${gameType}`
    );
    
    if (!response.ok) {
      return null;
    }
    
    return await response.json();
  }
  
  // Join lobby with optional settings
  async joinLobby(gameType, playerId, gameMode = null, aiCount = null) {
    const response = await fetch(
      `${this.baseUrl}/lobby/${this.contextId}/${gameType}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          player_id: playerId,
          game_mode: gameMode,
          ai_count: aiCount
        })
      }
    );
    
    if (!response.ok) {
      throw new Error('Failed to join lobby');
    }
    
    const result = await response.json();
    
    // Redirect to game portal if redirect_url is provided
    if (result.redirect_url) {
      window.location.href = result.redirect_url;
    }
    
    return result;
  }
  
  // Update lobby settings (host only)
  async updateSettings(gameType, playerId, gameMode = null, aiCount = null) {
    const response = await fetch(
      `${this.baseUrl}/lobby/${this.contextId}/${gameType}/settings`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          player_id: playerId,
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
  
  // Poll lobby status for real-time updates (HTTP polling fallback)
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
  
  // Connect WebSocket for real-time updates (recommended)
  async connectWebSocket(gameId, playerId, callbacks = {}) {
    // Get WebSocket URL
    const response = await fetch(
      `https://apps.oblivio-company.com/experiments/game_portal/backend/websocket/url/${gameId}/${playerId}`
    );
    
    if (!response.ok) {
      throw new Error('Failed to get WebSocket URL');
    }
    
    const { websocket_url } = await response.json();
    
    // Connect to WebSocket
    const ws = new WebSocket(websocket_url);
    
    ws.onopen = () => {
      console.log('WebSocket connected');
      if (callbacks.onConnected) callbacks.onConnected();
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      switch (data.type) {
        case 'connection_success':
          if (callbacks.onGameState) {
            callbacks.onGameState(data.game_state, data.players, data.game_status);
          }
          break;
          
        case 'player_joined':
        case 'player_connected':
        case 'player_disconnected':
          if (callbacks.onPlayersUpdate) {
            callbacks.onPlayersUpdate(data.players || []);
          }
          break;
          
        case 'game_started':
          if (callbacks.onGameStarted) {
            callbacks.onGameStarted(data.game_state, data.players);
          }
          break;
          
        case 'state_update':
          if (callbacks.onGameState) {
            callbacks.onGameState(data.game_state, data.players, data.game_status);
          }
          break;
          
        case 'error':
          console.error('Game error:', data.message);
          if (callbacks.onError) {
            callbacks.onError(data.message);
          }
          break;
      }
    };
    
    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      if (callbacks.onError) {
        callbacks.onError(error);
      }
    };
    
    ws.onclose = () => {
      console.log('WebSocket disconnected');
      if (callbacks.onDisconnected) {
        callbacks.onDisconnected();
      }
    };
    
    // Return WebSocket with helper methods
    return {
      ws,
      send: (type, data = {}) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type, ...data }));
        }
      },
      startGame: () => ws.send(JSON.stringify({ type: 'start_game' })),
      makeMove: (moveData) => ws.send(JSON.stringify({ type: 'make_move', move_data: moveData })),
      readyForNext: () => ws.send(JSON.stringify({ type: 'ready_for_next' })),
      disconnect: () => ws.close()
    };
  }
}

// Usage in your platform:
const gameIntegration = new GamePortalIntegration('my-group-123');

// Display lobby status
async function renderLobbyStatus() {
  const blackjackLobby = await gameIntegration.getLobbyStatus('blackjack');
  const dominoesLobby = await gameIntegration.getLobbyStatus('dominoes');
  
  // Display player counts
  document.getElementById('blackjack-count').textContent = 
    `${blackjackLobby?.player_count || 0}/${blackjackLobby?.max_players || 4}`;
  document.getElementById('dominoes-count').textContent = 
    `${dominoesLobby?.player_count || 0}/${dominoesLobby?.max_players || 4}`;
}

// Handle join button click
document.getElementById('join-blackjack').addEventListener('click', async () => {
  const userId = getCurrentUserId();  // Your user ID system
  const gameMode = document.getElementById('game-mode').value;
  const aiCount = parseInt(document.getElementById('ai-count').value);
  
  await gameIntegration.joinLobby('blackjack', userId, gameMode, aiCount);
});

// Option 1: HTTP Polling (fallback)
gameIntegration.startPolling('blackjack', (lobby) => {
  updateLobbyDisplay(lobby);
});

// Option 2: WebSocket (recommended for real-time updates)
let gameWs = null;
document.getElementById('join-blackjack').addEventListener('click', async () => {
  const userId = getCurrentUserId();
  const gameMode = document.getElementById('game-mode').value;
  const aiCount = parseInt(document.getElementById('ai-count').value);
  
  const result = await gameIntegration.joinLobby('blackjack', userId, gameMode, aiCount);
  
  // Connect WebSocket for real-time updates
  gameWs = await gameIntegration.connectWebSocket(result.game_id, userId, {
    onGameState: (gameState, players, gameStatus) => {
      updateGameUI(gameState, players, gameStatus);
    },
    onPlayersUpdate: (players) => {
      updatePlayersList(players);
    },
    onGameStarted: (gameState, players) => {
      showGameStarted(gameState, players);
    },
    onError: (error) => {
      showError(error);
    }
  });
  
  // Handle game actions
  document.getElementById('start-game').addEventListener('click', () => {
    gameWs.startGame();
  });
  
  document.getElementById('make-move').addEventListener('click', () => {
    const moveData = getMoveData(); // Your move data
    gameWs.makeMove(moveData);
  });
});
```

## Best Practices

1. **Real-Time Updates**: Use WebSocket connections for real-time updates instead of HTTP polling when possible
2. **WebSocket Reconnection**: Implement automatic reconnection logic for WebSocket connections
3. **Fallback to Polling**: Use HTTP polling as a fallback if WebSocket is unavailable
4. **Polling Frequency**: If using HTTP polling, poll every 2-5 seconds to balance responsiveness and server load
5. **Error Handling**: Handle network errors gracefully and retry failed requests
6. **User Experience**: Show loading states while connecting and provide clear join links
7. **Game State**: Use the `status` field to determine what UI to show (lobby vs. in-progress)
8. **Share Links**: Always provide shareable links so users can invite friends
9. **Placeholder Handling**: Placeholders are automatically handled - they're created during game creation and replaced when users join. You don't need to handle placeholders manually.
10. **Player IDs**: Use stable, unique identifiers for players (e.g., user IDs from your database). Browser fingerprints are used for public games.
11. **State Synchronization**: Use WebSocket `state_update` messages to keep your UI in sync with the game state

## Security Notes

- All endpoints are CORS-enabled for `*.oblivio-company.com` domains
- Game state summaries exclude sensitive information (other players' hands, etc.)
- Players must join games through the official Game Portal interface
- No authentication required for public games (uses browser fingerprinting)
- Placeholders are automatically filtered from the UI and never displayed to users
- Player IDs are validated server-side to prevent malicious input

## Example Use Cases

1. **Social Networks**: Create games within groups/circles and display live updates
2. **Organization Platforms**: Show active games in organization rooms/channels
3. **Event Management**: Display games during events with real-time player counts
4. **Tournament Systems**: Display multiple games with their current status
5. **Embedded Widgets**: Embed game widgets in blog posts or articles
6. **Any Platform**: Works with any system that has unique context identifiers

## Context Identifier

The `context_id` parameter (shown as `circle_id` in the API path) is **any unique string identifier** from your system. Examples:
- Circle ID: `"68e1748923e65640e2872282"`
- Group ID: `"group-abc-123"`
- Room ID: `"room-xyz-456"`
- Organization ID: `"org-789"`
- Any string: `"my-unique-context-identifier"`

The Game Portal doesn't care what the identifier represents - it just uses it to create a unique lobby per context + game type combination.

