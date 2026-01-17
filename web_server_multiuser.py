from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
from datetime import datetime, timedelta
import secrets
import sqlite3
from functools import wraps

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# Simple session configuration (no Flask-Session needed)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

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
# ROUTES
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

# API Routes
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
            'is_admin': user['is_admin']
        }
    })

# Bot status endpoint (dummy for now)
@app.route('/api/advertiser/status', methods=['GET'])
@login_required
def bot_status():
    return jsonify({
        'running': False,
        'active_tokens': 0,
        'channels_tracked': 0
    })

# ============================================================================
# INITIALIZATION
# ============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("🌐 Discord Advertiser Dashboard - MINIMAL VERSION")
    print("=" * 60)
    
    init_db()
    
    port = int(os.environ.get('PORT', 5000))
    
    print(f"📡 Server starting on port {port}")
    print(f"🎨 Dashboard: http://localhost:{port}")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=port, debug=False)
