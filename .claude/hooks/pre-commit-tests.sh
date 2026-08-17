#!/bin/bash
# Запуск тестов перед коммитом — блокирует коммит при падении.
# Разбор JSON — на Python: jq может отсутствовать в системе, а с
# отсутствующим jq хук молча пропускал бы всё.
INPUT=$(cat)
TOOL_INPUT=$(printf '%s' "$INPUT" | python -c "
import json, sys
try:
    print(json.load(sys.stdin).get('tool_input', {}).get('command', ''))
except Exception:
    pass
" 2>/dev/null)
if ! echo "$TOOL_INPUT" | grep -q "git commit"; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# ЗАГЛУШКА. Стек не выбран, тестов пока нет — команда всегда проходит.
# ЗАМЕНИТЬ после выбора стека на реальную команду тестов
# (например: pytest -q). Тогда же вписать её в CLAUDE.md,
# секция «Запуск тестов».
true
STATUS=$?
if [ $STATUS -ne 0 ]; then
  echo "Тесты не прошли — коммит заблокирован" >&2
  exit 2
fi
exit 0
