# Game Portal Third-Party Integration Guide

This guide explains how third-party websites (like `mycircles.oblivio-company.com`) can integrate with the Game Portal to create and display games.

## Overview

The Game Portal provides a CORS-enabled backend API that allows external sites to:
- Automatically create games
- Poll for game updates (players joined, game started, etc.)
- Display game information to users
- Allow users to join games via shareable links

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

### 4. Create or Join Lobby (Circle Integration)

For circle-based games (e.g., mycircles integration), use the lobby endpoint to automatically create or join an existing waiting lobby:

```javascript
async function createOrJoinLobby(circleId, gameType, playerId) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/lobby/${circleId}/${gameType}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_id: playerId })
    }
  );
  
  if (!response.ok) {
    throw new Error('Failed to create/join lobby');
  }
  
  const result = await response.json();
  // Returns: { game_id, player_id, game_type, game_mode, action: "created" | "joined", 
  //            players, ai_players, player_count, status }
  
  return result;
}

// Example: Create or join a blackjack lobby for circle "circle123"
const lobby = await createOrJoinLobby('circle123', 'blackjack', 'user456');
console.log(`Lobby ${lobby.action}:`, lobby.game_id);
```

**How it works:**
1. First checks if a waiting lobby exists for the given `circle_id` and `game_type`
2. If found, joins the existing lobby (replacing placeholder if present)
3. If not found, creates a new lobby with a placeholder player
4. The placeholder is automatically replaced when the user visits the game link

**Response fields:**
- `action`: `"created"` if a new lobby was created, `"joined"` if joined an existing one
- `game_id`: The game ID to use for joining/polling
- `status`: Current game status (`"waiting"`, `"in_progress"`, etc.)
- `players`: Array of player IDs (placeholders are filtered from display)
- `ai_players`: Array of AI player IDs

### 5. Complete Integration Example

Here's a complete example for a third-party site:

```javascript
// On your site (e.g., mycircles.oblivio-company.com)

// Step 1: Create a game when user clicks "Start Game"
async function createGame(gameType = 'blackjack', aiCount = 1) {
  const response = await fetch(
    `https://apps.oblivio-company.com/experiments/game_portal/backend/new/${gameType}?ai_count=${aiCount}`,
    { method: 'GET' }
  );
  
  if (!response.ok) {
    throw new Error('Failed to create game');
  }
  
  const gameData = await response.json();
  const gameId = gameData.game_id;
  
  // Step 2: Display game widget
  const widget = createGameWidget(gameId);
  document.body.appendChild(widget);
  
  // Step 3: Show shareable link
  const shareLink = `https://apps.oblivio-company.com/experiments/game_portal?game=${gameId}`;
  showShareLink(shareLink);
  
  // Step 4: Poll for updates
  startPolling(gameId);
}

// Poll for updates and update UI
function startPolling(gameId) {
  const pollInterval = setInterval(async () => {
    try {
      const updates = await pollGameUpdates(gameId);
      
      // Update your UI based on game state
      if (updates.is_finished) {
        clearInterval(pollInterval);
        showGameFinished(updates);
      } else if (updates.is_started) {
        showGameInProgress(updates);
      } else {
        showLobby(updates);
      }
    } catch (error) {
      console.error('Polling error:', error);
    }
  }, 2000); // Poll every 2 seconds
}
```

## Best Practices

1. **Polling Frequency**: Poll every 2-5 seconds to balance responsiveness and server load
2. **Error Handling**: Handle network errors gracefully and retry failed requests
3. **User Experience**: Show loading states while polling and provide clear join links
4. **Game State**: Use the `status` field to determine what UI to show (lobby vs. in-progress)
5. **Share Links**: Always provide shareable links so users can invite friends
6. **Placeholder Handling**: Placeholders are automatically handled - they're created during game creation and replaced when users join. You don't need to handle placeholders manually.
7. **Player IDs**: Use stable, unique identifiers for players (e.g., user IDs from your database). Browser fingerprints are used for public games.

## Security Notes

- All endpoints are CORS-enabled for `*.oblivio-company.com` domains
- Game state summaries exclude sensitive information (other players' hands, etc.)
- Players must join games through the official Game Portal interface
- No authentication required for public games (uses browser fingerprinting)
- Placeholders are automatically filtered from the UI and never displayed to users
- Player IDs are validated server-side to prevent malicious input

## Example Use Cases

1. **Social Circles**: Create games within user circles and display live updates
2. **Event Pages**: Show active games during events with real-time player counts
3. **Tournament Pages**: Display multiple games with their current status
4. **Embedded Widgets**: Embed game widgets in blog posts or articles

