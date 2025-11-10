# MyCircles UI/UX Integration Guide

This guide shows how mycircles can leverage the Game Portal API to create dynamic, real-time UI updates.

## Overview

The Game Portal provides a polling API endpoint that returns the complete game state, allowing mycircles to:
- Display real-time player counts
- Show game status (waiting, in progress, finished)
- Display live scores and leaderboards
- Show current turn information
- Create interactive game widgets

## Key API Endpoint

### Poll for Game Updates

**Endpoint:** `GET /experiments/game_portal/backend/api/game/{game_id}/poll`

**Returns:**
- Complete player list with AI/spectator status
- Game status (waiting, in_progress, round_finished, hand_finished, finished)
- Game state summary (scores, current turn, round number, etc.)
- Player counts and capacity information

## Real-Time UI Update Patterns

### 1. Basic Polling with UI Updates

```javascript
// mycircles.oblivio-company.com

class GamePortalWidget {
  constructor(gameId, containerId) {
    this.gameId = gameId;
    this.container = document.getElementById(containerId);
    this.pollInterval = null;
    this.lastUpdate = null;
  }

  async startPolling(intervalMs = 2000) {
    // Poll immediately
    await this.updateUI();
    
    // Then poll every intervalMs
    this.pollInterval = setInterval(() => {
      this.updateUI();
    }, intervalMs);
  }

  stopPolling() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  async updateUI() {
    try {
      const response = await fetch(
        `https://apps.oblivio-company.com/experiments/game_portal/backend/api/game/${this.gameId}/poll`,
        { method: 'GET' }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const gameData = await response.json();
      this.render(gameData);
      this.lastUpdate = Date.now();
    } catch (error) {
      console.error('Failed to poll game updates:', error);
      this.renderError(error);
    }
  }

  render(gameData) {
    const { status, players, player_count, max_players, game_state_summary } = gameData;

    // Update status badge
    this.updateStatusBadge(status);

    // Update player list
    this.updatePlayerList(players, player_count, max_players);

    // Update game state (if in progress)
    if (game_state_summary) {
      this.updateGameState(game_state_summary, status);
    }

    // Update join button
    this.updateJoinButton(gameData);
  }

  updateStatusBadge(status) {
    const badge = this.container.querySelector('.status-badge');
    if (!badge) return;

    const statusConfig = {
      waiting: { text: 'Waiting', class: 'status-waiting', icon: '⏳' },
      in_progress: { text: 'In Progress', class: 'status-active', icon: '🎮' },
      round_finished: { text: 'Round Complete', class: 'status-paused', icon: '✅' },
      hand_finished: { text: 'Hand Complete', class: 'status-paused', icon: '✅' },
      finished: { text: 'Finished', class: 'status-finished', icon: '🏁' }
    };

    const config = statusConfig[status] || statusConfig.waiting;
    badge.textContent = `${config.icon} ${config.text}`;
    badge.className = `status-badge ${config.class}`;
  }

  updatePlayerList(players, currentCount, maxCount) {
    const playerListEl = this.container.querySelector('.players-list');
    if (!playerListEl) return;

    // Filter out suspicious player IDs for display
    const validPlayers = players.filter(p => {
      const pid = p.player_id || p;
      return !pid.match(/^player_\d+/);
    });

    playerListEl.innerHTML = validPlayers.map(p => {
      const pid = p.player_id || p;
      const isAI = p.is_ai || pid.startsWith('AI_');
      const isSpectator = p.is_spectator || false;
      
      const badge = isAI ? '🤖 AI' : isSpectator ? '👀 Spectator' : '👤';
      const displayName = isAI ? 'AutoBot' : this.formatPlayerId(pid);
      
      return `<div class="player-item ${isAI ? 'ai-player' : ''}">
        <span class="player-badge">${badge}</span>
        <span class="player-name">${displayName}</span>
      </div>`;
    }).join('');

    // Update player count
    const countEl = this.container.querySelector('.player-count');
    if (countEl) {
      countEl.textContent = `${currentCount}/${maxCount} players`;
    }
  }

  updateGameState(gameStateSummary, status) {
    const gameStateEl = this.container.querySelector('.game-state');
    if (!gameStateEl) return;

    if (status === 'in_progress' || status === 'round_finished' || status === 'hand_finished') {
      // Show scores/leaderboard
      if (gameStateSummary.scores) {
        const scoresHtml = this.renderScores(gameStateSummary.scores, gameStateSummary.hand_wins);
        gameStateEl.innerHTML = scoresHtml;
      }

      // Show current turn
      if (gameStateSummary.current_turn) {
        const turnEl = this.container.querySelector('.current-turn');
        if (turnEl) {
          const turnPlayer = this.formatPlayerId(gameStateSummary.current_turn);
          turnEl.textContent = `Current Turn: ${turnPlayer}`;
        }
      }

      // Show round/hand number
      if (gameStateSummary.round_number) {
        const roundEl = this.container.querySelector('.round-number');
        if (roundEl) {
          roundEl.textContent = `Round ${gameStateSummary.round_number}`;
        }
      }
    }
  }

  renderScores(scores, handWins) {
    if (!scores) return '';

    const sortedScores = Object.entries(scores)
      .sort((a, b) => b[1] - a[1])
      .map(([pid, score], index) => {
        const medal = index === 0 ? '🥇' : index === 1 ? '🥈' : index === 2 ? '🥉' : '';
        const playerName = this.formatPlayerId(pid);
        const wins = handWins?.[pid] || 0;
        return `<div class="score-item">
          <span class="medal">${medal}</span>
          <span class="player-name">${playerName}</span>
          <span class="score">${score} pts</span>
          ${wins > 0 ? `<span class="wins">(${wins} wins)</span>` : ''}
        </div>`;
      });

    return `<div class="leaderboard">
      <h4>📊 Leaderboard</h4>
      ${sortedScores.join('')}
    </div>`;
  }

  updateJoinButton(gameData) {
    const joinBtn = this.container.querySelector('.join-button');
    if (!joinBtn) return;

    const { status, player_count, max_players, can_join } = gameData;

    if (status === 'finished') {
      joinBtn.textContent = 'Game Finished';
      joinBtn.disabled = true;
      joinBtn.classList.add('disabled');
    } else if (player_count >= max_players) {
      joinBtn.textContent = 'Game Full';
      joinBtn.disabled = true;
      joinBtn.classList.add('disabled');
    } else if (status === 'in_progress') {
      joinBtn.textContent = 'Join as Spectator';
      joinBtn.disabled = false;
      joinBtn.classList.remove('disabled');
    } else {
      joinBtn.textContent = `Join Game (${player_count}/${max_players})`;
      joinBtn.disabled = !can_join;
      joinBtn.classList.toggle('disabled', !can_join);
    }

    // Update link
    const gameUrl = `https://apps.oblivio-company.com/experiments/game_portal?game=${this.gameId}`;
    joinBtn.href = gameUrl;
  }

  formatPlayerId(playerId) {
    // Format player ID for display (hide suspicious IDs)
    if (playerId && playerId.match(/^player_\d+/)) {
      return 'Unknown Player';
    }
    if (playerId && playerId.startsWith('AI_')) {
      return 'AutoBot';
    }
    // Show first 8 characters
    return playerId ? `Player ${playerId.substring(0, 8)}` : 'Unknown';
  }

  renderError(error) {
    const errorEl = this.container.querySelector('.error-message');
    if (errorEl) {
      errorEl.textContent = `Failed to load game: ${error.message}`;
      errorEl.style.display = 'block';
    }
  }
}
```

### 2. HTML Template

```html
<!-- mycircles.oblivio-company.com -->
<div id="game-widget-{gameId}" class="game-portal-widget">
  <div class="widget-header">
    <h3>🎮 Game Portal</h3>
    <span class="status-badge">⏳ Waiting</span>
  </div>

  <div class="widget-body">
    <div class="player-count">0/4 players</div>
    
    <div class="players-list">
      <!-- Populated by JavaScript -->
    </div>

    <div class="game-state">
      <!-- Shows scores/leaderboard when game is in progress -->
    </div>

    <div class="current-turn" style="display: none;">
      <!-- Shows current player's turn -->
    </div>

    <div class="round-number" style="display: none;">
      <!-- Shows round/hand number -->
    </div>

    <a href="#" class="join-button" target="_blank">
      Join Game
    </a>
  </div>

  <div class="error-message" style="display: none;"></div>
</div>
```

### 3. CSS Styling

```css
.game-portal-widget {
  border: 2px solid #e2e8f0;
  border-radius: 12px;
  padding: 1.5rem;
  background: white;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.widget-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.status-badge {
  padding: 0.5rem 1rem;
  border-radius: 8px;
  font-weight: 600;
  font-size: 0.875rem;
}

.status-waiting { background: #fef3c7; color: #92400e; }
.status-active { background: #d1fae5; color: #065f46; }
.status-paused { background: #dbeafe; color: #1e40af; }
.status-finished { background: #f3f4f6; color: #374151; }

.player-count {
  font-size: 0.875rem;
  color: #6b7280;
  margin-bottom: 1rem;
}

.players-list {
  margin-bottom: 1rem;
}

.player-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  border-radius: 6px;
  margin-bottom: 0.25rem;
}

.player-item.ai-player {
  background: #fef3c7;
}

.leaderboard {
  margin-top: 1rem;
  padding: 1rem;
  background: #f9fafb;
  border-radius: 8px;
}

.score-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
}

.join-button {
  display: block;
  text-align: center;
  padding: 0.75rem 1.5rem;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 8px;
  text-decoration: none;
  font-weight: 600;
  margin-top: 1rem;
}

.join-button.disabled {
  background: #d1d5db;
  cursor: not-allowed;
  opacity: 0.6;
}
```

### 4. Usage Example

```javascript
// Initialize widget when page loads
document.addEventListener('DOMContentLoaded', () => {
  const gameId = 'LOBBY_D6E5B0'; // Get from URL or API
  
  const widget = new GamePortalWidget(gameId, 'game-widget-container');
  widget.startPolling(2000); // Poll every 2 seconds

  // Stop polling when user leaves page
  window.addEventListener('beforeunload', () => {
    widget.stopPolling();
  });
});
```

## Advanced Features

### 1. Optimistic UI Updates

```javascript
// Update UI immediately when user performs action, then sync with server
async function joinGame(gameId, playerId) {
  // Optimistic update
  updateUIOptimistically({ player_count: currentCount + 1 });

  // Then sync with server
  try {
    const response = await fetch(
      `https://apps.oblivio-company.com/experiments/game_portal/backend/api/game/${gameId}/join`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player_id: playerId })
      }
    );
    
    const result = await response.json();
    // Sync with actual state
    widget.updateUI();
  } catch (error) {
    // Revert optimistic update
    revertOptimisticUpdate();
  }
}
```

### 2. Change Detection (Only Update When State Changes)

```javascript
class GamePortalWidget {
  constructor(gameId, containerId) {
    // ... existing code ...
    this.lastStateHash = null;
  }

  async updateUI() {
    const gameData = await this.fetchGameData();
    const stateHash = this.hashState(gameData);

    // Only update if state actually changed
    if (stateHash !== this.lastStateHash) {
      this.render(gameData);
      this.lastStateHash = stateHash;
    }
  }

  hashState(gameData) {
    // Create a simple hash of the game state
    const key = `${gameData.status}-${gameData.player_count}-${JSON.stringify(gameData.game_state_summary)}`;
    return btoa(key).substring(0, 16);
  }
}
```

### 3. Real-Time Notifications

```javascript
class GamePortalWidget {
  constructor(gameId, containerId) {
    // ... existing code ...
    this.previousState = null;
  }

  render(gameData) {
    // Detect state changes and show notifications
    if (this.previousState) {
      this.detectChanges(this.previousState, gameData);
    }

    // ... existing render code ...
    this.previousState = JSON.parse(JSON.stringify(gameData));
  }

  detectChanges(oldState, newState) {
    // Player joined
    if (newState.player_count > oldState.player_count) {
      this.showNotification('👤 Player joined!', 'success');
    }

    // Game started
    if (oldState.status === 'waiting' && newState.status === 'in_progress') {
      this.showNotification('🎮 Game started!', 'info');
    }

    // Round finished
    if (oldState.status === 'in_progress' && newState.status === 'round_finished') {
      this.showNotification('✅ Round complete!', 'success');
    }

    // Game finished
    if (newState.status === 'finished') {
      this.showNotification('🏁 Game finished!', 'info');
      this.stopPolling();
    }
  }

  showNotification(message, type) {
    // Create and show a toast notification
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
      notification.remove();
    }, 3000);
  }
}
```

## Best Practices

1. **Polling Frequency**: Poll every 2-5 seconds for balance between responsiveness and server load
2. **Error Handling**: Gracefully handle network errors and retry failed requests
3. **Loading States**: Show loading indicators while fetching data
4. **State Caching**: Cache previous state to detect changes and avoid unnecessary DOM updates
5. **Player ID Formatting**: Always format player IDs for display (filter suspicious IDs, show friendly names)
6. **Responsive Design**: Make widgets responsive for mobile and desktop
7. **Accessibility**: Use semantic HTML and ARIA labels for screen readers

## Complete Integration Example

```javascript
// mycircles.oblivio-company.com - Complete integration

class MyCirclesGameIntegration {
  constructor() {
    this.widgets = new Map();
  }

  // Initialize game widget for a circle
  async initCircleGame(circleId, gameType) {
    // Get or create lobby
    const lobby = await this.getOrCreateLobby(circleId, gameType);
    
    // Create widget
    const widget = new GamePortalWidget(lobby.game_id, `game-widget-${circleId}-${gameType}`);
    widget.startPolling(2000);
    
    this.widgets.set(`${circleId}-${gameType}`, widget);
    return widget;
  }

  async getOrCreateLobby(circleId, gameType) {
    const response = await fetch(
      `https://apps.oblivio-company.com/experiments/game_portal/backend/mycircles/lobby/${circleId}/${gameType}`,
      { method: 'GET' }
    );
    return await response.json();
  }

  // Clean up when leaving page
  cleanup() {
    this.widgets.forEach(widget => widget.stopPolling());
    this.widgets.clear();
  }
}

// Usage
const gameIntegration = new MyCirclesGameIntegration();

// Initialize game for circle
gameIntegration.initCircleGame('circle123', 'blackjack');

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
  gameIntegration.cleanup();
});
```

## API Response Structure

The poll endpoint returns:

```json
{
  "game_id": "LOBBY_D6E5B0",
  "game_type": "blackjack",
  "game_mode": "best_of_5",
  "status": "in_progress",
  "host_id": "d27462a2c4ce...",
  "players": [
    {
      "player_id": "d27462a2c4ce...",
      "is_ai": false,
      "is_spectator": false
    },
    {
      "player_id": "AI_LOBBY_D6E5B0_0",
      "is_ai": true,
      "is_spectator": false
    }
  ],
  "player_count": 2,
  "spectator_count": 0,
  "min_players": 2,
  "max_players": 4,
  "can_start": false,
  "is_started": true,
  "is_finished": false,
  "game_state_summary": {
    "round_number": 1,
    "current_turn": "d27462a2c4ce...",
    "scores": {
      "d27462a2c4ce...": 15,
      "AI_LOBBY_D6E5B0_0": 18
    },
    "hand_wins": {
      "d27462a2c4ce...": 0,
      "AI_LOBBY_D6E5B0_0": 0
    }
  }
}
```

This structure allows mycircles to build rich, real-time UI experiences that accurately reflect the game state!

