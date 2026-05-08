<p align="center">
  <img src="assets/redroller-hero.jpg" alt="Redroller banner" width="100%">
</p>

<p align="center">
  <a href="https://github.com/stafilakok">
    <img alt="Developer: stafilakok" src="https://img.shields.io/badge/dev-stafilakok-af000c?style=for-the-badge&logo=github&logoColor=white">
  </a>
  <a href="https://github.com/gorizontniy">
    <img alt="Developer: gorizontniy" src="https://img.shields.io/badge/dev-gorizontniy-281715?style=for-the-badge&logo=github&logoColor=white">
  </a>
</p>

# 🔴 Redroller — народная рулетка Yandex Cloud

> **Локальное desktop-приложение для охоты за нужным публичным IPv4 в Yandex Cloud.**  
> Никакой ручной сборки JSON-конфигов для обычного пользователя: аккаунты, ключи, зоны, цели, Telegram и изоляция настраиваются прямо внутри приложения.

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.9%2B-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey">
  <img alt="App" src="https://img.shields.io/badge/app-Redroller-red">
  <img alt="Storage" src="https://img.shields.io/badge/storage-SQLite-green">
  <img alt="Secrets" src="https://img.shields.io/badge/secrets-encrypted-critical">
  <img alt="Status" src="https://img.shields.io/badge/status-Yandex%20Cloud%20baseline-orange">
</p>

---

## Поддержать проект

Если Redroller оказался полезен и хочется ускорить развитие проекта, можно поддержать авторов:

<p align="center">
  <a href="https://dalink.to/gorizontniy">
    <img alt="Поддержать через DaLink" src="https://img.shields.io/badge/support-DaLink-af000c?style=for-the-badge">
  </a>
  <a href="https://tonviewer.com/UQAG7KAzuYJDQ96JGYyN8wD5GOkq1sCRM787IAqOgSKPyL_z">
    <img alt="TON wallet" src="https://img.shields.io/badge/TON-wallet-0098EA?style=for-the-badge">
  </a>
</p>

DaLink: [dalink.to/gorizontniy](https://dalink.to/gorizontniy)

TON: `UQAG7KAzuYJDQ96JGYyN8wD5GOkq1sCRM787IAqOgSKPyL_z`

Спасибо за любую поддержку. Она помогает доводить Redroller до нормального релиза, тестировать live-сценарии и двигать проект к multi-provider control center.

---

## Благодарности

Отдельное спасибо Telegram-каналу [Whitelist RKN](https://t.me/whitelistRKN) за поддержку и внимание к проекту.

---

## ✦ Идея

**Redroller** превращает ручную охоту за нужным IPv4 в управляемое локальное приложение.

Оператору больше не нужно руками собирать `config.json`, таскать ключи по папкам и запускать длинные CLI-команды. Основной сценарий теперь такой:

```text
запустил Redroller.exe → добавил аккаунт → вставил ключ → выбрал зоны → нажал «КРУТИТЬ БСы»
```

Проще: партия сказала «нужен адрес» — машина открыла панель и пошла крутить.

Проект работает локально. Все аккаунты, ключи, база, runtime-файлы, state и логи остаются на машине пользователя.

---

## 🚀 Главный принцип

> **Всё, что нужно для обычного запуска, настраивается прямо в приложении.**

Через интерфейс Redroller можно настроить:

- 🧾 аккаунты Yandex Cloud;
- 🔐 JSON-ключ сервисного аккаунта;
- 🏢 `organization_id`;
- 💳 `billing_account_id`;
- ☁️ `service_cloud_id`;
- 📁 режим крутки: облака или один проект;
- 🌍 зоны ротации;
- 🎯 целевые `target_ips` и `target_cidrs`;
- 🛡️ изоляцию cloud-id и folder-id;
- 🤖 Telegram-уведомления;
- 🎰 запуск, остановку и наблюдение за рулеткой.

Файлы `config.example.json` и CLI-режим остаются для разработчиков, диагностики и ручного advanced-запуска. Для нормального пользователя точка входа — **Redroller.exe**.

---

## ⚙️ Что умеет

- 🎲 резервировать публичные IPv4 в Yandex Cloud;
- 🎯 проверять адреса по заданным IP/CIDR;
- 🧹 удалять неподходящие адреса и временные ресурсы;
- 🏗️ работать через cloud/folder/hybrid-ротацию;
- 🛡️ защищать выбранные cloud-id и folder-id через изоляцию;
- 🏆 автоматически изолировать cloud/folder после успешного IP;
- 🔐 шифровать JSON-ключи сервисных аккаунтов;
- 📊 показывать живой статус через локальную web-панель;
- 🤖 отправлять Telegram-уведомления;
- 🧾 вести `state.json`, `run.log` и историю попыток;
- 📦 собираться в Windows `.exe` и macOS `.dmg`;
- 🧪 проверяться unit-тестами.

---

## 🧱 Архитектура

```text
RedRoller/
├── README.md
├── Redroller.exe                         # скачивается из GitHub Releases
├── .gitignore
└── bin/
    ├── yc_ip_hunter.py                   # движок ротации IPv4
    ├── web_panel.py                      # локальная web-панель (REST API + SQLite)
    ├── web_panel_launcher.py             # desktop-лаунчер приложения
    ├── telegram_bot.py                   # Telegram-оболочка для удалённого управления
    ├── config.example.json               # шаблон для advanced/CLI-режима
    ├── telegram_bot_config.example.json
    ├── requirements.txt                  # PyJWT, cryptography
    ├── build_web_panel_exe.ps1           # сборка в .exe через PyInstaller
    ├── test_web_panel.py                 # тесты web-панели и миграций
    ├── test_web_panel_launcher.py        # тесты лаунчера
    ├── test_yc_ip_hunter.py              # тесты движка
    ├── test_telegram_bot.py              # тесты Telegram-бота
    └── web/
        ├── index.html
        ├── app.css
        └── app.js
```

---

## 📋 Требования и запасной запуск

### Готовый `.exe`

Готовый `Redroller.exe` со страницы [**Releases**](https://github.com/gorizontniy/RedRoller/releases) должен запускаться без установленного Python, pip, PyInstaller и Yandex Cloud CLI: веб-панель, движок ротации, `PyJWT` и `cryptography` упакованы внутрь приложения.

Для обычного запуска нужно подготовить только окружение:

| Что | Зачем | Ссылка |
|---|---|---|
| Windows 10/11 | основная поддерживаемая платформа | [Microsoft Windows](https://www.microsoft.com/windows) |
| Microsoft Edge или Google Chrome | Redroller открывает локальную панель в app-окне браузера; если Edge/Chrome не найден, откроется браузер по умолчанию | [Edge](https://www.microsoft.com/edge/download), [Chrome](https://www.google.com/chrome/) |
| Yandex Cloud аккаунт с платёжным аккаунтом | Redroller создаёт временные cloud/folder и резервирует публичные IPv4 | [Yandex Cloud Console](https://console.yandex.cloud/) |
| JSON-ключ сервисного аккаунта | по нему Redroller получает IAM token и работает с Yandex Cloud API | [документация по authorized keys](https://yandex.cloud/en/docs/iam/operations/authentication/manage-authorized-keys) |
| Права сервисного аккаунта | нужны для cloud/folder, billing binding, VPC и reserved address операций | [справочник ролей Yandex Cloud](https://yandex.cloud/en/docs/iam/roles-reference) |
| Telegram bot token, опционально | только если нужны уведомления в Telegram | [BotFather](https://t.me/BotFather) |

Yandex Cloud CLI для обычной работы не требуется: приложение ходит в API напрямую.

Если `.exe` не стартует, проверьте, что Windows не заблокировал скачанный файл: **Свойства файла → Разблокировать**. Логи запуска лежат в:

```text
%LOCALAPPDATA%\Redroller\.web-runtime
```

### Запасной способ: запуск из GitHub

Если готовый `.exe` не работает на конкретной машине, можно запустить Redroller из исходников.

**1. Установить инструменты**

| Что | Зачем | Ссылка |
|---|---|---|
| Python 3.9+ | запуск web-панели, движка и тестов | [Python for Windows](https://www.python.org/downloads/windows/) |
| Git for Windows | клонирование репозитория и обновления | [git-scm.com/download/win](https://git-scm.com/download/win) |
| PowerShell | запуск команд и сборочного скрипта | [документация PowerShell](https://learn.microsoft.com/powershell/) |

**2. Скачать проект**

```powershell
git clone https://github.com/gorizontniy/RedRoller.git
cd RedRoller
```

**3. Поставить Python-зависимости**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\bin\requirements.txt
```

Если PowerShell блокирует активацию `.venv`, для текущего окна можно выполнить:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Сейчас в `requirements.txt` нужны:

```text
PyJWT
cryptography
```

**4. Запустить приложение**

```powershell
python .\bin\web_panel_launcher.py
```

Запасной запуск без desktop-окна:

```powershell
python .\bin\web_panel.py --host 127.0.0.1 --port 8787
```

После этого откройте `http://127.0.0.1:8787`.

### Скрипты и сборка

| Скрипт | Для чего |
|---|---|
| `.\bin\web_panel.py` | локальная web-панель |
| `.\bin\web_panel_launcher.py` | desktop-лаунчер панели |
| `.\bin\yc_ip_hunter.py` | CLI-движок ротации IPv4 |
| `.\bin\telegram_bot.py` | отдельный Telegram-control bot |
| `.\bin\build_web_panel_exe.ps1` | сборка `Redroller.exe` через PyInstaller |
| `./bin/build_web_panel_dmg.sh` | сборка `Redroller.app` и `Redroller-macOS.dmg` через PyInstaller и `hdiutil` |

Для сборки `.exe` из исходников дополнительно нужен PyInstaller:

```powershell
python -m pip install pyinstaller
.\bin\build_web_panel_exe.ps1
```

Для сборки `.dmg` на macOS:

```bash
python3 -m pip install -r bin/requirements.txt
./bin/build_web_panel_dmg.sh
```

Если зависимости ставятся в виртуальное окружение, можно явно указать Python:

```bash
PYTHON_BIN=.venv/bin/python ./bin/build_web_panel_dmg.sh
```

---

## 🚀 Быстрый старт

### Способ 1. Готовый `.exe` (для обычного пользователя)

Скачайте последний `Redroller.exe` со страницы [**Releases**](https://github.com/gorizontniy/RedRoller/releases) и запустите:

```powershell
.\Redroller.exe
```

Откроется локальная панель:

```text
http://127.0.0.1:8787
```

### Способ 2. Запуск из исходников (для разработчика)

**1. Клонировать репозиторий**

```powershell
git clone https://github.com/gorizontniy/RedRoller.git
cd RedRoller
```

**2. Установить зависимости**

```powershell
python -m pip install -r bin\requirements.txt
```

**3. Запустить веб-панель**

```powershell
python bin\web_panel_launcher.py
```

Или напрямую без desktop-окна:

```powershell
python bin\web_panel.py
```

Панель откроется по адресу `http://127.0.0.1:8787`.

### Добавить аккаунт в интерфейсе

В приложении открыть вкладку **Аккаунты** и заполнить поля:

| Поле | Что вставить |
|---|---|
| `name` | любое понятное имя аккаунта |
| `organization_id` | ID организации Yandex Cloud |
| `billing_account_id` | ID платёжного аккаунта |
| `service_cloud_id` | ID служебного облака |
| JSON-ключ | весь скачанный файл авторизованного ключа сервисного аккаунта |
| зоны | зоны, где будет идти ротация |
| после успеха | остановиться после первого IP или продолжать собирать несколько IP |
| `target_cidrs` / `target_ips` | нужные диапазоны или конкретные адреса |

### Нажать кнопку запуска

```text
КРУТИТЬ БСы
```

После этого Redroller сам создаёт runtime-конфиг, кладёт его в локальную runtime-папку, запускает движок и показывает живой статус в интерфейсе.

---

## 🖥️ Web-панель

Панель — основной пользовательский слой Redroller.

Через неё выполняется вся настройка:

- создание и редактирование аккаунтов;
- выбор активного аккаунта;
- загрузка JSON-ключа сервисного аккаунта;
- выбор режима крутки;
- выбор зон ролла;
- настройка целевых IP/CIDR;
- включение Telegram;
- запуск и остановка процесса;
- просмотр текущего IP;
- просмотр подробного лога;
- просмотр истории попыток;
- управление изоляцией cloud-id и folder-id.

Данные панели хранятся локально в SQLite.

При запуске через `.exe` runtime лежит здесь:

```text
%LOCALAPPDATA%\Redroller\.web-runtime
```

Там находятся:

```text
ip_rotator.sqlite3
secret.key
accounts/
runner logs
runtime config.json
state.json
run.log
browser-profile/
```

> ⚠️ Если удалить `secret.key`, ранее сохранённые JSON-ключи сервисных аккаунтов нельзя будет расшифровать.

---

## 🔐 Работа с ключами

В Redroller нужен **авторизованный ключ сервисного аккаунта в формате JSON**.

В Yandex Cloud путь такой:

```text
Identity and Access Management
→ Сервисные аккаунты
→ нужный сервисный аккаунт
→ Создать ключ
→ Создать авторизованный ключ
→ Скачать файл с ключами
```

После создания Yandex Cloud показывает два текстовых блока:

- **Ваш открытый ключ** — публичная часть, отдельно в Redroller не вставляется;
- **Ваш закрытый ключ** — приватная часть, отдельно в Redroller тоже не вставляется.

Нужно нажать **Скачать файл с ключами**, открыть скачанный `.json` и вставить в поле **JSON-ключ сервисного аккаунта** весь файл целиком. Внутри JSON будут служебные поля вроде `id`, `service_account_id` и `private_key`.

Такой файл содержит приватную часть ключа, поэтому обращайтесь с ним как с паролем: не отправляйте его в чат, не коммитьте в GitHub и не храните в общих папках.

Redroller:

- проверяет, что ключ похож на ключ сервисного аккаунта;
- шифрует его перед сохранением в SQLite;
- не показывает ключ обратно через API;
- создаёт временный `sa-key.json` только в runtime-директории конкретного аккаунта;
- использует этот ключ при запуске `yc_ip_hunter.py`.

Оператор не должен вручную раскладывать ключи по проекту для обычного запуска.

---

## 🎰 Режимы крутки

### ☁️ Гибридная крутка

Основной режим.

Redroller создаёт disposable cloud/folder, привязывает billing, выдаёт права сервисному аккаунту, резервирует адреса, проверяет попадание и удаляет промахи.

Подходит для массовой охоты по диапазонам.

В приложении для этого выбирается режим:

```text
Гибридная крутка
```

`target_cloud_id` и `folder_id` в этом режиме очищаются автоматически.

### 📁 Крутка 1 проекта

Режим для работы внутри конкретного существующего cloud/folder.

В приложении выбирается режим:

```text
Крутка 1 проекта
```

Нужно указать:

- `target_cloud_id`;
- `folder_id`.

Подходит, если ресурсы должны создаваться только внутри заранее выбранного проекта.

---

## 🎯 Целевые диапазоны

Цели задаются прямо в приложении в полях `target_ips` и `target_cidrs`.
Это белый список: адрес считается успешным, если он совпал с конкретным IP или попал в один из CIDR.

Список можно менять двумя способами:

- при создании или редактировании аккаунта;
- во вкладке **Цели**, где можно добавить или удалить IP/CIDR без изменения ключа, зон и Yandex Cloud-настроек.

Пустой список целей не сохраняется: нужен хотя бы один IP или CIDR.

Пример диапазонов:

```json
[
  "84.201.188.0/23",
  "84.201.184.0/22",
  "84.201.128.0/18",
  "158.160.0.0/16"
]
```

Если выпавший адрес попадает в один из диапазонов, он считается успешным.

---

## 🛡️ Изоляция

Вкладка **Изоляция** защищает выбранные `cloud-id` и `folder-id` от удаления и участия в обычной охоте.

Правила:

- изоляция сохраняется отдельной кнопкой;
- список хранится отдельно от общего редактирования аккаунта;
- пустые строки удаляются;
- дубли убираются;
- некорректные cloud-id и folder-id отклоняются;
- обновление происходит атомарно;
- обычное сохранение аккаунта не затирает изоляцию.

Это нужно, чтобы найденные или важные cloud/folder с рабочими машинами не были случайно уничтожены или переиспользованы роллером.

---

## 🏆 Поведение после успеха

В аккаунте есть настройка **После успеха**:

- **Остановиться после первого найденного IP** — безопасный режим для первого запуска и обычного пользователя.
- **Продолжать и собирать несколько IP** — режим сбора: Redroller сохраняет успех, изолирует cloud/folder и идёт дальше за следующим адресом.

Когда найден целевой IP:

1. результат сохраняется;
2. address остаётся зарезервированным;
3. Telegram отправляет уведомление, если включён;
4. cloud и folder автоматически добавляются в изоляцию аккаунта;
5. если выбран безопасный режим, процесс останавливается после первого успеха;
6. если включён режим продолжения, Redroller идёт дальше за следующим адресом.

В таблице **История IP** повторные адреса помечаются бейджем `ПОВТОР`. Первое появление адреса показывается без отдельной отметки. Успешный статус и кнопка `YC` открывают каталог результата в Yandex Cloud Console.

Если после ручной проверки успешный IP оказался неподходящим, его можно убрать из этой же таблицы:

1. остановите активную ротацию, если она ещё идёт;
2. найдите успешную строку в **История IP**;
3. нажмите **Удалить IP** в колонке **Действие**;
4. подтвердите удаление.

Redroller удалит зарезервированный address и снимет его `cloud-id`/`folder-id` с изоляции аккаунта. Если результат был в disposable-каталоге гибридной крутки, каталог будет отправлен на удаление. После этого Redroller проверит disposable-cloud: если cloud управляется Redroller и в нём не осталось активных каталогов, cloud тоже будет отправлено на удаление. Если Yandex Cloud ещё держит каталог в статусе удаления, в истории появится кнопка **Удалить cloud** — её можно нажать позже для повторной попытки.

Если это выбранный каталог режима **Крутка 1 проекта**, каталог и cloud останутся на месте.

Обычные промахи и временные cloud/folder Redroller чистит сам во время ротации. Ручная кнопка нужна именно для случая, когда IP сначала считался успешным и был защищён, а после ручной проверки оказался неподходящим.

---

## 🤖 Telegram-уведомления

Telegram настраивается прямо в приложении во вкладке **Telegram**.

Можно сохранить:

- `chat_id`;
- bot token;
- статус включения уведомлений.

Token шифруется тем же локальным ключом и не отдаётся обратно через API.

Через интерфейс также можно отправить тестовое сообщение.

---

## 🧾 Advanced: CLI-режим

CLI оставлен для диагностики, тестирования и ручного запуска.

Обычному пользователю он не нужен: приложение само генерирует runtime-конфиг и запускает движок.

Dry-run:

```powershell
python .\bin\yc_ip_hunter.py --config .\bin\config.json --dry-run
```

Боевой запуск:

```powershell
python .\bin\yc_ip_hunter.py --config .\bin\config.json --run --yes-delete-cloud
```

Флаг `--yes-delete-cloud` нужен намеренно. Без него удаление cloud не произойдёт даже при `allow_delete_cloud: true`.

Это защита от быстрых пальцев и медленного сожаления.

---

## 🤖 Advanced: Telegram-бот

Telegram-бот позволяет управлять ротацией удалённо: запускать/останавливать аккаунты, смотреть живой лог, запрашивать пересоздание cloud и экспортировать цели.

### Настройка

1. Скопировать шаблон:

```powershell
cd bin
copy telegram_bot_config.example.json telegram_bot_config.json
```

2. В `telegram_bot_config.json` указать `bot_token_env` или положить токен в переменную окружения `TELEGRAM_BOT_TOKEN`, а также добавить свой `chat_id` в `allowed_chat_ids`.

3. Запустить бота:

```powershell
python .\bin\telegram_bot.py --config .\bin\telegram_bot_config.json
```

Бот будет слушать Telegram-команды и управлять аккаунтами, указанными в `accounts`.

---

## 📦 Сборка Windows EXE

Для сборки нужен PyInstaller (скрипт ставит его сам, если не установлен):

```powershell
python -m pip install -r bin\requirements.txt
.\bin\build_web_panel_exe.ps1
```

Сборка создаёт:

```text
Redroller.exe
dist/
└── release/
    ├── Redroller.exe
    └── README.txt
```

Release-папка нужна для чистой выдачи пользователю: только `.exe` и короткая инструкция, без технического мусора.

## 📦 Сборка macOS DMG

Собирать `.dmg` нужно на macOS той архитектуры, для которой нужен релиз:

```bash
python3 -m pip install -r bin/requirements.txt
./bin/build_web_panel_dmg.sh
```

Скрипт использует `python3` по умолчанию. Для venv-сборки:

```bash
PYTHON_BIN=.venv/bin/python ./bin/build_web_panel_dmg.sh
```

Сборка создаёт:

```text
dist/
├── Redroller-macOS.dmg
└── release-macos/
    ├── Redroller.app
    └── README-macOS.txt
```

Локальные данные macOS-сборки хранятся в:

```text
~/Library/Application Support/Redroller/.web-runtime
```

Без Apple Developer ID приложение будет неподписанным: при первом запуске может понадобиться `Control-click` → `Open`.

---

## 🧪 Тесты

```powershell
python -m pip install -r bin\requirements.txt
python -m unittest discover -s .\bin -p "test_*.py" -v
```

Покрываются:

- web-панель;
- миграции SQLite;
- шифрование ключей;
- Telegram-настройки;
- runtime-конфиги;
- изоляция cloud-id/folder-id;
- автоизоляция найденных cloud/folder;
- launcher-поведение;
- CLI-логика охотника.

---

## 🔐 Что нельзя коммитить

Локальные секреты и runtime-файлы не должны попадать в репозиторий:

```text
config.json
config.*.json
sa-key.json
*-key.json
*.pem
*.key
.env
.env.*
*.sqlite
*.sqlite3
state.json
run.log
*.log
.web-runtime/
.pyinstaller-build/
dist/
build/
```

Если JSON-ключ сервисного аккаунта попал в публичный репозиторий — ключ надо немедленно удалить в Yandex Cloud и выпустить новый.

---

## 🧭 Что нужно подготовить в Yandex Cloud

В приложении всё настраивается через форму, но сами значения нужно взять в Yandex Cloud:

| Значение | Где взять |
|---|---|
| `organization_id` | страница организации / Cloud Center |
| `billing_account_id` | раздел Billing |
| `service_cloud_id` | ID облака, где живёт сервисный аккаунт |
| JSON-ключ | IAM → сервисный аккаунт → создать авторизованный ключ → скачать файл с ключами → вставить весь JSON |
| `target_cidrs` / `target_ips` | нужные диапазоны или конкретные адреса |
| зоны | `ru-central1-a`, `ru-central1-e` и другие доступные зоны |

После этого всё вводится в Redroller через UI.

---

## 🧨 Что происходит при запуске из приложения

```text
Redroller.exe
   ↓
локальная web-панель
   ↓
SQLite + encrypted secrets
   ↓
runtime config для выбранного аккаунта
   ↓
yc_ip_hunter.py
   ↓
Yandex Cloud API
   ↓
адрес найден / промах удалён / cloud защищён
```

Пользователь работает с кнопками и формами. Внутренний JSON и runtime-файлы приложение создаёт само.

---

## ⚠️ Дисклеймер

Redroller — утилита для управления собственными облачными ресурсами и проверки выделяемых публичных IPv4.

За лимиты, биллинг, квоты, удаление cloud, расходы, права сервисного аккаунта и последствия нажатия кнопки отвечает оператор.

Средство производства дано. Ответственность тоже.
