"""Haiku/Sonnet モデル振り分け"""


def select_model(task_type: str = "default") -> str:
    """タスク種別に応じてモデルを選択する。

    重要な判断 → Sonnet、軽い応答 → Haiku
    """
    heavy_tasks = {"decision", "monthly", "weekly", "analysis"}
    if task_type in heavy_tasks:
        return "claude-sonnet-4-20250514"
    return "claude-haiku-4-5-20251001"
