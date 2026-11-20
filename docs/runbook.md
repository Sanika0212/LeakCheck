# Runbook

## Local, end to end

```bash
uv sync --python 3.12 --all-extras
uv run leakcheck generate              # six synthetic contractors, data/generated
uv run leakcheck load                  # straight into the Iceberg bronze table
uv run leakcheck warehouse             # dbt silver, Great Expectations, extraction, dbt gold
uv run leakcheck warehouse --extractor llm   # same with qwen2.5:7b (needs `ollama serve`)
uv run leakcheck train                 # Feast features, LightGBM, MLflow registry
uv run leakcheck agent job_000123      # drafts for one job (needs Ollama)
uv run leakcheck approve               # http://localhost:8070
uv run leakcheck mcp                   # the same tools for any MCP client, over stdio
uv run dagster dev -m leakcheck.orchestration
```

`uv run leakcheck pipeline` runs generate, load, warehouse and train in one go.

## Streaming path

```bash
# A single-node Kafka (Homebrew): format once, then start
kafka-storage format --standalone -t "$(kafka-storage random-uuid)" -c /opt/homebrew/etc/kafka/server.properties
kafka-server-start /opt/homebrew/etc/kafka/server.properties

uv run leakcheck serve                 # webhook intake on :8060, producing to Kafka
uv run leakcheck consume               # lc.* topics into Iceberg every few seconds
uv run leakcheck replay --rate 50      # post the generated events, signed
uv run python eval/run_freshness.py    # does all of this itself and measures latency
```

## Evaluations

```bash
uv run python eval/run_detect.py --extractor rules
uv run python eval/run_detect.py --extractor llm     # answers cached in data/llm_cache.jsonl
uv run python eval/run_extract.py
uv run python eval/run_agent.py --cases 100
uv run python eval/run_freshness.py
uv run python poc/poc.py
```

The LLM cache is committed, so the LLM numbers reproduce without a model.

## Cloud (not yet deployed)

Each stack in `infra/terraform` validates in CI. To deploy, in this order:

1. `confluent`: export `CONFLUENT_CLOUD_API_KEY` and `CONFLUENT_CLOUD_API_SECRET`, then
   `terraform apply`. Note the bootstrap endpoint.
2. `aws`: build the Lambda package, then
   `terraform apply -var kafka_bootstrap=... -var budget_email=...`.
3. `snowflake`: set the `SNOWFLAKE_*` variables, then `terraform apply`. Point a
   dbt-snowflake target at the `LEAKCHECK` database.
4. `azure`: `terraform apply -var subscription_id=...`, then run the Azure DevOps pipeline to
   deploy the Function App.

`terraform destroy` in the reverse order returns spend to zero. The AWS stack has a budget
alert at 80% of `monthly_budget_usd`.
