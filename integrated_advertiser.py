"""
Integrated Discord Advertiser Service
Runs in background thread, sends messages for all users
"""

import asyncio
import aiohttp
import sqlite3
import json
import random
from datetime import datetime, timedelta
from typing import Dict, Optional

class UserAdvertiser:
    """Handles advertising for a single user"""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.active_tokens = 0
        self.channels_tracked = 0
        self.last_send = None
        
    def get_db(self):
        conn = sqlite3.connect('advertiser.db')
        conn.row_factory = sqlite3.Row
        return conn
    
    def add_log(self, level: str, message: str):
        """Add log entry to database"""
        try:
            conn = self.get_db()
            conn.execute(
                'INSERT INTO activity_logs (user_id, level, message) VALUES (?, ?, ?)',
                (self.user_id, level, message)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Log error: {e}")
    
    def update_stats(self, messages_sent: int = 0):
        """Update user statistics"""
        try:
            conn = self.get_db()
            conn.execute(
                'UPDATE user_stats SET total_sent = total_sent + ?, last_activity = ? WHERE user_id = ?',
                (messages_sent, datetime.now().isoformat(), self.user_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Stats update error: {e}")
    
    async def send_message(self, session: aiohttp.ClientSession, token: str, channel_id: str, message: str) -> bool:
        """Send a message to a Discord channel"""
        url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        payload = {"content": message}
        
        try:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    return True
                elif response.status == 429:
                    # Rate limited
                    data = await response.json()
                    retry_after = data.get('retry_after', 5)
                    self.add_log('WARNING', f'Rate limited on channel {channel_id}, waiting {retry_after}s')
                    await asyncio.sleep(retry_after)
                    return False
                elif response.status == 401:
                    self.add_log('ERROR', f'Invalid token for channel {channel_id}')
                    return False
                elif response.status == 403:
                    self.add_log('WARNING', f'No permission to send in channel {channel_id}')
                    return False
                else:
                    self.add_log('ERROR', f'Failed to send to {channel_id}: Status {response.status}')
                    return False
        except Exception as e:
            self.add_log('ERROR', f'Network error sending to {channel_id}: {str(e)}')
            return False
    
    async def run_cycle(self):
        """Run one advertising cycle"""
        conn = self.get_db()
        
        # Get user config
        config = conn.execute('SELECT * FROM user_configs WHERE user_id = ?', (self.user_id,)).fetchone()
        if not config or not config['advertisement_message']:
            conn.close()
            self.add_log('WARNING', 'No advertisement message configured')
            return 0
        
        message = config['advertisement_message']
        
        # Get tokens
        tokens = conn.execute('SELECT token FROM user_tokens WHERE user_id = ?', (self.user_id,)).fetchall()
        if not tokens:
            conn.close()
            self.add_log('WARNING', 'No tokens configured')
            return 0
        
        # Get channels
        channels = conn.execute('SELECT * FROM user_channels WHERE user_id = ?', (self.user_id,)).fetchall()
        if not channels:
            conn.close()
            self.add_log('WARNING', 'No channels configured')
            return 0
        
        conn.close()
        
        self.active_tokens = len(tokens)
        self.channels_tracked = len(channels)
        
        messages_sent = 0
        
        async with aiohttp.ClientSession() as session:
            for channel in channels:
                token_index = channel['token_index']
                if token_index >= len(tokens):
                    token_index = 0
                
                token = tokens[token_index]['token']
                channel_id = channel['channel_id']
                
                success = await self.send_message(session, token, channel_id, message)
                if success:
                    messages_sent += 1
                    self.add_log('SUCCESS', f'Sent message to channel {channel_id}')
                
                # Random delay between messages (2-5 seconds)
                await asyncio.sleep(random.uniform(2, 5))
        
        return messages_sent
    
    async def run(self):
        """Main advertising loop"""
        self.running = True
        self.add_log('INFO', 'Advertiser started')
        
        while self.running:
            try:
                # Get interval from config
                conn = self.get_db()
                config = conn.execute('SELECT interval_minutes FROM user_configs WHERE user_id = ?', 
                                     (self.user_id,)).fetchone()
                conn.close()
                
                interval = (config['interval_minutes'] if config else 60) * 60  # Convert to seconds
                
                # Run advertising cycle
                messages_sent = await self.run_cycle()
                
                if messages_sent > 0:
                    self.update_stats(messages_sent)
                    self.add_log('INFO', f'Cycle complete: {messages_sent} messages sent')
                
                self.last_send = datetime.now()
                
                # Wait for next cycle
                self.add_log('INFO', f'Waiting {interval // 60} minutes until next cycle')
                
                # Check every 10 seconds if we should stop
                for _ in range(interval // 10):
                    if not self.running:
                        break
                    await asyncio.sleep(10)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.add_log('ERROR', f'Advertiser error: {str(e)}')
                await asyncio.sleep(60)  # Wait 1 minute on error
        
        self.add_log('INFO', 'Advertiser stopped')
    
    def stop(self):
        """Stop the advertiser"""
        self.running = False
        if self.task:
            self.task.cancel()


class AdvertiserService:
    """Manages all user advertisers"""
    
    def __init__(self):
        self.user_advertisers: Dict[int, UserAdvertiser] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
    
    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop to use"""
        self.loop = loop
    
    async def start_user_advertiser(self, user_id: int) -> bool:
        """Start advertiser for a user"""
        try:
            # Stop existing if running
            if user_id in self.user_advertisers:
                await self.stop_user_advertiser(user_id)
            
            # Create new advertiser
            advertiser = UserAdvertiser(user_id)
            self.user_advertisers[user_id] = advertiser
            
            # Start the task
            advertiser.task = asyncio.create_task(advertiser.run())
            
            return True
        except Exception as e:
            print(f"Failed to start advertiser for user {user_id}: {e}")
            return False
    
    async def stop_user_advertiser(self, user_id: int):
        """Stop advertiser for a user"""
        if user_id in self.user_advertisers:
            advertiser = self.user_advertisers[user_id]
            advertiser.stop()
            
            if advertiser.task:
                try:
                    advertiser.task.cancel()
                    await asyncio.sleep(0.1)
                except:
                    pass
            
            del self.user_advertisers[user_id]
    
    def get_user_status(self, user_id: int) -> dict:
        """Get status of user's advertiser"""
        if user_id in self.user_advertisers:
            advertiser = self.user_advertisers[user_id]
            return {
                'running': advertiser.running,
                'active_tokens': advertiser.active_tokens,
                'channels_tracked': advertiser.channels_tracked,
                'last_send': advertiser.last_send.isoformat() if advertiser.last_send else None
            }
        return {
            'running': False,
            'active_tokens': 0,
            'channels_tracked': 0,
            'last_send': None
        }
    
    def is_user_running(self, user_id: int) -> bool:
        """Check if user's advertiser is running"""
        return user_id in self.user_advertisers and self.user_advertisers[user_id].running


# Global instance
advertiser_service = AdvertiserService()
