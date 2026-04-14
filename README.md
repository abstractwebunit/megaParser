# megaParser

Multi-account Telegram parser. Kurigram userbots + PostgreSQL + aiogram control bot.

Собирает: участников групп, историю сообщений, поиск по keywords, realtime мониторинг.
Anti-ban: per-account rate limits, work/rest циклы, night pause, FloodWait handler.

## Быстрый старт

```bash
# 1. Установить зависимости
uv sync  # или: pip install -e .

# 2. Подготовить PostgreSQL
createdb megaparser

# 3. Заполнить .env
cp .env.example .env
python -m app.cli.manage gen-key  # сгенерировать FERNET_KEY, вставить в .env
# заполнить DATABASE_URL, TELEGRAM_BOT_TOKEN, ALLOWED_ADMIN_IDS

# 4. Накатить миграции
alembic upgrade head
# или: python -m app.cli.manage migrate

# 5. Импортировать прокси (формат: login:pass@ip:port)
python -m app.cli.manage import-proxies proxies.txt
python -m app.cli.manage test-proxies

# 6. Импортировать аккаунты (ZIP с .session + .json парами)
python -m app.cli.manage import-accounts /path/to/211.zip

# 7. Привязать прокси к аккаунтам (round-robin)
python -m app.cli.manage assign-proxies

# 8. Health-check — проверить что аккаунты коннектятся
python -m app.cli.manage health-check

# 9. Добавить seed группу
python -m app.cli.manage seed-group @durov

# 10. Запустить runner + бот
python -m app.main
# или: megaparser-run
```

## Структура

```
app/
├── main.py                entrypoint (runner + bot + monitor в одном loop)
├── settings.py            pydantic-settings (.env + config.yaml)
├── crypto.py              Fernet для session_string/2FA
├── db/
│   ├── base.py           engine, session_factory
│   ├── models.py         SQLAlchemy 2.0 модели
│   └── repo.py           repository функции
├── core/
│   ├── client_factory.py Kurigram Client из DB TelegramAccount
│   ├── session_loader.py детект Telethon/Pyrogram .session
│   ├── account_manager.py pool, ротация, ban tracking
│   ├── rate_limiter.py   per-account throttling (порт из tg-harvester)
│   └── errors.py         классификация Kurigram исключений
├── services/
│   ├── parser_messages.py  history, батчами, incremental resume
│   ├── parser_members.py   RECENT + alphabet-трюк для >10k
│   ├── discovery.py        seed + keyword + chain-walk
│   ├── monitor.py          realtime handlers с failover
│   └── runner.py           orchestrator: queue + workers + recovery
├── bot/
│   ├── bot.py             aiogram Bot + Dispatcher
│   └── handlers.py        admin-only команды
└── cli/
    ├── manage.py          CLI entrypoint (click)
    ├── importer.py        ZIP/dir import
    └── proxy_pool.py      proxies.txt parser + validator
```

## Команды бота (admin-only)

- `/start` — запустить runner
- `/stop` — приостановить (workers доживают текущее)
- `/shutdown` — полный graceful shutdown
- `/status` — сводка: runner state, аккаунты, очередь
- `/stats` — messages_24h / users / groups / pending_tasks
- `/accounts` — список аккаунтов с status + daily counters
- `/tasks` — последние 20 ParserTask
- `/seed @group` — добавить группу в очередь на скан
- `/find <keyword>` — создать discover-таску по keyword
- `/monitor add|remove @group` — включить/выключить realtime мониторинг
- `/health` — health endpoint для внешнего мониторинга

## Rate limit config (config.yaml)

Стартовые числа намеренно консервативные:

| Параметр | Значение |
|---|---|
| delay_between_groups | 8-15 сек |
| max_groups_per_day | 300 |
| max_groups_per_hour | 25 |
| account_work_minutes | 120-180 |
| account_rest_minutes | 30-60 |
| night_pause_utc | 2-7 |
| flood_long_threshold_seconds | 300 |
| max_concurrent_accounts | **3** (критично, пока все proxies на одном IP) |

Если за первую неделю 0 FloodWait — плавно поднимайте `max_concurrent_accounts` до 5-10.

## Критичные заметки безопасности

- **Все 49 прокси из примера на одном IP** (`185.252.215.173`). Это single exit для всех аккаунтов — Telegram коррелирует. Держите `max_concurrent_accounts=3` и параллельные операции минимум. Для прод — нужно 10+ разных IP.
- `session_string` и `twoFA` шифруются Fernet в БД. `FERNET_KEY` — в `.env`, не коммитить.
- `.env` в `.gitignore`. `sessions/*.session` тоже — это backup оригиналов.
- Каждый аккаунт приходит с уникальным `api_id/api_hash` и `device fingerprint` из JSON. Не трогаем.

## Backup

```bash
# pg_dump в dumps/megaparser_YYYYMMDD.sql.gz
python -m app.cli.manage backup

# cron (ежедневно в 03:17)
17 3 * * * cd /path/to/megaParser && python -m app.cli.manage backup
```

## FAQ

**Как проверить что аккаунт может search_global?** — `can_search=False` по умолчанию. Включить вручную: `UPDATE telegram_accounts SET can_search=true WHERE name='...'`.

**Что делать если аккаунт banned?** — Runner сам разбанит по истечении `ban_until` (recovery_loop раз в 5 мин). Вручную: `UPDATE account_states SET status='idle', ban_until=NULL WHERE account_id=...`.

**Как импортировать распакованные аккаунты?** — та же команда: `import-accounts /path/to/folder/` (автодетект директории).

**Как добавить аккаунт в monitor-пул?** — `UPDATE telegram_accounts SET role='monitor' WHERE name='...'`. Минимум 2 monitor-аккаунта для failover.

## Troubleshooting

- **`session format unknown`** — .session из нестандартного экспортёра. Либо сконвертировать через opentele вручную, либо залогиниться заново через Kurigram.
- **`FloodWait` на старте** — 100 клиентов одновременно. Снизить `max_concurrent_accounts`, батчи по 5 с задержкой уже заложены.
- **`ConnectionError`** — прокси мёртв. `test-proxies` → перепривязать.
- **PG pool exhaustion** — увеличить `pool_size` в `db/base.py` или уменьшить worker count.
