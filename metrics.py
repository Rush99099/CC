import time
from prettytable import PrettyTable
from rich.console import Console
from rich.table import Table

console = Console()
# Global dictionary to store metrics: {agent_id: {task_id: [(timestamp, metric)]}}
metrics_data = {}

def get_metric_for_ack(agent_id, task_id):
    """Retrieves the metric for the given agent and task."""
    if agent_id in metrics_data and task_id in metrics_data[agent_id]:
        return metrics_data[agent_id][task_id]
    return None

def store_metric(agent_id, task_id, metric):
    """Stores a metric for the given agent and task."""
    if agent_id not in metrics_data:
        metrics_data[agent_id] = {}
    if task_id not in metrics_data[agent_id]:
        metrics_data[agent_id][task_id] = []
    metrics_data[agent_id][task_id].append((metric))

def display_metrics():
    """Display all stored metrics using a rich table."""
    table = Table(title="Metrics")
    table.add_column("Agent ID", justify="center")
    table.add_column("Task ID", justify="center")
    table.add_column("Metrics", justify="left")
    for agent_id, tasks in metrics_data.items():
        for task_id, metrics in tasks.items():
            metrics_str = "\n".join([f"{metric}" for metric in metrics])
            table.add_row(agent_id, str(task_id), metrics_str)
    console.print(table)

def get_metrics_by_task(task_id):
    """Retrieves metrics for a specific task across all agents."""
    task_metrics = []
    for agent_id, tasks in metrics_data.items():
        if task_id in tasks:
            task_metrics.extend([(agent_id, entry) for entry in tasks[task_id]])
    return task_metrics