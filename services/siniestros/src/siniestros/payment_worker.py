import argparse
import time

from siniestros.domain import Config
from siniestros.infrastructure.pagos_parametricos import ProcesadorPagosParametricos
from siniestros.repository import Repository


def run(once: bool, limit: int, idle_seconds: float) -> None:
    repository = Repository(Config.from_environment())
    processor = ProcesadorPagosParametricos(repository)
    while True:
        result = processor.procesar(limit)
        if once:
            return
        if (
            result["casosIncorporados"]
            + result["confirmados"]
            + result["rechazados"]
            + result["pendientes"]
            == 0
        ):
            time.sleep(idle_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ejecuta y concilia pagos parametricos simulados.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--idle-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if not 1 <= args.limit <= 5_000 or args.idle_seconds <= 0:
        parser.error("limit debe estar entre 1 y 5000 e idle-seconds debe ser positivo")
    run(args.once, args.limit, args.idle_seconds)


if __name__ == "__main__":
    main()
