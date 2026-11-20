"""Command line entry points.

leakcheck calibrate | generate | load | serve | replay | consume | warehouse | train
leakcheck agent JOB_ID | mcp | approve | pipeline
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="leakcheck")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("calibrate", help="refresh data/calibration.json from BLS and Open-Meteo")
    g = sub.add_parser("generate", help="synthetic businesses with planted leakage")
    g.add_argument("--seed", type=int, default=7)
    sub.add_parser("load", help="generated events straight into the Iceberg bronze table")
    s = sub.add_parser("serve", help="webhook intake (FastAPI) producing to Kafka")
    s.add_argument("--port", type=int, default=8060)
    r = sub.add_parser("replay", help="post generated events to the webhook, signed")
    r.add_argument("--url", default="http://localhost:8060")
    r.add_argument("--rate", type=float, default=50.0, help="events per second")
    r.add_argument("--limit", type=int, default=0)
    c = sub.add_parser("consume", help="Kafka lc.* topics into Iceberg bronze")
    c.add_argument("--bootstrap", default="localhost:9092")
    c.add_argument("--idle", type=float, default=None, help="stop after this many idle seconds")
    w = sub.add_parser("warehouse", help="dbt silver, quality checks, extraction, dbt gold")
    w.add_argument("--extractor", default="rules", choices=["rules", "llm"])
    sub.add_parser("train", help="quote-hours model: Feast features, LightGBM, MLflow")
    a = sub.add_parser("agent", help="run the invoicing agent on one job")
    a.add_argument("job_id")
    sub.add_parser("mcp", help="MCP server over stdio")
    ap = sub.add_parser("approve", help="the owner's approval page")
    ap.add_argument("--port", type=int, default=8070)
    pl = sub.add_parser("pipeline", help="generate, load, warehouse and train in one go")
    pl.add_argument("--extractor", default="rules", choices=["rules", "llm"])
    args = p.parse_args(argv)

    if args.cmd == "calibrate":
        from leakcheck import calibration

        print(calibration.save())
    elif args.cmd == "generate":
        from leakcheck.generator import generate

        world = generate(seed=args.seed)
        _print({"jobs": len(world.jobs), "leaks": len(world.truth)})
    elif args.cmd == "load":
        from leakcheck import bronze, events, ingest

        _print({"events": bronze.load_direct(events.events(), ingest.load_secrets())})
    elif args.cmd == "serve":
        import uvicorn

        from leakcheck.ingest import create_app

        uvicorn.run(create_app(), host="0.0.0.0", port=args.port, log_level="warning")
    elif args.cmd == "replay":
        import httpx

        from leakcheck import events, ingest

        secrets = ingest.load_secrets()
        sent = 0
        with httpx.Client(base_url=args.url, timeout=10) as client:
            for account, body in events.iter_bodies(events.events()):
                client.post(
                    f"/webhooks/{account}",
                    content=body,
                    headers={
                        "X-LeakCheck-Signature": ingest.sign(secrets[account], body),
                        "content-type": "application/json",
                    },
                ).raise_for_status()
                sent += 1
                if args.limit and sent >= args.limit:
                    break
                time.sleep(1 / args.rate)
        _print({"sent": sent})
    elif args.cmd == "consume":
        from leakcheck import bronze

        _print({"appended": bronze.consume(args.bootstrap, idle_seconds=args.idle)})
    elif args.cmd == "warehouse":
        from leakcheck import warehouse

        _print(warehouse.build(args.extractor))
    elif args.cmd == "train":
        from leakcheck import quote_model
        from leakcheck.events import read_table

        splits = {
            b["business_id"]: b["split"] for b in read_table(Path("data/generated"), "businesses")
        }
        _print(quote_model.train_and_evaluate(quote_model.frame(splits=splits)))
    elif args.cmd == "agent":
        from leakcheck.agent.agent import ollama_model, run_job
        from leakcheck.agent.tools import Tools

        _print(run_job(args.job_id, Tools(), ollama_model()))
    elif args.cmd == "mcp":
        from leakcheck.agent.mcp_server import main as mcp_main

        mcp_main()
    elif args.cmd == "approve":
        import uvicorn

        from leakcheck.agent.approval import default_app

        uvicorn.run(default_app(), host="127.0.0.1", port=args.port, log_level="warning")
    elif args.cmd == "pipeline":
        main(["generate"])
        main(["load"])
        main(["warehouse", "--extractor", args.extractor])
        main(["train"])


if __name__ == "__main__":
    main()
