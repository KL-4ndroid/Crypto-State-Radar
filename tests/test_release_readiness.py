from src.release_readiness import configuration_checks


def test_preview_cannot_pass_with_production_key_or_shared_token():
    env={"CSR_RESEARCH_DATABASE_URL":"not-printed","CSR_RESEARCH_ENVIRONMENT":"preview","CSR_SCAN_TOKEN":"fixture-a","CSR_WORKER_TOKEN":"fixture-b",
        "KV_REST_API_URL":"https://fixture.invalid","KV_REST_API_TOKEN":"fixture-c","CSR_STATE_KEY":"csr:preview:state",
        "CSR_SCAN_MODE":"shadow","CSR_RESEARCH_WORKER_URL":"https://isolated-preview.example"}
    assert all(configuration_checks(env).values())
    env.update(CSR_STATE_KEY="crypto-state-radar:state:v1",CSR_WORKER_TOKEN="fixture-a")
    checks=configuration_checks(env)
    assert not checks["preview_state_key_isolated"] and not checks["separate_worker_token"]
