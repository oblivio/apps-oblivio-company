// Dominito Frontend JavaScript

// Adapted for experiment structure
document.addEventListener('DOMContentLoaded', () => {
    // Get base path from current URL
    const currentPath = window.location.pathname;
    let basePath = '/experiments/game_portal';
    if (currentPath.includes('/experiments/game_portal')) {
        const pathParts = currentPath.split('/experiments/game_portal');
        basePath = pathParts[0] + '/experiments/game_portal';
    }
    
    // Fix Quick Start link to use correct base path
    const quickStartLink = document.getElementById('quick-start-link');
    if (quickStartLink) {
        quickStartLink.href = `${basePath}/new`;
    }
    
    // --- Browser Fingerprinting ---
    let browserFingerprint = null;
    
    async function generateFingerprint() {
        const stored = localStorage.getItem('browser_fingerprint');
        if (stored) {
            browserFingerprint = stored;
            return stored;
        }
        
        if (typeof FingerprintJS !== 'undefined') {
            try {
                const fp = await FingerprintJS.load();
                const result = await fp.get();
                browserFingerprint = result.visitorId;
                localStorage.setItem('browser_fingerprint', browserFingerprint);
                console.log('Browser Fingerprint (FingerprintJS):', browserFingerprint);
                return browserFingerprint;
            } catch (e) {
                console.warn('FingerprintJS failed, using fallback:', e);
            }
        }
        
        // Fallback: generate a simple fingerprint
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        ctx.textBaseline = 'top';
        ctx.font = '14px Arial';
        ctx.fillText('Fingerprint', 2, 2);
        
        const fingerprint = [
            navigator.userAgent,
            navigator.language,
            screen.width + 'x' + screen.height,
            new Date().getTimezoneOffset(),
            canvas.toDataURL()
        ].join('|');
        
        let hash = 0;
        for (let i = 0; i < fingerprint.length; i++) {
            const char = fingerprint.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;
        }
        browserFingerprint = 'fp_' + Math.abs(hash).toString(36);
        localStorage.setItem('browser_fingerprint', browserFingerprint);
        console.log('Browser Fingerprint (Fallback):', browserFingerprint);
        return browserFingerprint;
    }
    
    generateFingerprint().then(fp => {
        browserFingerprint = fp;
        updateUserIdentityDisplay();
    });
    
    function updateUserIdentityDisplay() {
        if (!browserFingerprint) return;
        
        const userIdentityDiv = document.getElementById('user-identity');
        const userIdenticonSvg = document.getElementById('user-identicon');
        const userDisplayName = document.getElementById('user-display-name');
        const userFingerprint = document.getElementById('user-fingerprint');
        
        if (userIdentityDiv) {
            userIdentityDiv.style.display = 'block';
        }
        
        if (userIdenticonSvg) {
            userIdenticonSvg.setAttribute('data-jdenticon-value', browserFingerprint);
        }
        
        if (userDisplayName) {
            userDisplayName.textContent = `Player ${browserFingerprint.substring(0, 8)}`;
        }
        
        if (userFingerprint) {
            userFingerprint.textContent = browserFingerprint;
        }
        
        setTimeout(() => {
            if (typeof jdenticon !== 'undefined' && userIdenticonSvg) {
                jdenticon.update(userIdenticonSvg);
            }
        }, 100);
    }
    
    // --- Initialize Floating Particles ---
    function initParticles() {
        const particlesContainer = document.getElementById('particles');
        if (!particlesContainer) return;
        
        for (let i = 0; i < 18; i++) {
            const particle = document.createElement('div');
            particle.className = 'particle';
            particlesContainer.appendChild(particle);
        }
    }
    
    document.addEventListener('mousemove', (e) => {
        const mouseX = e.clientX;
        const mouseY = e.clientY;
        
        let styleEl = document.getElementById('mouse-gradient-style');
        if (!styleEl) {
            styleEl = document.createElement('style');
            styleEl.id = 'mouse-gradient-style';
            document.head.appendChild(styleEl);
        }
        styleEl.textContent = `
            body::after {
                left: ${mouseX}px;
                top: ${mouseY}px;
            }
        `;
        
        document.body.classList.add('mouse-active');
    });
    
    document.addEventListener('mouseleave', () => {
        document.body.classList.remove('mouse-active');
    });
    
    initParticles();
    
    // --- Global State ---
    let socket = null;
    let localPlayerId = null;
    let localGameId = null;
    let localGameType = null;
    let currentGameState = null;
    let selectedTile = null; // Currently selected tile for playing
    let disconnectTimeout = null;
    let reconnectAttempts = 0;
    const MAX_RECONNECT_ATTEMPTS = 5;
    const DISCONNECT_DELAY_MS = 60000; // 60 seconds
    let autoStartTimer = null;
    let autoStartCountdown = null;
    
    // --- UI Elements ---
    const lobbyView = document.getElementById('lobby-view');
    const gameView = document.getElementById('game-view');
    const gameTypeSelect = document.getElementById('game-type');
    const dominoModeSelect = document.getElementById('domino-mode-select');
    const dominoGameModeSelect = document.getElementById('domino-game-mode');
    const createGameBtn = document.getElementById('create-game-btn');
    const gameIdInput = document.getElementById('game-id-input');
    const joinGameBtn = document.getElementById('join-game-btn');
    const gameTitle = document.getElementById('game-title');
    const gameIdDisplay = document.getElementById('game-id-display');
    const playerIdDisplay = document.getElementById('player-id-display');
    const turnDisplay = document.getElementById('turn-display');
    const copyLinkBtn = document.getElementById('copy-link-btn');
    const startGameBtn = document.getElementById('start-game-btn');
    const playersList = document.getElementById('players-list');
    const dominoesUI = document.getElementById('dominoes-ui');
    const dominoBoardDiv = document.getElementById('domino-board');
    const dominoEndsSpan = document.getElementById('domino-ends');
    const drawBtn = document.getElementById('draw-btn');
    const passBtn = document.getElementById('pass-btn');
    const playerHandDiv = document.getElementById('player-hand');
    const handValueSpan = document.getElementById('hand-value');
    const gameLogDiv = document.getElementById('game-log');
    
    // Show/hide game mode selector based on game type
    function updateGameModeSelector() {
        // Only dominoes is supported now
        if (gameTypeSelect.value === 'dominoes') {
            dominoModeSelect.style.display = 'block';
            // Ensure boricua option is available
            const boricuaOption = dominoGameModeSelect.querySelector('option[value="boricua"]');
            if (!boricuaOption) {
                const option = document.createElement('option');
                option.value = 'boricua';
                option.textContent = 'Boricua Style (First to 500, 2v2)';
                dominoGameModeSelect.appendChild(option);
            }
        } else {
            dominoModeSelect.style.display = 'none';
        }
    }
    
    gameTypeSelect.addEventListener('change', updateGameModeSelector);
    updateGameModeSelector();
    
    // Help button now handled by modal system (see below)
    
    // --- Welcome Message for First-Time Users ---
    const welcomeMessage = document.getElementById('welcome-message');
    const dismissWelcomeBtn = document.getElementById('dismiss-welcome-btn');
    
    // Check if user has seen welcome before
    const hasSeenWelcome = localStorage.getItem('dominoes_welcome_seen');
    
    if (dismissWelcomeBtn && welcomeMessage) {
        dismissWelcomeBtn.addEventListener('click', () => {
            welcomeMessage.classList.add('hidden');
            localStorage.setItem('dominoes_welcome_seen', 'true');
        });
    }
    
    // --- WebSocket Handlers ---
    function connectWebSocket(gameId, playerId) {
        // Validate parameters before connecting
        if (!gameId || gameId === 'undefined' || gameId === 'null' || typeof gameId !== 'string' || gameId.trim() === '') {
            console.error('connectWebSocket: Invalid gameId:', gameId);
            return;
        }
        
        if (!playerId || playerId === 'undefined' || playerId === 'null' || typeof playerId !== 'string' || playerId.trim() === '') {
            console.error('connectWebSocket: Invalid playerId:', playerId);
            return;
        }
        
        // Normalize values (trim and uppercase gameId)
        gameId = gameId.trim().toUpperCase();
        playerId = playerId.trim();
        
        localGameId = gameId;
        localPlayerId = playerId;
        
        // Check if auto-join script already created a WebSocket
        if (window.gameWebSocket && window.gameWebSocket.readyState === WebSocket.OPEN) {
            console.log('Using existing WebSocket from auto-join');
            socket = window.gameWebSocket;
            // Set up handlers if not already set
            if (!socket.onmessage || socket.onmessage.toString().includes('Auto-join')) {
                setupWebSocketHandlers(socket, gameId, playerId);
            }
            return;
        }
        
        // Determine WebSocket protocol
        // Use wss:// if page is loaded via HTTPS, ws:// if HTTP
        // This ensures protocol matching (HTTPS requires WSS, HTTP requires WS)
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}${basePath}/ws/game/${gameId}/${playerId}`;
        
        console.log('Connecting WebSocket to:', wsUrl);
        
        try {
            socket = new WebSocket(wsUrl);
            setupWebSocketHandlers(socket, gameId, playerId);
        } catch (error) {
            console.error('Failed to create WebSocket:', error);
            showError('❌ Failed to connect to game server. Please refresh and try again.');
        }
    }
    
    function setupWebSocketHandlers(ws, gameId, playerId) {
        if (gameIdDisplay) {
            gameIdDisplay.textContent = gameId;
        }
        
        const playerIdenticonSvg = document.getElementById('player-identicon-display');
        const playerDisplayName = document.getElementById('player-display-name');
        
        if (playerIdenticonSvg) {
            playerIdenticonSvg.setAttribute('data-jdenticon-value', playerId);
            setTimeout(() => {
                if (typeof jdenticon !== 'undefined') {
                    jdenticon.update(playerIdenticonSvg);
                }
            }, 100);
        }
        
        if (playerDisplayName) {
            playerDisplayName.textContent = `Player ${playerId.substring(0, 8)}`;
        }
        
        if (playerIdDisplay) {
            playerIdDisplay.textContent = playerId.substring(0, 12) + '...';
        }
        
        if (copyLinkBtn) {
            const shareUrl = `${window.location.origin}${basePath}?game=${gameId}`;
            copyLinkBtn.onclick = () => {
                navigator.clipboard.writeText(shareUrl).then(() => {
                copyLinkBtn.textContent = '✓ Copied!';
                copyLinkBtn.style.background = '#48bb78';
                setTimeout(() => {
                    copyLinkBtn.textContent = '📋 Copy Share Link';
                    copyLinkBtn.style.background = 'white';
                }, 2000);
                }).catch(() => {
                    const textarea = document.createElement('textarea');
                    textarea.value = shareUrl;
                    document.body.appendChild(textarea);
                    textarea.select();
                    document.execCommand('copy');
                    document.body.removeChild(textarea);
                copyLinkBtn.textContent = '✓ Copied!';
                copyLinkBtn.style.background = '#48bb78';
                setTimeout(() => {
                    copyLinkBtn.textContent = '📋 Copy Share Link';
                    copyLinkBtn.style.background = 'white';
                }, 2000);
                });
            };
        }
        
        ws.onopen = () => {
            console.log('WebSocket connected');
            lobbyView.classList.add('hidden');
            gameView.classList.remove('hidden');
            
            // Remove auto-join loading spinner if present
            const loadingDiv = document.getElementById('auto-join-loading');
            if (loadingDiv) loadingDiv.remove();
            
            // Clear any disconnect timeout
            if (disconnectTimeout) {
                clearTimeout(disconnectTimeout);
                disconnectTimeout = null;
            }
            reconnectAttempts = 0;
            
            // Save connection state to localStorage
            if (localGameId && localPlayerId) {
                localStorage.setItem('game_portal_game_id', localGameId);
                localStorage.setItem('game_portal_player_id', localPlayerId);
                localStorage.setItem('game_portal_connected', 'true');
                localStorage.removeItem('game_portal_disconnect_time');
            }
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log('Message from server:', data);
            
            switch (data.type) {
                case 'connection_success':
                    localGameType = data.game_type;
                    gameTitle.textContent = `${localGameType.charAt(0).toUpperCase() + localGameType.slice(1)} Game`;
                    renderPlayers(data.players || []);
                    
                    const gameStatus = data.game_status || (data.game_state ? data.game_state.status : 'waiting');
                    const isHost = data.is_host || false;
                    
                    // Show/hide start button based on game status
                    // Allow any player (not just host) to start the game
                    // This allows players joining from mycircles to start the game
                    if (gameStatus === 'waiting') {
                        startGameBtn.classList.remove('hidden');
                        const waitingMsg = document.getElementById('waiting-message');
                        if (waitingMsg) {
                            waitingMsg.style.display = 'block';
                            // Update player count
                            const validPlayers = (data.players || []).filter(p => {
                                const pid = typeof p === 'string' ? p : (p?.player_id || p?.playerId || p?.id);
                                return pid && !pid.startsWith('PLACEHOLDER_');
                            });
                            const maxPlayers = data.max_players || 4;
                            const minPlayers = data.min_players || 2;
                            const playerCountDisplay = document.getElementById('player-count-display');
                            if (playerCountDisplay) {
                                playerCountDisplay.textContent = `${validPlayers.length}/${maxPlayers}`;
                            }
                            
                            // Auto-start if minimum players are met
                            if (validPlayers.length >= minPlayers) {
                                startAutoStartCountdown(minPlayers, validPlayers.length);
                            } else {
                                clearAutoStartCountdown();
                            }
                        }
                    } else {
                        startGameBtn.classList.add('hidden');
                        const waitingMsg = document.getElementById('waiting-message');
                        if (waitingMsg) waitingMsg.style.display = 'none';
                        clearAutoStartCountdown();
                    }
                    
                    // Hide copy link button if game has started
                    if (gameStatus !== 'waiting') {
                        const gameInfoGrid = document.querySelector('div[style*="grid-template-columns"]');
                        if (gameInfoGrid && gameInfoGrid.firstElementChild) {
                            gameInfoGrid.firstElementChild.style.display = 'none';
                        }
                    }
                    
                    // Render game state if available (check if it's a valid game state object)
                    if (data.game_state && typeof data.game_state === 'object' && Object.keys(data.game_state).length > 0 && data.game_state.status !== 'waiting') {
                        currentGameState = data.game_state;
                        renderGame(data.game_state);
                    } else {
                        // Game is waiting - show lobby view
                        currentGameState = null;
                        renderGame(null);
                    }
                    break;
                case 'player_joined':
                case 'player_disconnected':
                case 'player_connected':
                    if (data.players) {
                        renderPlayers(data.players);
                        // Update player count in waiting message if visible
                        const waitingMsg = document.getElementById('waiting-message');
                        if (waitingMsg && waitingMsg.style.display !== 'none') {
                            const validPlayers = data.players.filter(p => {
                                const pid = typeof p === 'string' ? p : (p?.player_id || p?.playerId || p?.id);
                                return pid && !pid.startsWith('PLACEHOLDER_');
                            });
                            const maxPlayers = currentGameState?.max_players || 4;
                            const minPlayers = currentGameState?.min_players || 2;
                            const playerCountDisplay = document.getElementById('player-count-display');
                            if (playerCountDisplay) {
                                playerCountDisplay.textContent = `${validPlayers.length}/${maxPlayers}`;
                            }
                            
                            // Auto-start if minimum players are met
                            if (validPlayers.length >= minPlayers && currentGameState?.status === 'waiting') {
                                startAutoStartCountdown(minPlayers, validPlayers.length);
                            } else if (validPlayers.length < minPlayers) {
                                clearAutoStartCountdown();
                            }
                        }
                    } else {
                        addLogMessage(`Player ${data.player_id ? data.player_id.substring(0, 8) : 'Unknown'} connected/disconnected.`);
                    }
                    break;
                case 'game_started':
                    // Game just started - hide start button and show game UI
                    startGameBtn.classList.add('hidden');
                    const waitingMsg2 = document.getElementById('waiting-message');
                    if (waitingMsg2) waitingMsg2.style.display = 'none';
                    
                    // Clear auto-start countdown
                    clearAutoStartCountdown();
                    
                    // Hide copy link button
                    const gameInfoGrid = document.querySelector('div[style*="grid-template-columns"]');
                    if (gameInfoGrid && gameInfoGrid.firstElementChild) {
                        gameInfoGrid.firstElementChild.style.display = 'none';
                    }
                    
                    // Fall through to state_update handling
                case 'state_update':
                    currentGameState = data.game_state;
                    if (data.players) {
                        renderPlayers(data.players);
                    }
                    
                    // Only render game if we have valid game state
                    if (data.game_state && typeof data.game_state === 'object' && Object.keys(data.game_state).length > 0 && data.game_state.status !== 'waiting') {
                        renderGame(data.game_state);
                    }
                    
                    // Hide copy link button once game has started
                    const currentGameStatus = data.game_status || (data.game_state ? data.game_state.status : null);
                    if (currentGameStatus && currentGameStatus !== 'waiting') {
                        const gameInfoGrid2 = document.querySelector('div[style*="grid-template-columns"]');
                        if (gameInfoGrid2 && gameInfoGrid2.firstElementChild) {
                            gameInfoGrid2.firstElementChild.style.display = 'none';
                        }
                    }
                    
                    if (data.game_state && data.game_state.board) {
                        const leftEndDisplay = document.getElementById('left-end-display');
                        const rightEndDisplay = document.getElementById('right-end-display');
                        if (data.game_state.board.length > 0) {
                            if (leftEndDisplay) leftEndDisplay.textContent = data.game_state.board[0][0];
                            if (rightEndDisplay) rightEndDisplay.textContent = data.game_state.board[data.game_state.board.length - 1][1];
                        } else {
                            if (leftEndDisplay) leftEndDisplay.textContent = '-';
                            if (rightEndDisplay) rightEndDisplay.textContent = '-';
                        }
                    }
                    break;
                case 'error':
                    showError(`❌ Oops! ${data.message || 'Something went wrong. Please try again!'}`);
                    break;
            }
        };
        
        ws.onclose = (event) => {
            console.log('WebSocket disconnected', event);
            
            // Check if this was an initial connection failure (code 1006 = abnormal closure)
            if (event.code === 1006 && (!currentGameState || currentGameState.status === 'waiting')) {
                console.error('WebSocket connection failed. Possible reasons:');
                console.error('- Game not found or not ready');
                console.error('- Player not in game');
                console.error('- Server not running or endpoint incorrect');
                console.error('- Network issue');
                
                // Retry connection once after a delay
                if (!window.wsRetryAttempted) {
                    window.wsRetryAttempted = true;
                    console.log('Retrying WebSocket connection in 2 seconds...');
                    setTimeout(() => {
                        connectWebSocket(gameId, playerId);
                    }, 2000);
                    return;
                } else {
                    // Second attempt failed, show error
                    showError('❌ Failed to connect to game server. The game may not be ready yet. Please refresh and try again.');
                    lobbyView.classList.remove('hidden');
                    gameView.classList.add('hidden');
                    window.wsRetryAttempted = false; // Reset for next attempt
                    return;
                }
            }
            
            // Save disconnect time to localStorage
            if (localGameId && localPlayerId) {
                localStorage.setItem('game_portal_disconnect_time', Date.now().toString());
                localStorage.setItem('game_portal_connected', 'false');
            }
            
            // Only show alert after 60s delay if game has started
            if (currentGameState && currentGameState.status !== 'waiting') {
                // Set a timeout to show alert after delay
                disconnectTimeout = setTimeout(() => {
                    if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                        // Try to reconnect
                        attemptReconnect();
                    } else {
                        showError('⚠️ Connection lost. Please refresh the page.');
                        lobbyView.classList.remove('hidden');
                        gameView.classList.add('hidden');
                        // Clear localStorage
                        localStorage.removeItem('game_portal_game_id');
                        localStorage.removeItem('game_portal_player_id');
                        localStorage.removeItem('game_portal_connected');
                        localStorage.removeItem('game_portal_disconnect_time');
                    }
                }, DISCONNECT_DELAY_MS);
            } else {
                // If game hasn't started, show error in UI
                showError('Connection lost. Please refresh the page.');
                lobbyView.classList.remove('hidden');
                gameView.classList.add('hidden');
            }
        };
        
        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            // Don't show error immediately - wait for close event to get the actual error code
            // The close event will handle showing the error message
        };
        
        // Update global socket reference
        socket = ws;
    }
    
    // --- API Call Functions ---
    function showError(message) {
        const errorDiv = document.getElementById('error-message');
        const errorText = document.getElementById('error-text');
        if (errorDiv && errorText) {
            errorText.textContent = message;
            errorDiv.style.display = 'block';
            // Auto-hide after 5 seconds
            setTimeout(() => {
                errorDiv.style.display = 'none';
            }, 5000);
            // Scroll to error
            errorDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else {
            // Fallback: create a temporary error message in the UI
            const container = document.querySelector('.container');
            if (container) {
                const tempError = document.createElement('div');
                tempError.id = 'temp-error-message';
                tempError.style.cssText = `
                    background: linear-gradient(135deg, #f56565 0%, #e53e3e 100%);
                    color: white;
                    padding: 1rem 1.5rem;
                    border-radius: 12px;
                    margin: 1rem 0;
                    text-align: center;
                    font-weight: 600;
                    box-shadow: 0 4px 12px rgba(245, 101, 101, 0.3);
                    animation: fadeInUp 0.3s ease-out;
                `;
                tempError.textContent = message;
                container.insertBefore(tempError, container.firstChild);
                setTimeout(() => {
                    tempError.style.opacity = '0';
                    tempError.style.transform = 'translateY(-10px)';
                    setTimeout(() => tempError.remove(), 300);
                }, 5000);
            }
        }
    }
    
    async function createGame() {
        const gameType = gameTypeSelect.value;
        
        // Ensure user has selected a game type
        if (!gameType || gameType === '') {
            showError('❌ Please select a game type before creating a game!');
            gameTypeSelect.focus();
            return;
        }
        
        // Only dominoes is supported
        if (gameType !== 'dominoes') {
            showError('❌ Only Dominito (Dominoes) is currently supported!');
            gameTypeSelect.value = 'dominoes';
            updateGameModeSelector();
            return;
        }
        
        let gameMode = dominoGameModeSelect.value;
        
        // Get AI count from selector
        const aiCountSelect = document.getElementById('ai-count');
        const aiCount = aiCountSelect ? parseInt(aiCountSelect.value) || 0 : 0;
        
        if (!browserFingerprint) {
            browserFingerprint = await generateFingerprint();
        }
        
        try {
            const response = await fetch(`${basePath}/api/game/create`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    player_id: browserFingerprint, 
                    game_type: gameType, 
                    game_mode: gameMode,
                    ai_count: aiCount
                })
            });
            const data = await response.json();
            if (response.ok) {
                // Validate response data
                if (!data || !data.game_id) {
                    console.error('Create Game: Invalid response data', data);
                    showError('❌ Oops! Game was created but received invalid response. Please try again!');
                    return;
                }
                
                const gameId = data.game_id;
                const playerId = data.player_id || browserFingerprint;
                
                // Validate gameId is not undefined/null
                if (!gameId || gameId === 'undefined' || gameId === 'null' || typeof gameId !== 'string' || gameId.trim() === '') {
                    console.error('Create Game: Invalid game_id', gameId);
                    showError('❌ Oops! Game was created but received invalid game ID. Please try again!');
                    return;
                }
                
                const shareUrl = `${window.location.origin}${basePath}?game=${gameId}`;
                window.history.pushState({ gameId: gameId }, '', shareUrl);
                
                // Switch to game view immediately
                lobbyView.classList.add('hidden');
                gameView.classList.remove('hidden');
                
                // Set game ID display
                if (gameIdDisplay) {
                    gameIdDisplay.textContent = `Game ID: ${gameId}`;
                    gameIdDisplay.style.display = 'block';
                }
                
                // Verify game exists before connecting WebSocket
                async function verifyAndConnect() {
                    try {
                        // First verify the game exists
                        const verifyResponse = await fetch(`${basePath}/api/game/${gameId}`);
                        if (!verifyResponse.ok) {
                            console.error('Game verification failed:', verifyResponse.status);
                            showError('❌ Game not found. Please try creating a new game.');
                            lobbyView.classList.remove('hidden');
                            gameView.classList.add('hidden');
                            return;
                        }
                        
                        const gameData = await verifyResponse.json();
                        console.log('Game verified:', gameData);
                        
                        // Check if player is in the game
                        const players = gameData.players || [];
                        const playerInGame = players.some(p => {
                            const pid = typeof p === 'string' ? p : (p.player_id || p.playerId || p.id);
                            return pid === playerId;
                        });
                        
                        if (!playerInGame) {
                            console.error('Player not in game:', playerId, 'Players:', players);
                            showError('❌ You are not in this game. Please try joining again.');
                            lobbyView.classList.remove('hidden');
                            gameView.classList.add('hidden');
                            return;
                        }
                        
                        // Now connect WebSocket
                        console.log('Game verified, connecting WebSocket...');
                        connectWebSocket(gameId, playerId);
                    } catch (err) {
                        console.error('Failed to verify game:', err);
                        showError('❌ Failed to verify game. Please refresh and try again.');
                        lobbyView.classList.remove('hidden');
                        gameView.classList.add('hidden');
                    }
                }
                
                // Wait a bit for game to be fully created, then verify and connect
                setTimeout(verifyAndConnect, 1000);
            } else {
                showError(`❌ Oops! ${data.detail || data.error || 'Something went wrong. Please try again!'}`);
            }
        } catch (err) {
            console.error('Create Game failed:', err);
            showError('❌ Oops! Could not create the game. Please check your connection and try again!');
        }
    }
    
    async function joinGame(replaceAiId = null) {
        let gameId = gameIdInput.value.trim();
        
        // Extract game ID from URL if user pasted a full link
        if (gameId.includes('?game=')) {
            try {
                const url = new URL(gameId);
                gameId = url.searchParams.get('game') || gameId;
            } catch (e) {
                // If URL parsing fails, try regex extraction
                const match = gameId.match(/[?&]game=([A-Z0-9]+)/i);
                if (match) {
                    gameId = match[1];
                }
            }
        }
        
        gameId = gameId.toUpperCase();
        if (!gameId || gameId === 'UNDEFINED' || gameId === 'NULL' || gameId.length < 3) { 
            showError('❌ Please enter a valid Game ID or paste the shareable link!'); 
            gameIdInput.focus();
            return; 
        }
        
        if (!browserFingerprint) {
            browserFingerprint = await generateFingerprint();
        }
        
        try {
            // First check if game is in progress and get AI slots
            let aiSlots = [];
            try {
                const aiResponse = await fetch(`${basePath}/api/game/${gameId}/ai-slots`);
                if (aiResponse.ok) {
                    const aiData = await aiResponse.json();
                    aiSlots = aiData.ai_slots || [];
                }
            } catch (e) {
                console.log('Could not fetch AI slots:', e);
            }
            
            // If game is in progress and no replace_ai specified, show options
            if (aiSlots.length > 0 && !replaceAiId) {
                const replaceOption = confirm(
                    `Game is in progress!\n\n` +
                    `Would you like to replace an AI player?\n` +
                    `- Click OK to replace an AI player (play next round)\n` +
                    `- Click Cancel to cancel`
                );
                
                if (replaceOption && aiSlots.length > 0) {
                    // Replace first available AI
                    replaceAiId = aiSlots[0];
                } else {
                    // User cancelled - don't join
                    return;
                }
            }
            
            const response = await fetch(`${basePath}/api/game/${gameId}/join`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    player_id: browserFingerprint,
                    replace_ai: replaceAiId
                })
            });
            const data = await response.json();
            if (response.ok) {
                // Validate response data
                if (!data || !data.game_id) {
                    console.error('Join Game: Invalid response data', data);
                    showError('❌ Oops! Could not join game - invalid response. Please try again!');
                    return;
                }
                
                const gameId = data.game_id;
                const playerId = data.player_id || browserFingerprint;
                
                // Validate gameId is not undefined/null
                if (!gameId || gameId === 'undefined' || gameId === 'null' || typeof gameId !== 'string' || gameId.trim() === '') {
                    console.error('Join Game: Invalid game_id', gameId);
                    showError('❌ Oops! Could not join game - invalid game ID. Please try again!');
                    return;
                }
                
                const shareUrl = `${window.location.origin}${basePath}?game=${gameId}`;
                window.history.pushState({ gameId: gameId }, '', shareUrl);
                connectWebSocket(gameId, playerId);
                
                if (data.replaced_ai) {
                    // Show success message in UI
                    const errorDiv = document.getElementById('error-message');
                    const errorText = document.getElementById('error-text');
                    if (errorDiv && errorText) {
                        errorText.textContent = '✅ You replaced AI player! You\'ll play in the next round.';
                        errorDiv.style.background = 'linear-gradient(135deg, #48bb78 0%, #38a169 100%)';
                        errorDiv.style.display = 'block';
                        setTimeout(() => {
                            errorDiv.style.display = 'none';
                            errorDiv.style.background = 'linear-gradient(135deg, #f56565 0%, #e53e3e 100%)';
                        }, 3000);
                    }
                }
            } else {
                showError(`❌ Oops! ${data.detail || data.error || 'Could not join the game. Please check the Game ID and try again!'}`);
            }
        } catch (err) {
            console.error('Join Game failed:', err);
            showError('❌ Oops! Could not join the game. Please check your connection and try again!');
        }
    }
    
    // --- Auto-Start Countdown Functions ---
    function startAutoStartCountdown(minPlayers, currentPlayers) {
        // Clear any existing countdown
        clearAutoStartCountdown();
        
        // Only auto-start if we have minimum players
        if (currentPlayers < minPlayers) {
            return;
        }
        
        // Show countdown banner
        let countdownBanner = document.getElementById('auto-start-banner');
        if (!countdownBanner) {
            countdownBanner = document.createElement('div');
            countdownBanner.id = 'auto-start-banner';
            countdownBanner.style.cssText = `
                background: linear-gradient(135deg, #48bb78 0%, #38a169 100%);
                color: white;
                padding: 1rem 1.5rem;
                border-radius: 12px;
                margin: 1rem 0;
                text-align: center;
                font-weight: 600;
                box-shadow: 0 4px 12px rgba(72, 187, 120, 0.4);
                animation: slideDownFadeIn 0.4s ease-out;
            `;
            const waitingMsg = document.getElementById('waiting-message');
            if (waitingMsg && waitingMsg.parentNode) {
                waitingMsg.parentNode.insertBefore(countdownBanner, waitingMsg.nextSibling);
            }
        }
        
        let countdown = 3; // 3 second countdown
        countdownBanner.style.display = 'block';
        countdownBanner.innerHTML = `
            <div style="font-size: 1.1rem; margin-bottom: 0.5rem;">🚀 Game will start automatically in <span id="countdown-number" style="font-weight: 800; font-size: 1.3rem;">${countdown}</span> seconds!</div>
            <div style="font-size: 0.85rem; opacity: 0.9;">Or click "Start Game" to start immediately</div>
        `;
        
        const countdownNumber = document.getElementById('countdown-number');
        
        autoStartCountdown = setInterval(() => {
            countdown--;
            if (countdownNumber) {
                countdownNumber.textContent = countdown;
            }
            
            if (countdown <= 0) {
                clearAutoStartCountdown();
                // Auto-start the game
                sendStartGame();
            }
        }, 1000);
    }
    
    function clearAutoStartCountdown() {
        if (autoStartCountdown) {
            clearInterval(autoStartCountdown);
            autoStartCountdown = null;
        }
        const countdownBanner = document.getElementById('auto-start-banner');
        if (countdownBanner) {
            countdownBanner.style.display = 'none';
        }
    }
    
    // --- Send WebSocket Actions ---
    function sendStartGame() {
        clearAutoStartCountdown(); // Clear countdown when manually starting
        if (socket) socket.send(JSON.stringify({ type: 'start_game' }));
    }
    
    function sendMove(moveData) {
        if (socket) {
            if (moveData && moveData.action === 'ready_for_next_hand') {
                socket.send(JSON.stringify({ type: 'ready_for_next_hand' }));
            } else if (moveData && moveData.action === 'ready_for_next_round') {
                socket.send(JSON.stringify({ type: 'ready_for_next_round' }));
            } else {
                socket.send(JSON.stringify({ type: 'make_move', move_data: moveData }));
            }
        }
    }
    
    // --- Render Functions ---
    function renderGame(state) {
        const playerHandDiv = document.getElementById('player-hand');
        const yourHandSection = document.getElementById('your-hand-section');
        const stickyHandList = document.getElementById('hand-list-view');
        const gameLogDiv = document.getElementById('game-log');
        const turnDisplayCard = document.getElementById('turn-display-card');
        const gameTabs = document.getElementById('game-tabs');
        
        if (!state) {
            startGameBtn.classList.remove('hidden');
            dominoesUI.classList.add('hidden');
            if (yourHandSection) yourHandSection.style.display = 'none';
            if (gameTabs) gameTabs.classList.add('hidden');
            if (playerHandDiv) playerHandDiv.innerHTML = '';
            if (stickyHandList) stickyHandList.innerHTML = '';
            if (gameLogDiv) gameLogDiv.innerHTML = '';
            if (turnDisplayCard) turnDisplayCard.style.display = 'none';
            return;
        }
        
        // Show start button if game is waiting, hide it otherwise
        if (state.status === 'waiting') {
            startGameBtn.classList.remove('hidden');
            const waitingMsg = document.getElementById('waiting-message');
            if (waitingMsg) waitingMsg.style.display = 'block';
            if (yourHandSection) yourHandSection.style.display = 'none';
            if (gameTabs) gameTabs.classList.add('hidden');
            dominoesUI.classList.add('hidden');
            if (turnDisplayCard) turnDisplayCard.style.display = 'none';
            
            // Check if we should auto-start
            const validPlayers = (state.players || []).filter(p => {
                const pid = typeof p === 'string' ? p : (p?.player_id || p?.playerId || p?.id);
                return pid && !pid.startsWith('PLACEHOLDER_');
            });
            const minPlayers = state.min_players || 2;
            if (validPlayers.length >= minPlayers) {
                startAutoStartCountdown(minPlayers, validPlayers.length);
            } else {
                clearAutoStartCountdown();
            }
            
            return; // Don't render game UI when waiting
        }
        
        startGameBtn.classList.add('hidden');
        const waitingMsg = document.getElementById('waiting-message');
        if (waitingMsg) waitingMsg.style.display = 'none';
        if (gameTabs) gameTabs.classList.remove('hidden');
        if (gameLogDiv) gameLogDiv.style.display = 'block';
        if (turnDisplayCard) turnDisplayCard.style.display = 'block';
        
        // Render players and log
        if (state.players) {
            renderPlayers(state.players);
        }
        renderLog(state.log || []);
        
        dominoesUI.classList.add('hidden');
        
        // Check if state has required properties for active game
        if (!state.players || state.current_turn_index === undefined) {
            // Game state is incomplete (waiting or invalid) - don't render game UI
            return;
        }
        
        // Get current player ID - handle both string and object formats
        let currentPlayerId = null;
        if (state.players && state.current_turn_index !== undefined) {
            const turnIndex = state.current_turn_index;
            if (turnIndex >= 0 && turnIndex < state.players.length) {
                const currentPlayer = state.players[turnIndex];
                if (typeof currentPlayer === 'string') {
                    currentPlayerId = currentPlayer;
                } else if (typeof currentPlayer === 'object' && currentPlayer !== null) {
                    currentPlayerId = currentPlayer.player_id || currentPlayer.playerId || currentPlayer.id;
                }
            }
        }
        
        // Check if it's my turn - compare player IDs properly
        const myTurn = currentPlayerId && localPlayerId && currentPlayerId === localPlayerId;
        
        if (myTurn) {
            turnDisplay.textContent = "🎯 YOUR TURN! 🎯";
            turnDisplayCard.style.background = 'linear-gradient(135deg, #48bb78 0%, #38a169 100%)';
            turnDisplayCard.style.boxShadow = '0 8px 24px rgba(72, 187, 120, 0.5)';
        } else if (currentPlayerId) {
            // Use the helper function to get player display name
            const playerName = getPlayerDisplayName(currentPlayerId);
            turnDisplay.textContent = `⏳ ${playerName}'s Turn`;
            turnDisplayCard.style.background = 'linear-gradient(135deg, #f6ad55 0%, #ed8936 100%)';
            turnDisplayCard.style.boxShadow = '0 4px 12px rgba(246, 173, 85, 0.3)';
        } else {
            // Fallback if current player is not available
            turnDisplay.textContent = "⏳ Waiting...";
            turnDisplayCard.style.background = 'linear-gradient(135deg, #f6ad55 0%, #ed8936 100%)';
            turnDisplayCard.style.boxShadow = '0 4px 12px rgba(246, 173, 85, 0.3)';
        }
        
        if (localGameType === 'dominoes') {
            renderDominoes(state, myTurn);
        }
    }
    
    function renderPlayers(players) {
        // Filter out placeholder players before counting
        const validPlayers = players.filter(p => {
            let playerId;
            if (typeof p === 'string') {
                playerId = p;
            } else if (typeof p === 'object' && p !== null) {
                playerId = p.player_id || p.playerId || p.id || 'unknown';
            } else {
                return false;
            }
            return !playerId.startsWith('PLACEHOLDER_') && !playerId.match(/^player_\d+/);
        });
        
        // Update player count display - magical: shows 0/4 when empty!
        const playerCountDisplay = document.getElementById('player-count-display');
        if (playerCountDisplay) {
            const maxPlayers = currentGameState?.max_players || 4;
            const playerCount = validPlayers.length;
            playerCountDisplay.textContent = `${playerCount}/${maxPlayers}`;
            // Magical experience: empty lobby shows 0/4 and next player becomes host!
        }
        
        playersList.innerHTML = '<strong>Players:</strong> ';
        validPlayers.forEach(p => {
            // Handle both string and object player formats
            let playerId;
            if (typeof p === 'string') {
                playerId = p;
            } else if (typeof p === 'object' && p !== null) {
                playerId = p.player_id || p.playerId || p.id || 'unknown';
            } else {
                console.warn('Invalid player format:', p);
                return; // Skip invalid player formats
            }
            
            // Filter out placeholder players - they should never be displayed
            if (playerId && (playerId.startsWith('PLACEHOLDER_') || playerId.startsWith('placeholder_'))) {
                console.warn('Found placeholder player in players list, skipping display:', playerId);
                return; // Skip rendering placeholder players
            }
            
            // Note: We no longer filter out "player_#" patterns as they might be valid IDs
            // The issue was likely in how player IDs were being generated/displayed
            
            // Extract AI status
            const isAI = (typeof p === 'object' && p !== null) ? (p.isAI || p.is_ai || false) : false;
            
            const displayName = isAI ? `AutoBot` : `Player ${playerId.substring(0, 8)}`;
            const aiBadge = isAI ? '<span style="background: linear-gradient(135deg, #f6ad55 0%, #ed8936 100%); color: white; padding: 0.25rem 0.5rem; border-radius: 6px; font-size: 0.75rem; font-weight: 700; margin-left: 0.5rem;">🤖 AI</span>' : '';
            
            const replaceButton = isAI && currentGameState && currentGameState.status !== 'waiting' 
                ? `<button onclick="replaceAI('${playerId}')" style="background: linear-gradient(135deg, #48bb78 0%, #38a169 100%); color: white; padding: 0.25rem 0.75rem; border-radius: 6px; font-size: 0.75rem; font-weight: 600; margin-left: 0.5rem; border: none; cursor: pointer;">Replace AI</button>` 
                : '';
            
            playersList.innerHTML += `
                <div style="display: inline-flex; align-items: center; gap: 0.75rem; margin: 0.5rem 1rem 0.5rem 0; padding: 0.75rem 1rem; background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); transition: all 0.3s ease;">
                    <svg width="40" height="40" data-jdenticon-value="${playerId}" style="border-radius: 50%; background: #f7fafc; padding: 4px; flex-shrink: 0;"></svg>
                    <div style="flex: 1;">
                        <div style="font-weight: 600; color: #2d3748; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">${displayName}${aiBadge}${replaceButton}</div>
                        <div style="font-size: 0.75rem; color: #718096;">${playerId.substring(0, 12)}...</div>
                    </div>
                </div>
            `;
        });
        
        setTimeout(() => {
            if (typeof jdenticon !== 'undefined') {
                jdenticon();
            }
        }, 100);
    }
    
    // Global function for replacing AI (called from inline onclick)
    window.replaceAI = async function(aiId) {
        if (!localGameId || !browserFingerprint) {
            showError('❌ Cannot replace AI: not connected to a game');
            return;
        }
        
        try {
            const response = await fetch(`${basePath}/api/game/${localGameId}/join`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    player_id: browserFingerprint,
                    replace_ai: aiId,
                })
            });
            const data = await response.json();
            if (response.ok) {
                // Show success message in UI
                const errorDiv = document.getElementById('error-message');
                const errorText = document.getElementById('error-text');
                if (errorDiv && errorText) {
                    errorText.textContent = '✅ You replaced AI player! You\'ll play in the next round.';
                    errorDiv.style.background = 'linear-gradient(135deg, #48bb78 0%, #38a169 100%)';
                    errorDiv.style.display = 'block';
                    setTimeout(() => {
                        errorDiv.style.display = 'none';
                        errorDiv.style.background = 'linear-gradient(135deg, #f56565 0%, #e53e3e 100%)';
                    }, 3000);
                }
                // Reconnect to get updated state
                connectWebSocket(localGameId, browserFingerprint);
            } else {
                showError(`❌ ${data.detail || data.error || 'Could not replace AI player'}`);
            }
        } catch (err) {
            console.error('Replace AI failed:', err);
            showError('❌ Could not replace AI player. Please try again!');
        }
    };
    
    function renderLog(logEntries) {
        if (!logEntries || logEntries.length === 0) return;
        
        gameLogDiv.innerHTML = '';
        
        const cleanedEntries = [];
        const seenMessages = new Set();
        
        logEntries.forEach(entry => {
            if (entry.includes('LEADERBOARD') || entry.includes('Round Wins:')) {
                return;
            }
            
            if (entry.includes('Round Wins:') && entry.includes(',')) {
                return;
            }
            
            let cleanedEntry = entry;
            if (currentGameState && currentGameState.players) {
                currentGameState.players.forEach(pid => {
                    const displayName = getPlayerDisplayName(pid);
                    cleanedEntry = cleanedEntry.replace(new RegExp(pid, 'g'), displayName);
                });
            }
            
            if (!seenMessages.has(cleanedEntry)) {
                seenMessages.add(cleanedEntry);
                cleanedEntries.push(cleanedEntry);
            }
        });
        
        cleanedEntries.slice().reverse().forEach(entry => {
            let className = '';
            let isExciting = false;
            
            if (entry.includes('WINS THE GAME')) {
                className = 'victory-message';
                isExciting = true;
            } else if (entry.includes('WINS ROUND') || (entry.includes('Round') && entry.includes('complete'))) {
                className = 'round-complete-message';
                isExciting = true;
            } else if (entry.includes('beats dealer') || entry.includes('🎉')) {
                className = 'win-message';
                isExciting = true;
            } else if (entry.includes('loses') || entry.includes('busts') || entry.includes('😢')) {
                className = 'loss-message';
                isExciting = true;
            } else if (entry.includes('Round') && entry.includes('started')) {
                className = 'round-start-message';
                isExciting = true;
            }
            
            if (isExciting) {
                gameLogDiv.innerHTML += `<div class="${className}">${entry}</div>`;
            } else {
                gameLogDiv.innerHTML += `<p style="color: #718096; font-size: 0.9rem; margin: 0.25rem 0;">${entry}</p>`;
            }
        });
    }
    
    function addLogMessage(message) {
        gameLogDiv.innerHTML = `<p>${message}</p>` + gameLogDiv.innerHTML;
    }
    
    // --- Helper: Create a beautiful playing card element ---
    function createPlayingCard(card, isHidden = false) {
        if (isHidden) {
            const cardDiv = document.createElement('div');
            cardDiv.className = 'card hidden-card';
            return cardDiv;
        }
        
        const suit = card.suit || '';
        const rank = card.rank;
        const isRed = suit === '♥' || suit === '♦';
        
        const suitMap = {
            '♥': '♥',
            '♦': '♦',
            '♣': '♣',
            '♠': '♠'
        };
        const suitSymbol = suitMap[suit] || suit;
        
        const cardDiv = document.createElement('div');
        cardDiv.className = `card ${isRed ? 'red' : 'black'}`;
        
        const topLeftCorner = document.createElement('div');
        topLeftCorner.className = 'card-corner card-corner-top';
        const rankTop = document.createElement('span');
        rankTop.className = 'card-rank';
        rankTop.textContent = rank;
        const suitTop = document.createElement('span');
        suitTop.className = 'card-suit';
        suitTop.textContent = suitSymbol;
        topLeftCorner.appendChild(rankTop);
        topLeftCorner.appendChild(suitTop);
        cardDiv.appendChild(topLeftCorner);
        
        const centerSymbol = document.createElement('div');
        centerSymbol.className = 'card-center';
        centerSymbol.textContent = suitSymbol;
        cardDiv.appendChild(centerSymbol);
        
        const bottomRightCorner = document.createElement('div');
        bottomRightCorner.className = 'card-corner card-corner-bottom';
        const rankBottom = document.createElement('span');
        rankBottom.className = 'card-rank';
        rankBottom.textContent = rank;
        const suitBottom = document.createElement('span');
        suitBottom.className = 'card-suit';
        suitBottom.textContent = suitSymbol;
        bottomRightCorner.appendChild(rankBottom);
        bottomRightCorner.appendChild(suitBottom);
        cardDiv.appendChild(bottomRightCorner);
        
        return cardDiv;
    }
    
    function getPlayerDisplayName(playerId) {
        if (!playerId) return 'Unknown Player';
        if (playerId === localPlayerId) return 'You';
        
        // Check if it's an AI player
        if (playerId && playerId.startsWith('AI_')) {
            return 'AutoBot';
        }
        
        // Filter out placeholder players
        if (playerId && (playerId.startsWith('PLACEHOLDER_') || playerId.startsWith('placeholder_'))) {
            return 'Unknown Player';
        }
        
        // Try to find player in the players list DOM
        const playerCards = playersList.querySelectorAll('div[style*="display: inline-flex"]');
        for (const card of playerCards) {
            const svg = card.querySelector('svg');
            if (svg && svg.getAttribute('data-jdenticon-value') === playerId) {
                if (card.textContent.includes('🤖 AI')) {
                    return 'AutoBot';
                }
                const nameDiv = card.querySelector('div > div');
                if (nameDiv) {
                    const nameText = nameDiv.textContent.split('🤖')[0].trim();
                    if (nameText) return nameText;
                }
            }
        }
        
        // Fallback: generate a display name from player ID
        if (playerId && playerId.length > 0) {
            return `Player ${playerId.substring(0, 8)}`;
        }
        
        return 'Unknown Player';
    }
    
    // --- Helper: Create a domino tile element ---
    function createDominoTile(value1, value2, tileData = null) {
        const domino = document.createElement('div');
        domino.className = 'domino';
        
        const half1 = document.createElement('div');
        half1.className = `half half-${value1}`;
        for (let i = 0; i < value1; i++) {
            const pip = document.createElement('span');
            pip.className = 'pip';
            half1.appendChild(pip);
        }
        
        const half2 = document.createElement('div');
        half2.className = `half half-${value2}`;
        for (let i = 0; i < value2; i++) {
            const pip = document.createElement('span');
            pip.className = 'pip';
            half2.appendChild(pip);
        }
        
        domino.appendChild(half1);
        domino.appendChild(half2);
        
        if (tileData) {
            domino.dataset.tile = JSON.stringify(tileData);
        }
        
        return domino;
    }
    
    function renderBoardMap(board) {
        const boardMapDiv = document.getElementById('board-map');
        if (!boardMapDiv) {
            // Element doesn't exist, skip rendering
            return;
        }
        
        boardMapDiv.innerHTML = '';
        
        if (board.length === 0) {
            const emptyMsg = document.createElement('div');
            emptyMsg.style.cssText = 'text-align: center; color: rgba(255,255,255,0.6); font-style: italic; padding: 1rem;';
            emptyMsg.textContent = 'No tiles on board';
            boardMapDiv.appendChild(emptyMsg);
            return;
        }
        
        board.forEach((tile, index) => {
            const item = document.createElement('div');
            item.className = 'board-map-item';
            item.innerHTML = `
                <span>#${index + 1}</span>
                <span>[${tile[0]}|${tile[1]}]</span>
            `;
            boardMapDiv.appendChild(item);
        });
    }
    
    // --- Dominoes Specific Render ---
    function renderDominoes(state, myTurn) {
        dominoesUI.classList.remove('hidden');
        
        // Add focus to board and hand when it's someone's turn
        // The dominoBoardDiv IS the board-simple container
        const boardContainer = dominoBoardDiv;
        const yourHandSection = document.getElementById('your-hand-section');
        
        // Get current player info for display
        const currentPlayerId = state.players && state.current_turn_index !== undefined && 
            state.current_turn_index >= 0 && state.current_turn_index < state.players.length ?
            state.players[state.current_turn_index] : null;
        const currentPlayerName = currentPlayerId ? getPlayerDisplayName(currentPlayerId) : 'Unknown';
        
        if (state.status === 'in_progress') {
            if (myTurn) {
                // It's the current player's turn - add strong focus
                if (boardContainer) {
                    boardContainer.classList.add('board-focus-active');
                    boardContainer.classList.remove('board-focus-waiting');
                }
                if (yourHandSection) {
                    yourHandSection.classList.add('hand-focus-active');
                    yourHandSection.classList.remove('hand-focus-waiting');
                }
                
                // Add "Your Turn" banner to board
                let turnBanner = boardContainer?.querySelector('.turn-focus-banner');
                if (!turnBanner && boardContainer) {
                    turnBanner = document.createElement('div');
                    turnBanner.className = 'turn-focus-banner';
                    turnBanner.style.cssText = `
                        position: absolute;
                        top: -12px;
                        left: 50%;
                        transform: translateX(-50%);
                        background: linear-gradient(135deg, #48bb78 0%, #38a169 100%);
                        color: white;
                        padding: 0.5rem 1.5rem;
                        border-radius: 20px;
                        font-weight: 700;
                        font-size: 0.9rem;
                        box-shadow: 0 4px 12px rgba(72, 187, 120, 0.5);
                        z-index: 100;
                        white-space: nowrap;
                        animation: bannerPulse 2s ease-in-out infinite;
                    `;
                    turnBanner.textContent = '🎯 YOUR TURN - PLAY A TILE! 🎯';
                    boardContainer.style.position = 'relative';
                    boardContainer.appendChild(turnBanner);
                }
            } else {
                // It's someone else's turn - add subtle focus
                if (boardContainer) {
                    boardContainer.classList.add('board-focus-waiting');
                    boardContainer.classList.remove('board-focus-active');
                    
                    // Add "Waiting" banner
                    let waitBanner = boardContainer.querySelector('.turn-focus-banner');
                    if (!waitBanner) {
                        waitBanner = document.createElement('div');
                        waitBanner.className = 'turn-focus-banner';
                        waitBanner.style.cssText = `
                            position: absolute;
                            top: -12px;
                            left: 50%;
                            transform: translateX(-50%);
                            background: linear-gradient(135deg, #f6ad55 0%, #ed8936 100%);
                            color: white;
                            padding: 0.5rem 1.5rem;
                            border-radius: 20px;
                            font-weight: 600;
                            font-size: 0.85rem;
                            box-shadow: 0 4px 12px rgba(246, 173, 85, 0.4);
                            z-index: 100;
                            white-space: nowrap;
                        `;
                        waitBanner.textContent = `⏳ Waiting for ${currentPlayerName}...`;
                        boardContainer.style.position = 'relative';
                        boardContainer.appendChild(waitBanner);
                    } else {
                        waitBanner.textContent = `⏳ Waiting for ${currentPlayerName}...`;
                        waitBanner.style.background = 'linear-gradient(135deg, #f6ad55 0%, #ed8936 100%)';
                    }
                }
                if (yourHandSection) {
                    yourHandSection.classList.add('hand-focus-waiting');
                    yourHandSection.classList.remove('hand-focus-active');
                }
            }
        } else {
            // Game not in progress - remove all focus
            if (boardContainer) {
                boardContainer.classList.remove('board-focus-active', 'board-focus-waiting');
                const banner = boardContainer.querySelector('.turn-focus-banner');
                if (banner) banner.remove();
            }
            if (yourHandSection) {
                yourHandSection.classList.remove('hand-focus-active', 'hand-focus-waiting');
            }
        }
        
        // Show welcome message for first-time users
        const welcomeMessage = document.getElementById('welcome-message');
        const hasSeenWelcome = localStorage.getItem('dominoes_welcome_seen');
        if (welcomeMessage && !hasSeenWelcome && state.status === 'in_progress' && myTurn) {
            welcomeMessage.classList.remove('hidden');
        }
        
        const nextHandContainer = document.getElementById('next-hand-container');
        const nextHandBtn = document.getElementById('next-hand-btn');
        const readyStatusSpan = document.getElementById('ready-status');
        
        if (state.status === 'hand_finished') {
            if (nextHandContainer) {
                nextHandContainer.classList.remove('hidden');
                nextHandContainer.style.display = 'block';
                
                // Smooth scroll to ready button
                setTimeout(() => {
                    nextHandContainer.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }, 500);
            }
            
            const readyPlayers = state.ready_for_next_hand || {};
            const totalPlayers = state.players ? state.players.length : 0;
            const readyCount = Object.keys(readyPlayers).length;
            const isReady = readyPlayers[localPlayerId] || false;
            const handNum = state.hand_number || 1;
            const nextHandNum = handNum + 1;
            
            // Build ready players list
            let readyListHtml = '';
            if (state.players) {
                // Get player info from the players list (which has isAI flags)
                const playerInfoMap = {};
                if (currentGameState && currentGameState.players) {
                    // Try to get player info from the last rendered players list
                    const playerCards = playersList.querySelectorAll('div[style*="display: inline-flex"]');
                    playerCards.forEach(card => {
                        const svg = card.querySelector('svg');
                        if (svg) {
                            const pid = svg.getAttribute('data-jdenticon-value');
                            const isAI = card.textContent.includes('🤖 AI');
                            playerInfoMap[pid] = { isAI };
                        }
                    });
                }
                
                state.players.forEach(pid => {
                    const playerName = getPlayerDisplayName(pid);
                    const isPlayerReady = readyPlayers[pid] || false;
                    const isAI = playerInfoMap[pid]?.isAI || pid.startsWith('AI_');
                    
                    if (isPlayerReady) {
                        readyListHtml += `<div style="display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem; background: rgba(255, 255, 255, 0.2); border-radius: 8px; margin: 0.25rem 0;">
                            <span style="font-size: 1.2rem;">✓</span>
                            <span style="font-weight: 600;">${playerName}</span>
                            ${isAI ? '<span style="font-size: 0.75rem; opacity: 0.8;">(AI)</span>' : ''}
                        </div>`;
                    } else {
                        readyListHtml += `<div style="display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem; background: rgba(255, 255, 255, 0.1); border-radius: 8px; margin: 0.25rem 0; opacity: 0.7;">
                            <span style="font-size: 1.2rem;">⏳</span>
                            <span>${playerName}</span>
                            ${isAI ? '<span style="font-size: 0.75rem; opacity: 0.8;">(AI)</span>' : ''}
                        </div>`;
                    }
                });
            }
            
            if (readyStatusSpan) {
                if (readyCount >= totalPlayers) {
                    readyStatusSpan.innerHTML = `
                        <div style="font-size: 1.2rem; margin-bottom: 0.5rem;">🎉 All players ready!</div>
                        <div style="font-size: 0.9rem; opacity: 0.9;">Starting Hand ${nextHandNum} in a moment...</div>
                        <div style="margin-top: 1rem; max-height: 150px; overflow-y: auto; font-size: 0.9rem;">${readyListHtml}</div>
                    `;
                    readyStatusSpan.style.color = '#c6f6d5';
                    readyStatusSpan.style.fontWeight = '700';
                } else {
                    const remaining = totalPlayers - readyCount;
                    readyStatusSpan.innerHTML = `
                        <div style="font-size: 1rem; margin-bottom: 0.75rem;">Waiting for ${remaining} more player${remaining !== 1 ? 's' : ''}... (${readyCount}/${totalPlayers} ready)</div>
                        <div style="margin-top: 0.75rem; max-height: 150px; overflow-y: auto; font-size: 0.9rem;">${readyListHtml}</div>
                    `;
                    readyStatusSpan.style.color = 'rgba(255, 255, 255, 0.95)';
                    readyStatusSpan.style.fontWeight = '600';
                }
            }
            
            if (nextHandBtn) {
                if (isReady) {
                    nextHandBtn.disabled = true;
                    nextHandBtn.textContent = '✓ You\'re Ready!';
                    nextHandBtn.style.opacity = '0.8';
                    nextHandBtn.style.cursor = 'not-allowed';
                    nextHandBtn.style.background = 'linear-gradient(135deg, #48bb78 0%, #38a169 100%)';
                    nextHandBtn.style.animation = 'none';
                } else {
                    nextHandBtn.disabled = false;
                    nextHandBtn.textContent = `▶ Ready for Hand ${nextHandNum}`;
                    nextHandBtn.style.opacity = '1';
                    nextHandBtn.style.cursor = 'pointer';
                    nextHandBtn.style.background = 'white';
                    nextHandBtn.style.animation = 'pulse 2s ease-in-out infinite';
                }
            }
        } else {
            if (nextHandContainer) {
                nextHandContainer.classList.add('hidden');
                nextHandContainer.style.display = 'none';
            }
        }
        
        // Handle scores display - make it compact and only show when needed
        let scoresDiv = document.getElementById('domino-scores');
        
        if (state.game_mode === 'boricua' && state.team_scores) {
            if (!scoresDiv) {
                scoresDiv = document.createElement('div');
                scoresDiv.id = 'domino-scores';
                scoresDiv.style.cssText = 'margin-bottom: 0.75rem; padding: 0.75rem; background: rgba(255, 255, 255, 0.1); border-radius: 8px; font-size: 0.9rem;';
                dominoesUI.insertBefore(scoresDiv, dominoesUI.firstChild);
            }
            scoresDiv.style.display = 'block';
            const teams = state.teams;
            const teamScores = state.team_scores;
            scoresDiv.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
                    <span style="font-weight: 700; font-size: 0.95rem;">📊 Team Scores:</span>
                    <span style="font-size: 0.85rem; opacity: 0.9;">Hand #${state.hand_number}</span>
                </div>
                <div style="display: flex; gap: 1rem; margin-top: 0.5rem; flex-wrap: wrap;">
                    <div style="flex: 1; min-width: 120px; padding: 0.5rem; background: ${teamScores.team1 >= teamScores.team2 ? 'rgba(76, 175, 80, 0.2)' : 'rgba(255, 255, 255, 0.1)'}; border-radius: 6px; font-weight: ${teamScores.team1 >= teamScores.team2 ? '700' : '600'};">
                        Team 1: <strong>${teamScores.team1}</strong>
                    </div>
                    <div style="flex: 1; min-width: 120px; padding: 0.5rem; background: ${teamScores.team2 >= teamScores.team1 ? 'rgba(76, 175, 80, 0.2)' : 'rgba(255, 255, 255, 0.1)'}; border-radius: 6px; font-weight: ${teamScores.team2 >= teamScores.team1 ? '700' : '600'};">
                        Team 2: <strong>${teamScores.team2}</strong>
                    </div>
                </div>
            `;
        } else if (state.game_mode === 'classic' && state.hand_wins) {
            if (!scoresDiv) {
                scoresDiv = document.createElement('div');
                scoresDiv.id = 'domino-scores';
                scoresDiv.style.cssText = 'margin-bottom: 0.75rem; padding: 0.75rem; background: rgba(255, 255, 255, 0.1); border-radius: 8px; font-size: 0.9rem;';
                dominoesUI.insertBefore(scoresDiv, dominoesUI.firstChild);
            }
            scoresDiv.style.display = 'block';
            const sortedWins = Object.entries(state.hand_wins).sort((a, b) => b[1] - a[1]);
            let scoresHtml = `
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
                    <span style="font-weight: 700; font-size: 0.95rem;">📊 Hand Wins:</span>
                    <span style="font-size: 0.85rem; opacity: 0.9;">Hand #${state.hand_number}</span>
                </div>
                <div style="display: flex; gap: 0.75rem; margin-top: 0.5rem; flex-wrap: wrap;">
            `;
            sortedWins.forEach(([pid, wins], index) => {
                const isWinner = wins >= 3;
                const playerName = getPlayerDisplayName(pid);
                scoresHtml += `
                    <div style="padding: 0.4rem 0.75rem; background: ${isWinner ? 'rgba(76, 175, 80, 0.2)' : 'rgba(255, 255, 255, 0.1)'}; border-radius: 6px; font-weight: ${isWinner ? '700' : '600'}; font-size: 0.85rem;">
                        ${playerName}: <strong>${wins}</strong>
                    </div>
                `;
            });
            scoresHtml += `</div>`;
            scoresDiv.innerHTML = scoresHtml;
        } else {
            // Hide scores div when not needed
            if (scoresDiv) {
                scoresDiv.style.display = 'none';
            }
        }
        
        if (handValueSpan) {
            handValueSpan.textContent = '';
        }
        
        dominoBoardDiv.innerHTML = '';
        
        // Add helpful legend for newbies
        if (state.board.length > 0) {
            const legendDiv = document.createElement('div');
            legendDiv.style.cssText = `
                background: rgba(255, 255, 255, 0.15);
                padding: 0.75rem 1rem;
                border-radius: 8px;
                margin-bottom: 1rem;
                font-size: 0.85rem;
                color: rgba(255, 255, 255, 0.95);
                text-align: center;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 1rem;
                flex-wrap: wrap;
            `;
            legendDiv.innerHTML = `
                <span style="display: flex; align-items: center; gap: 0.5rem;">
                    <span style="font-weight: 700;">📋 Board Legend:</span>
                </span>
                <span style="display: flex; align-items: center; gap: 0.5rem;">
                    <span style="background: rgba(255, 255, 0, 0.3); padding: 0.2rem 0.5rem; border-radius: 4px; font-weight: 600;">🟡</span>
                    <span>Playable Ends</span>
                </span>
                <span style="display: flex; align-items: center; gap: 0.5rem;">
                    <span style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 0.2rem 0.5rem; border-radius: 4px; font-weight: 600; color: white; font-size: 0.7rem;">#1</span>
                    <span>Move Number</span>
                </span>
            `;
            dominoBoardDiv.appendChild(legendDiv);
        }
        
        if (state.board.length === 0) {
            const emptyMsg = document.createElement('div');
            emptyMsg.style.cssText = 'text-align: center; color: rgba(255,255,255,0.7); font-style: italic; padding: 1rem;';
            emptyMsg.textContent = 'No tiles on the board yet. Play the first tile!';
            dominoBoardDiv.appendChild(emptyMsg);
        } else {
            // Get move history to show who played what
            const moveHistory = state.move_history || [];
            
            // Create a wrapper for better layout
            const boardWrapper = document.createElement('div');
            boardWrapper.style.cssText = 'display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; justify-content: center; padding: 1rem;';
            
            state.board.forEach((tile, index) => {
                // Find move info for this tile
                const moveInfo = moveHistory[index] || null;
                const playerId = moveInfo ? moveInfo.player_id : null;
                const timestamp = moveInfo ? moveInfo.timestamp : null;
                const moveNumber = moveInfo ? moveInfo.move_number : (index + 1);
                
                // Create container for tile with info
                const tileContainer = document.createElement('div');
                tileContainer.style.cssText = 'display: flex; flex-direction: column; align-items: center; gap: 0.5rem; position: relative;';
                tileContainer.className = 'board-tile-container';
                
                // Check if this is a playable end (first or last tile)
                const isPlayableEnd = index === 0 || index === state.board.length - 1;
                
                // Create visual domino tile
                const dominoTile = createDominoTile(tile[0], tile[1]);
                dominoTile.classList.add('board-tile');
                dominoTile.style.cssText = 'margin: 0; cursor: default;';
                
                // Highlight playable ends
                if (isPlayableEnd) {
                    dominoTile.style.border = '3px solid rgba(255, 255, 0, 0.8)';
                    dominoTile.style.boxShadow = '0 4px 15px rgba(255, 255, 0, 0.5), 0 1px 2px rgba(0,0,0,0.1)';
                    if (index === 0) {
                        dominoTile.style.borderLeft = '5px solid rgba(255, 255, 0, 1)';
                    } else {
                        dominoTile.style.borderRight = '5px solid rgba(255, 255, 0, 1)';
                    }
                }
                
                // Add player info badge
                const infoBadge = document.createElement('div');
                infoBadge.style.cssText = `
                    position: absolute;
                    top: -8px;
                    right: -8px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    font-size: 0.65rem;
                    font-weight: 700;
                    padding: 0.2rem 0.4rem;
                    border-radius: 8px;
                    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
                    z-index: 10;
                    white-space: nowrap;
                    min-width: 20px;
                    text-align: center;
                `;
                infoBadge.textContent = `#${moveNumber}`;
                infoBadge.title = moveInfo ? 
                    `Move #${moveNumber} by ${getPlayerDisplayName(playerId)}${timestamp ? ` at ${new Date(timestamp).toLocaleTimeString()}` : ''}` :
                    `Move #${moveNumber}`;
                dominoTile.appendChild(infoBadge);
                
                // Add playable end indicator
                if (isPlayableEnd) {
                    const endIndicator = document.createElement('div');
                    endIndicator.style.cssText = `
                        position: absolute;
                        ${index === 0 ? 'left: -12px;' : 'right: -12px;'}
                        top: 50%;
                        transform: translateY(-50%);
                        background: rgba(255, 255, 0, 0.9);
                        color: #1a1a2e;
                        font-size: 0.6rem;
                        font-weight: 800;
                        padding: 0.15rem 0.3rem;
                        border-radius: 4px;
                        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
                        z-index: 10;
                        white-space: nowrap;
                    `;
                    endIndicator.textContent = index === 0 ? 'LEFT' : 'RIGHT';
                    endIndicator.title = `This is the ${index === 0 ? 'left' : 'right'} playable end`;
                    dominoTile.appendChild(endIndicator);
                }
                
                // Add player name and time below tile
                const infoDiv = document.createElement('div');
                infoDiv.style.cssText = `
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    gap: 0.15rem;
                    font-size: 0.7rem;
                    color: rgba(255, 255, 255, 0.95);
                    text-align: center;
                    max-width: 90px;
                    line-height: 1.2;
                `;
                
                if (playerId) {
                    const playerName = getPlayerDisplayName(playerId);
                    const isAI = playerId.startsWith('AI_');
                    const nameSpan = document.createElement('div');
                    nameSpan.style.cssText = `
                        font-weight: 600;
                        color: ${isAI ? '#fbbf24' : '#ffffff'};
                        text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
                    `;
                    nameSpan.textContent = isAI ? `🤖 ${playerName}` : playerName;
                    infoDiv.appendChild(nameSpan);
                    
                    if (timestamp) {
                        const timeSpan = document.createElement('div');
                        timeSpan.style.cssText = 'font-size: 0.6rem; opacity: 0.85; color: rgba(255, 255, 255, 0.9);';
                        const moveTime = new Date(timestamp);
                        const now = new Date();
                        const diffMs = now - moveTime;
                        const diffSec = Math.floor(diffMs / 1000);
                        const diffMin = Math.floor(diffSec / 60);
                        
                        if (diffSec < 10) {
                            timeSpan.textContent = 'just now';
                        } else if (diffSec < 60) {
                            timeSpan.textContent = `${diffSec}s ago`;
                        } else if (diffMin < 60) {
                            timeSpan.textContent = `${diffMin}m ago`;
                        } else {
                            timeSpan.textContent = moveTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                        }
                        infoDiv.appendChild(timeSpan);
                    }
                } else {
                    const unknownSpan = document.createElement('div');
                    unknownSpan.style.cssText = 'font-weight: 600; opacity: 0.7;';
                    unknownSpan.textContent = 'Unknown';
                    infoDiv.appendChild(unknownSpan);
                }
                
                tileContainer.appendChild(dominoTile);
                tileContainer.appendChild(infoDiv);
                boardWrapper.appendChild(tileContainer);
            });
            
            dominoBoardDiv.appendChild(boardWrapper);
        }
        
        renderBoardMap(state.board);
        
        // Update board ends display
        const dominoEndsSpan = document.getElementById('domino-ends');
        if (dominoEndsSpan) {
            if (state.board.length === 0) {
                dominoEndsSpan.textContent = 'Empty - Start the game!';
            } else {
                const leftEnd = state.board[0][0];
                const rightEnd = state.board[state.board.length - 1][1];
                if (leftEnd === rightEnd) {
                    dominoEndsSpan.textContent = `${leftEnd} | ${rightEnd} (both ends match!)`;
                } else {
                    dominoEndsSpan.textContent = `${leftEnd} | ${rightEnd}`;
                }
            }
        }
        
        
        const myHand = state.hands[localPlayerId];
        let playableTiles = [];
        let playableTilesInfo = {}; // Store which ends each tile can match
        let hasPlayableTile = false;
        let leftEnd = null;
        let rightEnd = null;
        
        if (Array.isArray(myHand)) {
            if (state.board.length === 0) {
                playableTiles = myHand.map(t => JSON.stringify(t));
                hasPlayableTile = myHand.length > 0;
                // All tiles are playable when board is empty
                myHand.forEach(tile => {
                    playableTilesInfo[JSON.stringify(tile)] = { left: true, right: true };
                });
            } else {
                leftEnd = state.board[0][0];
                rightEnd = state.board[state.board.length - 1][1];
                
                myHand.forEach(tile => {
                    const tileKey = JSON.stringify(tile);
                    const canPlayLeft = tile[0] === leftEnd || tile[1] === leftEnd;
                    const canPlayRight = tile[0] === rightEnd || tile[1] === rightEnd;
                    
                    if (canPlayLeft || canPlayRight) {
                        playableTiles.push(tileKey);
                        playableTilesInfo[tileKey] = { 
                            left: canPlayLeft, 
                            right: canPlayRight,
                            leftMatch: canPlayLeft ? (tile[0] === leftEnd ? tile[0] : tile[1]) : null,
                            rightMatch: canPlayRight ? (tile[0] === rightEnd ? tile[0] : tile[1]) : null
                        };
                    }
                });
                hasPlayableTile = playableTiles.length > 0;
            }
        }
        
        // Buttons are now on tiles themselves, no need to hide separate buttons
        
        
        // Get hand elements - now integrated into main game view
        const yourHandSection = document.getElementById('your-hand-section');
        const stickyHandList = document.getElementById('hand-list-view');
        const handValueSticky = document.getElementById('hand-value-sticky');
        
        // Update hand value displays
        if (handValueSticky) handValueSticky.textContent = myHand.length;
        
        // Clear all hand containers
        if (playerHandDiv) playerHandDiv.innerHTML = '';
        if (stickyHandList) stickyHandList.innerHTML = '';
        
        // Add event delegation for playable tiles (works with cloned nodes)
        if (playerHandDiv && myTurn && state.status === 'in_progress') {
            // Remove old listener if exists
            const oldHandler = playerHandDiv.getAttribute('data-click-handler');
            if (oldHandler) {
                playerHandDiv.removeEventListener('click', window[oldHandler]);
            }
            
            // Create new handler
            const clickHandler = (e) => {
                // Find the closest domino element (works even if clicking on child elements like dots)
                let clickedDomino = e.target.closest('.domino');
                if (!clickedDomino) {
                    // If not found, check if the target itself is a domino
                    clickedDomino = e.target.classList.contains('domino') ? e.target : null;
                }
                
                if (clickedDomino && clickedDomino.classList.contains('playable') && clickedDomino.getAttribute('data-playable') === 'true') {
                    e.stopPropagation();
                    e.preventDefault();
                    try {
                        const tile = JSON.parse(clickedDomino.getAttribute('data-tile'));
                        const tileInfo = JSON.parse(clickedDomino.getAttribute('data-tile-info'));
                        showTilePlayModal(tile, tileInfo, state.board);
                    } catch (err) {
                        console.error('Error parsing tile data:', err, clickedDomino);
                    }
                }
            };
            
            // Store handler reference
            const handlerName = `tileClickHandler_${Date.now()}`;
            window[handlerName] = clickHandler;
            playerHandDiv.setAttribute('data-click-handler', handlerName);
            playerHandDiv.addEventListener('click', clickHandler);
        }
        
        if (stickyHandList && myTurn && state.status === 'in_progress') {
            // Remove old listener if exists
            const oldHandler = stickyHandList.getAttribute('data-click-handler');
            if (oldHandler) {
                stickyHandList.removeEventListener('click', window[oldHandler]);
            }
            
            // Create new handler
            const clickHandler = (e) => {
                // Find the closest domino element (works even if clicking on child elements like dots)
                let clickedDomino = e.target.closest('.domino');
                if (!clickedDomino) {
                    // If not found, check if the target itself is a domino
                    clickedDomino = e.target.classList.contains('domino') ? e.target : null;
                }
                
                if (clickedDomino && clickedDomino.classList.contains('playable') && clickedDomino.getAttribute('data-playable') === 'true') {
                    e.stopPropagation();
                    e.preventDefault();
                    try {
                        const tile = JSON.parse(clickedDomino.getAttribute('data-tile'));
                        const tileInfo = JSON.parse(clickedDomino.getAttribute('data-tile-info'));
                        showTilePlayModal(tile, tileInfo, state.board);
                    } catch (err) {
                        console.error('Error parsing tile data:', err, clickedDomino);
                    }
                }
            };
            
            // Store handler reference
            const handlerName = `tileClickHandlerSticky_${Date.now()}`;
            window[handlerName] = clickHandler;
            stickyHandList.setAttribute('data-click-handler', handlerName);
            stickyHandList.addEventListener('click', clickHandler);
        }
        
        // Show hand section
        if (yourHandSection) {
            yourHandSection.style.display = 'block';
        }
        
        if (Array.isArray(myHand)) {
            myHand.forEach(tile => {
                const tileKey = JSON.stringify(tile);
                const isPlayable = playableTiles.includes(tileKey);
                const tileInfo = playableTilesInfo[tileKey] || {};
                
                const domino = createDominoTile(tile[0], tile[1], tile);
                
                let tileWrapper = null;
                
                if (myTurn && state.status === 'in_progress') {
                    if (isPlayable) {
                        domino.classList.add('playable');
                        
                        // Store tile data in data attributes for event delegation
                        domino.setAttribute('data-tile', JSON.stringify(tile));
                        domino.setAttribute('data-tile-info', JSON.stringify(tileInfo));
                        domino.setAttribute('data-playable', 'true');
                        
                        // Just wrap domino
                        tileWrapper = document.createElement('div');
                        tileWrapper.style.cssText = 'display: flex; flex-direction: column; align-items: center; margin: 0.5rem;';
                        tileWrapper.appendChild(domino);
                        
                        // Add tooltip
                        if (tileInfo.left && tileInfo.right) {
                            domino.title = `Click to choose LEFT or RIGHT`;
                        } else if (tileInfo.left) {
                            domino.title = `Click to play on LEFT`;
                        } else if (tileInfo.right) {
                            domino.title = `Click to play on RIGHT`;
                        } else {
                            domino.title = 'Click to play';
                        }
                    } else {
                        domino.classList.add('unplayable');
                        if (state.board.length > 0) {
                            domino.title = `Cannot play - needs to match ${leftEnd} (left) or ${rightEnd} (right)`;
                        } else {
                            domino.title = 'This tile cannot be played';
                        }
                        domino.draggable = false;
                    }
                } else {
                    domino.classList.add('disabled');
                    domino.draggable = false;
                    if (!isPlayable && state.board.length > 0) {
                        domino.title = `Waiting for turn - needs to match ${leftEnd} (left) or ${rightEnd} (right)`;
                    }
                }
                
                // Append tiles - use wrapped version if available, otherwise just the domino
                if (tileWrapper) {
                    // Playable tile
                    if (playerHandDiv) playerHandDiv.appendChild(tileWrapper.cloneNode(true));
                    if (stickyHandList) stickyHandList.appendChild(tileWrapper.cloneNode(true));
                } else {
                    // Unplayable or not your turn - just append the domino
                    if (playerHandDiv) playerHandDiv.appendChild(domino);
                    if (stickyHandList) stickyHandList.appendChild(domino.cloneNode(true));
                }
            });
        }
        
        const actions = document.getElementById('domino-actions');
        const drawBtn = document.getElementById('draw-btn');
        const passBtn = document.getElementById('pass-btn');
        
        if (myTurn && state.status === 'in_progress') {
            actions.classList.remove('hidden');
            
            let boneyardCount = 0;
            if (state.boneyard_count !== undefined) {
                boneyardCount = state.boneyard_count;
            } else if (typeof state.boneyard === 'string') {
                const match = state.boneyard.match(/(\d+)/);
                boneyardCount = match ? parseInt(match[1]) : 0;
            } else if (Array.isArray(state.boneyard)) {
                boneyardCount = state.boneyard.length;
            }
            
            // Draw button: Only enabled if boneyard has tiles AND player has NO playable tiles
            if (boneyardCount > 0 && !hasPlayableTile) {
                drawBtn.disabled = false;
                drawBtn.title = 'Draw a tile from the boneyard';
            } else if (boneyardCount > 0 && hasPlayableTile) {
                drawBtn.disabled = true;
                drawBtn.title = 'You have playable tiles! You must play a tile, not draw.';
            } else {
                drawBtn.disabled = true;
                drawBtn.title = 'Boneyard is empty';
            }
            
            if (!hasPlayableTile && boneyardCount === 0) {
                passBtn.disabled = false;
                passBtn.title = 'No playable tiles and boneyard is empty';
            } else if (!hasPlayableTile && boneyardCount > 0) {
                passBtn.disabled = true;
                passBtn.title = 'You must draw from the boneyard first';
            } else {
                passBtn.disabled = true;
                passBtn.title = 'You have playable tiles';
            }
            
            const hintEl = document.getElementById('domino-hint');
            const guidanceEl = document.getElementById('action-guidance');
            const guidanceText = document.getElementById('guidance-text');
            
            if (!hasPlayableTile) {
                if (boneyardCount > 0) {
                    if (handValueSpan) {
                        handValueSpan.textContent = '⚠️ No playable tiles - Click "Draw" to get a new tile';
                        handValueSpan.style.color = '#ff9800';
                    }
                    if (hintEl) hintEl.textContent = '💡 Tip: You can draw a tile from the boneyard to try to get a playable one!';
                    if (guidanceEl && guidanceText) {
                        guidanceEl.style.display = 'block';
                        guidanceText.innerHTML = 'No matching tiles! <strong>Tap "🎴 Draw"</strong> to get a new tile from the boneyard!';
                    }
                } else {
                    if (handValueSpan) {
                        handValueSpan.textContent = '⚠️ No playable tiles - Click "Pass" to skip your turn';
                        handValueSpan.style.color = '#ff9800';
                    }
                    if (hintEl) hintEl.textContent = '💡 Tip: When you can\'t play and the boneyard is empty, you must pass.';
                    if (guidanceEl && guidanceText) {
                        guidanceEl.style.display = 'block';
                        guidanceText.innerHTML = 'No playable tiles and boneyard is empty. <strong>Tap "⏭️ Pass"</strong> to skip your turn.';
                    }
                }
            } else {
                if (handValueSpan) {
                    handValueSpan.textContent = `✓ ${playableTiles.length} playable tile(s) - Click a tile to choose LEFT or RIGHT`;
                    handValueSpan.style.color = '#4caf50';
                }
                if (hintEl) hintEl.textContent = '💡 Tip: Click a green highlighted tile to choose LEFT or RIGHT!';
                if (guidanceEl && guidanceText) {
                    guidanceEl.style.display = 'block';
                    const tileWord = playableTiles.length === 1 ? 'tile' : 'tiles';
                    guidanceText.innerHTML = `<strong>${playableTiles.length} playable ${tileWord}!</strong> Click a <strong style="color: #c6f6d5;">green highlighted tile</strong> to choose LEFT or RIGHT and play it! 🎯`;
                }
            }
            
            // Add dismiss button handler
            const dismissBtn = document.getElementById('dismiss-guidance-btn');
            if (dismissBtn && !dismissBtn.hasAttribute('data-listener-added')) {
                dismissBtn.setAttribute('data-listener-added', 'true');
                dismissBtn.addEventListener('click', () => {
                    if (guidanceEl) {
                        guidanceEl.style.display = 'none';
                        localStorage.setItem('dominito_guidance_dismissed', 'true');
                    }
                });
            }
            
            // Auto-hide guidance if user dismissed it before (but show again after 24 hours)
            const guidanceDismissed = localStorage.getItem('dominito_guidance_dismissed');
            if (guidanceDismissed === 'true' && guidanceEl) {
                // Still show it, but user can dismiss again
            }
        } else {
            actions.classList.add('hidden');
            if (handValueSpan) {
                handValueSpan.textContent = '';
            }
        }
    }
    
    // Show modal to select LEFT or RIGHT when tile is clicked
    function showTilePlayModal(tile, tileInfo, board) {
        // Remove existing modal if any
        const existingModal = document.getElementById('tile-play-modal');
        if (existingModal) {
            existingModal.remove();
        }
        
        // Get board ends
        let leftEnd = null;
        let rightEnd = null;
        if (board.length > 0) {
            leftEnd = board[0][0];
            rightEnd = board[board.length - 1][1];
        }
        
        // Determine which sides are available
        const canPlayLeft = tileInfo.left || (board.length === 0);
        const canPlayRight = tileInfo.right || (board.length === 0);
        
        // Create modal overlay
        const modal = document.createElement('div');
        modal.id = 'tile-play-modal';
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
            padding: 1rem;
            animation: fadeIn 0.2s ease-out;
        `;
        
        // Create modal content
        const content = document.createElement('div');
        content.style.cssText = `
            background: white;
            border-radius: 20px;
            padding: 2rem;
            max-width: 500px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            text-align: center;
            animation: slideUp 0.3s ease-out;
        `;
        
        // Create tile preview
        const tilePreview = createDominoTile(tile[0], tile[1], tile);
        tilePreview.style.cssText = 'margin: 0 auto 1.5rem; transform: scale(1.3);';
        
        content.innerHTML = `
            <h3 style="font-size: 1.5rem; font-weight: 700; color: #1a1a2e; margin-bottom: 1rem;">
                Choose Where to Play! 🎯
            </h3>
            <p style="font-size: 1.1rem; color: #4a5568; margin-bottom: 1.5rem; line-height: 1.6;">
                Select which side to play this tile on:
            </p>
            <div style="display: flex; gap: 1rem; justify-content: center; margin-bottom: 1.5rem; flex-wrap: wrap;">
                <button id="modal-left-btn" style="
                    background: ${canPlayLeft ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)' : '#cbd5e0'};
                    color: white;
                    border: none;
                    padding: 1.25rem 2rem;
                    border-radius: 12px;
                    font-size: 1.2rem;
                    font-weight: 700;
                    cursor: ${canPlayLeft ? 'pointer' : 'not-allowed'};
                    min-width: 140px;
                    box-shadow: ${canPlayLeft ? '0 4px 15px rgba(102, 126, 234, 0.4)' : 'none'};
                    transition: all 0.2s ease;
                    opacity: ${canPlayLeft ? '1' : '0.6'};
                ">
                    ⬅️ LEFT<br>
                    <span style="font-size: 0.9rem; opacity: 0.9;">${board.length > 0 ? `End: ${leftEnd}` : 'Start'}</span>
                </button>
                <button id="modal-right-btn" style="
                    background: ${canPlayRight ? 'linear-gradient(135deg, #48bb78 0%, #38a169 100%)' : '#cbd5e0'};
                    color: white;
                    border: none;
                    padding: 1.25rem 2rem;
                    border-radius: 12px;
                    font-size: 1.2rem;
                    font-weight: 700;
                    cursor: ${canPlayRight ? 'pointer' : 'not-allowed'};
                    min-width: 140px;
                    box-shadow: ${canPlayRight ? '0 4px 15px rgba(72, 187, 120, 0.4)' : 'none'};
                    transition: all 0.2s ease;
                    opacity: ${canPlayRight ? '1' : '0.6'};
                ">
                    ➡️ RIGHT<br>
                    <span style="font-size: 0.9rem; opacity: 0.9;">${board.length > 0 ? `End: ${rightEnd}` : 'Start'}</span>
                </button>
            </div>
            <button id="modal-cancel-btn" style="
                background: #e2e8f0;
                color: #4a5568;
                border: none;
                padding: 0.75rem 1.5rem;
                border-radius: 8px;
                font-weight: 600;
                cursor: pointer;
                font-size: 1rem;
                margin-top: 1rem;
            ">Cancel</button>
        `;
        
        // Insert tile preview
        const buttonsDiv = content.querySelector('div');
        buttonsDiv.insertBefore(tilePreview, buttonsDiv.firstChild);
        
        // Add button handlers
        const leftBtn = content.querySelector('#modal-left-btn');
        const rightBtn = content.querySelector('#modal-right-btn');
        const cancelBtn = content.querySelector('#modal-cancel-btn');
        
        if (canPlayLeft) {
            leftBtn.addEventListener('click', () => {
                modal.remove();
                sendMove({ action: 'play', tile: tile, side: 'left' });
            });
            leftBtn.addEventListener('mouseenter', () => {
                if (canPlayLeft) leftBtn.style.transform = 'scale(1.05)';
            });
            leftBtn.addEventListener('mouseleave', () => {
                leftBtn.style.transform = 'scale(1)';
            });
        }
        
        if (canPlayRight) {
            rightBtn.addEventListener('click', () => {
                modal.remove();
                sendMove({ action: 'play', tile: tile, side: 'right' });
            });
            rightBtn.addEventListener('mouseenter', () => {
                if (canPlayRight) rightBtn.style.transform = 'scale(1.05)';
            });
            rightBtn.addEventListener('mouseleave', () => {
                rightBtn.style.transform = 'scale(1)';
            });
        }
        
        cancelBtn.addEventListener('click', () => {
            modal.remove();
        });
        
        // Close on background click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });
        
        modal.appendChild(content);
        document.body.appendChild(modal);
        
        // Focus trap and accessibility
        if (canPlayLeft) {
            leftBtn.focus();
        } else if (canPlayRight) {
            rightBtn.focus();
        } else {
            cancelBtn.focus();
        }
    }
    
    function showSideChoiceModal(tile, leftEnd, rightEnd, callback) {
        // Remove existing modal if any
        const existingModal = document.getElementById('side-choice-modal');
        if (existingModal) {
            existingModal.remove();
        }
        
        // Create modal overlay
        const modal = document.createElement('div');
        modal.id = 'side-choice-modal';
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
            padding: 1rem;
            animation: fadeIn 0.2s ease-out;
        `;
        
        // Create modal content
        const content = document.createElement('div');
        content.style.cssText = `
            background: white;
            border-radius: 20px;
            padding: 2rem;
            max-width: 500px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            text-align: center;
            animation: slideUp 0.3s ease-out;
        `;
        
        // Add CSS animations if not already added
        if (!document.getElementById('side-choice-modal-styles')) {
            const style = document.createElement('style');
            style.id = 'side-choice-modal-styles';
            style.textContent = `
                @keyframes fadeIn {
                    from { opacity: 0; }
                    to { opacity: 1; }
                }
                @keyframes slideUp {
                    from { transform: translateY(30px); opacity: 0; }
                    to { transform: translateY(0); opacity: 1; }
                }
                @media (max-width: 768px) {
                    #side-choice-modal > div {
                        padding: 1.5rem !important;
                        border-radius: 16px !important;
                    }
                }
            `;
            document.head.appendChild(style);
        }
        
        // Create tile preview
        const tilePreview = createDominoTile(tile[0], tile[1], tile);
        tilePreview.style.cssText = 'margin: 0 auto 1.5rem; transform: scale(1.2);';
        
        content.innerHTML = `
            <h3 style="font-size: 1.5rem; font-weight: 700; color: #1a1a2e; margin-bottom: 1rem;">
                Choose Where to Play! 🎯
            </h3>
            <p style="font-size: 1.1rem; color: #4a5568; margin-bottom: 1.5rem; line-height: 1.6;">
                This tile can play on both ends!<br>
                Choose which side to play it on:
            </p>
            <div style="display: flex; gap: 1rem; justify-content: center; margin-bottom: 1.5rem; flex-wrap: wrap;">
                <button id="choose-left-btn" style="
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    border: none;
                    padding: 1.25rem 2rem;
                    border-radius: 12px;
                    font-size: 1.2rem;
                    font-weight: 700;
                    cursor: pointer;
                    min-width: 140px;
                    box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
                    transition: all 0.2s ease;
                ">
                    ⬅️ LEFT<br>
                    <span style="font-size: 0.9rem; opacity: 0.9;">End: ${leftEnd}</span>
                </button>
                <button id="choose-right-btn" style="
                    background: linear-gradient(135deg, #48bb78 0%, #38a169 100%);
                    color: white;
                    border: none;
                    padding: 1.25rem 2rem;
                    border-radius: 12px;
                    font-size: 1.2rem;
                    font-weight: 700;
                    cursor: pointer;
                    min-width: 140px;
                    box-shadow: 0 4px 15px rgba(72, 187, 120, 0.4);
                    transition: all 0.2s ease;
                ">
                    ➡️ RIGHT<br>
                    <span style="font-size: 0.9rem; opacity: 0.9;">End: ${rightEnd}</span>
                </button>
            </div>
        `;
        
        // Insert tile preview
        const buttonsDiv = content.querySelector('div');
        buttonsDiv.insertBefore(tilePreview, buttonsDiv.firstChild);
        
        // Add button handlers
        const leftBtn = content.querySelector('#choose-left-btn');
        const rightBtn = content.querySelector('#choose-right-btn');
        
        leftBtn.addEventListener('click', () => {
            modal.remove();
            callback('left');
        });
        
        rightBtn.addEventListener('click', () => {
            modal.remove();
            callback('right');
        });
        
        leftBtn.addEventListener('touchstart', () => {
            leftBtn.style.transform = 'scale(0.95)';
        });
        leftBtn.addEventListener('touchend', () => {
            leftBtn.style.transform = 'scale(1)';
        });
        
        rightBtn.addEventListener('touchstart', () => {
            rightBtn.style.transform = 'scale(0.95)';
        });
        rightBtn.addEventListener('touchend', () => {
            rightBtn.style.transform = 'scale(1)';
        });
        
        // Close on background click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });
        
        modal.appendChild(content);
        document.body.appendChild(modal);
        
        // Focus trap and accessibility
        leftBtn.focus();
    }
    
    
    // --- Reconnection Logic ---
    async function attemptReconnect() {
        if (!localGameId || !localPlayerId) {
            return;
        }
        
        reconnectAttempts++;
        console.log(`Attempting to reconnect (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`);
        
        try {
            // Try to reconnect WebSocket
            const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${wsProtocol}//${window.location.host}${basePath}/ws/game/${localGameId}/${localPlayerId}`;
            
            socket = new WebSocket(wsUrl);
            
            socket.onopen = () => {
                console.log('Reconnected successfully!');
                reconnectAttempts = 0;
                if (disconnectTimeout) {
                    clearTimeout(disconnectTimeout);
                    disconnectTimeout = null;
                }
                localStorage.setItem('game_portal_connected', 'true');
                localStorage.removeItem('game_portal_disconnect_time');
            };
            
            socket.onmessage = (event) => {
                const data = JSON.parse(event.data);
                console.log('Message from server:', data);
                
                switch (data.type) {
                    case 'connection_success':
                        localGameType = data.game_type;
                        gameTitle.textContent = `${localGameType} Game`;
                        renderPlayers(data.players);
                        if (data.game_state) {
                            currentGameState = data.game_state;
                            renderGame(data.game_state);
                        }
                        break;
                    case 'game_started':
                    case 'state_update':
                        currentGameState = data.game_state;
                        if (data.players) {
                            renderPlayers(data.players);
                        }
                        renderGame(data.game_state);
                        if (data.game_state && data.game_state.status !== 'waiting') {
                            const gameInfoGrid = document.querySelector('div[style*="grid-template-columns"]');
                            if (gameInfoGrid && gameInfoGrid.firstElementChild) {
                                gameInfoGrid.firstElementChild.style.display = 'none';
                            }
                        }
                        break;
                    case 'error':
                        console.error('Error:', data.message);
                        break;
                }
            };
            
            socket.onclose = (event) => {
                console.log('Reconnection failed', event);
                if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                    // Retry after a delay
                    setTimeout(() => {
                        attemptReconnect();
                    }, 2000 * reconnectAttempts); // Exponential backoff
                } else {
                    showError('Failed to reconnect. Please refresh the page.');
                    localStorage.removeItem('game_portal_game_id');
                    localStorage.removeItem('game_portal_player_id');
                    localStorage.removeItem('game_portal_connected');
                    localStorage.removeItem('game_portal_disconnect_time');
                }
            };
            
            socket.onerror = (error) => {
                console.error('WebSocket error during reconnect:', error);
            };
        } catch (err) {
            console.error('Reconnection error:', err);
            if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                setTimeout(() => {
                    attemptReconnect();
                }, 2000 * reconnectAttempts);
            }
        }
    }
    
    // --- Clear Game State ---
    function clearGameState() {
        localStorage.removeItem('game_portal_game_id');
        localStorage.removeItem('game_portal_player_id');
        localStorage.removeItem('game_portal_connected');
        localStorage.removeItem('game_portal_disconnect_time');
        localGameId = null;
        localPlayerId = null;
        currentGameState = null;
        if (socket) {
            socket.close();
            socket = null;
        }
        lobbyView.classList.remove('hidden');
        gameView.classList.add('hidden');
    }
    
    // --- Auto-Recovery on Page Load ---
    async function checkForAutoRecovery() {
        const savedGameId = localStorage.getItem('game_portal_game_id');
        const savedPlayerId = localStorage.getItem('game_portal_player_id');
        const isConnected = localStorage.getItem('game_portal_connected') === 'true';
        const disconnectTime = localStorage.getItem('game_portal_disconnect_time');
        
        if (savedGameId && savedPlayerId) {
            // Check if disconnect was recent (within 60s)
            if (disconnectTime) {
                const timeSinceDisconnect = Date.now() - parseInt(disconnectTime);
                if (timeSinceDisconnect < DISCONNECT_DELAY_MS) {
                    // Auto-recover
                    console.log('Auto-recovering connection...');
                    localGameId = savedGameId;
                    localPlayerId = savedPlayerId;
                    
                    if (!browserFingerprint) {
                        browserFingerprint = await generateFingerprint();
                    }
                    
                    // Verify player ID matches fingerprint
                    if (savedPlayerId === browserFingerprint) {
                        // Auto-reconnect
                        connectWebSocket(savedGameId, savedPlayerId);
                        return true;
                    } else {
                        // Player ID mismatch - clear state
                        clearGameState();
                        return false;
                    }
                } else {
                    // Disconnect was too long ago - clear state
                    clearGameState();
                    return false;
                }
            } else if (isConnected) {
                // Try to reconnect if we think we're connected
                console.log('Attempting to restore connection...');
                localGameId = savedGameId;
                localPlayerId = savedPlayerId;
                
                if (!browserFingerprint) {
                    browserFingerprint = await generateFingerprint();
                }
                
                if (savedPlayerId === browserFingerprint) {
                    connectWebSocket(savedGameId, savedPlayerId);
                    return true;
                } else {
                    // Player ID mismatch - clear state
                    clearGameState();
                    return false;
                }
            } else {
                // Not connected and no recent disconnect - clear stale state
                clearGameState();
                return false;
            }
        }
        return false;
    }
    
    // --- Leave Game Function ---
    function leaveGame() {
        if (confirm('Are you sure you want to leave this game? You will need to rejoin to continue playing.')) {
            clearGameState();
            // Clear URL parameter
            window.history.replaceState({}, '', basePath);
            showError('✅ You have left the game. You can create a new game or join another one.');
        }
    }
    
    // --- Initial Event Listeners ---
    createGameBtn.addEventListener('click', createGame);
    joinGameBtn.addEventListener('click', joinGame);
    
    const leaveGameBtn = document.getElementById('leave-game-btn');
    if (leaveGameBtn) {
        leaveGameBtn.addEventListener('click', leaveGame);
    }
    
    function checkUrlForGame() {
        const urlParams = new URLSearchParams(window.location.search);
        let gameId = urlParams.get('game');
        const playerIdFromUrl = urlParams.get('player_id');
        const replacePlaceholder = urlParams.get('replace_placeholder') === 'true';
        
        // Validate gameId - reject "undefined", "null", empty strings, or invalid values
        if (gameId) {
            gameId = gameId.trim();
            if (gameId === 'undefined' || gameId === 'null' || gameId === '' || gameId.length < 3) {
                console.warn('checkUrlForGame: Invalid game ID detected, cleaning URL:', gameId);
                // Clean up the URL by removing invalid game parameter
                urlParams.delete('game');
                const cleanUrl = window.location.pathname + (urlParams.toString() ? '?' + urlParams.toString() : '');
                window.history.replaceState({}, '', cleanUrl);
                gameId = null;
                // Show lobby view since we cleaned the invalid parameter
                if (lobbyView) {
                    lobbyView.style.display = 'block';
                    lobbyView.classList.remove('hidden');
                }
                if (gameView) {
                    gameView.classList.add('hidden');
                }
            }
        }
        
        console.log('checkUrlForGame:', {
            gameId: gameId || '(none)',
            playerIdFromUrl: playerIdFromUrl || '(none)',
            replacePlaceholder: replacePlaceholder || false,
            autoJoinGameId: window.autoJoinGameId || '(none)',
            autoJoinPlayerId: window.autoJoinPlayerId || '(none)'
        });
        
        // Check if auto-join script already joined
        if (window.autoJoinGameId && window.autoJoinPlayerId) {
            // Validate that values are not undefined/null strings
            const validGameId = window.autoJoinGameId && 
                window.autoJoinGameId !== 'undefined' && 
                window.autoJoinGameId !== 'null' && 
                typeof window.autoJoinGameId === 'string' && 
                window.autoJoinGameId.trim() !== '';
            const validPlayerId = window.autoJoinPlayerId && 
                window.autoJoinPlayerId !== 'undefined' && 
                window.autoJoinPlayerId !== 'null' && 
                typeof window.autoJoinPlayerId === 'string' && 
                window.autoJoinPlayerId.trim() !== '';
            
            if (validGameId && validPlayerId) {
                console.log('Auto-join detected, connecting WebSocket...');
                // Auto-join script has already called the join API
                // Now we just need to connect the WebSocket
                setTimeout(async () => {
                    if (!browserFingerprint) {
                        browserFingerprint = await generateFingerprint();
                    }
                    // Use the player_id from auto-join (which uses browser fingerprint)
                    console.log('Connecting WebSocket with:', { gameId: window.autoJoinGameId, playerId: window.autoJoinPlayerId });
                    connectWebSocket(window.autoJoinGameId, window.autoJoinPlayerId);
                }, 100);
                return;
            } else {
                console.warn('Auto-join values are invalid:', { 
                    gameId: window.autoJoinGameId, 
                    playerId: window.autoJoinPlayerId,
                    validGameId,
                    validPlayerId
                });
            }
        }
        
        // If URL has game parameter but auto-join hasn't run yet
        // This can happen if app.js loads before auto-join script completes
        if (gameId) {
            console.log('URL has game parameter, waiting for auto-join...');
            
            // If replace_placeholder is true, we need to replace the placeholder player
            if (replacePlaceholder) {
                console.log('Replace placeholder detected, joining with browser fingerprint...');
                setTimeout(async () => {
                    if (!browserFingerprint) {
                        browserFingerprint = await generateFingerprint();
                    }
                    // Join the game with browser fingerprint, which will replace the placeholder
                    try {
                        const response = await fetch(`${basePath}/api/game/${gameId}`, {
                            method: 'GET'
                        });
                        const gameData = await response.json();
                        
                        // Find placeholder player
                        const placeholderPlayer = gameData.players?.find(p => {
                            const pid = typeof p === 'string' ? p : p.player_id || p;
                            return pid && pid.startsWith('PLACEHOLDER_');
                        });
                        
                        if (placeholderPlayer) {
                            const placeholderId = typeof placeholderPlayer === 'string' ? placeholderPlayer : placeholderPlayer.player_id || placeholderPlayer;
                            console.log('Found placeholder player, replacing with browser fingerprint:', placeholderId);
                            
                            // Join the game, which should replace the placeholder
                            const joinResponse = await fetch(`${basePath}/api/game/${gameId}/join`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ 
                                    player_id: browserFingerprint
                                })
                            });
                            const joinData = await joinResponse.json();
                            
                            if (joinResponse.ok) {
                                console.log('Successfully replaced placeholder, connecting WebSocket...');
                                // Verify placeholder was actually replaced
                                if (joinData.replaced_placeholder) {
                                    console.log('Placeholder replacement confirmed:', joinData.replaced_placeholder);
                                }
                                connectWebSocket(gameId, browserFingerprint);
                            } else {
                                console.error('Failed to replace placeholder:', joinData);
                                // If join failed, try again after a short delay
                                setTimeout(async () => {
                                    const retryResponse = await fetch(`${basePath}/api/game/${gameId}/join`, {
                                        method: 'POST',
                                        headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ 
                                            player_id: browserFingerprint
                                        })
                                    });
                                    const retryData = await retryResponse.json();
                                    if (retryResponse.ok) {
                                        console.log('Placeholder replacement succeeded on retry');
                                        connectWebSocket(gameId, browserFingerprint);
                                    } else {
                                        console.error('Placeholder replacement failed on retry:', retryData);
                                        // Last resort: try to connect anyway
                                        connectWebSocket(gameId, browserFingerprint);
                                    }
                                }, 500);
                            }
                        } else {
                            // No placeholder found, just join normally
                            console.log('No placeholder found, joining normally...');
                            const joinResponse = await fetch(`${basePath}/api/game/${gameId}/join`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ 
                                    player_id: browserFingerprint
                                })
                            });
                            const joinData = await joinResponse.json();
                            if (joinResponse.ok) {
                                connectWebSocket(gameId, browserFingerprint);
                            } else {
                                console.error('Failed to join game:', joinData);
                            }
                        }
                    } catch (err) {
                        console.error('Error replacing placeholder:', err);
                        // Fallback: try to connect anyway
                        connectWebSocket(gameId, browserFingerprint);
                    }
                }, 100);
                return;
            }
            
            // If player_id is in URL, use it directly (from auto-create endpoint)
            if (playerIdFromUrl) {
                console.log('Player ID from URL, connecting directly...');
                setTimeout(async () => {
                    if (!browserFingerprint) {
                        browserFingerprint = await generateFingerprint();
                    }
                    // Use player_id from URL if provided, otherwise use browser fingerprint
                    const playerIdToUse = playerIdFromUrl || browserFingerprint;
                    connectWebSocket(gameId, playerIdToUse);
                }, 100);
                return;
            }
            
            // Don't show lobby - auto-join should handle it
            // Wait a bit for auto-join to complete
            const checkAutoJoin = setInterval(() => {
                if (window.autoJoinGameId && window.autoJoinPlayerId) {
                    // Validate that values are not undefined/null strings
                    const validGameId = window.autoJoinGameId && 
                        window.autoJoinGameId !== 'undefined' && 
                        window.autoJoinGameId !== 'null' && 
                        typeof window.autoJoinGameId === 'string' && 
                        window.autoJoinGameId.trim() !== '';
                    const validPlayerId = window.autoJoinPlayerId && 
                        window.autoJoinPlayerId !== 'undefined' && 
                        window.autoJoinPlayerId !== 'null' && 
                        typeof window.autoJoinPlayerId === 'string' && 
                        window.autoJoinPlayerId.trim() !== '';
                    
                    if (validGameId && validPlayerId) {
                        console.log('Auto-join completed, connecting WebSocket...');
                        clearInterval(checkAutoJoin);
                        if (!browserFingerprint) {
                            generateFingerprint().then(() => {
                                connectWebSocket(window.autoJoinGameId, window.autoJoinPlayerId);
                            });
                        } else {
                            connectWebSocket(window.autoJoinGameId, window.autoJoinPlayerId);
                        }
                    } else {
                        console.warn('Auto-join values are invalid, waiting...', { 
                            gameId: window.autoJoinGameId, 
                            playerId: window.autoJoinPlayerId 
                        });
                    }
                }
            }, 100);
            
            // Stop checking after 5 seconds
            setTimeout(() => {
                clearInterval(checkAutoJoin);
                // Validate that values are valid (not undefined/null strings)
                const validGameId = window.autoJoinGameId && 
                    window.autoJoinGameId !== 'undefined' && 
                    window.autoJoinGameId !== 'null' && 
                    typeof window.autoJoinGameId === 'string' && 
                    window.autoJoinGameId.trim() !== '';
                const validPlayerId = window.autoJoinPlayerId && 
                    window.autoJoinPlayerId !== 'undefined' && 
                    window.autoJoinPlayerId !== 'null' && 
                    typeof window.autoJoinPlayerId === 'string' && 
                    window.autoJoinPlayerId.trim() !== '';
                
                if (!validGameId || !validPlayerId) {
                    console.warn('Auto-join did not complete with valid values, showing lobby', {
                        gameId: window.autoJoinGameId,
                        playerId: window.autoJoinPlayerId
                    });
                    // If auto-join failed, show lobby with pre-filled game ID
                    if (gameIdInput) {
                        gameIdInput.value = gameId.toUpperCase();
                    }
                    if (lobbyView) {
                        lobbyView.style.display = 'block';
                        lobbyView.classList.remove('hidden');
                    }
                }
            }, 5000);
            return; // Don't proceed with normal flow
        } else {
            // Check for auto-recovery even without URL param
            checkForAutoRecovery();
        }
    }
    
    // Expose checkUrlForGame to window for auto-join script
    window.checkUrlForGame = checkUrlForGame;
    
    // Listen for auto-join completion event
    window.addEventListener('autoJoinComplete', (event) => {
        console.log('Auto-join complete event received', event.detail);
        if (event.detail && event.detail.gameId && event.detail.playerId) {
            window.autoJoinGameId = event.detail.gameId;
            window.autoJoinPlayerId = event.detail.playerId;
        }
        // Trigger connection check
        setTimeout(() => {
            checkUrlForGame();
        }, 100);
    });
    
    checkUrlForGame();
    
    startGameBtn.addEventListener('click', sendStartGame);
    // Note: hitBtn and standBtn removed - they were for blackjack which is no longer supported
    drawBtn.addEventListener('click', () => sendMove({ action: 'draw' }));
    passBtn.addEventListener('click', () => sendMove({ action: 'pass' }));
    
    // --- Tab Navigation ---
    const tabButtons = document.querySelectorAll('.game-tab');
    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.getAttribute('data-tab');
            
            // Update active tab
            tabButtons.forEach(b => {
                b.classList.remove('active');
                b.style.borderBottomColor = 'transparent';
                b.style.color = '#718096';
                b.style.fontWeight = '600';
            });
            btn.classList.add('active');
            btn.style.borderBottomColor = '#667eea';
            btn.style.color = '#667eea';
            btn.style.fontWeight = '700';
            
            // Show/hide tab content
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
                content.style.display = 'none';
            });
            const targetContent = document.getElementById(`tab-content-${tabName}`);
            if (targetContent) {
                targetContent.classList.add('active');
                targetContent.style.display = 'block';
            }
        });
    });
    
    // --- Modal System ---
    const modalOverlay = document.getElementById('modal-overlay');
    const modalBody = document.getElementById('modal-body');
    const modalCloseBtn = document.getElementById('modal-close-btn');
    
    function showModal(content) {
        if (modalBody) modalBody.innerHTML = content;
        if (modalOverlay) {
            modalOverlay.classList.remove('hidden');
            modalOverlay.style.display = 'flex';
        }
        document.body.style.overflow = 'hidden';
    }
    
    function hideModal() {
        if (modalOverlay) {
            modalOverlay.classList.add('hidden');
            modalOverlay.style.display = 'none';
        }
        document.body.style.overflow = '';
    }
    
    if (modalCloseBtn) {
        modalCloseBtn.addEventListener('click', hideModal);
    }
    
    if (modalOverlay) {
        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) {
                hideModal();
            }
        });
    }
    
    // --- Help Modal ---
    const helpBtn = document.getElementById('help-btn');
    if (helpBtn) {
        helpBtn.addEventListener('click', () => {
            const helpContent = `
                <h2 style="color: #1a1a2e; margin: 0 0 1.5rem 0; font-size: 2rem; font-weight: 800;">📚 How to Play Dominito</h2>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem; font-size: 1rem; line-height: 1.7;">
                    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 1.5rem; border-radius: 12px;">
                        <h3 style="color: white; margin: 0 0 0.75rem 0; font-size: 1.3rem; font-weight: 700;">🎯 Goal</h3>
                        <p style="margin: 0; opacity: 0.95;">Match tiles by connecting matching numbers. First to play all tiles wins the hand!</p>
                    </div>
                    <div style="background: linear-gradient(135deg, #48bb78 0%, #38a169 100%); color: white; padding: 1.5rem; border-radius: 12px;">
                        <h3 style="color: white; margin: 0 0 0.75rem 0; font-size: 1.3rem; font-weight: 700;">✅ Playing Tiles</h3>
                        <p style="margin: 0; opacity: 0.95;">Click a <strong style="color: #c6f6d5;">highlighted green tile</strong> to play it. Or drag it to the left or right drop zone!</p>
                    </div>
                    <div style="background: linear-gradient(135deg, #f6ad55 0%, #ed8936 100%); color: white; padding: 1.5rem; border-radius: 12px;">
                        <h3 style="color: white; margin: 0 0 0.75rem 0; font-size: 1.3rem; font-weight: 700;">🎴 Drawing</h3>
                        <p style="margin: 0; opacity: 0.95;">If you can't play, click "Draw" to get a new tile from the boneyard.</p>
                    </div>
                    <div style="background: linear-gradient(135deg, #f56565 0%, #e53e3e 100%); color: white; padding: 1.5rem; border-radius: 12px;">
                        <h3 style="color: white; margin: 0 0 0.75rem 0; font-size: 1.3rem; font-weight: 700;">⏭️ Passing</h3>
                        <p style="margin: 0; opacity: 0.95;">When boneyard is empty and you can't play, click "Pass" to skip your turn.</p>
                    </div>
                </div>
            `;
            showModal(helpContent);
        });
    }
    
    // --- Game Info Modal ---
    const gameInfoBtn = document.getElementById('game-info-btn');
    if (gameInfoBtn) {
        gameInfoBtn.addEventListener('click', () => {
            const gameIdDisplay = document.getElementById('game-id-display');
            const playerDisplayName = document.getElementById('player-display-name');
            const playerIdDisplay = document.getElementById('player-id-display');
            const copyLinkBtn = document.getElementById('copy-link-btn');
            
            const gameId = gameIdDisplay ? gameIdDisplay.textContent : 'N/A';
            const playerName = playerDisplayName ? playerDisplayName.textContent : 'Player';
            const playerId = playerIdDisplay ? playerIdDisplay.textContent : 'N/A';
            
            const infoContent = `
                <h2 style="color: #1a1a2e; margin: 0 0 1.5rem 0; font-size: 2rem; font-weight: 800;">ℹ️ Game Information</h2>
                <div style="display: flex; flex-direction: column; gap: 1.5rem;">
                    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 1.5rem; border-radius: 12px;">
                        <div style="font-size: 0.9rem; opacity: 0.9; margin-bottom: 0.5rem;">Game ID</div>
                        <div style="font-size: 1.75rem; font-weight: 700; margin-bottom: 0.75rem; font-family: monospace;">${gameId}</div>
                        <button id="modal-copy-link-btn" style="background: white; color: #667eea; font-weight: 700; padding: 0.75rem 1.5rem; border-radius: 10px; border: none; cursor: pointer; font-size: 1rem; width: 100%; transition: all 0.2s ease;">📋 Copy Share Link</button>
                    </div>
                    <div style="background: linear-gradient(135deg, #48bb78 0%, #38a169 100%); color: white; padding: 1.5rem; border-radius: 12px;">
                        <div style="font-size: 0.9rem; opacity: 0.9; margin-bottom: 0.75rem;">You are</div>
                        <div style="display: flex; align-items: center; gap: 0.75rem;">
                            <svg id="modal-player-identicon" width="56" height="56" style="border-radius: 50%; background: rgba(255, 255, 255, 0.2); padding: 4px; flex-shrink: 0;"></svg>
                            <div style="flex: 1;">
                                <div style="font-size: 1.25rem; font-weight: 700; margin-bottom: 0.25rem;">${playerName}</div>
                                <div style="font-size: 0.8rem; opacity: 0.8; font-family: monospace;">${playerId}</div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            showModal(infoContent);
            
            // Copy identicon
            const playerIdenticonDisplay = document.getElementById('player-identicon-display');
            const modalIdenticon = document.getElementById('modal-player-identicon');
            if (playerIdenticonDisplay && modalIdenticon) {
                modalIdenticon.innerHTML = playerIdenticonDisplay.innerHTML;
            }
            
            // Copy link functionality
            const modalCopyBtn = document.getElementById('modal-copy-link-btn');
            if (modalCopyBtn) {
                modalCopyBtn.addEventListener('click', () => {
                    const shareUrl = `${window.location.origin}${basePath}?game=${gameId}`;
                    navigator.clipboard.writeText(shareUrl).then(() => {
                        modalCopyBtn.textContent = '✓ Copied!';
                        modalCopyBtn.style.background = '#48bb78';
                        modalCopyBtn.style.color = 'white';
                        setTimeout(() => {
                            modalCopyBtn.textContent = '📋 Copy Share Link';
                            modalCopyBtn.style.background = 'white';
                            modalCopyBtn.style.color = '#667eea';
                        }, 2000);
                    }).catch(err => {
                        console.error('Failed to copy:', err);
                        showError('❌ Failed to copy link. Please try again.');
                    });
                });
            }
        });
    }
    
    // Gallery view removed - using responsive design instead
    
    const nextHandBtn = document.getElementById('next-hand-btn');
    if (nextHandBtn) {
        nextHandBtn.addEventListener('click', () => {
            if (!nextHandBtn.disabled) {
                sendMove({ action: 'ready_for_next_hand' });
                nextHandBtn.disabled = true;
                nextHandBtn.textContent = '✓ You\'re Ready!';
                nextHandBtn.style.opacity = '0.8';
                nextHandBtn.style.cursor = 'not-allowed';
                nextHandBtn.style.background = 'linear-gradient(135deg, #48bb78 0%, #38a169 100%)';
                nextHandBtn.style.animation = 'none';
                
                // Visual feedback
                nextHandBtn.style.transform = 'scale(0.95)';
                setTimeout(() => {
                    nextHandBtn.style.transform = 'scale(1)';
                }, 150);
            }
        });
    }
    
    const nextRoundBtn = document.getElementById('next-round-btn');
    if (nextRoundBtn) {
        nextRoundBtn.addEventListener('click', () => {
            if (!nextRoundBtn.disabled) {
                sendMove({ action: 'ready_for_next_round' });
                nextRoundBtn.disabled = true;
                nextRoundBtn.textContent = '✓ You\'re Ready!';
                nextRoundBtn.style.opacity = '0.8';
                nextRoundBtn.style.cursor = 'not-allowed';
                nextRoundBtn.style.background = 'linear-gradient(135deg, #48bb78 0%, #38a169 100%)';
                nextRoundBtn.style.animation = 'none';
                
                // Visual feedback
                nextRoundBtn.style.transform = 'scale(0.95)';
                setTimeout(() => {
                    nextRoundBtn.style.transform = 'scale(1)';
                }, 150);
            }
        });
    }
});
