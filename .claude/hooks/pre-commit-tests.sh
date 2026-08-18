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

# Команда тестов из устава (CLAUDE.md, «Запуск тестов»).
# Пока скелета проекта нет (нет manage.py / pyproject.toml),
# прогонять нечего — пропускаем, чтобы не блокировать
# документационные коммиты.
if [ -f "manage.py" ] || [ -f "pyproject.toml" ]; then
  python -m pytest -q 2>&1
  STATUS=$?
  if [ $STATUS -ne 0 ]; then
    echo "Тесты не прошли — коммит заблокирован" >&2
    exit 2
  fi
fi
exit 0
