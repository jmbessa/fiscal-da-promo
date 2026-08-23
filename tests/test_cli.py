from afiliado import cli, pipeline


def test_run_dry_invokes_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        (open("config.yaml", encoding="utf-8").read()
         .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))),
        encoding="utf-8")

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None):
        chamado.update(dry_run=dry_run, n_sources=len(sources), n_channels=len(channels))
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--dry-run", "--config", str(cfg_file)]) == 0
    assert chamado == {"dry_run": True, "n_sources": 1, "n_channels": 0}
