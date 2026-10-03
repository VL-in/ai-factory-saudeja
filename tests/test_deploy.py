"""
SaúdeJá — smoke do caminho de deploy (Passo 11.1).

O que vai para o Hugging Face Space passa por três peças que nenhum outro
teste exercitava: o staging de lista fechada, a imagem que o builda e a espera
pelo Space depois do sync. Antes do Passo 11.1 a coerência entre elas era
convenção. Aqui ela vira teste, sem Docker e sem rede:

- **staging x Dockerfile** (A3): toda origem de `COPY` do
  `infra/deploy/dockerfile` está no staging, e nada proibido (dataset, config
  do DVC, `.env`) entra nele;
- **ambientes enxutos**: cada workflow instala só uma parte de
  `requirements/`, e a imagem do Space só `api.txt` + `ui.txt`. O que cada um
  executa tem de importar sem os pacotes que ele não instala -- um import
  de dependência que o ambiente não tem aparece aqui, e não no build do Space
  ou no meio do job;
- **espera pelo Space** (A2) e **smoke local**: a lógica de
  `scripts/smoke_deploy.py` com um HTTP falso, inclusive os caminhos de falha
  (build quebrado, runtime parado no commit anterior, modelo errado servido).

O build de verdade, como uid 1000, é o job `imagem` do `ci.yml`.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import montar_staging_space
import smoke_deploy

DOCKERFILE = REPO_ROOT / "infra" / "deploy" / "dockerfile"
ENTRYPOINT = REPO_ROOT / "infra" / "deploy" / "entrypoint.sh"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
REQUIREMENTS = REPO_ROOT / "requirements"


def _instrucoes_copy(dockerfile: Path) -> list[str]:
    """Origens dos COPY (sem --from, que não lê do contexto)."""
    origens = []
    for linha in dockerfile.read_text(encoding="utf-8").splitlines():
        partes = linha.split()
        if partes[:1] == ["COPY"] and not any(p.startswith("--from") for p in partes):
            argumentos = [p for p in partes[1:] if not p.startswith("--")]
            origens += argumentos[:-1]
    return origens


@pytest.fixture(scope="module")
def staging(tmp_path_factory):
    destino = tmp_path_factory.mktemp("space")
    conteudo = montar_staging_space.montar(destino)
    return destino, conteudo


# --------------------------------------------------------------------------
# staging x Dockerfile
# --------------------------------------------------------------------------
def test_toda_origem_de_copy_do_dockerfile_esta_no_staging(staging):
    """A3: o Space builda o staging, não o checkout. Um COPY novo no
    Dockerfile sem a origem na lista fechada passa no build local (contexto =
    repositório) e quebra só no Space."""
    destino, _ = staging
    faltando = [o for o in _instrucoes_copy(DOCKERFILE) if not (destino / o).exists()]
    assert faltando == [], f"COPY sem origem no staging: {faltando}"


def test_staging_nao_leva_nada_proibido_nem_cache_de_bytecode(staging):
    _, conteudo = staging
    assert not [c for c in conteudo if montar_staging_space.PROIBIDO.search(c)]
    assert not [c for c in conteudo if "__pycache__" in c or c.endswith(".pyc")]
    assert not [c for c in conteudo if c.startswith(("tests/", "docs/", "scripts/", "supabase/"))]


def test_staging_tem_dockerfile_na_raiz_e_front_matter_na_porta_da_ui(staging):
    destino, conteudo = staging
    assert "Dockerfile" in conteudo
    front_matter = (destino / "README.md").read_text(encoding="utf-8").split("---")[1]
    app_port = re.search(r"app_port:\s*(\d+)", front_matter).group(1)
    assert f"UI_PORT={app_port}" in DOCKERFILE.read_text(encoding="utf-8")


def test_staging_sem_modelo_falha_com_instrucao_de_dvc_pull(tmp_path):
    raiz = tmp_path / "repo"
    for origem, _ in montar_staging_space.ARQUIVOS:
        if origem != "data/model.pkl":
            (raiz / origem).parent.mkdir(parents=True, exist_ok=True)
            (raiz / origem).write_text("x", encoding="utf-8")
    (raiz / "src").mkdir()

    with pytest.raises(montar_staging_space.ErroStaging, match="dvc pull"):
        montar_staging_space.montar(tmp_path / "space", raiz=raiz)


@pytest.mark.parametrize(
    "intruso",
    ["data/consultas-treino.csv", ".dvc/config.local", "src/.env", "data/x.csv.dvc", "mlflow.db"],
)
def test_guarda_do_staging_pega_arquivo_proibido(tmp_path, intruso):
    (tmp_path / intruso).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / intruso).write_text("segredo", encoding="utf-8")
    assert montar_staging_space.arquivos_proibidos(tmp_path) == [intruso]


def test_cli_do_staging_lista_o_conteudo(tmp_path, capsys):
    assert montar_staging_space.main(["--destino", str(tmp_path / "space")]) == 0
    saida = capsys.readouterr().out
    assert "data/model.pkl" in saida and "[ok]" in saida


# --------------------------------------------------------------------------
# imagem
# --------------------------------------------------------------------------
def test_imagem_roda_como_usuario_nao_root():
    """A4: o Space roda o container com uid 1000. Um USER root (ou nenhum)
    deixaria o smoke do CI testando uma condição que produção não tem."""
    usuarios = re.findall(r"^USER\s+(\S+)", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert usuarios and usuarios[-1] not in ("root", "0")


def test_imagem_de_deploy_nao_instala_o_ambiente_de_treino():
    texto = DOCKERFILE.read_text(encoding="utf-8")
    instalados = re.findall(r"-r\s+(requirements/\S+)", texto)
    assert sorted(instalados) == ["requirements/api.txt", "requirements/ui.txt"]


def test_entrypoint_sobe_os_dois_processos_e_cai_junto():
    texto = ENTRYPOINT.read_bytes()
    assert b"\r\n" not in texto  # CRLF no shebang quebra o container
    assert b"wait -n" in texto
    assert b"api.main:app" in texto and b"src/ui/app.py" in texto


# --------------------------------------------------------------------------
# ambientes enxutos: cada um importa o que executa
# --------------------------------------------------------------------------
# Nome no requirements -> módulos importáveis que ele traz. Pacote sem módulo
# próprio (metapacote, stubs, dados) fica com lista vazia.
MODULOS_DO_PACOTE = {
    "pandas": ["pandas"],
    "numpy": ["numpy"],
    "scikit-learn": ["sklearn"],
    "lightgbm": ["lightgbm"],
    "joblib": ["joblib"],
    "python-dotenv": ["dotenv"],
    "pyyaml": ["yaml"],
    "tzdata": [],
    "fastapi": ["fastapi"],
    "uvicorn": ["uvicorn"],
    "shap": ["shap"],
    "httpx": ["httpx"],
    "streamlit": ["streamlit"],
    "supabase": ["supabase", "supabase_auth", "postgrest"],
    "matplotlib": ["matplotlib"],
    "jupyter": [],
    "imbalanced-learn": ["imblearn"],
    "mlflow": ["mlflow"],
    "pytest": ["pytest"],
    "dvc": ["dvc"],
    "ruff": ["ruff"],
    "mypy": ["mypy"],
    "types-pyyaml": [],
}

# Imports tardios que o comando do workflow de fato executa (a importação do
# arquivo não os alcança). `canario.py avaliar`/`reverter --registrar-no-banco`
# gravam `canarios_revertidos`.
IMPORTS_TARDIOS = {"src/canario.py": ["db.repositories"]}


def _pacotes(arquivo: Path) -> set[str]:
    pacotes: set[str] = set()
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#")[0].strip()
        if linha.startswith("-r"):
            pacotes |= _pacotes(arquivo.parent / linha[2:].strip())
        elif linha:
            pacotes.add(re.split(r"[\[=<>~ ]", linha)[0].lower())
    return pacotes


def _todos_os_pacotes() -> set[str]:
    return set().union(*(_pacotes(a) for a in REQUIREMENTS.glob("*.txt")))


def _bloqueados(instalados: set[str]) -> list[str]:
    return sorted(m for p in _todos_os_pacotes() - instalados for m in MODULOS_DO_PACOTE[p])


def _imports_de_topo(arquivo: Path) -> list[str]:
    """Para o app Streamlit, que executa a tela ao ser importado: só os
    imports do topo do script."""
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nomes = []
    for no in arvore.body:
        if isinstance(no, ast.Import):
            nomes += [a.name for a in no.names]
        elif isinstance(no, ast.ImportFrom) and no.module and no.level == 0:
            nomes += [f"{no.module}:{a.name}" for a in no.names]
    return nomes


def _ambientes_dos_workflows() -> dict[str, tuple[set[str], list[str]]]:
    """job -> (pacotes instalados, scripts Python executados), lido do YAML.
    Jobs que instalam o dev.txt têm tudo e ficam de fora."""
    import yaml

    ambientes = {}
    for arquivo in sorted(WORKFLOWS.glob("*.yml")):
        conteudo = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        for nome_job, job in conteudo["jobs"].items():
            runs = "\n".join(p.get("run", "") for p in job.get("steps", []))
            instalacoes = [linha for linha in runs.splitlines() if "pip install" in linha]
            if not instalacoes or any("dev.txt" in linha for linha in instalacoes):
                continue
            pacotes: set[str] = set()
            for linha in instalacoes:
                for req in re.findall(r"-r\s+(requirements/\S+\.txt)", linha):
                    pacotes |= _pacotes(REPO_ROOT / req)
                pacotes |= {p.lower() for p in re.findall(r"\^([\w-]+)==", linha)}
            scripts = sorted(set(re.findall(r"python ((?:src|scripts)/\S+\.py)", runs)))
            ambientes[f"{arquivo.name}::{nome_job}"] = (pacotes, scripts)
    return ambientes


_CODIGO_DO_IMPORT = """
import importlib, importlib.abc, runpy, sys
bloqueados = set(sys.argv[1].split(",")) - {""}

class ForaDoAmbiente(importlib.abc.MetaPathFinder):
    def find_spec(self, nome, path=None, target=None):
        if nome.split(".")[0] in bloqueados:
            raise ModuleNotFoundError(f"No module named {nome!r} (fora do ambiente)", name=nome)
        return None

sys.meta_path.insert(0, ForaDoAmbiente())
for alvo in sys.argv[2:]:
    if alvo.endswith(".py"):
        runpy.run_path(alvo, run_name="smoke_de_ambiente")
    elif ":" in alvo:  # from modulo import nome (nome pode ser submódulo)
        modulo, nome = alvo.split(":")
        if not hasattr(importlib.import_module(modulo), nome):
            importlib.import_module(f"{modulo}.{nome}")
    else:
        importlib.import_module(alvo)
"""


def _importar_sem(bloqueados: list[str], alvos: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _CODIGO_DO_IMPORT, ",".join(bloqueados), *alvos],
        cwd=REPO_ROOT,
        env={**_env_minimo(), "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=180,
    )


def _env_minimo() -> dict[str, str]:
    import os

    # Sem SUPABASE_*: importar não pode depender de banco configurado.
    return {k: v for k, v in os.environ.items() if not k.startswith("SUPABASE")}


def test_todo_pacote_dos_requirements_tem_modulo_mapeado():
    """Dependência nova sem entrada em MODULOS_DO_PACOTE passaria pelo teste
    de ambiente sem nunca ser bloqueada."""
    assert _todos_os_pacotes() - set(MODULOS_DO_PACOTE) == set()


def test_workflows_com_ambiente_enxuto_foram_encontrados():
    """Se o parser do YAML deixar de achar os jobs, o teste abaixo passaria
    vazio. Os cinco ambientes que existem hoje têm de aparecer."""
    assert {
        "ci.yml::imagem",
        "deploy.yml::deploy",
        "job_d2.yml::job-d2",
        "canario.yml::canario",
    } <= set(_ambientes_dos_workflows())


@pytest.mark.parametrize("ambiente", sorted(_ambientes_dos_workflows()))
def test_cada_workflow_importa_o_que_executa_so_com_o_que_instala(ambiente):
    instalados, scripts = _ambientes_dos_workflows()[ambiente]
    alvos = scripts + [m for s in scripts for m in IMPORTS_TARDIOS.get(s, [])]

    resultado = _importar_sem(_bloqueados(instalados), alvos)

    assert resultado.returncode == 0, (
        f"{ambiente} instala {sorted(instalados)} e não consegue importar {alvos}:\n"
        f"{resultado.stderr[-2000:]}"
    )


def test_imagem_do_space_importa_api_e_ui_so_com_api_e_ui_txt():
    """O mesmo para a imagem de deploy: `api.main` (uvicorn) e os imports do
    `src/ui/app.py` (streamlit run), com tudo que não vem de api.txt/ui.txt
    -- matplotlib, imbalanced-learn, DVC -- bloqueado. É a prova, sem Docker,
    de que a UI e a API não dependem do ambiente de treino."""
    instalados = _pacotes(REQUIREMENTS / "api.txt") | _pacotes(REQUIREMENTS / "ui.txt")
    alvos = ["api.main", *_imports_de_topo(REPO_ROOT / "src" / "ui" / "app.py")]

    resultado = _importar_sem(_bloqueados(instalados), alvos)

    assert "imblearn" in _bloqueados(instalados)
    assert resultado.returncode == 0, resultado.stderr[-2000:]


def test_o_bloqueio_do_ambiente_funciona():
    """Controle negativo: sem ele, um bloqueio quebrado faria os testes acima
    passarem por não bloquear nada."""
    resultado = _importar_sem(["mlflow"], ["src/train.py"])
    assert resultado.returncode != 0
    assert "fora do ambiente" in resultado.stderr


# --------------------------------------------------------------------------
# smoke_deploy: HTTP falso
# --------------------------------------------------------------------------
class _Relogio:
    """Relógio que só anda quando o código dorme -- timeout sem esperar."""

    def __init__(self):
        self.agora = 0.0

    def __call__(self):
        return self.agora

    def dormir(self, segundos):
        self.agora += segundos


def _http(rotas):
    """`rotas[url]` é uma lista de respostas (status, corpo) consumida em
    ordem; a última se repete."""
    chamadas = []

    def buscar(url, cabecalhos):
        chamadas.append((url, cabecalhos))
        respostas = rotas.get(url, [(0, "")])
        return respostas.pop(0) if len(respostas) > 1 else respostas[0]

    buscar.chamadas = chamadas
    return buscar


UI, API = "http://localhost:7860", "http://localhost:8000"


def test_local_espera_ui_e_api_e_confere_o_modelo():
    relogio = _Relogio()
    buscar = _http(
        {
            f"{UI}/_stcore/health": [(0, ""), (0, ""), (200, "ok")],
            f"{API}/health": [(200, json.dumps({"status": "ok", "model_version": "abc123"}))],
        }
    )

    smoke_deploy.verificar_local(
        UI, API, "abc123", buscar=buscar, dormir=relogio.dormir, relogio=relogio
    )

    assert relogio.agora == 4  # duas tentativas de 2s antes da UI subir


def test_local_falha_se_a_api_serve_outro_modelo():
    relogio = _Relogio()
    buscar = _http(
        {
            f"{UI}/_stcore/health": [(200, "ok")],
            f"{API}/health": [(200, json.dumps({"model_version": "velho"}))],
        }
    )
    with pytest.raises(smoke_deploy.FalhaSmoke, match="velho"):
        smoke_deploy.verificar_local(
            UI, API, "novo", buscar=buscar, dormir=relogio.dormir, relogio=relogio
        )


def test_local_falha_no_timeout_com_o_ultimo_motivo():
    relogio = _Relogio()
    buscar = _http({f"{UI}/_stcore/health": [(200, "ok")], f"{API}/health": [(500, "")]})
    with pytest.raises(smoke_deploy.FalhaSmoke, match="API respondeu 500"):
        smoke_deploy.verificar_local(
            UI, API, "x", timeout=10, buscar=buscar, dormir=relogio.dormir, relogio=relogio
        )


SPACE = "VL-in/saude_ja"
API_SPACE = f"{smoke_deploy.API_HF}/{SPACE}"


def _runtime(estagio, sha, dominio="vl-in-saude-ja.hf.space"):
    return (200, json.dumps({"stage": estagio, "sha": sha, "domains": [{"domain": dominio}]}))


def test_space_espera_o_commit_novo_sair_do_build_e_responder():
    """Logo depois do sync o runtime ainda está RUNNING no commit anterior --
    aceitar esse RUNNING seria declarar no ar um deploy que nem começou."""
    relogio = _Relogio()
    buscar = _http(
        {
            API_SPACE: [(200, json.dumps({"sha": "novo"}))],
            f"{API_SPACE}/runtime": [
                _runtime("RUNNING", "velho"),
                _runtime("BUILDING", "novo"),
                _runtime("APP_STARTING", "novo"),
                _runtime("RUNNING", "novo"),
            ],
            "https://vl-in-saude-ja.hf.space/_stcore/health": [(502, ""), (200, "ok")],
        }
    )

    url = smoke_deploy.verificar_space(
        SPACE, "tok", buscar=buscar, dormir=relogio.dormir, relogio=relogio
    )

    assert url == "https://vl-in-saude-ja.hf.space"
    runtimes = [u for u, _ in buscar.chamadas if u.endswith("/runtime")]
    assert len(runtimes) == 4
    # O token vai só para a API do Hub, nunca para a URL pública do app.
    assert all(c == {} for u, c in buscar.chamadas if "hf.space" in u)
    assert all(c == {"Authorization": "Bearer tok"} for u, c in buscar.chamadas if "/api/" in u)


def test_space_com_build_quebrado_no_commit_novo_falha_na_hora():
    relogio = _Relogio()
    buscar = _http(
        {
            API_SPACE: [(200, json.dumps({"sha": "novo"}))],
            f"{API_SPACE}/runtime": [_runtime("BUILDING", "novo"), _runtime("BUILD_ERROR", "novo")],
        }
    )
    with pytest.raises(smoke_deploy.FalhaSmoke, match="BUILD_ERROR"):
        smoke_deploy.verificar_space(SPACE, buscar=buscar, dormir=relogio.dormir, relogio=relogio)
    assert relogio.agora < 60


def test_space_com_erro_do_deploy_anterior_ainda_espera_o_novo():
    """BUILD_ERROR no sha velho é o deploy anterior: não é motivo para falhar
    este -- mas o novo tem de chegar dentro do prazo."""
    relogio = _Relogio()
    buscar = _http(
        {
            API_SPACE: [(200, json.dumps({"sha": "novo"}))],
            f"{API_SPACE}/runtime": [_runtime("BUILD_ERROR", "velho")],
        }
    )
    with pytest.raises(smoke_deploy.FalhaSmoke, match="esperando novo"):
        smoke_deploy.verificar_space(
            SPACE, timeout=60, buscar=buscar, dormir=relogio.dormir, relogio=relogio
        )


def test_dominio_padrao_segue_a_regra_do_hub():
    assert smoke_deploy.dominio_padrao("VL-in/saude_ja.v2") == "vl-in-saude-ja-v2.hf.space"


def test_buscar_http_recusa_esquema_que_nao_e_http():
    with pytest.raises(ValueError, match="esquema"):
        smoke_deploy.buscar_http("file:///etc/passwd", {})


def test_buscar_http_contra_servidor_local_de_verdade():
    """O HTTP falso acima assume o contrato (status, corpo) e "0 = nada
    respondeu"; aqui ele é conferido contra um servidor real na loopback."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            codigo = 200 if self.path == "/health" else 503
            self.send_response(codigo)
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}' if codigo == 200 else b"")

        def log_message(self, *args):
            pass

    servidor = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{servidor.server_address[1]}"
    try:
        assert smoke_deploy.buscar_http(f"{base}/health", {}) == (200, '{"status": "ok"}')
        assert smoke_deploy.buscar_http(f"{base}/outra", {})[0] == 503
    finally:
        servidor.shutdown()
        servidor.server_close()
    assert smoke_deploy.buscar_http(f"{base}/health", {}) == (0, "")  # ninguém escutando


def test_cli_space_publica_a_url_no_summary(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        smoke_deploy, "verificar_space", lambda space_id, token, timeout: "https://x.hf.space"
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert smoke_deploy.main(["space", "--space-id", SPACE]) == 0
    assert "https://x.hf.space" in summary.read_text(encoding="utf-8")


def test_cli_local_falha_com_anotacao_do_actions(monkeypatch, capsys):
    def falha(*args, **kwargs):
        raise smoke_deploy.FalhaSmoke("UI respondeu nada")

    monkeypatch.setattr(smoke_deploy, "verificar_local", falha)

    assert smoke_deploy.main(["local", "--modelo", str(REPO_ROOT / "data" / "model.pkl")]) == 1
    assert capsys.readouterr().out.startswith("::error::UI respondeu nada")
