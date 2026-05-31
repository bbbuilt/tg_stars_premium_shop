# Fragment Stars Demo Bot

Минимальный Telegram-бот для примера работы с Fragment API.

В репозитории используется SDK
[`bbbuilt/fragment-stars-api`](https://github.com/bbbuilt/fragment-stars-api).
Весь код вокруг Fragment специально оставлен тонким, чтобы было видно, как
подключить эту библиотеку в своем проекте.

Оставлено только самое нужное:

- запуск через Telegram polling;
- оплата в TON на ваш кошелек;
- один эквайринг: FreeKassa;
- покупка Telegram Stars через [`bbbuilt/fragment-stars-api`](https://github.com/bbbuilt/fragment-stars-api);
- SQLite для хранения заказов.

## Главное про KYC

Для стабильной работы ставьте:

```env
FRAGMENT_API_MODE=kyc
FRAGMENT_PAYMENT_METHOD=ton
FRAGMENT_COOKIES_BASE64=
```

`no_kyc` удобен для быстрого теста, но он менее стабильный: чаще упирается в ограничения, проверки и временные ошибки. В KYC режиме API использует вашу авторизованную Fragment-сессию, поэтому покупки обычно проходят надежнее.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните `.env`, затем запустите:

```bash
python bot.py
```

Webhook, nginx, домен и systemd для демо не нужны.

## Что заполнить в `.env`

Пример заполнения без реальных ключей:

```env
BOT_TOKEN=<bot_id>:<bot_secret_from_botfather>
ADMIN_USER_ID=123456789
SUPPORT_USERNAME=your_support_username

FRAGMENT_API_URL=https://fragment-api.ydns.eu:8443
FRAGMENT_API_MODE=kyc
FRAGMENT_PAYMENT_METHOD=ton
FRAGMENT_WALLET_MNEMONIC=word_01 word_02 ... word_24
FRAGMENT_COOKIES_BASE64=<base64_encoded_fragment_cookies_json>

TON_WALLET_ADDRESS=UQ...your_ton_wallet_address
TONCENTER_API_KEY=<toncenter_api_key_optional>

ENABLE_FREEKASSA=false
FREEKASSA_API_KEY=<freekassa_api_key_optional>
FREEKASSA_SHOP_ID=0
```

Обязательно:

- `BOT_TOKEN` - токен от `@BotFather`;
- `FRAGMENT_WALLET_MNEMONIC` - 24 слова TON-кошелька, с которого Fragment покупает Stars;
- `FRAGMENT_PAYMENT_METHOD` - `ton` или `usdt_ton`; по умолчанию `ton`;
- `TON_WALLET_ADDRESS` - адрес, куда пользователи отправляют TON.

Рекомендуется:

- `FRAGMENT_API_MODE=kyc`;
- `FRAGMENT_COOKIES_BASE64` - cookies Fragment в Base64;
- `TONCENTER_API_KEY` - ключ TonCenter для более стабильной проверки TON платежей.

Опционально для FreeKassa:

- `ENABLE_FREEKASSA=true`;
- `FREEKASSA_API_KEY`;
- `FREEKASSA_SHOP_ID`;
- `FREEKASSA_METHOD=44`.


## Готовые режимы Fragment API

Выбор режима задается двумя переменными:

- `FRAGMENT_API_MODE=kyc` или `no_kyc`;
- `FRAGMENT_PAYMENT_METHOD=ton` или `usdt_ton`.

### 1. KYC + TON

Самый безопасный вариант для старта: клиентские cookies Fragment, 0% комиссии API, оплата Fragment в TON.

```env
FRAGMENT_API_MODE=kyc
FRAGMENT_PAYMENT_METHOD=ton
FRAGMENT_COOKIES_BASE64=<base64_encoded_fragment_cookies_json>
```

### 2. KYC + USDT on TON

KYC режим с оплатой Fragment в USDT on TON. Комиссия API остается 0%.

```env
FRAGMENT_API_MODE=kyc
FRAGMENT_PAYMENT_METHOD=usdt_ton
FRAGMENT_COOKIES_BASE64=<base64_encoded_fragment_cookies_json>
```

Кошелек из `FRAGMENT_WALLET_MNEMONIC` должен иметь USDT on TON и немного TON на газ.

### 3. Non-KYC + TON

Без Fragment cookies. API использует owner-сессию и берет no-KYC комиссию.

```env
FRAGMENT_API_MODE=no_kyc
FRAGMENT_PAYMENT_METHOD=ton
FRAGMENT_COOKIES_BASE64=
```

### 4. Non-KYC + USDT on TON

Без Fragment cookies. Базовая цена Stars оплачивается в USDT on TON, комиссия API — в TON.

```env
FRAGMENT_API_MODE=no_kyc
FRAGMENT_PAYMENT_METHOD=usdt_ton
FRAGMENT_COOKIES_BASE64=
```

Важно: Non-KYC Premium с USDT сейчас не включен; для Premium используйте `FRAGMENT_PAYMENT_METHOD=ton`.

## Как получить cookies для KYC

1. Откройте [fragment.com](https://fragment.com).
2. Войдите в аккаунт.
3. Подключите тот же TON-кошелек, чьи 24 слова указаны в `FRAGMENT_WALLET_MNEMONIC`.
4. Откройте DevTools браузера.
5. Найдите cookies Fragment.
6. Соберите JSON с вашими значениями cookies:

```json
{"stel_token":"","stel_ton_token":"","stel_ssid":"","stel_both":""}
```

7. Закодируйте JSON в Base64 и вставьте в `FRAGMENT_COOKIES_BASE64`.

Важно: cookies должны быть сняты после подключения нужного кошелька. Если cookies от другой сессии или другого кошелька, Fragment может отклонять покупку.

## Как работает заказ

1. Пользователь нажимает `Купить Stars`.
2. Вводит username получателя.
3. Вводит количество Stars.
4. Бот считает примерную цену.
5. Пользователь выбирает TON или FreeKassa.
6. `PaymentMonitor` каждые 30 секунд проверяет оплату.
7. После оплаты бот вызывает `FragmentAPIService.buy_stars(...)`.
8. Заказ получает статус `completed` или `failed`.

## Структура проекта

```text
bot.py
handlers/
  start.py          # /start, меню, справка
  order.py          # весь flow покупки Stars
services/
  config.py         # чтение .env
  db.py             # SQLite orders
  fragment_api.py   # thin wrapper over bbbuilt/fragment-stars-api: get_rates + buy_stars
  ton.py            # курс TON/USD + поиск входящих платежей
  freekassa.py      # создание ссылки и polling статуса
  payment_monitor.py # проверка оплат и покупка через Fragment
```

## Что делает каждая важная функция

`bot.py`

- `main()` - загружает `.env`, создает БД, проверяет Fragment API, запускает polling и монитор оплат.
- `check_fragment_api()` - делает health-check через `get_rates()` и напоминает про KYC.
- `notify_admins()` - отправляет админам сообщение о запуске.

`handlers/order.py`

- `buy_stars()` - начинает покупку.
- `recipient_entered()` - сохраняет username получателя.
- `amount_entered()` - валидирует количество и считает цену.
- `pay_ton()` - создает заказ с TON-адресом и уникальным комментарием.
- `pay_freekassa()` - создает заказ и ссылку FreeKassa.
- `check_order()` - ручная кнопка `Я оплатил`.
- `my_orders()` - показывает последние заказы пользователя.

`services/fragment_api.py`

- использует [`bbbuilt/fragment-stars-api`](https://github.com/bbbuilt/fragment-stars-api) как основной SDK;
- `check_health()` - проверяет доступность Fragment API.
- `get_rates()` - получает комиссии `kyc` и `no_kyc`.
- `estimate_stars_price_usd()` - считает демо-цену для счета.
- `buy_stars()` - покупает Stars через Fragment API.

`services/payment_monitor.py`

- `start()` - запускает polling-монитор.
- `tick()` - один проход проверки.
- `check_payment()` - проверяет TON или FreeKassa оплату.
- `fulfill_paid_order()` - вызывает Fragment API после оплаты.

## Проверка TON платежей

Пользователь должен отправить:

- точную сумму TON;
- на адрес `TON_WALLET_ADDRESS`;
- с комментарием, который бот показал в заказе.

Без комментария бот не сможет автоматически связать транзакцию с заказом.

## FreeKassa

В этом примере нет webhook-обработчиков FreeKassa. Это сделано специально: polling проще для демонстрации и не требует публичного домена. Бот создает ссылку оплаты, а потом сам проверяет статус платежа через API.

## Типичные ошибки

- `Fragment API client не инициализирован` - не установлен [`bbbuilt/fragment-stars-api`](https://github.com/bbbuilt/fragment-stars-api).
- `FRAGMENT_COOKIES_BASE64 пустой` - для KYC режима нужны cookies.
- `Оплата пока не найдена` - проверьте сумму TON и комментарий.
- FreeKassa не отображается - проверьте `ENABLE_FREEKASSA=true`, `FREEKASSA_API_KEY`, `FREEKASSA_SHOP_ID`.

## Что удалено из старого бота

Админка, рефералка, Premium, RuKassa, Platega, YooKassa, webhook-серверы, продажа TON, миграционные скрипты и сложная бизнес-логика. Этот репозиторий теперь показывает один понятный сценарий: принять оплату и купить Stars через Fragment API.
