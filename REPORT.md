# Отчёт по лабораторной работе №5
## Redis-кэш, консистентность и rate limiting

**Студент:** _[ФИО]_  
**Группа:** _[Группа]_  
**Дата:** 03.04.2026

## 1. Реализация Redis-кэша
В `lab_05` реализованы два кэшируемых чтения:
- каталог: `GET /api/cache-demo/catalog`
- карточка заказа: `GET /api/cache-demo/orders/{order_id}/card`

Используемые ключи:
- `catalog:v1`
- `order_card:v1:{order_id}`

TTL:
- для каталога: `60` секунд;
- для карточки заказа: `60` секунд.

Логика `cache hit / miss`:
1. Если endpoint вызван с `use_cache=true`, сервис сначала пытается прочитать JSON из Redis.
2. При попадании (`hit`) ответ возвращается со `source = "cache"`.
3. При промахе (`miss`) данные грузятся из БД, сериализуются в JSON и кладутся в Redis с TTL.
4. При `use_cache=false` Redis полностью обходится, а ответ помечается `source = "db"`.

Для каталога источником истины является агрегат по `order_items`, для карточки заказа — данные из `orders` и связанных `order_items`.

## 2. Демонстрация неконсистентности (намеренно сломанный сценарий)
Сценарий проверялся тестом:

```bash
pytest app/tests/test_cache_stale_consistency.py -v -s
```

Последовательность:
1. Создавался заказ, после чего выполнялся `GET /api/cache-demo/orders/{id}/card?use_cache=true`.
   Первый ответ приходил из БД: `source = "db"`, `total_amount = 50.0`.
2. Карточка прогревала Redis-ключ `order_card:v1:{order_id}`.
3. Затем вызывался `POST /api/cache-demo/orders/{id}/mutate-without-invalidation` с `new_total_amount = 999.0`.
   Endpoint менял запись в БД, но умышленно не трогал кэш.
4. Повторный `GET .../card?use_cache=true` возвращал stale-ответ из Redis:
   `source = "cache"`, `total_amount = 50.0`.
5. Для сравнения `GET .../card?use_cache=false` возвращал уже актуальные данные БД:
   `source = "db"`, `total_amount = 999.0`.

Итог: пользователь видит устаревшие данные, хотя БД уже изменилась. Это классическая проблема stale cache при отсутствии инвалидации.

## 3. Починка через событийную инвалидацию
Починка реализована через минимальную событийную модель:
- событие `OrderUpdatedEvent` создаётся после успешного обновления заказа в endpoint
  `POST /api/cache-demo/orders/{id}/mutate-with-event-invalidation`;
- обработка выполняется в `CacheInvalidationEventBus`;
- сам обработчик вызывает методы `CacheService.invalidate_order_card()` и `CacheService.invalidate_catalog()`.

Инвалидируемые ключи:
- `order_card:v1:{order_id}` — потому что изменились данные конкретного заказа;
- `catalog:v1` — консервативно, чтобы не оставлять stale агрегаты каталога, если изменение потенциально влияет на витрину.

Проверка выполнялась тестом:

```bash
pytest app/tests/test_cache_event_invalidation.py -v -s
```

Результат:
1. Ключи `order_card:v1:{order_id}` и `catalog:v1` сначала создавались прогревом.
2. После `mutate-with-event-invalidation` оба ключа исчезали из Redis.
3. Следующий `GET /orders/{id}/card?use_cache=true` снова ходил в БД и возвращал свежий `total_amount = 1234.0`.
4. Уже последующий запрос возвращался из Redis, но с новым корректным значением.

Дополнительно подготовлена опциональная outbox-миграция `003_cache_invalidation_events.sql`, но в текущей реализации выбран минимальный и достаточно прозрачный вариант: синхронный invalidate в коде сразу после изменения заказа.

## 4. Rate limiting endpoint оплаты через Redis
Rate limiting реализован в `RateLimitMiddleware`.

Политика:
- `5` запросов за `10` секунд на одного субъекта и endpoint.

К каким endpoint применяется:
- `POST /api/payments/retry-demo`
- `POST /api/payments/pay`
- `POST /api/orders/{order_id}/pay`

Ключ лимита:
- базовый шаблон: `rate_limit:pay:{subject}`
- в middleware дополнительно включён путь endpoint, чтобы лимиты разных payment-роутов не конфликтовали.
- `subject` берётся из `X-RateLimit-Subject` или `X-Test-Client`, а если их нет — из client IP.

Механизм:
1. Для запроса выполняется `INCR` счётчика в Redis.
2. Для первого попадания задаётся `EXPIRE = 10`.
3. Если значение счётчика превысило лимит, middleware возвращает `429 Too Many Requests`.

Возвращаемые заголовки:
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`
- `Retry-After` при блокировке

Проверка выполнялась тестом:

```bash
pytest app/tests/test_payment_rate_limit_redis.py -v -s
```

Фактический результат:
- первые 5 запросов проходили;
- 6-й запрос получал `429`;
- заголовки корректно показывали остаток лимита, например от `4` после первого запроса до `0` перед блокировкой.

## 5. Бенчмарки RPS до/после кэша
В текущем окружении `wrk` и `locust` отсутствовали (`command not found`), поэтому для реальных замеров использовался `ab` (`ApacheBench`) как локальный эквивалент smoke-load-test.

Стенд замеров:
- локальный `uvicorn` на `127.0.0.1:8095`;
- SQLite-файл как benchmark-БД;
- memory-backed Redis fallback;
- `ab -n 200 -c 20`.

Перед замером `use_cache=true` кэш прогревался отдельным `GET`.

### Catalog endpoint
- Endpoint: `GET /api/cache-demo/catalog`
- Без кэша (`use_cache=false`):
  - RPS: `275.93`
  - mean request time: `72.482 ms`
  - p95 latency: `113 ms`
  - failed requests: `0`
- С кэшем (`use_cache=true`):
  - RPS: `728.51`
  - mean request time: `27.453 ms`
  - p95 latency: `52 ms`
  - failed requests: `0`
- Изменение:
  - RPS: примерно `+164%`
  - p95 latency: примерно `-54%`

### Order card endpoint
- Endpoint: `GET /api/cache-demo/orders/{order_id}/card`
- Без кэша (`use_cache=false`):
  - RPS: `107.73`
  - mean request time: `185.646 ms`
  - p95 latency: `297 ms`
  - failed requests: `0`
- С кэшем (`use_cache=true`):
  - RPS: `412.25`
  - mean request time: `48.515 ms`
  - p95 latency: `84 ms`
  - failed requests: `0`
- Изменение:
  - RPS: примерно `+283%`
  - p95 latency: примерно `-72%`

Вывод по нагрузке: на чтениях Redis-кэш даёт заметный выигрыш и по throughput, и по latency. Особенно это видно на тяжёлой карточке заказа, где без кэша каждый запрос снова собирает большой JSON из БД.

## 6. Выводы
1. Кэш особенно полезен на повторяющихся read-heavy endpoint, где ответ дороже собрать из БД, чем один раз положить в Redis и потом переиспользовать.
2. Само кэширование реализовать относительно просто, но инвалидация значительно сложнее: stale data появляется сразу, если забыть очистить хотя бы один связанный ключ.
3. Событийная инвалидация делает поведение более предсказуемым: после изменения заказа связанные ключи очищаются централизованно, а не “по памяти” в каждом месте кода.
4. Rate limiting нужен даже при наличии бизнес-валидаций и идемпотентности: он защищает систему от шторма запросов, случайных повторных кликов и избыточной нагрузки раньше, чем запрос дойдёт до бизнес-логики.
5. Лучший практический подход — комбинировать механизмы: Redis-кэш ускоряет чтения, инвалидация сохраняет консистентность, а rate limiting и идемпотентность защищают write-path от дубликатов и перегрузки.
