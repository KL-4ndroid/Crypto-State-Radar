from src.release_readiness import configuration_checks


def test_preview_cannot_pass_with_production_key_or_shared_token():
    env={"CSR_RESEARCH_DATABASE_URL":"not-printed","CSR_RESEARCH_ENVIRONMENT":"preview","CSR_SCAN_TOKEN":"fixture-a","CSR_WORKER_TOKEN":"fixture-b",
        "KV_REST_API_URL":"https://fixture.invalid","KV_REST_API_TOKEN":"fixture-c","CSR_STATE_KEY":"csr:preview:state",
        "CSR_SCAN_MODE":"shadow","CSR_RESEARCH_WORKER_URL":"https://isolated-preview.example"}
    assert all(configuration_checks(env).values())
    env.update(CSR_STATE_KEY="crypto-state-radar:state:v1",CSR_WORKER_TOKEN="fixture-a")
    checks=configuration_checks(env)
    assert not checks["preview_state_key_isolated"] and not checks["separate_worker_token"]


def test_qstash_requires_signatures_and_correct_origin_without_legacy_worker_url():
    env={"CSR_RESEARCH_DATABASE_URL":"fixture","CSR_RESEARCH_ENVIRONMENT":"production",
        "CSR_SCAN_TOKEN":"a","CSR_WORKER_TOKEN":"b","KV_REST_API_URL":"https://fixture.invalid",
        "KV_REST_API_TOKEN":"c","CSR_STATE_KEY":"production-key","CSR_SCAN_MODE":"live",
        "CSR_SCHEDULER_ENABLED":"true","QSTASH_CURRENT_SIGNING_KEY":"current",
        "QSTASH_NEXT_SIGNING_KEY":"next","CSR_SCHEDULER_ORIGIN":"https://crypto-state-radar.vercel.app"}
    assert all(configuration_checks(env,'production').values())
    env['CSR_SCHEDULER_ORIGIN']='https://other.example'
    assert not configuration_checks(env,'production')['production_scheduler_origin_matches']
    env['QSTASH_CURRENT_SIGNING_KEY']='[SENSITIVE]'
    assert not configuration_checks(env,'production')['qstash_signing_keys_configured']
