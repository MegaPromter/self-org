#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автозакрытие задачи — бухгалтерия шага закрытия одним вызовом.

Полная форма:  дописывает в заметку хэш коммита, ставит статус
«сделано», обновляет дату — и коммитит.
Малая форма:   вставляет строку в журнал мелких правок — и коммитит.
Перед коммитом гоняет валидатор vault; после — очищает табло.

Запуск (полная):  python .claude/hooks/close_task.py [--hash abc1234]
Запуск (малая):   python .claude/hooks/close_task.py --desc "что сделано" \
                  --files "какие файлы" [--hash abc1234]
Без --hash берётся последний коммит (HEAD).
"""
import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

# Консоль Windows по умолчанию не UTF-8 — переключаем потоки явно.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

КОРЕНЬ = Path(__file__).resolve().parents[2]
ТАБЛО = КОРЕНЬ / ".claude" / "task-state.json"
ЖУРНАЛ = КОРЕНЬ / "docs" / "vault" / "задачи" / "Мелкие правки (журнал).md"


def гит(*аргументы, проверять=True):
    р = subprocess.run(
        ["git", "-C", str(КОРЕНЬ)] + list(аргументы),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if проверять and р.returncode != 0:
        raise RuntimeError(f"git {' '.join(аргументы)}:\n" f"{р.stdout}{р.stderr}")
    return р


def main():
    п = argparse.ArgumentParser()
    п.add_argument("--hash", help="хэш коммита (по умолчанию HEAD)")
    п.add_argument("--desc", help="малая форма: что сделано")
    п.add_argument("--files", help="малая форма: какие файлы")
    арг = п.parse_args()

    табло = {}
    if ТАБЛО.exists():
        табло = json.loads(ТАБЛО.read_text(encoding="utf-8"))
    if not табло.get("задача"):
        print("Активной задачи в табло нет — закрывать нечего.")
        return 1

    хэш = арг.hash or гит("rev-parse", "--short", "HEAD").stdout.strip()
    сегодня = date.today().isoformat()
    имя = табло["задача"]
    форма = табло.get("форма", "полная")

    if форма == "малая":
        if not (арг.desc and арг.files):
            print("Для малой формы обязательны --desc и --files.")
            return 1
        текст = ЖУРНАЛ.read_text(encoding="utf-8")
        строки = текст.splitlines(keepends=True)
        for i, с in enumerate(строки):
            if с.startswith("|---"):
                строки.insert(
                    i + 1, f"| {сегодня} | {арг.desc} | {арг.files} | {хэш} |\n"
                )
                break
        else:
            print("В журнале не найдена шапка таблицы.")
            return 1
        ЖУРНАЛ.write_text("".join(строки), encoding="utf-8")
        изменён = ЖУРНАЛ
        сообщение = f"docs(vault): запись в журнал мелких правок ({хэш})"
    else:
        путь = КОРЕНЬ / табло["заметка"]
        if not путь.exists():
            print(f"Заметка не найдена: {путь}")
            return 1
        текст = путь.read_text(encoding="utf-8")
        for старый in ("статус: в работе", "статус: запланировано"):
            if старый in текст:
                текст = текст.replace(старый, "статус: сделано", 1)
                break
        текст = "\n".join(
            f"обновлено: {сегодня}" if с.startswith("обновлено:") else с
            for с in текст.splitlines()
        )
        if not текст.endswith("\n"):
            текст += "\n"
        текст += f"- {сегодня}: реализовано в коммите {хэш}.\n"
        путь.write_text(текст, encoding="utf-8")
        изменён = путь
        сообщение = (
            f"docs(vault): закрытие задачи «{имя}» — " f"хэш {хэш}, статус сделано"
        )

    # Валидатор — закрывать можно только чистый vault.
    в = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("validate_vault.py"))],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print(в.stdout, end="")
    if в.returncode != 0:
        print("Валидатор нашёл нарушения — закрывающий коммит не сделан.")
        return 1

    гит("add", str(изменён.relative_to(КОРЕНЬ)))
    к = гит("commit", "-m", сообщение, проверять=False)
    if к.returncode != 0:
        # pre-commit мог поправить файл (пробелы) — добавить и повторить.
        гит("add", str(изменён.relative_to(КОРЕНЬ)))
        к = гит("commit", "-m", сообщение)
    новый = гит("rev-parse", "--short", "HEAD").stdout.strip()

    ТАБЛО.write_text("{}\n", encoding="utf-8")
    print(
        f"Задача «{имя}» закрыта: хэш {хэш} записан, "
        f"закрывающий коммит {новый}, табло очищено."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
