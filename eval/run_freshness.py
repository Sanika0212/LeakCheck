"""How fresh are findings? Webhook to Iceberg latency on a real Kafka broker, and the gold build.

    python eval/run_freshness.py --events 3000 --rate 100

Starts a single-node Kafka broker (KRaft mode, its own data directory), the webhook intake
producing to it, and the bronze consumer appending to a separate Iceberg table every
second. Replays signed events over HTTP at a fixed rate, then reads the table back:
for every event, ``ingested_at - received_at`` is the time from the webhook accepting it to
the batch that holds it being committed in Iceberg. Then times one bronze-to-gold build
(dbt silver, quality checks, rule extraction, dbt gold) on that table.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import numpy as np
from common import write

KAFKA_BIN = Path(os.environ.get("KAFKA_HOME", "/opt/homebrew/opt/kafka")) / "bin"


def start_kafka(tmp: Path) -> subprocess.Popen[bytes]:
    cfg = tmp / "server.properties"
    cfg.write_text(
        "process.roles=broker,controller\nnode.id=1\n"
        "controller.quorum.bootstrap.servers=localhost:9093\n"
        "listeners=PLAINTEXT://localhost:9092,CONTROLLER://localhost:9093\n"
        "advertised.listeners=PLAINTEXT://localhost:9092\n"
        "controller.listener.names=CONTROLLER\ninter.broker.listener.name=PLAINTEXT\n"
        "listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT\n"
        f"log.dirs={tmp / 'logs'}\nnum.partitions=3\nauto.create.topics.enable=true\n"
        "offsets.topic.replication.factor=1\ntransaction.state.log.replication.factor=1\n"
        "transaction.state.log.min.isr=1\ngroup.initial.rebalance.delay.ms=0\n"
    )
    cluster = subprocess.run(
        [str(KAFKA_BIN / "kafka-storage"), "random-uuid"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        [str(KAFKA_BIN / "kafka-storage"), "format", "--standalone", "-t", cluster, "-c", str(cfg)],
        capture_output=True,
        check=True,
    )
    env = {**os.environ, "KAFKA_HEAP_OPTS": "-Xmx512m -Xms256m"}
    proc = subprocess.Popen(
        [str(KAFKA_BIN / "kafka-server-start"), str(cfg)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    from confluent_kafka.admin import AdminClient

    admin = AdminClient({"bootstrap.servers": "localhost:9092"})
    for _ in range(60):
        try:
            admin.list_topics(timeout=1)
            return proc
        except Exception:
            time.sleep(1)
    raise RuntimeError("Kafka did not start")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=int, default=3000)
    p.add_argument("--rate", type=float, default=100.0)
    args = p.parse_args()

    import uvicorn

    from leakcheck import bronze, events, ingest, warehouse

    tmp = Path(tempfile.mkdtemp(prefix="lc-fresh-"))
    kafka = start_kafka(tmp)
    lake = tmp / "lake"
    try:
        secrets = ingest.load_secrets()
        app = ingest.create_app(secrets, ingest.KafkaSink("localhost:9092"))
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=8061, log_level="warning")
        )
        threading.Thread(target=server.run, daemon=True).start()
        consumed: dict[str, int] = {}
        consumer = threading.Thread(
            target=lambda: consumed.update(
                n=bronze.consume(root=lake, flush_seconds=1.0, idle_seconds=20)
            ),
            daemon=True,
        )
        time.sleep(2)
        bodies = list(events.iter_bodies(events.events()))[: args.events]
        started = time.time()
        with httpx.Client(base_url="http://127.0.0.1:8061", timeout=10) as client:
            for i, (account, body) in enumerate(bodies):
                client.post(
                    f"/webhooks/{account}",
                    content=body,
                    headers={"X-LeakCheck-Signature": ingest.sign(secrets[account], body)},
                ).raise_for_status()
                if i == 0:
                    consumer.start()
                delay = started + (i + 1) / args.rate - time.time()
                if delay > 0:
                    time.sleep(delay)
        send_seconds = time.time() - started
        consumer.join(timeout=300)
        table = bronze.table(lake).scan().to_arrow()
        if table.num_rows != len(bodies):
            # A partial read would measure a backlog, not the pipeline's freshness.
            raise RuntimeError(
                f"only {table.num_rows} of {len(bodies)} events reached Iceberg; "
                "rerun on an idle machine"
            )
        lat = (
            np.array(table["ingested_at"].to_pylist(), dtype="datetime64[us]")
            - np.array(table["received_at"].to_pylist(), dtype="datetime64[us]")
        ).astype(float) / 1e6
        server.should_exit = True

        db = tmp / "warehouse.duckdb"
        os.environ["LC_WAREHOUSE"] = str(db)
        os.environ["LC_DBT_TARGET"] = str(tmp / "dbt-target")
        warehouse.WAREHOUSE = db
        t0 = time.time()
        warehouse.build("rules", lake=lake)
        gold_seconds = time.time() - t0
        result = {
            "events_sent": len(bodies),
            "rate_per_second": args.rate,
            "events_in_bronze": table.num_rows,
            "send_seconds": round(send_seconds, 1),
            "webhook_to_iceberg_seconds": {
                "p50": round(float(np.percentile(lat, 50)), 2),
                "p95": round(float(np.percentile(lat, 95)), 2),
                "p99": round(float(np.percentile(lat, 99)), 2),
                "max": round(float(lat.max()), 2),
            },
            "micro_batch_seconds": 1.0,
            "bronze_to_gold_build_seconds": round(gold_seconds, 1),
        }
        print(result)
        print("wrote", write("freshness", result))
    finally:
        kafka.terminate()
        kafka.wait(timeout=30)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
