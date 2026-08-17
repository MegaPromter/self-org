#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Хук-блокировка правок кода до согласования (PreToolUse на Edit|Write).

Пока заметка активной задачи не согласована, файлы кода не правятся.
Всегда разрешены: docs/ (включая vault), .claude/ (табло, хуки,
навыки) и всё вне проекта (память ИИ, черновики-scratchpad).

Пустое табло — напоминание, не блок (косметика и опечатки по уставу
процедуры не требуют; на выходе их страхует контролёр коммита).

Выходы: 0 — пропустить; 2 — заблокировать (объяснение в stderr).
"""
import json
import os
import sys
from pathlib import Path

# Все потоки — в UTF-8 (stdin обязательно: харнесс шлёт JSON в UTF-8).
for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

КОРЕНЬ = Path(
    os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]
)
ТАБЛО = КОРЕНЬ / ".claude" / "task-state.json"
ФАЗЫ_ПОСЛЕ_СОГЛАСОВАНИЯ = ("согласовано", "код", "тесты", "коммит")


def main():
    try:
        данные = json.load(sys.stdin)
    except Exception:
        return 0
    путь = (данные.get("tool_input") or {}).get("file_path") or ""
    if not путь:
        return 0
    try:
        отн = Path(путь).resolve().relative_to(КОРЕНЬ.resolve())
    except ValueError:
        return 0  # вне проекта: память ИИ, черновики
    отн_с = str(отн).replace("\\", "/").lower()
    if отн_с.startswith("docs/") or отн_с.startswith(".claude/"):
        return 0

    табло = {}
    if ТАБЛО.exists():
        try:
            табло = json.loads(ТАБЛО.read_text(encoding="utf-8"))
        except Exception:
            табло = {}
    if not табло.get("задача"):
        print(
            "Напоминание: активной задачи в табло нет. Если это "
            "задача по коду — начни с процедуры /reglament "
            "(заметка до кода). Косметика и опечатки — можно без "
            "процедуры."
        )
        return 0

    фаза = табло.get("фаза", "")
    форма = табло.get("форма", "полная")
    после = фаза in ФАЗЫ_ПОСЛЕ_СОГЛАСОВАНИЯ
    if после and (форма == "малая" or табло.get("одобрено") is True):
        return 0

    if форма == "малая":
        print(
            f"Правка кода заблокирована: задача «{табло['задача']}» "
            f"в фазе «{фаза}» — согласование малой формы ещё не "
            f"получено. Дождись «да» пользователя и переведи фазу "
            f"в «согласовано» в табло.",
            file=sys.stderr,
        )
    else:
        print(
            f"Правка кода заблокирована: задача «{табло['задача']}» "
            f"в фазе «{фаза}», штампа одобрения нет. Покажи "
            f"пользователю команду штампа (кнопкой Run):\n"
            f"  python .claude/hooks/approve_task.py\n"
            f"и продолжай только после отметки «одобрено» в табло.",
            file=sys.stderr,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
