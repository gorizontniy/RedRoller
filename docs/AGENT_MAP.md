# Карта агентов Redroller

Этот файл — общая карта для людей и coding agents. Его нужно читать перед
крупными изменениями и обновлять, когда меняются границы модулей.

## Где правда

- Направление продукта: `docs/ROADMAP.md`
- Карта модулей и зон ответственности: `docs/AGENT_MAP.md`
- Правила для агентов: `AGENTS.md`
- Пользовательское описание: `README.md`
- Реализация: `bin/`

## Текущая карта модулей

| Зона | Файлы | Ответственность | Привязка к провайдеру |
|---|---|---|---|
| Движок ротации | `bin/yc_ip_hunter.py` | Yandex Cloud API, IP allocation, cleanup, isolation, state/log. | Yandex-specific baseline. |
| Web backend | `bin/web_panel.py` | Local HTTP API, SQLite, encrypted secrets, lifecycle аккаунтов и запусков. | Сейчас в основном Yandex-specific schema. |
| Web frontend | `bin/web/index.html`, `bin/web/app.css`, `bin/web/app.js` | Operator UI, account form, run dashboard, Telegram, isolation. | Yandex-first UI, уже появляется roll-mode abstraction. |
| Telegram | `bin/telegram_bot.py` | Telegram-control surface и live monitoring. | Сейчас запускает Yandex script runner. |
| Launcher/release | `bin/web_panel_launcher.py`, `bin/build_web_panel_exe.ps1`, `Redroller.exe` | Desktop-запуск, app-window, Windows package. | Provider-neutral. |
| Config examples | `bin/config.example.json`, `bin/telegram_bot_config.example.json` | Шаблоны для advanced/CLI режима и тестов. | Yandex-specific. |
| Tests | `bin/test_*.py` | Safety net для движка, панели, лаунчера, Telegram. | Yandex плюс web-panel contracts. |

## Целевая архитектура

```text
UI: account center / parser dashboard
  -> local HTTP API
    -> provider registry
      -> provider adapter
        -> provider API client/parser
    -> encrypted local account store
    -> shared run/state/log store
```

Yandex Cloud — первая реализация provider adapter и эталон поведения. Это не
повод копировать Yandex-код под каждого следующего хостера.

## Что должен уметь provider adapter

Каждый provider adapter должен владеть:

- валидацией credentials;
- provider-specific account fields;
- безопасной сборкой runtime config;
- dry-run/demo behavior;
- live start/stop behavior;
- чтением статуса;
- cleanup/isolation behavior;
- provider-specific tests.

Panel-level код должен владеть:

- HTTP routing;
- SQLite connection и migrations;
- primitives шифрования;
- общими account/run records;
- provider registry dispatch;
- frontend API shape;
- release/runtime paths.

## Рабочие роли

### Product/UX agent

Зона:

- account center layout;
- provider cards;
- parser dashboard;
- operator copy;
- live-mode confirmations;
- визуальная целостность Redroller.

Не менять provider API behavior без синхронизации с backend agent.

### Backend/API agent

Зона:

- `bin/web_panel.py`;
- SQLite migrations;
- provider registry;
- HTTP API contracts;
- encrypted secret handling.

Обязан держать тесты миграции для существующих Yandex-данных.

### Provider agent

Зона:

- provider adapter implementation;
- provider API client/parser;
- provider-specific config schema;
- dry-run/live safety.

Первый provider task: вынести Yandex Cloud за provider-контракт до добавления
второго хостера.

### Release agent

Зона:

- `bin/web_panel_launcher.py`;
- `bin/build_web_panel_exe.ps1`;
- runtime directory behavior;
- release notes;
- `.exe` packaging.

Обязан проверять, что runtime data не попадает в commit.

### Test/Review agent

Зона:

- regression tests для migrations, API contracts, live/dry-run separation,
  secret storage и run lifecycle;
- PR checklist;
- verification notes.

## PR-flow

Ветки короткие, PR маленькие.

Примеры имён веток:

- `feature/provider-account-center`;
- `feature/yandex-provider-adapter`;
- `feature/selectel-skeleton`;
- `fix/runtime-migration`;
- `docs/roadmap-agent-map`.

PR должен отвечать на вопросы:

- что изменилось;
- зачем изменилось;
- какие файлы/зоны затронуты;
- какой тест запускался и какой результат;
- есть ли live-mode risk;
- нужны ли screenshots;
- есть ли SQLite/runtime migration notes.

Review expectations:

- один approve перед merge;
- никаких секретов, локальных DB, логов, runtime files;
- не смешивать unrelated refactors с provider work;
- roadmap обновлён, если поменялся scope;
- agent map обновлена, если поменялись границы модулей.

## Safety rules

- Не коммитить `config.json`, runtime DBs, logs, keys, `.web-runtime`,
  `.test-tmp` и реальные credentials.
- Live cloud operations должны быть явно подписаны в UI и покрыты тестами.
- Новые providers сначала получают dry-run/demo paths.
- Protected cloud/account resources нельзя удалять или ротировать неявно.
- Пока выносим abstraction, Yandex baseline должен оставаться рабочим.

## Ближайшие задачи для агентов

1. Спроектировать migration для provider/account data model.
2. Добавить provider cards в account center без поломки Yandex.
3. Выделить provider registry в `bin/web_panel.py`.
4. Вынести Yandex runtime config generation в Yandex adapter.
5. Добавить skeleton второго provider без live API calls.
