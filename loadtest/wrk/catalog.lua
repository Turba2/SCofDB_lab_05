-- wrk script: GET catalog cache endpoint
-- Usage:
-- USE_CACHE=true wrk -t4 -c100 -d30s -s loadtest/wrk/catalog.lua http://localhost:8082

wrk.method = "GET"
local use_cache = os.getenv("USE_CACHE") or "true"
wrk.path = "/api/cache-demo/catalog?use_cache=" .. use_cache
