import sqlite3
from datetime import datetime
from typing import List, Tuple

class MessageDatabase:
    """Класс для работы с очередью сообщений в SQLite"""
    
    def __init__(self, db_path='messages.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Инициализирует базу данных и создаёт таблицы"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER UNIQUE,
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                text TEXT,
                timestamp DATETIME,
                processed BOOLEAN DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_processed_timestamp
            ON messages(processed, timestamp)
        ''')
        
        conn.commit()
        conn.close()
        print("База данных инициализирована")
    
    def add_message(self, message_id: int, chat_id: int, user_id: int, 
                    username: str, text: str) -> bool:
        """
        Добавляет сообщение в очередь
        
        Returns:
            bool: True если сообщение добавлено, False если уже существует
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO messages (message_id, chat_id, user_id, username, text, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (message_id, chat_id, user_id, username, text, datetime.now()))
            conn.commit()
            print(f"Сообщение {message_id} от @{username} добавлено в очередь")
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def get_unprocessed_messages(self, limit: int = 20) -> List[Tuple]:
        """
        Получает необработанные сообщения из очереди
        
        Args:
            limit: максимальное количество сообщений (по умолчанию 20)
        
        Returns:
            List[Tuple]: список кортежей (id, message_id, chat_id, user_id, username, text, timestamp)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, message_id, chat_id, user_id, username, text, timestamp
            FROM messages
            WHERE processed = 0
            ORDER BY timestamp ASC
            LIMIT ?
        ''', (limit,))
        
        messages = cursor.fetchall()
        conn.close()
        
        print(f"Получено {len(messages)} необработанных сообщений")
        return messages
    
    def mark_as_processed(self, message_ids: List[int]):
        """
        Помечает сообщения как обработанные
        
        Args:
            message_ids: список id сообщений из БД (не message_id из Telegram!)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.executemany(
            'UPDATE messages SET processed = 1 WHERE id = ?',
            [(msg_id,) for msg_id in message_ids]
        )
        
        conn.commit()
        conn.close()
        print(f"Помечено как обработанные: {len(message_ids)} сообщений")
    
    def get_stats(self) -> dict:
        """Получает статистику по очереди"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM messages WHERE processed = 0')
        unprocessed = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM messages WHERE processed = 1')
        processed = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'unprocessed': unprocessed,
            'processed': processed,
            'total': unprocessed + processed
        }