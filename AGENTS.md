# Инструкция для агентов

Redroller развивается в multi-provider local control center. Перед изменениями
прочитай:

- `docs/ROADMAP.md`
- `docs/AGENT_MAP.md`
- `README.md`

`README.md` — пользовательская витрина проекта. Внутренние планы, agent notes,
координацию PR и архитектурную кухню держи в `docs/` и `AGENTS.md`, а не в
главном README.

## Текущая цель

Сохранить рабочий Yandex Cloud Redroller, выпустить первый понятный релиз и
постепенно превратить проект в:

- provider-neutral account center;
- общую parsing/run dashboard;
- provider adapters для Yandex Cloud, Selectel, Timeweb Cloud, Cloud.ru и других
  хостеров.

Текущий baseline:

- Yandex Cloud работает как эталонный provider;
- targets, isolation cloud-id/folder-id, Telegram и success behavior управляются
  через web-панель;
- по умолчанию live run останавливается после первого найденного IP;
- успешный cloud/folder всегда auto-protected.

## Правила работы

- PR должен быть сфокусирован.
- Не коммитить локальные секреты, runtime files, SQLite DB, логи, test output и
  реальные credentials.
- Не ломать текущий Yandex behavior без явной задачи.
- Добавлять или обновлять тесты для behavioral changes.
- Обновлять `docs/ROADMAP.md`, если меняется продуктовый scope.
- Обновлять `docs/AGENT_MAP.md`, если меняются границы модулей.
- В PR явно писать live-mode risk, если изменение может создавать, удалять,
  резервировать или ротировать cloud resources.
- Не смешивать пользовательскую документацию и внутреннюю координацию.
- Не добавлять скрытую телеметрию. Сбор успешных IP/usage stats/crash data
  допустим только как явный opt-in.

## Основная проверка

```powershell
python -m unittest discover -s .\bin -p "test_*.py" -v
```

Если системный Python недоступен, используй bundled Python runtime Codex и
укажи точную команду в финальном отчёте или PR.

Перед push/PR дополнительно:

```powershell
git status -sb
git diff --check
```

Проверь, что в diff нет ключей, локальных `config.json`, SQLite, `.web-runtime`,
логов, test-output и собранного мусора.

## Архитектурное направление

Не добавлять нового провайдера копипастой Yandex-панели. Сначала двигаться к
provider adapter contract:

- provider id и display name;
- credential/account validation;
- runtime config building;
- dry-run/live start and stop;
- status parsing;
- cleanup/isolation behavior;
- provider-specific tests.

Web panel должна dispatch-ить в provider adapters. Provider-specific код не
должен протекать в общие UI/API paths сильнее необходимого.
