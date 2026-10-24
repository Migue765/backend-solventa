from collections.abc import Hashable, Mapping
from pathlib import Path
from typing import Any, cast

import yaml
from openapi_spec_validator import validate

CONTRACTS_DIRECTORY = Path(__file__).resolve().parents[1] / "contracts" / "openapi"


def load_contract(path: Path) -> Mapping[Hashable, Any]:
    with path.open(encoding="utf-8") as contract_file:
        document = yaml.safe_load(contract_file)

    if not isinstance(document, dict):
        raise ValueError(f"{path} no contiene un documento OpenAPI válido.")

    return cast(Mapping[Hashable, Any], document)


def validate_contract(path: Path) -> None:
    validate(load_contract(path), base_uri=path.as_uri())


def contract_paths() -> list[Path]:
    return sorted((*CONTRACTS_DIRECTORY.glob("*.yaml"), *CONTRACTS_DIRECTORY.glob("*.yml")))


def main() -> None:
    paths = contract_paths()
    if not paths:
        raise RuntimeError("No se encontraron contratos OpenAPI.")

    for path in paths:
        validate_contract(path)
        print(f"Contrato válido: {path.relative_to(CONTRACTS_DIRECTORY.parent.parent)}")


if __name__ == "__main__":
    main()
