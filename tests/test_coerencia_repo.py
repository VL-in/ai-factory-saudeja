"""
Checagens de coerência estrutural do repositório: consistência entre
.gitignore, arquivos rastreados pelo git, e referências do dvc.yaml.
Não testam lógica de negócio -- servem para pegar "drift" de configuração
(ex.: artefato binário commitado por engano, dependência referenciada mas
ausente).
"""
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git_ls_files():
    saida = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [linha for linha in saida.stdout.splitlines() if linha]


def test_nenhum_artefato_de_mlruns_rastreado_pelo_git():
    """mlruns/ está no .gitignore por ser tracking local regerável do MLflow."""
    rastreados = [f for f in _git_ls_files() if f.startswith("mlruns/")]
    assert rastreados == [], (
        "Arquivos de mlruns/ estão versionados no git apesar do .gitignore: "
        f"{rastreados}"
    )


def test_nenhum_pkl_ou_db_de_dados_rastreado_pelo_git():
    """model.pkl e mlflow.db devem ser gerenciados pelo DVC, não pelo git."""
    rastreados = [
        f
        for f in _git_ls_files()
        if f.endswith((".pkl", "mlflow.db")) and not f.endswith(".dvc")
    ]
    assert rastreados == [], f"Binários deveriam estar sob DVC, não git: {rastreados}"


def test_env_nao_esta_rastreado_pelo_git():
    rastreados = [f for f in _git_ls_files() if f == ".env"]
    assert rastreados == [], ".env não deve ser versionado (contém config local)"


def test_dvc_yaml_deps_e_outs_existem_no_repo():
    """Toda dep precisa existir no repo OU ser out de outro stage do próprio
    pipeline (caso de deps entre stages, ex.: train depende de um out do
    preprocess -- só existe fisicamente depois do primeiro `dvc repro`)."""
    dvc_yaml = yaml.safe_load((REPO_ROOT / "dvc.yaml").read_text(encoding="utf-8"))
    stages = dvc_yaml.get("stages", {})

    outs_de_outros_stages = set()
    for stage in stages.values():
        for out in stage.get("outs", []):
            outs_de_outros_stages.add(out if isinstance(out, str) else next(iter(out)))

    for nome_stage, stage in stages.items():
        for dep in stage.get("deps", []):
            if dep in outs_de_outros_stages:
                continue
            caminho = REPO_ROOT / dep
            assert caminho.exists(), (
                f"dep '{dep}' do stage '{nome_stage}' não existe no repositório "
                "nem é out de outro stage do pipeline"
            )
        # outs não precisam existir antes do primeiro `dvc repro`, mas se
        # existirem devem estar registrados no dvc.lock (checado abaixo).


def test_dvc_lock_consistente_com_dvc_yaml():
    dvc_yaml = yaml.safe_load((REPO_ROOT / "dvc.yaml").read_text(encoding="utf-8"))
    dvc_lock = yaml.safe_load((REPO_ROOT / "dvc.lock").read_text(encoding="utf-8"))

    stages_yaml = set(dvc_yaml.get("stages", {}).keys())
    stages_lock = set(dvc_lock.get("stages", {}).keys())
    assert stages_yaml == stages_lock, (
        f"Stages divergentes entre dvc.yaml ({stages_yaml}) e dvc.lock ({stages_lock})"
    )


def test_requirements_nao_tem_pacotes_duplicados():
    linhas = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pacotes = [
        linha.split("==")[0].strip().lower()
        for linha in linhas
        if linha.strip() and not linha.strip().startswith("#")
    ]
    duplicados = {p for p in pacotes if pacotes.count(p) > 1}
    assert not duplicados, f"Pacotes duplicados em requirements.txt: {duplicados}"


def test_dockerfile_referenciado_pelo_dvc_yaml_existe():
    dvc_yaml_texto = (REPO_ROOT / "dvc.yaml").read_text(encoding="utf-8")
    assert "-f dockerfile" in dvc_yaml_texto
    assert (REPO_ROOT / "dockerfile").exists()
