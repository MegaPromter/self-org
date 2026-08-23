#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# reglament-plugin v1.4.3
"""Хук-контролёр коммита (PreToolUse на Bash).

Читает JSON вызова инструмента из stdin. Вызов коммита распознаётся
по токенам, а не по слову «commit» в строке: `pre-commit-tests.sh`
и ветка с таким именем за коммит не считаются. Коммит в ЧУЖОЙ
репозиторий (соседний проект) пропускается — там свой регламент.
Сообщение ищется и в команде, и в файле из `git commit -F путь`.
Пропускает всё, кроме:
  1. git commit без ссылки на документацию — блок. Разрешают коммит:
     - все файлы коммита внутри docs/vault/ (сам vault);
     - «vault: «Имя заметки»» в сообщении (заметка должна существовать);
     - пометка малой формы (строка ПОМЕТКА_ЖУРНАЛА);
     - метка-исключение из устава (МЕТКИ_ИСКЛЮЧЕНИЯ);
     - revert.
  2. коммита с файлами vault, если валидатор vault находит нарушения.

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


def есть_вызов_коммита(команда):
    """Настоящий вызов `git commit`, а не слово «commit» внутри имени
    файла (`pre-commit-tests.sh`), ветки или текста сообщения.

    Разбор по токенам: ищем токен «git», а следом — токен ровно
    «commit», пропуская между ними глобальные ключи git
    (`-C путь`, `-c ключ=значение`, `--git-dir=…`, `--no-pager`)."""
    for сегмент in re.split(r"&&|;|\|\||\|", команда):
        try:
            токены = shlex.split(сегмент)
        except ValueError:
            # Незакрытая кавычка — например, тело heredoc с сообщением.
            токены = сегмент.split()
        for i, т in enumerate(токены):
            if т.rsplit("/", 1)[-1] != "git":
                continue
            j = i + 1
            while j < len(токены):
                if токены[j] in ("-C", "-c", "--git-dir", "--work-tree"):
                    j += 2  # ключ, значение которого — отдельный токен
                elif токены[j].startswith("-"):
                    j += 1
                else:
                    break
            if j < len(токены) and токены[j] == "commit":
                return True
    return False


def целевая_папка(данные, команда):
    """Папка, в которой на самом деле выполнится коммит: учитываем
    `cd путь` и ключ `git -C путь` в самой команде, иначе — папка
    сессии из входного JSON."""
    папка = Path(данные.get("cwd") or КОРЕНЬ)

    def разрешить(путь):
        п = Path(путь)
        return п if п.is_absolute() else папка / п

    for сегмент in re.split(r"&&|;|\|\||\|", команда):
        # posix=False — иначе shlex съедает обратные слэши, и путь
        # Windows (D:\Program\…) превращается в мусор; кавычки при
        # этом остаются в токене, снимаем их сами.
        try:
            токены = [т.strip("\"'") for т in shlex.split(сегмент, posix=False)]
        except ValueError:
            токены = [т.strip("\"'") for т in сегмент.split()]
        if not токены:
            continue
        if токены[0] == "cd" and len(токены) > 1 and not токены[1].startswith("-"):
            папка = разрешить(токены[1])
        for i, т in enumerate(токены):
            if (
                т.rsplit("/", 1)[-1] == "git"
                and i + 2 < len(токены)
                and токены[i + 1] == "-C"
            ):
                папка = разрешить(токены[i + 2])
    return папка


def верх_репозитория(папка):
    """Корень репозитория для папки; None — определить не удалось."""
    try:
        р = subprocess.run(
            ["git", "-C", str(папка), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return None
    if р.returncode != 0 or not р.stdout.strip():
        return None
    try:
        return Path(р.stdout.strip()).resolve()
    except OSError:
        return None


def текст_сообщения(команда, папка):
    """Команда плюс содержимое файлов сообщения (`git commit -F путь`).

    Сообщение коммита часто передают файлом — многострочный текст
    иначе не проходит через командную строку. Тогда в самой команде
    ссылки на заметку нет, и без чтения файла хук блокирует
    правильный коммит. `-F -` (сообщение идёт на stdin) читать не
    надо: его текст остаётся в команде, там проверки его и найдут.
    Разбираются только сегменты с настоящим `git commit` — чтобы
    ключ `-F` чужой команды (например `grep -F`) не принимался за
    файл сообщения."""
    куски = [команда]
    for сегмент in re.split(r"&&|;|\|\||\|", команда):
        if not есть_вызов_коммита(сегмент):
            continue
        # posix=False — чтобы уцелели пути Windows; кавычки снимаем сами.
        try:
            токены = [т.strip("\"'") for т in shlex.split(сегмент, posix=False)]
        except ValueError:
            токены = [т.strip("\"'") for т in сегмент.split()]
        пути = []
        for i, т in enumerate(токены):
            if т in ("-F", "--file") and i + 1 < len(токены):
                пути.append(токены[i + 1])
            elif т.startswith("--file="):
                # posix=False оставляет кавычки внутри токена.
                пути.append(т.split("=", 1)[1].strip("\"'"))
        for п in пути:
            if not п or п == "-":
                continue
            ф = Path(п)
            if not ф.is_absolute():
                ф = папка / ф
            try:
                куски.append(ф.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue  # файла нет — проверяем по тому, что видно
    return "\n".join(куски)


def main():
    try:
        данные = json.load(sys.stdin)
    except Exception:
        return 0
    команда = (данные.get("tool_input") or {}).get("command") or ""
    if not команда:
        return 0

    if not есть_вызов_коммита(команда):
        return 0

    # Регламент этого проекта — про ЭТОТ репозиторий. Коммит в чужой
    # (соседний проект, репозиторий плагина) пропускаем: его
    # документация тут ни при чём, а у него свои хуки. Неопределённость
    # — в пользу проверки: лучше лишний раз спросить ссылку, чем
    # пропустить коммит этого проекта мимо регламента.
    папка = целевая_папка(данные, команда)
    свой = верх_репозитория(КОРЕНЬ)
    цель = верх_репозитория(папка)
    if свой and цель and цель != свой:
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

    сообщение = текст_сообщения(команда, папка)
    ниже = сообщение.lower()
    if ПОМЕТКА_ЖУРНАЛА in ниже:
        return 0
    if any(метка in ниже for метка in МЕТКИ_ИСКЛЮЧЕНИЯ):
        return 0
    # Ссылка на заметку: vault: «Имя» — кавычки любого вида.
    м = re.search(r"vault:\s*[«\"„']([^»\"“']+)[»\"“']", сообщение)
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
        "  - метку-исключение из устава: "
        + " / ".join(МЕТКИ_ИСКЛЮЧЕНИЯ)
        + ".",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
