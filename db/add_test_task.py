from task_db import TaskDatabase

db = TaskDatabase()

# Добавить задачу
task_id = db.add_task(
    title="Сделать рефакторинг",
    description="Переписать код",
    assignee_name="Ира",
    deadline="2026-06-08",
    priority="high"
)
print(f"Задача создана с ID: {task_id}")

# Получить все новые задачи
new_tasks = db.get_tasks_by_status('new')

# Получить все задачи
all_tasks = db.get_all_tasks()

# Отметить как выполненную
db.update_task_status(task_id, 'done')

# Статистика
stats = db.get_stats()
print(stats)  # {'new': 5, 'done': 10, 'total': 15}