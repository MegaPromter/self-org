#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Штамп одобрения задачи — запускает ТОЛЬКО пользователь.

Ставит в табло (.claude/task-state.json) отметку «одобрено» и фазу
«согласовано». Ассистенту запуск запрещён хуком check_commit.py.

Запуск: python .claude/hooks/approve_task.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

# Консоль Windows по умолчанию не UTF-8 — переключаем потоки явно.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ТАБЛО = Path(__file__).resolve().parents[1] / "task-state.json"


def main():
    табло = {}
    if ТАБЛО.exists():
        try:
            табло = json.loads(ТАБЛО.read_text(encoding="utf-8"))
        except Exception:
            табло = {}
    if not табло.get("задача"):
        print("Активной задачи в табло нет — одобрять нечего.")
        return 1
    сейчас = datetime.now()
    табло["одобрено"] = True
    табло["одобрено_в"] = сейчас.strftime("%Y-%m-%d %H:%M")
    табло["фаза"] = "согласовано"
    табло["обновлено"] = сейчас.strftime("%Y-%m-%d")
    ТАБЛО.write_text(
        json.dumps(табло, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"✅ Одобрено: «{табло['задача']}» "
        f"({табло['одобрено_в']}). Ассистент может приступать к коду."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
