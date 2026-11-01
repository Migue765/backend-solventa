"""Run bounded JMeter tests with a disposable local stack and durable safe evidence."""

import argparse
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests.jmeter.local_stack import LocalStack
from tests.jmeter.plans import main_plan, outage_plan, persistence_plan, recovery_plan
from tests.jmeter.results import evaluate


def execute(home: Path, plan: Path, stack: LocalStack, run_id: str) -> Path:
    jtl = plan.with_suffix(".jtl")
    if jtl.exists():
        raise FileExistsError(f"Refusing to append to existing evidence: {jtl}")
    args = [
        "java",
        "-Xms128m",
        "-Xmx768m",
        "-Dfile.encoding=UTF-8",
        "-jar",
        str(home / "bin" / "ApacheJMeter.jar"),
        "-n",
        "-t",
        str(plan),
        "-l",
        str(jtl),
        "-j",
        str(plan.with_suffix(".log")),
        "-JrunId=" + run_id,
        "-JwebPort=" + str(stack.servers["web"].server_port),
        "-JmobilePort=" + str(stack.servers["mobile"].server_port),
        "-Jjmeter.save.saveservice.output_format=csv",
        "-Jjmeter.save.saveservice.print_field_names=true",
    ]
    for field in (
        "response_data",
        "response_data.on_error",
        "samplerData",
        "requestHeaders",
        "responseHeaders",
        "url",
        "response_message",
        "assertion_results_failure_message",
    ):
        args.append(f"-Jjmeter.save.saveservice.{field}=false")
    with plan.with_suffix(".console.log").open("w", encoding="utf-8") as output:
        result = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise RuntimeError(
            f"JMeter failed with exit {result.returncode}; see {plan.stem}.console.log"
        )
    return jtl


def counts(stack: LocalStack) -> dict[str, int]:
    from cotizacion.infrastructure.persistence.models import (
        CotizacionModel,
        PagoSimuladoModel,
        SagaEmisionModel,
    )
    from polizas.infrastructure.models import PolizaModel

    result = {}
    for name, database, model in (
        ("accepted_quotes", "cotizacion", CotizacionModel),
        ("payments", "cotizacion", PagoSimuladoModel),
        ("sagas", "cotizacion", SagaEmisionModel),
        ("policies", "polizas", PolizaModel),
    ):
        engine = create_engine(stack.databases[database])
        try:
            with Session(engine) as session:
                result[name] = int(session.scalar(select(func.count()).select_from(model)) or 0)
        finally:
            engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jmeter-home", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/jmeter"))
    parser.add_argument("--mode", choices=("smoke", "performance"), default="smoke")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--allow-load", action="store_true")
    args = parser.parse_args()
    if not (args.jmeter_home / "bin" / "ApacheJMeter.jar").is_file():
        parser.error("Install JMeter first with python -m tests.jmeter.install")
    if args.threads < 1 or args.iterations < 1 or args.threads * args.iterations > 200:
        parser.error("Local harness permits 1..200 user journeys (eight products each).")
    if args.mode == "smoke" and (args.threads != 1 or args.iterations != 1):
        parser.error("Smoke requires exactly one user iteration.")
    if args.mode == "performance" and not args.allow_load:
        parser.error("Performance requires explicit --allow-load; never targets remote systems.")
    args.output.mkdir(parents=True, exist_ok=True)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    run_id = uuid4().hex
    summary: dict[str, object] = {}
    with TemporaryDirectory(prefix="solventa-jmeter-") as directory:
        with LocalStack(Path(directory)) as stack:
            plan = args.output / "main.jmx"
            main_plan(plan, args.threads, args.iterations, args.mode == "smoke")
            jtl = execute(args.jmeter_home, plan, stack, run_id)
            summary = evaluate(
                jtl,
                args.threads * args.iterations,
                args.mode == "performance",
                capacity_concurrency=args.threads if args.mode == "performance" else 0,
            )
            summary["run"] = {
                "mode": args.mode,
                "threads": args.threads,
                "iterations": args.iterations,
                "evaluated_at": datetime.now(UTC).isoformat(),
                "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "worktree_dirty": bool(
                    subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
                ),
                "jmeter": "5.6.3",
                "database": "SQLite temporary",
            }
            persisted = counts(stack)
            summary["persisted"] = persisted
            expected = 8 * args.threads * args.iterations
            if any(value != expected for value in persisted.values()):
                summary["passed"] = False
                summary["persistence_error"] = f"Expected exactly {expected} of every entity."
            if summary["passed"] and args.mode == "smoke":
                stack.restart_policy()
                plan = args.output / "restart.jmx"
                persistence_plan(plan)
                recovery = evaluate(
                    execute(args.jmeter_home, plan, stack, run_id),
                    0,
                    required_labels=("restart.persisted.dashboard", "restart.retry.payment"),
                )
                summary["restart"] = recovery
                if not recovery["passed"] or counts(stack) != persisted:
                    summary["passed"] = False
            if summary["passed"] and args.mode == "smoke":
                port = stack.stop("policies")
                plan = args.output / "outage.jmx"
                outage_plan(plan)
                try:
                    outage = evaluate(
                        execute(args.jmeter_home, plan, stack, run_id + "-outage"),
                        0,
                        required_labels=("outage.pending", "outage.dashboard.unavailable"),
                    )
                    before = counts(stack)
                finally:
                    stack.resume_policy(port)
                summary["outage"] = outage
                if before != {**persisted, "accepted_quotes": 9, "payments": 9, "sagas": 9}:
                    summary["passed"] = False
                    summary["outage_persistence_error"] = "Payment was not preserved exactly once."
                plan = args.output / "recovery.jmx"
                recovery_plan(plan)
                recovered = evaluate(
                    execute(args.jmeter_home, plan, stack, run_id + "-outage"),
                    0,
                    required_labels=(
                        "recovery.retry.0",
                        "recovery.retry.1",
                        "recovery.emitted.dashboard",
                    ),
                )
                summary["recovery"] = recovered
                summary["persisted_after_recovery"] = counts(stack)
                if (
                    not outage["passed"]
                    or not recovered["passed"]
                    or counts(stack) != dict.fromkeys(persisted, 9)
                ):
                    summary["passed"] = False
            (args.output / "summary.json").write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8"
            )
    print(json.dumps(summary, indent=2))
    if not summary["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
