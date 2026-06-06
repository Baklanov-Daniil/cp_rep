import sqlite3
from datetime import datetime
from typing import List, Dict, Optional

class TaskDatabase:
    """Хранит задачи, извлечённые LLM из сообщений."""
    
    def __init__(self, db_path: str = "tasks.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Создаёт таблицы, если их нет."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                assignee_name TEXT,
                deadline TEXT,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'new',
                source_message_id INTEGER,
                chat_id INTEGER,
                created_at DATETIME
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status 
            ON tasks(status)
        """)
        
        conn.commit()
        conn.close()
        print("База задач инициализирована")
    
    def add_task(self, title: str, description: str = "", 
                 assignee_name: str = None, deadline: str = None,
                 priority: str = "medium", source_message_id: int = None,
                 chat_id: int = None) -> int:
        """Добавляет задачу в БД. Возвращает ID задачи."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO tasks 
            (title, description, assignee_name, deadline, priority, 
             source_message_id, chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, description, assignee_name, deadline, priority,
              source_message_id, chat_id, datetime.now()))
        
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return task_id
    
    def get_tasks_by_status(self, status: str = 'new') -> List[Dict]:
        """Получает задачи по статусу."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM tasks 
            WHERE status = ? 
            ORDER BY created_at DESC
        """, (status,))
        
        tasks = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return tasks
    
    def get_all_tasks(self) -> List[Dict]:
        """Получает все задачи."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM tasks 
            ORDER BY created_at DESC
        """)
        
        tasks = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return tasks
    
    def get_task(self, task_id: int) -> Optional[Dict]:
        """Получает задачу по ID."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        
        task = cursor.fetchone()
        conn.close()
        
        return dict(task) if task else None
    
    def update_task_status(self, task_id: int, status: str) -> bool:
        """Обновляет статус задачи."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE tasks 
            SET status = ? 
            WHERE id = ?
        """, (status, task_id))
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success
    
    def delete_task(self, task_id: int) -> bool:
        """Удаляет задачу."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success
    
    def get_stats(self) -> Dict:
        """Статистика по задачам."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM tasks WHERE status = "new"')
        new_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM tasks WHERE status = "done"')
        done_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM tasks')
        total = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'new': new_count,
            'done': done_count,
            'total': total
        }