// API Base URL
const API_BASE = '/api';

// Current state
let currentConfig = {};
let refreshInterval = null;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    loadConfig();
    loadStats();
    loadChannels();
    loadTokens();
    loadProxies();
    initForms();
    
    // Auto-refresh stats every 10 seconds
    refreshInterval = setInterval(loadStats, 10000);
});

// Navigation
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            navigateTo(page);
        });
    });
}

function navigateTo(page) {
    // Update nav
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
        if (item.dataset.page === page) {
            item.classList.add('active');
        }
    });
    
    // Update pages
    document.querySelectorAll('.page').forEach(p => {
        p.classList.remove('active');
    });
    
    const targetPage = document.getElementById(`${page}-page`);
    if (targetPage) {
        targetPage.classList.add('active');
        
        // Load page-specific data
        if (page === 'channels') loadChannels();
        if (page === 'tokens') {
            loadTokens();
            loadProxies();
        }
        if (page === 'settings') loadSettings();
        if (page === 'logs') loadLogs();
    }
}

// Load Configuration
async function loadConfig() {
    try {
        const response = await fetch(`${API_BASE}/config`);
        const data = await response.json();
        currentConfig = data;
        
        // Update UI
        document.getElementById('ad-message').value = data.advertisement_message || '';
        document.getElementById('interval-minutes').value = data.interval_minutes || 60;
        document.getElementById('default-cooldown').value = data.default_cooldown || 60;
        document.getElementById('online-status-select').value = data.online_status || 'online';
        document.getElementById('use-proxies').checked = data.use_proxies !== false;
        document.getElementById('keep-online').checked = data.keep_tokens_online !== false;
        
    } catch (error) {
        console.error('Failed to load config:', error);
        showToast('Failed to load configuration', 'error');
    }
}

// Load Stats
async function loadStats() {
    try {
        const response = await fetch(`${API_BASE}/stats`);
        const data = await response.json();
        
        // Update stat cards
        document.getElementById('stat-messages').textContent = data.total_sent.toLocaleString();
        document.getElementById('stat-tokens').textContent = `${data.active_tokens} / ${data.total_tokens}`;
        document.getElementById('stat-channels').textContent = data.total_channels;
        document.getElementById('stat-uptime').textContent = data.uptime;
        
        // Update status items
        document.getElementById('proxy-status').textContent = data.use_proxies ? 'Enabled' : 'Disabled';
        document.getElementById('online-status').textContent = data.keep_online ? data.online_status.toUpperCase() : 'Disabled';
        document.getElementById('interval-status').textContent = `${data.interval_minutes} min`;
        document.getElementById('last-activity').textContent = data.last_activity ? new Date(data.last_activity).toLocaleTimeString() : 'Never';
        
    } catch (error) {
        console.error('Failed to load stats:', error);
    }
}

// Refresh Stats
function refreshStats() {
    loadStats();
    showToast('Statistics refreshed', 'success');
}

// Load Settings
function loadSettings() {
    loadConfig();
}

// Save Settings
async function saveSettings() {
    const settings = {
        advertisement_message: document.getElementById('ad-message').value,
        interval_minutes: parseInt(document.getElementById('interval-minutes').value),
        default_cooldown: parseInt(document.getElementById('default-cooldown').value),
        online_status: document.getElementById('online-status-select').value,
        use_proxies: document.getElementById('use-proxies').checked,
        keep_tokens_online: document.getElementById('keep-online').checked
    };
    
    try {
        const response = await fetch(`${API_BASE}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast('Settings saved successfully', 'success');
            loadStats();
        } else {
            showToast('Failed to save settings', 'error');
        }
    } catch (error) {
        console.error('Failed to save settings:', error);
        showToast('Failed to save settings', 'error');
    }
}

// Load Channels
async function loadChannels() {
    try {
        const response = await fetch(`${API_BASE}/channels`);
        const data = await response.json();
        
        const container = document.getElementById('channels-list');
        container.innerHTML = '';
        
        const tokenChannels = data.token_channels || {};
        const cooldowns = data.channel_cooldowns || {};
        
        if (Object.keys(tokenChannels).length === 0) {
            container.innerHTML = '<div class="list-item"><div class="list-item-content"><div class="list-item-title">No channels configured</div></div></div>';
            return;
        }
        
        for (const [tokenIndex, channels] of Object.entries(tokenChannels)) {
            channels.forEach(channelId => {
                const cooldown = cooldowns[channelId] || currentConfig.default_cooldown || 60;
                
                const item = document.createElement('div');
                item.className = 'list-item';
                item.innerHTML = `
                    <div class="list-item-content">
                        <div class="list-item-title">${channelId}</div>
                        <div class="list-item-meta">Token #${tokenIndex} • Cooldown: ${cooldown}m</div>
                    </div>
                    <div class="list-item-actions">
                        <button class="btn-icon" onclick="removeChannel(${tokenIndex}, '${channelId}')">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <line x1="18" y1="6" x2="6" y2="18"/>
                                <line x1="6" y1="6" x2="18" y2="18"/>
                            </svg>
                        </button>
                    </div>
                `;
                container.appendChild(item);
            });
        }
    } catch (error) {
        console.error('Failed to load channels:', error);
        showToast('Failed to load channels', 'error');
    }
}

// Add Channel
async function addChannel(tokenIndex, channelId, cooldown) {
    try {
        const response = await fetch(`${API_BASE}/channels/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token_index: tokenIndex, channel_id: channelId })
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Set cooldown if specified
            if (cooldown) {
                await fetch(`${API_BASE}/channels/cooldown`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ channel_id: channelId, cooldown_minutes: cooldown })
                });
            }
            
            showToast('Channel added successfully', 'success');
            loadChannels();
            loadStats();
        } else {
            showToast(result.message || 'Failed to add channel', 'error');
        }
    } catch (error) {
        console.error('Failed to add channel:', error);
        showToast('Failed to add channel', 'error');
    }
}

// Remove Channel
async function removeChannel(tokenIndex, channelId) {
    if (!confirm('Remove this channel?')) return;
    
    try {
        const response = await fetch(`${API_BASE}/channels/remove`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token_index: tokenIndex, channel_id: channelId })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast('Channel removed', 'success');
            loadChannels();
            loadStats();
        } else {
            showToast(result.message || 'Failed to remove channel', 'error');
        }
    } catch (error) {
        console.error('Failed to remove channel:', error);
        showToast('Failed to remove channel', 'error');
    }
}

// Load Servers (deprecated - silently ignore)
async function loadServers() {
    // Servers feature removed - do nothing
    return;
}

// Add Server (deprecated)
async function addServer(serverId) {
    console.log('Servers feature not available');
    return;
}

// Remove Server (deprecated)
async function removeServer(serverId) {
    console.log('Servers feature not available');
    return;
}

// Load Tokens
async function loadTokens() {
    try {
        const response = await fetch(`${API_BASE}/tokens`);
        const data = await response.json();
        
        document.getElementById('token-count').textContent = `${data.count} tokens`;
        
        // Don't show actual tokens for security
        document.getElementById('tokens-textarea').placeholder = 
            `${data.count} token(s) loaded. Paste new tokens here to update...`;
        
    } catch (error) {
        console.error('Failed to load tokens:', error);
        showToast('Failed to load tokens', 'error');
    }
}

// Save Tokens
async function saveTokens() {
    const textarea = document.getElementById('tokens-textarea');
    const tokens = textarea.value.split('\n').filter(t => t.trim() && !t.startsWith('#'));
    
    if (tokens.length === 0) {
        showToast('No tokens to save', 'error');
        return;
    }
    
    if (!confirm(`Save ${tokens.length} token(s)?`)) return;
    
    try {
        const response = await fetch(`${API_BASE}/tokens`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tokens })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast(result.message, 'success');
            textarea.value = '';
            loadTokens();
            loadStats();
        } else {
            showToast('Failed to save tokens', 'error');
        }
    } catch (error) {
        console.error('Failed to save tokens:', error);
        showToast('Failed to save tokens', 'error');
    }
}

// Load Proxies
async function loadProxies() {
    try {
        const response = await fetch(`${API_BASE}/proxies`);
        const data = await response.json();
        
        document.getElementById('proxy-count').textContent = `${data.count} proxies`;
        document.getElementById('proxies-textarea').value = data.proxies.join('\n');
        
    } catch (error) {
        console.error('Failed to load proxies:', error);
        showToast('Failed to load proxies', 'error');
    }
}

// Save Proxies
async function saveProxies() {
    const textarea = document.getElementById('proxies-textarea');
    const proxies = textarea.value.split('\n').filter(p => p.trim() && !p.startsWith('#'));
    
    try {
        const response = await fetch(`${API_BASE}/proxies`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxies })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast(result.message, 'success');
            loadProxies();
            loadStats();
        } else {
            showToast('Failed to save proxies', 'error');
        }
    } catch (error) {
        console.error('Failed to save proxies:', error);
        showToast('Failed to save proxies', 'error');
    }
}

// Load Logs
async function loadLogs() {
    try {
        const response = await fetch(`${API_BASE}/logs`);
        const data = await response.json();
        
        const container = document.getElementById('logs-container');
        container.innerHTML = '';
        
        if (data.logs.length === 0) {
            container.innerHTML = `
                <div class="log-entry log-info">
                    <span class="log-time">--:--:--</span>
                    <span class="log-level">INFO</span>
                    <span class="log-message">No recent activity</span>
                </div>
            `;
            return;
        }
        
        data.logs.forEach(log => {
            const time = new Date(log.timestamp).toLocaleTimeString();
            const levelClass = `log-${log.level.toLowerCase()}`;
            
            const entry = document.createElement('div');
            entry.className = `log-entry ${levelClass}`;
            entry.innerHTML = `
                <span class="log-time">${time}</span>
                <span class="log-level">${log.level}</span>
                <span class="log-message">${log.message}</span>
            `;
            container.appendChild(entry);
        });
    } catch (error) {
        console.error('Failed to load logs:', error);
    }
}

// Initialize Forms
function initForms() {
    // Add Channel Form
    const channelForm = document.getElementById('add-channel-form');
    if (channelForm) {
        channelForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const tokenIndex = parseInt(document.getElementById('channel-token-index').value);
            const channelId = document.getElementById('channel-id').value.trim();
            const cooldown = document.getElementById('channel-cooldown').value;
            
            if (channelId) {
                addChannel(tokenIndex, channelId, cooldown ? parseInt(cooldown) : null);
                e.target.reset();
            }
        });
    }
    
    // Add Server Form (optional - may not exist)
    const serverForm = document.getElementById('add-server-form');
    if (serverForm) {
        serverForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const serverId = document.getElementById('server-id').value.trim();
            
            if (serverId) {
                addServer(serverId);
                e.target.reset();
            }
        });
    }
}

// Toast Notifications
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type}`;
    toast.classList.add('show');
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (refreshInterval) {
        clearInterval(refreshInterval);
    }
});
/* ============================================================================
   ADD THESE FUNCTIONS TO app.js
   Bot Control Functions - Add at the END of the file
   ============================================================================ */

// Bot Status Polling
let botStatusInterval = null;

// Start polling bot status
function startBotStatusPolling() {
    if (botStatusInterval) {
        clearInterval(botStatusInterval);
    }
    
    // Poll every 5 seconds
    botStatusInterval = setInterval(() => {
        refreshBotStatus();
    }, 5000);
}

// Stop polling
function stopBotStatusPolling() {
    if (botStatusInterval) {
        clearInterval(botStatusInterval);
        botStatusInterval = null;
    }
}

// Refresh bot status
async function refreshBotStatus() {
    try {
        const response = await fetch('/api/advertiser/status');
        const data = await response.json();
        
        updateBotUI(data);
    } catch (error) {
        console.error('Failed to refresh bot status:', error);
    }
}

// Update bot UI - UPDATED VERSION
function updateBotUI(status) {
    // Dashboard mini version (if it exists)
    const statusBadge = document.getElementById('bot-status-badge');
    const statusText = document.getElementById('bot-status-text');
    const startBtn = document.getElementById('start-bot-btn');
    const stopBtn = document.getElementById('stop-bot-btn');
    const activeTokens = document.getElementById('bot-active-tokens');
    const channelsTracked = document.getElementById('bot-channels-tracked');
    
    // Bot Control Page version
    const statusBadgePage = document.getElementById('bot-status-badge-page');
    const statusTextPage = document.getElementById('bot-status-text-page');
    const startBtnPage = document.getElementById('start-bot-btn-page');
    const stopBtnPage = document.getElementById('stop-bot-btn-page');
    const activeTokensPage = document.getElementById('bot-active-tokens-page');
    const channelsTrackedPage = document.getElementById('bot-channels-tracked-page');
    const messagesTodayPage = document.getElementById('bot-messages-today-page');
    
    if (status.running) {
        // Bot is running
        if (statusBadge) {
            statusBadge.classList.remove('status-error');
            statusBadge.classList.add('status-running');
            statusText.textContent = 'Running';
        }
        
        if (statusBadgePage) {
            statusBadgePage.classList.remove('status-error');
            statusBadgePage.classList.add('status-running');
            statusTextPage.textContent = 'Running';
        }
        
        if (startBtn) startBtn.style.display = 'none';
        if (stopBtn) stopBtn.style.display = 'flex';
        if (startBtnPage) startBtnPage.style.display = 'none';
        if (stopBtnPage) stopBtnPage.style.display = 'flex';
        
        const tokens = status.active_tokens || 0;
        const channels = status.channels_tracked || 0;
        
        if (activeTokens) activeTokens.textContent = tokens;
        if (channelsTracked) channelsTracked.textContent = channels;
        if (activeTokensPage) activeTokensPage.textContent = tokens;
        if (channelsTrackedPage) channelsTrackedPage.textContent = channels;
        if (messagesTodayPage) messagesTodayPage.textContent = '0'; // TODO: Implement
        
        // Start polling if not already
        if (!botStatusInterval) {
            startBotStatusPolling();
        }
    } else {
        // Bot is stopped
        if (statusBadge) {
            statusBadge.classList.remove('status-running', 'status-error');
            statusText.textContent = 'Stopped';
        }
        
        if (statusBadgePage) {
            statusBadgePage.classList.remove('status-running', 'status-error');
            statusTextPage.textContent = 'Stopped';
        }
        
        if (startBtn) startBtn.style.display = 'flex';
        if (stopBtn) stopBtn.style.display = 'none';
        if (startBtnPage) startBtnPage.style.display = 'flex';
        if (stopBtnPage) stopBtnPage.style.display = 'none';
        
        if (activeTokens) activeTokens.textContent = '0';
        if (channelsTracked) channelsTracked.textContent = '0';
        if (activeTokensPage) activeTokensPage.textContent = '0';
        if (channelsTrackedPage) channelsTrackedPage.textContent = '0';
        if (messagesTodayPage) messagesTodayPage.textContent = '0';
        
        // Stop polling
        stopBotStatusPolling();
    }
}

// Start bot
async function startBot() {
    const startBtn = document.getElementById('start-bot-btn-page') || document.getElementById('start-bot-btn');
    if (!startBtn) return;
    
    const originalText = startBtn.innerHTML;
    
    // Show loading
    startBtn.disabled = true;
    startBtn.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
            <polyline points="23 4 23 10 17 10"/>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
        </svg>
        Starting...
    `;
    
    try {
        const response = await fetch('/api/advertiser/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Bot started successfully!', 'success');
            
            // Reset button and refresh status
            startBtn.disabled = false;
            startBtn.innerHTML = originalText;
            
            // Wait a moment then refresh status
            setTimeout(() => {
                refreshBotStatus();
                loadStats();
                loadBotControlConfig();
            }, 1000);
        } else {
            showToast(data.message || 'Failed to start bot', 'error');
            startBtn.disabled = false;
            startBtn.innerHTML = originalText;
        }
    } catch (error) {
        console.error('Start bot error:', error);
        showToast('Error starting bot. Please try again.', 'error');
        startBtn.disabled = false;
        startBtn.innerHTML = originalText;
    }
}

// Stop bot
async function stopBot() {
    if (!confirm('Are you sure you want to stop the bot?')) {
        return;
    }
    
    const stopBtn = document.getElementById('stop-bot-btn-page') || document.getElementById('stop-bot-btn');
    if (!stopBtn) return;
    
    const originalText = stopBtn.innerHTML;
    
    // Show loading
    stopBtn.disabled = true;
    stopBtn.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
            <polyline points="23 4 23 10 17 10"/>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
        </svg>
        Stopping...
    `;
    
    try {
        const response = await fetch('/api/advertiser/stop', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Bot stopped successfully', 'success');
            
            // Refresh status
            setTimeout(() => {
                refreshBotStatus();
                loadStats();
            }, 500);
        } else {
            showToast(data.message || 'Failed to stop bot', 'error');
            stopBtn.disabled = false;
            stopBtn.innerHTML = originalText;
        }
    } catch (error) {
        console.error('Stop bot error:', error);
        showToast('Error stopping bot. Please try again.', 'error');
        stopBtn.disabled = false;
        stopBtn.innerHTML = originalText;
    }
}

// Toast notification
function showToast(message, type = 'info') {
    // Create toast element if it doesn't exist
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    
    // Set message and type
    toast.textContent = message;
    toast.className = `toast ${type} show`;
    
    // Hide after 3 seconds
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Helper function to switch pages
function switchPage(pageName) {
    const navItem = document.querySelector(`[data-page="${pageName}"]`);
    if (navItem) {
        navItem.click();
    }
}

// Check auth and load user info
async function checkAuth() {
    try {
        const response = await fetch('/api/auth/current');
        const data = await response.json();
        
        if (data.user) {
            // Update username in sidebar
            const usernameEl = document.getElementById('sidebar-username');
            if (usernameEl) {
                usernameEl.textContent = data.user.username;
            }
            return true;
        } else {
            window.location.href = '/login';
            return false;
        }
    } catch (error) {
        console.error('Auth check failed:', error);
        window.location.href = '/login';
        return false;
    }
}

// Logout function
async function logout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        window.location.href = '/login';
    } catch (error) {
        console.error('Logout error:', error);
        window.location.href = '/login';
    }
}

// Initialize bot status on page load
document.addEventListener('DOMContentLoaded', () => {
    // Check authentication
    checkAuth();
    
    // Check bot status immediately
    refreshBotStatus();
    
    // Also refresh when bot control page becomes active
    const botControlTab = document.querySelector('[data-page="bot-control"]');
    if (botControlTab) {
        botControlTab.addEventListener('click', () => {
            setTimeout(refreshBotStatus, 100);
            setTimeout(loadBotControlConfig, 100);
        });
    }
});

// Load Bot Control Configuration Status
async function loadBotControlConfig() {
    try {
        // Get tokens count
        const tokensResponse = await fetch('/api/tokens');
        const tokensData = await tokensResponse.json();
        const tokenCount = tokensData.tokens ? tokensData.tokens.length : 0;
        
        // Get channels count
        const channelsResponse = await fetch('/api/channels');
        const channelsData = await channelsResponse.json();
        const channelCount = channelsData.channels ? channelsData.channels.length : 0;
        
        // Get config
        const configResponse = await fetch('/api/config');
        const configData = await configResponse.json();
        const hasMessage = configData.advertisement_message && configData.advertisement_message.trim().length > 0;
        const interval = configData.interval_minutes || 60;
        
        // Update Configuration Status section
        const tokensStatus = document.getElementById('config-tokens-status');
        const channelsStatus = document.getElementById('config-channels-status');
        const messageStatus = document.getElementById('config-message-status');
        const intervalStatus = document.getElementById('config-interval-status');
        
        if (tokensStatus) {
            const valueEl = tokensStatus.querySelector('.status-value');
            if (valueEl) {
                valueEl.textContent = tokenCount > 0 ? `${tokenCount} configured` : 'Not configured';
                valueEl.style.color = tokenCount > 0 ? 'var(--accent-primary)' : 'var(--text-muted)';
            }
        }
        
        if (channelsStatus) {
            const valueEl = channelsStatus.querySelector('.status-value');
            if (valueEl) {
                valueEl.textContent = channelCount > 0 ? `${channelCount} configured` : 'Not configured';
                valueEl.style.color = channelCount > 0 ? 'var(--accent-primary)' : 'var(--text-muted)';
            }
        }
        
        if (messageStatus) {
            const valueEl = messageStatus.querySelector('.status-value');
            if (valueEl) {
                valueEl.textContent = hasMessage ? 'Configured' : 'Not set';
                valueEl.style.color = hasMessage ? 'var(--accent-primary)' : 'var(--text-muted)';
            }
        }
        
        if (intervalStatus) {
            const valueEl = intervalStatus.querySelector('.status-value');
            if (valueEl) {
                valueEl.textContent = `${interval} minutes`;
                valueEl.style.color = 'var(--accent-primary)';
            }
        }
        
        // Also update the stats on bot control page
        const activeTokensPage = document.getElementById('bot-active-tokens-page');
        const channelsTrackedPage = document.getElementById('bot-channels-tracked-page');
        
        if (activeTokensPage && !document.querySelector('.status-running')) {
            activeTokensPage.textContent = tokenCount;
        }
        if (channelsTrackedPage && !document.querySelector('.status-running')) {
            channelsTrackedPage.textContent = channelCount;
        }
        
    } catch (error) {
        console.error('Failed to load bot control config:', error);
    }
}

// Call loadBotControlConfig on page load
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(loadBotControlConfig, 500);
});

// Clean up on page unload
window.addEventListener('beforeunload', () => {
    stopBotStatusPolling();
});

// Add spin animation for loading spinners
const style = document.createElement('style');
style.textContent = `
    @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
`;
document.head.appendChild(style);
