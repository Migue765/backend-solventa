from scripts.validate_openapi import contract_paths, validate_contract


def test_openapi_contracts_are_valid() -> None:
    paths = contract_paths()

    assert paths, "Debe existir al menos un contrato OpenAPI."
    for path in paths:
        validate_contract(path)
