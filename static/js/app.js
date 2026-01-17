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
    loadServers();
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
        if (page === 'servers') loadServers();
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

// Load Servers
async function loadServers() {
    try {
        const response = await fetch(`${API_BASE}/servers`);
        const data = await response.json();
        
        const container = document.getElementById('servers-list');
        container.innerHTML = '';
        
        const servers = data.servers || [];
        
        if (servers.length === 0) {
            container.innerHTML = '<div class="list-item"><div class="list-item-content"><div class="list-item-title">No servers configured</div></div></div>';
            return;
        }
        
        servers.forEach(serverId => {
            const item = document.createElement('div');
            item.className = 'list-item';
            item.innerHTML = `
                <div class="list-item-content">
                    <div class="list-item-title">${serverId}</div>
                    <div class="list-item-meta">Server ID</div>
                </div>
                <div class="list-item-actions">
                    <button class="btn-icon" onclick="removeServer('${serverId}')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"/>
                            <line x1="6" y1="6" x2="18" y2="18"/>
                        </svg>
                    </button>
                </div>
            `;
            container.appendChild(item);
        });
    } catch (error) {
        console.error('Failed to load servers:', error);
        showToast('Failed to load servers', 'error');
    }
}

// Add Server
async function addServer(serverId) {
    try {
        const response = await fetch(`${API_BASE}/servers/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_id: serverId })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast('Server added successfully', 'success');
            loadServers();
            loadStats();
        } else {
            showToast(result.message || 'Failed to add server', 'error');
        }
    } catch (error) {
        console.error('Failed to add server:', error);
        showToast('Failed to add server', 'error');
    }
}

// Remove Server
async function removeServer(serverId) {
    if (!confirm('Remove this server?')) return;
    
    try {
        const response = await fetch(`${API_BASE}/servers/remove`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_id: serverId })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast('Server removed', 'success');
            loadServers();
            loadStats();
        } else {
            showToast(result.message || 'Failed to remove server', 'error');
        }
    } catch (error) {
        console.error('Failed to remove server:', error);
        showToast('Failed to remove server', 'error');
    }
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
    document.getElementById('add-channel-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const tokenIndex = parseInt(document.getElementById('channel-token-index').value);
        const channelId = document.getElementById('channel-id').value.trim();
        const cooldown = document.getElementById('channel-cooldown').value;
        
        if (channelId) {
            addChannel(tokenIndex, channelId, cooldown ? parseInt(cooldown) : null);
            e.target.reset();
        }
    });
    
    // Add Server Form
    document.getElementById('add-server-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const serverId = document.getElementById('server-id').value.trim();
        
        if (serverId) {
            addServer(serverId);
            e.target.reset();
        }
    });
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
