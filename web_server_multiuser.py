from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
from datetime import datetime, timedelta
import secrets
import sqlite3
from functools import wraps
import asyncio
import threading
import sys
import platform

# Import advertiser service
from integrated_advertiser import advertiser_service

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# Session configuration - PERSISTENT SESSIONS
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # 30 days
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_PERMANENT'] = True

# ============================================================================
# ADVERTISER SERVICE STARTUP
# ============================================================================

advertiser_loop = None
advertiser_thread = None

def start_advertiser_service():
    """Start the advertiser service in a background thread"""
    global advertiser_loop, advertiser_thread
    
    def run_loop():
        global advertiser_loop
        advertiser_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(advertiser_loop)
        advertiser_loop.run_forever()
    
    advertiser_thread = threading.Thread(target=run_loop, daemon=True)
    advertiser_thread.start()
    print("✅ Advertiser service started in background")

# Start service when Flask starts
start_advertiser_service()

def run_async(coro):
    """Helper to run async functions from sync Flask routes"""
    if advertiser_loop:
        future = asyncio.run_coroutine_threadsafe(coro, advertiser_loop)
        return future.result(timeout=30)
    return None

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_db():
    conn = sqlite3.connect('advertiser.db')
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_admin BOOLEAN DEFAULT 0
    )''')
    
    # User configs table
    c.execute('''CREATE TABLE IF NOT EXISTS user_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        advertisement_message TEXT,
        interval_minutes INTEGER DEFAULT 60,
        default_cooldown INTEGER DEFAULT 60,
        use_proxies BOOLEAN DEFAULT 1,
        keep_tokens_online BOOLEAN DEFAULT 1,
        online_status TEXT DEFAULT 'online',
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    
    # User tokens table
    c.execute('''CREATE TABLE IF NOT EXISTS user_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL,
        masked_token TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    
    # User proxies table
    c.execute('''CREATE TABLE IF NOT EXISTS user_proxies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        proxy TEXT NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    
    # User channels table
    c.execute('''CREATE TABLE IF NOT EXISTS user_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_index INTEGER NOT NULL,
        channel_id TEXT NOT NULL,
        cooldown_minutes INTEGER DEFAULT 60,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    
    # User servers table
    c.execute('''CREATE TABLE IF NOT EXISTS user_servers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        server_id TEXT NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    
    # User stats table
    c.execute('''CREATE TABLE IF NOT EXISTS user_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        total_sent INTEGER DEFAULT 0,
        last_activity TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    
    # Activity logs table
    c.execute('''CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level TEXT NOT NULL,
        message TEXT NOT NULL,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    
    conn.commit()
    conn.close()

# Database helper functions
def get_db():
    conn = sqlite3.connect('advertiser.db')
    conn.row_factory = sqlite3.Row
    return conn

# ============================================================================
# AUTHENTICATION DECORATORS
# ============================================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        if not session.get('is_admin'):
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def mask_token(token):
    if len(token) > 20:
        return f"{token[:10]}...{token[-10:]}"
    return "***"

def get_user_config(user_id):
    conn = get_db()
    config = conn.execute('SELECT * FROM user_configs WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    
    if config:
        return dict(config)
    else:
        conn = get_db()
        conn.execute('''INSERT INTO user_configs (user_id, advertisement_message, interval_minutes, 
                        default_cooldown, use_proxies, keep_tokens_online, online_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (user_id, '', 60, 60, 1, 1, 'online'))
        conn.commit()
        config = conn.execute('SELECT * FROM user_configs WHERE user_id = ?', (user_id,)).fetchone()
        conn.close()
        return dict(config)

def add_log(user_id, level, message, details=None):
    conn = get_db()
    conn.execute('INSERT INTO activity_logs (user_id, level, message, details) VALUES (?, ?, ?, ?)',
                 (user_id, level, message, json.dumps(details) if details else None))
    conn.commit()
    
    # Keep only last 100 logs per user
    conn.execute('''DELETE FROM activity_logs WHERE id NOT IN (
        SELECT id FROM activity_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 100
    ) AND user_id = ?''', (user_id, user_id))
    conn.commit()
    conn.close()

def ensure_first_admin():
    """Make the first registered user an admin if no admins exist"""
    conn = get_db()
    
    admin_count = conn.execute('SELECT COUNT(*) as count FROM users WHERE is_admin = 1').fetchone()['count']
    
    # Option 1: Check for ADMIN_USERNAME or ADMIN_EMAIL environment variable
    admin_username = os.environ.get('ADMIN_USERNAME')
    admin_email = os.environ.get('ADMIN_EMAIL')
    
    if admin_username or admin_email:
        if admin_username:
            user = conn.execute('SELECT id, username FROM users WHERE username = ?', (admin_username,)).fetchone()
            if user and admin_count == 0:
                conn.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (user['id'],))
                conn.commit()
                print(f"✅ Made user '{user['username']}' an admin (via ADMIN_USERNAME env)")
        
        if admin_email and admin_count == 0:
            user = conn.execute('SELECT id, username FROM users WHERE email = ?', (admin_email,)).fetchone()
            if user:
                conn.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (user['id'],))
                conn.commit()
                print(f"✅ Made user '{user['username']}' an admin (via ADMIN_EMAIL env)")
    
    # Option 2: Fallback to first user if no admin exists
    if admin_count == 0:
        first_user = conn.execute('SELECT id, username FROM users ORDER BY id LIMIT 1').fetchone()
        if first_user:
            conn.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (first_user['id'],))
            conn.commit()
            print(f"✅ Made user '{first_user['username']}' (ID {first_user['id']}) an admin (first user)")
    
    conn.close()

# ============================================================================
# PAGE ROUTES
# ============================================================================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/signup')
def signup_page():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('signup.html')

@app.route('/admin')
@admin_required
def admin_dashboard():
    return render_template('admin.html')

@app.route('/admin/setup')
def admin_setup_page():
    """Quick admin setup page for first-time deployment"""
    conn = get_db()
    admin_count = conn.execute('SELECT COUNT(*) as count FROM users WHERE is_admin = 1').fetchone()['count']
    user_count = conn.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
    conn.close()
    
    # Only show if no admins exist
    if admin_count > 0:
        return redirect(url_for('admin_dashboard'))
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Setup</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: #0a0e14;
                color: #e8eaed;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                margin: 0;
            }}
            .container {{
                background: #1e252e;
                padding: 40px;
                border-radius: 16px;
                max-width: 500px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.5);
            }}
            h1 {{
                color: #00ff88;
                margin: 0 0 20px 0;
            }}
            p {{
                color: #9ca3af;
                margin: 10px 0;
            }}
            input {{
                width: 100%;
                padding: 12px;
                margin: 10px 0;
                background: #151b23;
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                color: #e8eaed;
                font-size: 15px;
            }}
            button {{
                width: 100%;
                padding: 14px;
                background: linear-gradient(135deg, #00ff88, #00d4ff);
                border: none;
                border-radius: 8px;
                color: #0a0e14;
                font-weight: 600;
                font-size: 15px;
                cursor: pointer;
                margin-top: 20px;
            }}
            button:hover {{
                opacity: 0.9;
            }}
            .success {{
                background: rgba(0,255,136,0.1);
                border: 1px solid #00ff88;
                padding: 12px;
                border-radius: 8px;
                margin-top: 20px;
                display: none;
            }}
            .error {{
                background: rgba(255,68,102,0.1);
                border: 1px solid #ff4466;
                padding: 12px;
                border-radius: 8px;
                margin-top: 20px;
                display: none;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🛡️ Admin Setup</h1>
            <p>No admin users exist yet. Total users: {user_count}</p>
            <p>Enter your username to become admin:</p>
            
            <input type="text" id="username" placeholder="Enter your username" />
            <button onclick="setupAdmin()">Make Me Admin</button>
            
            <div id="success" class="success">
                ✅ Success! You are now an admin. <a href="/login" style="color: #00ff88;">Login here</a>
            </div>
            <div id="error" class="error">
                ❌ <span id="errorMsg">Error occurred</span>
            </div>
        </div>
        
        <script>
            async function setupAdmin() {{
                const username = document.getElementById('username').value.trim();
                if (!username) {{
                    document.getElementById('errorMsg').textContent = 'Please enter your username';
                    document.getElementById('error').style.display = 'block';
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/admin/quick-setup', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{username}})
                    }});
                    
                    const data = await response.json();
                    
                    if (data.success) {{
                        document.getElementById('success').style.display = 'block';
                        document.getElementById('error').style.display = 'none';
                    }} else {{
                        document.getElementById('errorMsg').textContent = data.message || 'Failed to setup admin';
                        document.getElementById('error').style.display = 'block';
                        document.getElementById('success').style.display = 'none';
                    }}
                }} catch (error) {{
                    document.getElementById('errorMsg').textContent = 'Connection error';
                    document.getElementById('error').style.display = 'block';
                }}
            }}
        </script>
    </body>
    </html>
    '''

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    data = request.json
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    
    if not username or not email or not password:
        return jsonify({'success': False, 'message': 'All fields are required'}), 400
    
    if len(username) < 3:
        return jsonify({'success': False, 'message': 'Username must be at least 3 characters'}), 400
    
    if len(password) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
    
    conn = get_db()
    existing = conn.execute('SELECT id FROM users WHERE username = ? OR email = ?', 
                           (username, email)).fetchone()
    
    if existing:
        conn.close()
        return jsonify({'success': False, 'message': 'Username or email already exists'}), 400
    
    password_hash = generate_password_hash(password)
    try:
        cursor = conn.execute('INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                            (username, email, password_hash))
        user_id = cursor.lastrowid
        
        conn.execute('INSERT INTO user_stats (user_id, total_sent) VALUES (?, 0)', (user_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Account created successfully'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'message': f'Error creating account: {str(e)}'}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'success': False, 'message': 'Username and password required'}), 400
    
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE username = ? OR email = ?', 
                       (username, username)).fetchone()
    conn.close()
    
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'success': False, 'message': 'Invalid username or password'}), 401
    
    session.permanent = True
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['is_admin'] = user['is_admin']
    
    return jsonify({
        'success': True,
        'message': 'Login successful',
        'user': {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'is_admin': user['is_admin']
        }
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/auth/current', methods=['GET'])
@login_required
def current_user():
    conn = get_db()
    user = conn.execute('SELECT id, username, email, is_admin FROM users WHERE id = ?', 
                       (session['user_id'],)).fetchone()
    conn.close()
    
    if user:
        return jsonify({
            'success': True,
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email'],
                'is_admin': user['is_admin']
            }
        })
    return jsonify({'success': False, 'message': 'User not found'}), 404

# ============================================================================
# CONFIG ROUTES
# ============================================================================

@app.route('/api/config', methods=['GET'])
@login_required
def get_config():
    user_id = session['user_id']
    config = get_user_config(user_id)
    
    conn = get_db()
    token_count = conn.execute('SELECT COUNT(*) as count FROM user_tokens WHERE user_id = ?', 
                               (user_id,)).fetchone()['count']
    proxy_count = conn.execute('SELECT COUNT(*) as count FROM user_proxies WHERE user_id = ?', 
                               (user_id,)).fetchone()['count']
    conn.close()
    
    return jsonify({
        'advertisement_message': config['advertisement_message'] or '',
        'interval_minutes': config['interval_minutes'],
        'default_cooldown': config['default_cooldown'],
        'use_proxies': bool(config['use_proxies']),
        'keep_tokens_online': bool(config['keep_tokens_online']),
        'online_status': config['online_status'],
        'token_count': token_count,
        'proxy_count': proxy_count
    })

@app.route('/api/config', methods=['POST'])
@login_required
def update_config():
    user_id = session['user_id']
    data = request.json
    
    conn = get_db()
    
    updates = []
    params = []
    
    if 'advertisement_message' in data:
        updates.append('advertisement_message = ?')
        params.append(data['advertisement_message'])
    
    if 'interval_minutes' in data:
        updates.append('interval_minutes = ?')
        params.append(int(data['interval_minutes']))
    
    if 'default_cooldown' in data:
        updates.append('default_cooldown = ?')
        params.append(int(data['default_cooldown']))
    
    if 'use_proxies' in data:
        updates.append('use_proxies = ?')
        params.append(1 if data['use_proxies'] else 0)
    
    if 'keep_tokens_online' in data:
        updates.append('keep_tokens_online = ?')
        params.append(1 if data['keep_tokens_online'] else 0)
    
    if 'online_status' in data:
        updates.append('online_status = ?')
        params.append(data['online_status'])
    
    if updates:
        params.append(user_id)
        conn.execute(f"UPDATE user_configs SET {', '.join(updates)} WHERE user_id = ?", params)
        conn.commit()
    
    conn.close()
    
    add_log(user_id, 'INFO', 'Configuration updated')
    return jsonify({'success': True, 'message': 'Configuration updated'})

# ============================================================================
# TOKENS ROUTES
# ============================================================================

@app.route('/api/tokens', methods=['GET'])
@login_required
def get_tokens():
    user_id = session['user_id']
    conn = get_db()
    tokens = conn.execute('SELECT masked_token FROM user_tokens WHERE user_id = ?', 
                         (user_id,)).fetchall()
    conn.close()
    
    masked = [t['masked_token'] for t in tokens]
    return jsonify({'tokens': masked, 'count': len(masked)})

@app.route('/api/tokens', methods=['POST'])
@login_required
def update_tokens():
    user_id = session['user_id']
    data = request.json
    tokens = data.get('tokens', [])
    
    valid_tokens = [t.strip() for t in tokens if len(t.strip()) > 20]
    
    conn = get_db()
    conn.execute('DELETE FROM user_tokens WHERE user_id = ?', (user_id,))
    
    for token in valid_tokens:
        masked = mask_token(token)
        conn.execute('INSERT INTO user_tokens (user_id, token, masked_token) VALUES (?, ?, ?)',
                    (user_id, token, masked))
    
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Updated tokens', {'count': len(valid_tokens)})
    return jsonify({'success': True, 'message': f'Saved {len(valid_tokens)} tokens'})

@app.route('/api/tokens/raw', methods=['GET'])
@login_required
def get_raw_tokens():
    user_id = session['user_id']
    conn = get_db()
    tokens = conn.execute('SELECT token FROM user_tokens WHERE user_id = ?', 
                         (user_id,)).fetchall()
    conn.close()
    
    return jsonify({'tokens': [t['token'] for t in tokens]})

# ============================================================================
# PROXIES ROUTES
# ============================================================================

@app.route('/api/proxies', methods=['GET'])
@login_required
def get_proxies():
    user_id = session['user_id']
    conn = get_db()
    proxies = conn.execute('SELECT proxy FROM user_proxies WHERE user_id = ?', 
                          (user_id,)).fetchall()
    conn.close()
    
    return jsonify({'proxies': [p['proxy'] for p in proxies], 'count': len(proxies)})

@app.route('/api/proxies', methods=['POST'])
@login_required
def update_proxies():
    user_id = session['user_id']
    data = request.json
    proxies = data.get('proxies', [])
    
    valid_proxies = [p.strip() for p in proxies if p.strip()]
    
    conn = get_db()
    conn.execute('DELETE FROM user_proxies WHERE user_id = ?', (user_id,))
    
    for proxy in valid_proxies:
        conn.execute('INSERT INTO user_proxies (user_id, proxy) VALUES (?, ?)',
                    (user_id, proxy))
    
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Updated proxies', {'count': len(valid_proxies)})
    return jsonify({'success': True, 'message': f'Saved {len(valid_proxies)} proxies'})

# ============================================================================
# CHANNELS ROUTES
# ============================================================================

@app.route('/api/channels', methods=['GET'])
@login_required
def get_channels():
    user_id = session['user_id']
    conn = get_db()
    channels = conn.execute('SELECT * FROM user_channels WHERE user_id = ? ORDER BY token_index, channel_id', 
                           (user_id,)).fetchall()
    conn.close()
    
    token_channels = {}
    channel_cooldowns = {}
    
    for ch in channels:
        token_idx = str(ch['token_index'])
        if token_idx not in token_channels:
            token_channels[token_idx] = []
        
        token_channels[token_idx].append(ch['channel_id'])
        channel_cooldowns[ch['channel_id']] = ch['cooldown_minutes']
    
    return jsonify({
        'token_channels': token_channels,
        'channel_cooldowns': channel_cooldowns
    })

@app.route('/api/channels/add', methods=['POST'])
@login_required
def add_channel():
    user_id = session['user_id']
    data = request.json
    token_index = int(data.get('token_index'))
    channel_id = str(data.get('channel_id'))
    cooldown = int(data.get('cooldown_minutes', 60))
    
    conn = get_db()
    
    existing = conn.execute('SELECT id FROM user_channels WHERE user_id = ? AND token_index = ? AND channel_id = ?',
                           (user_id, token_index, channel_id)).fetchone()
    
    if existing:
        conn.close()
        return jsonify({'success': False, 'message': 'Channel already exists'})
    
    conn.execute('INSERT INTO user_channels (user_id, token_index, channel_id, cooldown_minutes) VALUES (?, ?, ?, ?)',
                (user_id, token_index, channel_id, cooldown))
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Added channel {channel_id} to token {token_index}')
    return jsonify({'success': True, 'message': f'Channel added to token {token_index}'})

@app.route('/api/channels/remove', methods=['POST'])
@login_required
def remove_channel():
    user_id = session['user_id']
    data = request.json
    token_index = int(data.get('token_index'))
    channel_id = str(data.get('channel_id'))
    
    conn = get_db()
    conn.execute('DELETE FROM user_channels WHERE user_id = ? AND token_index = ? AND channel_id = ?',
                (user_id, token_index, channel_id))
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Removed channel {channel_id} from token {token_index}')
    return jsonify({'success': True, 'message': 'Channel removed'})

@app.route('/api/channels/cooldown', methods=['POST'])
@login_required
def set_channel_cooldown():
    user_id = session['user_id']
    data = request.json
    channel_id = str(data.get('channel_id'))
    cooldown = int(data.get('cooldown_minutes'))
    
    conn = get_db()
    conn.execute('UPDATE user_channels SET cooldown_minutes = ? WHERE user_id = ? AND channel_id = ?',
                (cooldown, user_id, channel_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': f'Cooldown set to {cooldown} minutes'})

# ============================================================================
# SERVERS ROUTES
# ============================================================================

@app.route('/api/servers', methods=['GET'])
@login_required
def get_servers():
    user_id = session['user_id']
    conn = get_db()
    servers = conn.execute('SELECT server_id FROM user_servers WHERE user_id = ?', 
                          (user_id,)).fetchall()
    conn.close()
    
    return jsonify({'servers': [s['server_id'] for s in servers]})

@app.route('/api/servers/add', methods=['POST'])
@login_required
def add_server():
    user_id = session['user_id']
    data = request.json
    server_id = str(data.get('server_id'))
    
    conn = get_db()
    
    existing = conn.execute('SELECT id FROM user_servers WHERE user_id = ? AND server_id = ?',
                           (user_id, server_id)).fetchone()
    
    if existing:
        conn.close()
        return jsonify({'success': False, 'message': 'Server already exists'})
    
    conn.execute('INSERT INTO user_servers (user_id, server_id) VALUES (?, ?)',
                (user_id, server_id))
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Added server {server_id}')
    return jsonify({'success': True, 'message': 'Server added'})

@app.route('/api/servers/remove', methods=['POST'])
@login_required
def remove_server():
    user_id = session['user_id']
    data = request.json
    server_id = str(data.get('server_id'))
    
    conn = get_db()
    conn.execute('DELETE FROM user_servers WHERE user_id = ? AND server_id = ?',
                (user_id, server_id))
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Removed server {server_id}')
    return jsonify({'success': True, 'message': 'Server removed'})

# ============================================================================
# STATS ROUTES
# ============================================================================

@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    user_id = session['user_id']
    
    conn = get_db()
    
    stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
    
    token_count = conn.execute('SELECT COUNT(*) as count FROM user_tokens WHERE user_id = ?', 
                               (user_id,)).fetchone()['count']
    channel_count = conn.execute('SELECT COUNT(*) as count FROM user_channels WHERE user_id = ?', 
                                 (user_id,)).fetchone()['count']
    server_count = conn.execute('SELECT COUNT(*) as count FROM user_servers WHERE user_id = ?', 
                                (user_id,)).fetchone()['count']
    proxy_count = conn.execute('SELECT COUNT(*) as count FROM user_proxies WHERE user_id = ?', 
                               (user_id,)).fetchone()['count']
    
    config = get_user_config(user_id)
    
    conn.close()
    
    # Get advertiser status
    advertiser_status = advertiser_service.get_user_status(user_id)
    
    uptime = "0h 0m"
    
    return jsonify({
        'total_sent': stats['total_sent'] if stats else 0,
        'active_tokens': advertiser_status['active_tokens'],
        'total_tokens': token_count,
        'total_channels': channel_count,
        'total_servers': server_count,
        'proxy_count': proxy_count,
        'uptime': uptime,
        'last_activity': stats['last_activity'] if stats and stats['last_activity'] else None,
        'interval_minutes': config['interval_minutes'],
        'use_proxies': bool(config['use_proxies']),
        'keep_online': bool(config['keep_tokens_online']),
        'online_status': config['online_status']
    })

@app.route('/api/stats/increment', methods=['POST'])
@login_required
def increment_stats():
    user_id = session['user_id']
    data = request.json
    
    conn = get_db()
    
    if 'total_sent' in data:
        conn.execute('UPDATE user_stats SET total_sent = total_sent + ?, last_activity = CURRENT_TIMESTAMP WHERE user_id = ?',
                    (data['total_sent'], user_id))
    else:
        conn.execute('UPDATE user_stats SET last_activity = CURRENT_TIMESTAMP WHERE user_id = ?',
                    (user_id,))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

# ============================================================================
# LOGS ROUTES
# ============================================================================

@app.route('/api/logs', methods=['GET'])
@login_required
def get_logs():
    user_id = session['user_id']
    
    conn = get_db()
    logs = conn.execute('SELECT * FROM activity_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 100',
                       (user_id,)).fetchall()
    conn.close()
    
    return jsonify({
        'logs': [{
            'timestamp': log['timestamp'],
            'level': log['level'],
            'message': log['message'],
            'details': json.loads(log['details']) if log['details'] else {}
        } for log in logs]
    })

@app.route('/api/logs/add', methods=['POST'])
@login_required
def add_log_api():
    user_id = session['user_id']
    data = request.json
    
    add_log(user_id, data.get('level', 'INFO'), data.get('message', ''), data.get('details'))
    
    return jsonify({'success': True})

# ============================================================================
# ADVERTISER CONTROL ROUTES
# ============================================================================

@app.route('/api/advertiser/start', methods=['POST'])
@login_required
def start_advertiser():
    user_id = session['user_id']
    
    try:
        success = run_async(advertiser_service.start_user_advertiser(user_id))
        
        if success:
            add_log(user_id, 'SUCCESS', 'Advertiser started')
            return jsonify({
                'success': True,
                'message': 'Advertiser started successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to start advertiser. Check your configuration.'
            }), 400
    except Exception as e:
        add_log(user_id, 'ERROR', 'Failed to start advertiser', {'error': str(e)})
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500

@app.route('/api/advertiser/stop', methods=['POST'])
@login_required
def stop_advertiser():
    user_id = session['user_id']
    
    try:
        run_async(advertiser_service.stop_user_advertiser(user_id))
        
        add_log(user_id, 'INFO', 'Advertiser stopped')
        return jsonify({
            'success': True,
            'message': 'Advertiser stopped successfully'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500

@app.route('/api/advertiser/status', methods=['GET'])
@login_required
def get_advertiser_status():
    user_id = session['user_id']
    
    try:
        status = advertiser_service.get_user_status(user_id)
        return jsonify({
            'success': True,
            'status': status
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500

# ============================================================================
# ADMIN ROUTES
# ============================================================================

@app.route('/api/admin/quick-setup', methods=['POST'])
def admin_quick_setup():
    """Quick admin setup route - only works if no admins exist"""
    data = request.json
    username = data.get('username', '').strip()
    
    if not username:
        return jsonify({'success': False, 'message': 'Username required'}), 400
    
    conn = get_db()
    
    # Check if any admin exists
    admin_count = conn.execute('SELECT COUNT(*) as count FROM users WHERE is_admin = 1').fetchone()['count']
    
    if admin_count > 0:
        conn.close()
        return jsonify({'success': False, 'message': 'Admin already exists'}), 403
    
    # Find user by username
    user = conn.execute('SELECT id, username FROM users WHERE username = ?', (username,)).fetchone()
    
    if not user:
        conn.close()
        return jsonify({'success': False, 'message': 'User not found. Please sign up first.'}), 404
    
    # Make user admin
    conn.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (user['id'],))
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'message': f'{user["username"]} is now an admin!'
    })

@app.route('/api/admin/stats/overview', methods=['GET'])
@admin_required
def admin_overview():
    conn = get_db()
    
    total_users = conn.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
    
    # Active users (last 24 hours)
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    active_users = conn.execute(
        'SELECT COUNT(DISTINCT user_id) as count FROM user_stats WHERE last_activity > ?',
        (yesterday,)
    ).fetchone()['count']
    
    # Recent signups (last 7 days)
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    recent_signups = conn.execute(
        'SELECT COUNT(*) as count FROM users WHERE created_at > ?',
        (week_ago,)
    ).fetchone()['count']
    
    # Total messages
    total_messages = conn.execute('SELECT SUM(total_sent) as sum FROM user_stats').fetchone()['sum'] or 0
    
    # Total tokens
    total_tokens = conn.execute('SELECT COUNT(*) as count FROM user_tokens').fetchone()['count']
    
    # Total channels
    total_channels = conn.execute('SELECT COUNT(*) as count FROM user_channels').fetchone()['count']
    
    conn.close()
    
    # Running advertisers
    running_advertisers = len([uid for uid, adv in advertiser_service.advertisers.items() if adv.running])
    
    return jsonify({
        'total_users': total_users,
        'active_users': active_users,
        'recent_signups': recent_signups,
        'total_messages': total_messages,
        'total_tokens': total_tokens,
        'total_channels': total_channels,
        'running_advertisers': running_advertisers
    })

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_users():
    conn = get_db()
    
    users = conn.execute('''
        SELECT u.*, 
               COALESCE(s.total_sent, 0) as total_sent,
               s.last_activity,
               (SELECT COUNT(*) FROM user_tokens WHERE user_id = u.id) as token_count,
               (SELECT COUNT(*) FROM user_channels WHERE user_id = u.id) as channel_count
        FROM users u
        LEFT JOIN user_stats s ON u.id = s.user_id
        ORDER BY u.id
    ''').fetchall()
    
    conn.close()
    
    user_list = []
    for user in users:
        advertiser_status = advertiser_service.get_user_status(user['id'])
        
        user_list.append({
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'is_admin': bool(user['is_admin']),
            'created_at': user['created_at'],
            'total_sent': user['total_sent'],
            'last_activity': user['last_activity'],
            'token_count': user['token_count'],
            'channel_count': user['channel_count'],
            'advertiser_running': advertiser_status['running'],
            'active_tokens': advertiser_status['active_tokens']
        })
    
    return jsonify({'users': user_list})

@app.route('/api/admin/user/<int:user_id>', methods=['GET'])
@admin_required
def admin_user_details(user_id):
    conn = get_db()
    
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'User not found'}), 404
    
    stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
    config = conn.execute('SELECT * FROM user_configs WHERE user_id = ?', (user_id,)).fetchone()
    
    tokens = conn.execute('SELECT masked_token FROM user_tokens WHERE user_id = ?', (user_id,)).fetchall()
    channels = conn.execute('SELECT * FROM user_channels WHERE user_id = ?', (user_id,)).fetchall()
    
    recent_logs = conn.execute(
        'SELECT * FROM activity_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20',
        (user_id,)
    ).fetchall()
    
    conn.close()
    
    return jsonify({
        'user': {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'is_admin': bool(user['is_admin']),
            'created_at': user['created_at']
        },
        'stats': {
            'total_sent': stats['total_sent'] if stats else 0,
            'last_activity': stats['last_activity'] if stats else None
        },
        'config': {
            'interval_minutes': config['interval_minutes'] if config else 60,
            'default_cooldown': config['default_cooldown'] if config else 60,
            'use_proxies': bool(config['use_proxies']) if config else False,
            'message_length': len(config['advertisement_message'] or '') if config else 0
        },
        'tokens': [t['masked_token'] for t in tokens],
        'channels': [dict(c) for c in channels],
        'recent_logs': [{
            'timestamp': log['timestamp'],
            'level': log['level'],
            'message': log['message']
        } for log in recent_logs]
    })

@app.route('/api/admin/user/<int:user_id>/stop-advertiser', methods=['POST'])
@admin_required
def admin_stop_user_advertiser(user_id):
    try:
        run_async(advertiser_service.stop_user_advertiser(user_id))
        return jsonify({'success': True, 'message': 'Advertiser stopped'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/user/<int:user_id>/delete', methods=['DELETE'])
@admin_required
def admin_delete_user(user_id):
    # Don't allow deleting admins
    conn = get_db()
    user = conn.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if not user:
        conn.close()
        return jsonify({'success': False, 'error': 'User not found'}), 404
    
    if user['is_admin']:
        conn.close()
        return jsonify({'success': False, 'error': 'Cannot delete admin users'}), 403
    
    # Stop advertiser first
    try:
        run_async(advertiser_service.stop_user_advertiser(user_id))
    except:
        pass
    
    # Delete user (CASCADE will handle related tables)
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'User deleted successfully'})

@app.route('/api/admin/activity/recent', methods=['GET'])
@admin_required
def admin_recent_activity():
    limit = int(request.args.get('limit', 50))
    
    conn = get_db()
    logs = conn.execute('''
        SELECT a.*, u.username
        FROM activity_logs a
        JOIN users u ON a.user_id = u.id
        ORDER BY a.timestamp DESC
        LIMIT ?
    ''', (limit,)).fetchall()
    conn.close()
    
    return jsonify({
        'logs': [{
            'username': log['username'],
            'level': log['level'],
            'message': log['message'],
            'timestamp': log['timestamp']
        } for log in logs]
    })

@app.route('/api/admin/system/info', methods=['GET'])
@admin_required
def admin_system_info():
    # Get database size
    db_size = 0
    try:
        db_size = os.path.getsize('advertiser.db')
    except:
        pass
    
    return jsonify({
        'python_version': sys.version.split()[0],
        'platform': platform.system(),
        'platform_release': platform.release(),
        'database_size': db_size,
        'flask_debug': app.debug
    })

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("🌐 Multi-User Discord Advertiser Dashboard")
    print("=" * 60)
    
    init_db()
    ensure_first_admin()
    
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    
    print(f"📡 Server starting on port {port}")
    print(f"🎨 Dashboard: http://localhost:{port}")
    print(f"🛡️ Admin Panel: http://localhost:{port}/admin")
    print(f"🔐 Multi-user authentication enabled")
    print(f"💾 Database: advertiser.db (SQLite)")
    print(f"🤖 Background advertiser: ENABLED")
    print(f"🐛 Debug mode: {debug}")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=port, debug=debug)
