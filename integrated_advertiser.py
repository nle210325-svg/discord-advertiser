"""
Integrated Multi-User Discord Advertiser Service
Runs in background and manages advertising for all users
"""

import asyncio
import aiohttp
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import traceback

class UserAdvertiser:
    """Handles advertising for a single user"""
    
    def __init__(self, user_id: int, db_path: str = 'advertiser.db'):
        self.user_id = user_id
        self.db_path = db_path
        self.running = False
        self.task = None
        self.sessions: List[aiohttp.ClientSession] = []
        self.channel_last_sent: Dict[str, datetime] = {}
        
    def get_db(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def add_log(self, level: str, message: str, details: dict = None):
        """Add activity log for user"""
        try:
            conn = self.get_db()
            conn.execute(
                'INSERT INTO activity_logs (user_id, level, message, details) VALUES (?, ?, ?, ?)',
                (self.user_id, level, message, json.dumps(details) if details else None)
            )
            conn.commit()
            
            # Keep only last 100 logs
            conn.execute('''DELETE FROM activity_logs WHERE id NOT IN (
                SELECT id FROM activity_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 100
            ) AND user_id = ?''', (self.user_id, self.user_id))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error adding log: {e}")
    
    async def send_message(self, session: aiohttp.ClientSession, token: str, 
                          channel_id: str, message: str, proxy: Optional[str] = None) -> bool:
        """Send a Discord message"""
        url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        payload = {"content": message}
        
        try:
            kwargs = {"headers": headers, "json": payload}
            if proxy:
                kwargs["proxy"] = proxy
            
            async with session.post(url, **kwargs, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return True
                elif response.status == 429:
                    data = await response.json()
                    retry_after = data.get('retry_after', 5)
                    self.add_log('WARNING', f'Rate limited on channel {channel_id}', 
                                {'retry_after': retry_after})
                    return False
                else:
                    error_text = await response.text()
                    self.add_log('ERROR', f'Failed to send to {channel_id}', 
                                {'status': response.status, 'error': error_text[:100]})
                    return False
        except asyncio.TimeoutError:
            self.add_log('ERROR', f'Timeout sending to {channel_id}')
            return False
        except Exception as e:
            self.add_log('ERROR', f'Exception sending to {channel_id}', {'error': str(e)})
            return False
    
    async def advertise_loop(self):
        """Main advertising loop"""
        self.add_log('INFO', 'Advertiser started')
        
        while self.running:
            try:
                # Get user config
                conn = self.get_db()
                config = conn.execute('SELECT * FROM user_configs WHERE user_id = ?', 
                                    (self.user_id,)).fetchone()
                
                if not config:
                    self.add_log('ERROR', 'No configuration found')
                    await asyncio.sleep(60)
                    continue
                
                message = config['advertisement_message']
                interval = config['interval_minutes'] * 60
                use_proxies = bool(config['use_proxies'])
                
                if not message or not message.strip():
                    self.add_log('WARNING', 'No advertisement message set')
                    await asyncio.sleep(60)
                    continue
                
                # Get tokens
                tokens = conn.execute('SELECT token FROM user_tokens WHERE user_id = ?', 
                                     (self.user_id,)).fetchall()
                tokens = [t['token'] for t in tokens]
                
                if not tokens:
                    self.add_log('WARNING', 'No tokens configured')
                    await asyncio.sleep(60)
                    continue
                
                # Get proxies
                proxies = []
                if use_proxies:
                    proxy_rows = conn.execute('SELECT proxy FROM user_proxies WHERE user_id = ?', 
                                            (self.user_id,)).fetchall()
                    proxies = [p['proxy'] for p in proxy_rows]
                
                # Get channels for each token
                channels = conn.execute(
                    'SELECT token_index, channel_id, cooldown_minutes FROM user_channels WHERE user_id = ?',
                    (self.user_id,)
                ).fetchall()
                
                conn.close()
                
                if not channels:
                    self.add_log('WARNING', 'No channels configured')
                    await asyncio.sleep(60)
                    continue
                
                # Create sessions if needed
                if not self.sessions:
                    for _ in tokens:
                        session = aiohttp.ClientSession()
                        self.sessions.append(session)
                
                # Send messages
                sent_count = 0
                now = datetime.now()
                
                for channel in channels:
                    if not self.running:
                        break
                    
                    token_idx = channel['token_index']
                    channel_id = channel['channel_id']
                    cooldown_minutes = channel['cooldown_minutes']
                    
                    # Check if token index is valid
                    if token_idx >= len(tokens):
                        continue
                    
                    # Check cooldown
                    last_sent = self.channel_last_sent.get(channel_id)
                    if last_sent:
                        time_since = (now - last_sent).total_seconds() / 60
                        if time_since < cooldown_minutes:
                            continue
                    
                    # Get token and proxy
                    token = tokens[token_idx]
                    proxy = proxies[token_idx % len(proxies)] if proxies else None
                    session = self.sessions[token_idx]
                    
                    # Send message
                    success = await self.send_message(session, token, channel_id, message, proxy)
                    
                    if success:
                        self.channel_last_sent[channel_id] = now
                        sent_count += 1
                        self.add_log('SUCCESS', f'Sent to channel {channel_id}', 
                                   {'token_index': token_idx})
                        
                        # Update stats
                        conn = self.get_db()
                        conn.execute(
                            'UPDATE user_stats SET total_sent = total_sent + 1, last_activity = CURRENT_TIMESTAMP WHERE user_id = ?',
                            (self.user_id,)
                        )
                        conn.commit()
                        conn.close()
                    
                    # Small delay between messages
                    await asyncio.sleep(2)
                
                if sent_count > 0:
                    self.add_log('INFO', f'Sent {sent_count} messages')
                
                # Wait for next interval
                await asyncio.sleep(interval)
                
            except Exception as e:
                self.add_log('ERROR', f'Error in advertise loop: {str(e)}', 
                           {'traceback': traceback.format_exc()})
                await asyncio.sleep(60)
    
    async def start(self):
        """Start the advertiser"""
        if self.running:
            return False
        
        self.running = True
        self.task = asyncio.create_task(self.advertise_loop())
        return True
    
    async def stop(self):
        """Stop the advertiser"""
        self.running = False
        
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        # Close sessions
        for session in self.sessions:
            await session.close()
        self.sessions.clear()
        
        self.add_log('INFO', 'Advertiser stopped')


class AdvertiserService:
    """Manages advertisers for all users"""
    
    def __init__(self, db_path: str = 'advertiser.db'):
        self.db_path = db_path
        self.advertisers: Dict[int, UserAdvertiser] = {}
    
    async def start_user_advertiser(self, user_id: int) -> bool:
        """Start advertiser for a user"""
        if user_id in self.advertisers and self.advertisers[user_id].running:
            return False
        
        advertiser = UserAdvertiser(user_id, self.db_path)
        success = await advertiser.start()
        
        if success:
            self.advertisers[user_id] = advertiser
        
        return success
    
    async def stop_user_advertiser(self, user_id: int):
        """Stop advertiser for a user"""
        if user_id in self.advertisers:
            await self.advertisers[user_id].stop()
            del self.advertisers[user_id]
    
    def get_user_status(self, user_id: int) -> dict:
        """Get status of user's advertiser"""
        if user_id in self.advertisers and self.advertisers[user_id].running:
            advertiser = self.advertisers[user_id]
            return {
                'running': True,
                'active_tokens': len(advertiser.sessions),
                'channels_tracked': len(advertiser.channel_last_sent)
            }
        return {
            'running': False,
            'active_tokens': 0,
            'channels_tracked': 0
        }
    
    async def stop_all(self):
        """Stop all advertisers"""
        for user_id in list(self.advertisers.keys()):
            await self.stop_user_advertiser(user_id)


# Global service instance
advertiser_service = AdvertiserService()