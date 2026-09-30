# max-sport-bot — SPORTBOT / MAX

Бот для группы в MAX: утренние анонсы матчей, недельная афиша, результаты матчей (победа / ничья / поражение),
контроль изменений расписания и личные служебные уведомления владельцу при сбоях.

Работает постоянным процессом на Amvera (`python sport_bot.py`, данные в `/data`). Рабочий источник кода —
git-репозиторий Amvera (`master`); этот GitHub-репозиторий — резервная копия. Подробности, модель данных и порядок
деплоя — в `HANDOFF.md`.

## Тесты

```
pip install maxapi aiohttp      # на Windows ещё: pip install tzdata
python -m unittest discover -s tests -v
```
