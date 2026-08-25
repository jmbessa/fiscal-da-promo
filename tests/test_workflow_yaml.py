import yaml


def test_publish_workflow_has_hourly_cron_covering_08_23_brt():
    with open(".github/workflows/publish.yml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    on_section = data.get("on", data.get(True))  # PyYAML lê "on:" como bool True (YAML 1.1)
    crons = [entry["cron"] for entry in on_section["schedule"]]
    assert sum(c.startswith("0 11-23") for c in crons) == 1
    assert sum(c.startswith("0 0-2") for c in crons) == 1
    assert len(crons) == 2


def test_publish_workflow_passes_meli_secrets():
    # README lista as credenciais do ML como "já repassadas pelo publish.yml"
    # — sem isso no env: do passo de execução, a fonte meli fica sem
    # credenciais em todo run do Actions.
    with open(".github/workflows/publish.yml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    steps = data["jobs"]["run"]["steps"]
    step = next(s for s in steps if s.get("name") == "Executar pipeline")
    env = step["env"]
    assert env["MELI_CLIENT_ID"] == "${{ secrets.MELI_CLIENT_ID }}"
    assert env["MELI_CLIENT_SECRET"] == "${{ secrets.MELI_CLIENT_SECRET }}"
    assert env["MELI_REFRESH_TOKEN"] == "${{ secrets.MELI_REFRESH_TOKEN }}"
