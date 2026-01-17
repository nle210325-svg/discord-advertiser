from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
from datetime import datetime, timedelta
import secrets
import sqlite3
from functools import wraps
import platform
import asyncio
import threading

# Import advertiser service
from integrated_advertiser import advertiser_service

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# Session configuration - 30 days persistent
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

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
        advertiser_service.set_loop(advertiser_loop)
        advertiser_loop.run_forever()
    
    advertiser_thread = threading.Thread(target=run_loop, daemon=True)
    advertiser_thread.start()
    print("✅ Advertiser service started in background")

def run_async(coro):
    """Helper to run async functions from sync Flask routes"""
    if advertiser_loop:
        future = asyncio.run_coroutine_threadsafe(coro, advertiser_loop)
        try:
            return future.result(timeout=30)
        except Exception as e:
            print(f"Async error: {e}")
            return None
    return None

# Start the advertiser service
start_advertiser_service()

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
        message_length INTEGER DEFAULT 100,
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
        cooldown INTEGER DEFAULT 60,
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

def get_db():
    conn = sqlite3.connect('advertiser.db')
    conn.row_factory = sqlite3.Row
    return conn

def add_log(user_id, level, message, details=None):
    """Add activity log entry"""
    try:
        conn = get_db()
        conn.execute(
            'INSERT INTO activity_logs (user_id, level, message, details) VALUES (?, ?, ?, ?)',
            (user_id, level, message, json.dumps(details) if details else None)
        )
        conn.commit()
        conn.close()
    except:
        pass

def mask_token(token):
    """Mask token for display"""
    if len(token) > 20:
        return token[:10] + '...' + token[-10:]
    return token[:5] + '...'

def ensure_first_admin():
    """Make the first registered user an admin if no admins exist"""
    conn = get_db()
    admin_count = conn.execute('SELECT COUNT(*) as count FROM users WHERE is_admin = 1').fetchone()['count']
    
    # Check environment variables for admin
    admin_username = os.environ.get('ADMIN_USERNAME')
    
    if admin_username and admin_count == 0:
        user = conn.execute('SELECT id, username FROM users WHERE username = ?', (admin_username,)).fetchone()
        if user:
            conn.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (user['id'],))
            conn.commit()
            print(f"✅ Made user '{user['username']}' an admin (via ADMIN_USERNAME env)")
    
    # Fallback: make first user admin if no admin exists
    if admin_count == 0:
        first_user = conn.execute('SELECT id, username FROM users ORDER BY id ASC LIMIT 1').fetchone()
        if first_user:
            conn.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (first_user['id'],))
            conn.commit()
            print(f"✅ Made first user '{first_user['username']}' an admin")
    
    conn.close()

# ============================================================================
# AUTHENTICATION DECORATORS
# ============================================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
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
# PAGE ROUTES
# ============================================================================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard_page'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard_page():
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

# ============================================================================
# AUTH API ROUTES
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
        
        conn.execute('INSERT INTO user_configs (user_id) VALUES (?)', (user_id,))
        conn.execute('INSERT INTO user_stats (user_id, total_sent) VALUES (?, 0)', (user_id,))
        conn.commit()
        conn.close()
        
        # Check if this is first user
        ensure_first_admin()
        
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
    session['is_admin'] = bool(user['is_admin'])
    
    add_log(user['id'], 'INFO', 'User logged in')
    
    return jsonify({
        'success': True,
        'message': 'Login successful',
        'user': {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'is_admin': bool(user['is_admin'])
        }
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    if 'user_id' in session:
        add_log(session['user_id'], 'INFO', 'User logged out')
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/auth/current', methods=['GET'])
def current_user():
    if 'user_id' not in session:
        return jsonify({'user': None})
    
    conn = get_db()
    user = conn.execute('SELECT id, username, email, is_admin FROM users WHERE id = ?',
                       (session['user_id'],)).fetchone()
    conn.close()
    
    if not user:
        session.clear()
        return jsonify({'user': None})
    
    return jsonify({
        'user': {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'is_admin': bool(user['is_admin'])
        }
    })

# ============================================================================
# STATS API ROUTES
# ============================================================================

@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    user_id = session['user_id']
    conn = get_db()
    
    stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
    tokens = conn.execute('SELECT COUNT(*) as count FROM user_tokens WHERE user_id = ?', (user_id,)).fetchone()
    channels = conn.execute('SELECT COUNT(*) as count FROM user_channels WHERE user_id = ?', (user_id,)).fetchone()
    
    conn.close()
    
    return jsonify({
        'total_sent': stats['total_sent'] if stats else 0,
        'token_count': tokens['count'],
        'channel_count': channels['count'],
        'active_tokens': 0,
        'last_activity': stats['last_activity'] if stats else None
    })

# ============================================================================
# TOKEN API ROUTES
# ============================================================================

@app.route('/api/tokens', methods=['GET'])
@login_required
def get_tokens():
    user_id = session['user_id']
    conn = get_db()
    tokens = conn.execute('SELECT * FROM user_tokens WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    
    return jsonify({
        'tokens': [{'id': t['id'], 'masked_token': t['masked_token'], 'added_at': t['added_at']} for t in tokens]
    })

@app.route('/api/tokens', methods=['POST'])
@login_required
def save_tokens():
    user_id = session['user_id']
    data = request.json
    tokens_input = data.get('tokens', '')
    
    # Handle both string and list input
    if isinstance(tokens_input, list):
        tokens = [t.strip() for t in tokens_input if t and t.strip()]
    else:
        tokens = [t.strip() for t in tokens_input.strip().split('\n') if t.strip()]
    
    conn = get_db()
    # Clear existing tokens
    conn.execute('DELETE FROM user_tokens WHERE user_id = ?', (user_id,))
    
    # Add new tokens
    for token in tokens:
        masked = mask_token(token)
        conn.execute('INSERT INTO user_tokens (user_id, token, masked_token) VALUES (?, ?, ?)',
                    (user_id, token, masked))
    
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Updated tokens: {len(tokens)} tokens saved')
    
    return jsonify({'success': True, 'message': f'{len(tokens)} tokens saved'})

# ============================================================================
# PROXY API ROUTES
# ============================================================================

@app.route('/api/proxies', methods=['GET'])
@login_required
def get_proxies():
    user_id = session['user_id']
    conn = get_db()
    proxies = conn.execute('SELECT * FROM user_proxies WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    
    return jsonify({
        'proxies': [{'id': p['id'], 'proxy': p['proxy'], 'added_at': p['added_at']} for p in proxies]
    })

@app.route('/api/proxies', methods=['POST'])
@login_required
def save_proxies():
    user_id = session['user_id']
    data = request.json
    proxies_input = data.get('proxies', '')
    
    # Handle both string and list input
    if isinstance(proxies_input, list):
        proxies = [p.strip() for p in proxies_input if p and p.strip()]
    else:
        proxies = [p.strip() for p in proxies_input.strip().split('\n') if p.strip()]
    
    conn = get_db()
    # Clear existing proxies
    conn.execute('DELETE FROM user_proxies WHERE user_id = ?', (user_id,))
    
    # Add new proxies
    for proxy in proxies:
        conn.execute('INSERT INTO user_proxies (user_id, proxy) VALUES (?, ?)', (user_id, proxy))
    
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Updated proxies: {len(proxies)} proxies saved')
    
    return jsonify({'success': True, 'message': f'{len(proxies)} proxies saved'})

# ============================================================================
# CHANNEL API ROUTES
# ============================================================================

@app.route('/api/channels', methods=['GET'])
@login_required
def get_channels():
    user_id = session['user_id']
    conn = get_db()
    channels = conn.execute('SELECT * FROM user_channels WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    
    return jsonify({
        'channels': [{'id': c['id'], 'token_index': c['token_index'], 'channel_id': c['channel_id'], 
                     'cooldown': c['cooldown'], 'added_at': c['added_at']} for c in channels]
    })

@app.route('/api/channels', methods=['POST'])
@login_required
def add_channel():
    user_id = session['user_id']
    data = request.json
    
    token_index = data.get('token_index', 0)
    channel_id = data.get('channel_id', '').strip()
    cooldown = data.get('cooldown', 60)
    
    if not channel_id:
        return jsonify({'success': False, 'message': 'Channel ID is required'}), 400
    
    conn = get_db()
    conn.execute('INSERT INTO user_channels (user_id, token_index, channel_id, cooldown) VALUES (?, ?, ?, ?)',
                (user_id, token_index, channel_id, cooldown))
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', f'Added channel: {channel_id}')
    
    return jsonify({'success': True, 'message': 'Channel added'})

@app.route('/api/channels/<int:channel_id>', methods=['DELETE'])
@login_required
def delete_channel(channel_id):
    user_id = session['user_id']
    conn = get_db()
    conn.execute('DELETE FROM user_channels WHERE id = ? AND user_id = ?', (channel_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Channel deleted'})

# ============================================================================
# SETTINGS API ROUTES
# ============================================================================

@app.route('/api/config', methods=['GET'])
@login_required
def get_config():
    user_id = session['user_id']
    conn = get_db()
    config = conn.execute('SELECT * FROM user_configs WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    
    if not config:
        return jsonify({
            'advertisement_message': '',
            'interval_minutes': 60,
            'default_cooldown': 60,
            'use_proxies': True,
            'keep_tokens_online': True,
            'online_status': 'online',
            'message_length': 100
        })
    
    return jsonify({
        'advertisement_message': config['advertisement_message'] or '',
        'interval_minutes': config['interval_minutes'],
        'default_cooldown': config['default_cooldown'],
        'use_proxies': bool(config['use_proxies']),
        'keep_tokens_online': bool(config['keep_tokens_online']),
        'online_status': config['online_status'],
        'message_length': config['message_length']
    })

@app.route('/api/config', methods=['POST'])
@login_required
def save_config():
    user_id = session['user_id']
    data = request.json
    
    conn = get_db()
    conn.execute('''UPDATE user_configs SET 
        advertisement_message = ?,
        interval_minutes = ?,
        default_cooldown = ?,
        use_proxies = ?,
        keep_tokens_online = ?,
        online_status = ?,
        message_length = ?
        WHERE user_id = ?''', (
        data.get('advertisement_message', ''),
        data.get('interval_minutes', 60),
        data.get('default_cooldown', 60),
        1 if data.get('use_proxies', True) else 0,
        1 if data.get('keep_tokens_online', True) else 0,
        data.get('online_status', 'online'),
        data.get('message_length', 100),
        user_id
    ))
    conn.commit()
    conn.close()
    
    add_log(user_id, 'INFO', 'Settings updated')
    
    return jsonify({'success': True, 'message': 'Settings saved'})

# ============================================================================
# LOGS API ROUTES
# ============================================================================

@app.route('/api/logs', methods=['GET'])
@login_required
def get_logs():
    user_id = session['user_id']
    limit = request.args.get('limit', 50, type=int)
    
    conn = get_db()
    logs = conn.execute(
        'SELECT * FROM activity_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?',
        (user_id, limit)
    ).fetchall()
    conn.close()
    
    return jsonify({
        'logs': [{'id': l['id'], 'level': l['level'], 'message': l['message'], 
                 'timestamp': l['timestamp']} for l in logs]
    })

# ============================================================================
# BOT CONTROL API ROUTES
# ============================================================================

@app.route('/api/advertiser/status', methods=['GET'])
@login_required
def bot_status():
    user_id = session['user_id']
    conn = get_db()
    tokens = conn.execute('SELECT COUNT(*) as count FROM user_tokens WHERE user_id = ?', (user_id,)).fetchone()
    channels = conn.execute('SELECT COUNT(*) as count FROM user_channels WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    
    status = advertiser_service.get_user_status(user_id)
    
    return jsonify({
        'running': status['running'],
        'active_tokens': status['active_tokens'],
        'channels_tracked': status['channels_tracked'] or channels['count'],
        'total_tokens': tokens['count'],
        'last_send': status['last_send']
    })

@app.route('/api/advertiser/start', methods=['POST'])
@login_required
def start_bot():
    user_id = session['user_id']
    
    conn = get_db()
    tokens = conn.execute('SELECT COUNT(*) as count FROM user_tokens WHERE user_id = ?', (user_id,)).fetchone()
    channels = conn.execute('SELECT COUNT(*) as count FROM user_channels WHERE user_id = ?', (user_id,)).fetchone()
    config = conn.execute('SELECT advertisement_message FROM user_configs WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    
    if tokens['count'] == 0:
        return jsonify({'success': False, 'message': 'Please add at least one token first'})
    
    if channels['count'] == 0:
        return jsonify({'success': False, 'message': 'Please add at least one channel first'})
    
    if not config or not config['advertisement_message']:
        return jsonify({'success': False, 'message': 'Please set an advertisement message in Settings first'})
    
    try:
        success = run_async(advertiser_service.start_user_advertiser(user_id))
        if success:
            add_log(user_id, 'INFO', 'Bot started successfully')
            return jsonify({'success': True, 'message': 'Bot started successfully!'})
        else:
            return jsonify({'success': False, 'message': 'Failed to start bot'})
    except Exception as e:
        add_log(user_id, 'ERROR', f'Failed to start bot: {str(e)}')
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@app.route('/api/advertiser/stop', methods=['POST'])
@login_required
def stop_bot():
    user_id = session['user_id']
    
    try:
        run_async(advertiser_service.stop_user_advertiser(user_id))
        add_log(user_id, 'INFO', 'Bot stopped')
        return jsonify({'success': True, 'message': 'Bot stopped'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

# ============================================================================
# ADMIN API ROUTES
# ============================================================================

@app.route('/api/admin/stats/overview', methods=['GET'])
@admin_required
def admin_overview():
    conn = get_db()
    
    total_users = conn.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
    total_messages = conn.execute('SELECT SUM(total_sent) as total FROM user_stats').fetchone()['total'] or 0
    total_tokens = conn.execute('SELECT COUNT(*) as count FROM user_tokens').fetchone()['count']
    total_channels = conn.execute('SELECT COUNT(*) as count FROM user_channels').fetchone()['count']
    
    conn.close()
    
    return jsonify({
        'total_users': total_users,
        'total_messages': total_messages,
        'active_advertisers': 0,
        'total_tokens': total_tokens,
        'total_channels': total_channels
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
        ORDER BY u.created_at DESC
    ''').fetchall()
    conn.close()
    
    return jsonify({
        'users': [{
            'id': u['id'],
            'username': u['username'],
            'email': u['email'],
            'is_admin': bool(u['is_admin']),
            'created_at': u['created_at'],
            'total_sent': u['total_sent'],
            'last_activity': u['last_activity'],
            'token_count': u['token_count'],
            'channel_count': u['channel_count'],
            'advertiser_running': False,
            'active_tokens': 0
        } for u in users]
    })

@app.route('/api/admin/user/<int:user_id>', methods=['GET'])
@admin_required
def admin_user_detail(user_id):
    conn = get_db()
    
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'User not found'}), 404
    
    stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
    config = conn.execute('SELECT * FROM user_configs WHERE user_id = ?', (user_id,)).fetchone()
    tokens = conn.execute('SELECT id, masked_token FROM user_tokens WHERE user_id = ?', (user_id,)).fetchall()
    channels = conn.execute('SELECT * FROM user_channels WHERE user_id = ?', (user_id,)).fetchall()
    logs = conn.execute('SELECT * FROM activity_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20', (user_id,)).fetchall()
    
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
            'use_proxies': bool(config['use_proxies']) if config else True,
            'message_length': config['message_length'] if config else 100
        },
        'tokens': [{'id': t['id'], 'masked_token': t['masked_token']} for t in tokens],
        'channels': [{'id': c['id'], 'channel_id': c['channel_id']} for c in channels],
        'recent_logs': [{'level': l['level'], 'message': l['message'], 'timestamp': l['timestamp']} for l in logs]
    })

@app.route('/api/admin/user/<int:user_id>/delete', methods=['DELETE'])
@admin_required
def admin_delete_user(user_id):
    if user_id == session['user_id']:
        return jsonify({'success': False, 'error': 'Cannot delete yourself'}), 400
    
    conn = get_db()
    user = conn.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if not user:
        conn.close()
        return jsonify({'success': False, 'error': 'User not found'}), 404
    
    if user['is_admin']:
        conn.close()
        return jsonify({'success': False, 'error': 'Cannot delete admin users'}), 400
    
    # Delete all user data
    conn.execute('DELETE FROM activity_logs WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM user_stats WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM user_channels WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM user_proxies WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM user_tokens WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM user_configs WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'User deleted'})

@app.route('/api/admin/activity/recent', methods=['GET'])
@admin_required
def admin_recent_activity():
    limit = request.args.get('limit', 50, type=int)
    
    conn = get_db()
    logs = conn.execute('''
        SELECT l.*, u.username 
        FROM activity_logs l 
        JOIN users u ON l.user_id = u.id 
        ORDER BY l.timestamp DESC 
        LIMIT ?
    ''', (limit,)).fetchall()
    conn.close()
    
    return jsonify({
        'logs': [{'id': l['id'], 'username': l['username'], 'level': l['level'], 
                 'message': l['message'], 'timestamp': l['timestamp']} for l in logs]
    })

@app.route('/api/admin/system/info', methods=['GET'])
@admin_required
def admin_system_info():
    import sys
    
    db_size = 0
    try:
        db_size = os.path.getsize('advertiser.db')
    except:
        pass
    
    return jsonify({
        'python_version': sys.version,
        'platform': platform.system(),
        'platform_release': platform.release(),
        'database_size': db_size,
        'flask_debug': app.debug
    })

# ============================================================================
# INITIALIZATION
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
    print(f"🤖 Bot service: DISABLED (upload integrated_advertiser.py to enable)")
    print(f"🐛 Debug mode: {debug}")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=port, debug=debug)
