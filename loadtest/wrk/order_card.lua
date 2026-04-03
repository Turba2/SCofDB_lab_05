-- wrk script: GET order card endpoint
-- Usage:
-- ORDER_ID=<uuid> USE_CACHE=true wrk -t4 -c100 -d30s -s loadtest/wrk/order_card.lua http://localhost:8082
--

wrk.method = "GET"
local order_id = os.getenv("ORDER_ID") or "PUT_REAL_ORDER_ID_HERE"
local use_cache = os.getenv("USE_CACHE") or "true"
wrk.path = "/api/cache-demo/orders/" .. order_id .. "/card?use_cache=" .. use_cache
