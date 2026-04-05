from dcs_dungeon_master.app import bootstrap_application


def test_bootstrap_application_without_external_dependencies() -> None:
    application = bootstrap_application()

    summary = application.summary()
    assert summary["dry_run"] is True
    assert summary["service_statuses"]["olympus_gateway"] == "stub-ready"
    assert summary["service_statuses"]["model_adapters"] == "stub-ready"
