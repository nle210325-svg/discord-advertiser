from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
from datetime import datetime, timedelta
import secrets
import sqlite3
from functools import wraps

import os

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# Session configuration
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True if using HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Database initialization
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
    
    # User configs table (per-user settings)
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

# Initialize database on startup
init_db()

# Database helper functions
def get_db():
    conn = sqlite3.connect('advertiser.db')
    conn.row_factory = sqlite3.Row
    return conn

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
        return f(*args, **kwargs)
    return decorated_function

# Utility functions
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
        # Create default config
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

# Auth routes
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

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    data = request.json
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    
    # Validation
    if not username or not email or not password:
        return jsonify({'success': False, 'message': 'All fields are required'}), 400
    
    if len(username) < 3:
        return jsonify({'success': False, 'message': 'Username must be at least 3 characters'}), 400
    
    if len(password) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
    
    # Check if user exists
    conn = get_db()
    existing = conn.execute('SELECT id FROM users WHERE username = ? OR email = ?', 
                           (username, email)).fetchone()
    
    if existing:
        conn.close()
        return jsonify({'success': False, 'message': 'Username or email already exists'}), 400
    
    # Create user
    password_hash = generate_password_hash(password)
    try:
        cursor = conn.execute('INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                            (username, email, password_hash))
        user_id = cursor.lastrowid
        
        # Initialize user stats
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
    
    # Create session
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

# Config routes
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
    
    # Update config
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

# Tokens routes
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
    
    # Clear existing tokens
    conn.execute('DELETE FROM user_tokens WHERE user_id = ?', (user_id,))
    
    # Add new tokens
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
    """Get actual tokens for the bot to use"""
    user_id = session['user_id']
    conn = get_db()
    tokens = conn.execute('SELECT token FROM user_tokens WHERE user_id = ?', 
                         (user_id,)).fetchall()
    conn.close()
    
    return jsonify({'tokens': [t['token'] for t in tokens]})

# Proxies routes
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
    
    # Clear existing proxies
    conn.execute('DELETE FROM user_proxies WHERE user_id = ?', (user_id,))
    
    # Add new proxies
    for proxy in valid_proxies:
        conn.execute('INSERT INTO user_proxies (user_id, proxy) VALUES (?, ?)',
                    (user_id, proxy))
    
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Updated proxies', {'count': len(valid_proxies)})
    return jsonify({'success': True, 'message': f'Saved {len(valid_proxies)} proxies'})

# Channels routes
@app.route('/api/channels', methods=['GET'])
@login_required
def get_channels():
    user_id = session['user_id']
    conn = get_db()
    channels = conn.execute('SELECT * FROM user_channels WHERE user_id = ? ORDER BY token_index, channel_id', 
                           (user_id,)).fetchall()
    conn.close()
    
    # Organize by token index
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
    
    # Check if already exists
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

# Servers routes
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
    
    # Check if exists
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

# Stats routes
@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    user_id = session['user_id']
    
    conn = get_db()
    
    # Get user stats
    stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
    
    # Get counts
    token_count = conn.execute('SELECT COUNT(*) as count FROM user_tokens WHERE user_id = ?', 
                               (user_id,)).fetchone()['count']
    channel_count = conn.execute('SELECT COUNT(*) as count FROM user_channels WHERE user_id = ?', 
                                 (user_id,)).fetchone()['count']
    server_count = conn.execute('SELECT COUNT(*) as count FROM user_servers WHERE user_id = ?', 
                                (user_id,)).fetchone()['count']
    proxy_count = conn.execute('SELECT COUNT(*) as count FROM user_proxies WHERE user_id = ?', 
                               (user_id,)).fetchone()['count']
    
    # Get config
    config = get_user_config(user_id)
    
    conn.close()
    
    # Calculate uptime (mock - in real scenario, track when bot started)
    uptime = "0h 0m"
    
    return jsonify({
        'total_sent': stats['total_sent'] if stats else 0,
        'active_tokens': 0,  # This would be updated by the bot
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

# Logs routes
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

if __name__ == '__main__':
    print("=" * 60)
    print("🌐 Multi-User Discord Advertiser Dashboard")
    print("=" * 60)
    print(f"📡 Server starting on http://0.0.0.0:5000")
    print(f"🎨 Dashboard: http://localhost:5000")
    print(f"🔐 Multi-user authentication enabled")
    print(f"💾 Database: advertiser.db (SQLite)")
    print("=" * 60)
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
