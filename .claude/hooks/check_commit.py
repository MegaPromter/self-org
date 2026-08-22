#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Хук-контролёр коммита (PreToolUse на Bash).

Читает JSON вызова инструмента из stdin. Пропускает всё, кроме:
  1. запуска штампа одобрения (approve_task) в любом виде, кроме
     отдельной команды «интерпретатор + скрипт»: именно такую
     отправляет кнопка Run у пользователя, а спрятать штамп
     внутри цепочки команд нельзя. Кто нажал — пишет сам скрипт
     в табло («способ»);
  2. git commit без ссылки на документацию — блок. Разрешают коммит:
     - все файлы коммита внутри docs/vault/ (сам vault);
     - «vault: «Имя заметки»» в сообщении (заметка должна существовать);
     - пометка малой формы (строка ПОМЕТКА_ЖУРНАЛА);
     - метка-исключение из регламента (МЕТКИ_ИСКЛЮЧЕНИЯ);
     - revert.
  3. коммита с файлами vault, если валидатор vault находит нарушения.

Выходы: 0 — пропустить; 2 — заблокировать (объяснение в stderr).
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Все потоки — в UTF-8 (stdin обязательно: харнесс шлёт JSON в UTF-8).
for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

КОРЕНЬ = Path(
    os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]
)
VAULT = КОРЕНЬ / "docs" / "vault"
МЕТКИ_ИСКЛЮЧЕНИЯ = ("косметика", "линтер", "опечатка", "откат")
ПОМЕТКА_ЖУРНАЛА = "журнал мелких правок"


ШТАМП = "approve_task.py"
ШТАМП_ОТНОСИТЕЛЬНО = ".claude/hooks/" + ШТАМП
_ИНТЕРПРЕТАТОРЫ = ("python", "py", "bash", "sh")


def _имя(токен):
    """Имя файла из пути: «.venv/Scripts/python.exe» → «python.exe»."""
    return токен.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _токены(сегмент):
    try:
        return shlex.split(сегмент.strip())
    except ValueError:
        return сегмент.strip().split()


def _запускает_штамп(сегмент):
    """Сегмент действительно запускает скрипт штампа?

    Упоминание имени в тексте (grep, git add, чтение через stdin)
    запуском не считается — иначе хук ловит разговоры о скрипте,
    а не его вызов.
    """
    токены = _токены(сегмент)
    if not токены:
        return False
    if _имя(токены[0]) == ШТАМП:
        return True
    if not _имя(токены[0]).startswith(_ИНТЕРПРЕТАТОРЫ):
        return False
    for токен in токены[1:6]:
        if токен == "-" or токен in ("-c", "-m"):
            return False  # код из stdin, строки или модуля — не наш файл
        # Ключи интерпретатора и их значения (-X utf8) пропускаем:
        # запуск скрипта узнаём по самому пути с «.py».
        if токен.lower().endswith(".py"):
            return _имя(токен) == ШТАМП
    return False


def _канонический(сегмент):
    """Скрипт — последнее слово команды: ни аргументов, ни хвостов.

    Ключи самого интерпретатора (-X utf8 и подобные) допустимы:
    они стоят до скрипта.
    """
    токены = _токены(сегмент)
    for номер, токен in enumerate(токены):
        if _имя(токен) == ШТАМП:
            return номер == len(токены) - 1
    return False


def файлы_коммита(команда):
    """Файлы будущего коммита: уже в индексе + аргументы git add
    из той же команды. Возвращает (список, точно_ли_известен)."""
    файлы = []
    известен = True
    р = subprocess.run(
        ["git", "-C", str(КОРЕНЬ), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if р.returncode == 0:
        файлы += [с for с in р.stdout.splitlines() if с.strip()]
    for сегмент in re.split(r"&&|;|\|\|", команда):
        м = re.search(r"\bgit\s+add\s+(.*)$", сегмент.strip())
        if not м:
            continue
        try:
            токены = shlex.split(м.group(1))
        except ValueError:
            известен = False
            continue
        for т in токены:
            if т in (".", "-A", "--all", "-u", "--update") or т.startswith("-"):
                известен = False
            else:
                файлы.append(т)
    return файлы, известен


def main():
    try:
        данные = json.load(sys.stdin)
    except Exception:
        return 0
    команда = (данные.get("tool_input") or {}).get("command") or ""
    if not команда:
        return 0

    # Штамп одобрения — только отдельной командой «интерпретатор +
    # скрипт», такую отправляет кнопка Run у пользователя. Всё
    # остальное с этим скриптом (довески, цепочки, вызовы изнутри
    # других команд) блокируем: штамп не должен проскочить
    # незаметно, спрятанный внутри чужой команды.
    сегменты = re.split(r"&&|;|\|\||\|", команда)
    запуски = [с for с in сегменты if _запускает_штамп(с)]
    if запуски:
        одиночный = len(сегменты) == 1 and _канонический(запуски[0])
        if not одиночный:
            print(
                "Штамп одобрения запускается только отдельной командой "
                f"«python {ШТАМП_ОТНОСИТЕЛЬНО}» — без довесков "
                "и не внутри другой команды. Покажи её пользователю "
                "и дождись отметки «одобрено» в табло "
                "(.claude/task-state.json).",
                file=sys.stderr,
            )
            return 2

    if not re.search(r"\bgit\b[^|;&]*\bcommit\b", команда):
        return 0
    if re.search(r"\bgit\s+revert\b", команда) or 'Revert "' in команда:
        return 0

    файлы, известен = файлы_коммита(команда)
    файлы = [ф.replace("\\", "/") for ф in файлы]
    только_vault = (
        известен and файлы and all(ф.startswith("docs/vault/") for ф in файлы)
    )
    есть_vault = any(ф.startswith("docs/vault/") for ф in файлы)

    # Валидатор vault — на любой коммит, задевающий docs/vault/.
    if есть_vault:
        в = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("validate_vault.py"))],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if в.returncode != 0:
            print(
                "Валидатор vault нашёл нарушения — коммит "
                "заблокирован:\n" + (в.stdout or "") + (в.stderr or ""),
                file=sys.stderr,
            )
            return 2

    if только_vault:
        return 0

    ниже = команда.lower()
    if ПОМЕТКА_ЖУРНАЛА in ниже:
        return 0
    if any(метка in ниже for метка in МЕТКИ_ИСКЛЮЧЕНИЯ):
        return 0
    # Ссылка на заметку: vault: «Имя» — кавычки любого вида.
    м = re.search(r"vault:\s*[«\"„']([^»\"“']+)[»\"“']", команда)
    if м:
        имя = м.group(1).strip()
        if any(ф.stem == имя for ф in VAULT.rglob("*.md")):
            return 0
        print(
            f"В сообщении названа заметка «{имя}», но файла "
            f"«{имя}.md» в docs/vault/ нет. Проверь имя заметки.",
            file=sys.stderr,
        )
        return 2

    print(
        "Коммит заблокирован (правило регламента): в сообщении "
        "нет ссылки на документацию. Добавь одно из:\n"
        "  - vault: «Имя заметки» (заметка должна существовать);\n"
        f"  - пометку «{ПОМЕТКА_ЖУРНАЛА}» (малая форма);\n"
        "  - метку-исключение из регламента: "
        + " / ".join(МЕТКИ_ИСКЛЮЧЕНИЯ)
        + ".",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
