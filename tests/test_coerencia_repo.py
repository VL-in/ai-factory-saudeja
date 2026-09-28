"""
Checagens de coerência estrutural do repositório: consistência entre
.gitignore, arquivos rastreados pelo git, e referências do dvc.yaml.
Não testam lógica de negócio -- servem para pegar "drift" de configuração
(ex.: artefato binário commitado por engano, dependência referenciada mas
ausente).
"""
import ast
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
    """requirements.txt foi dividido em requirements/{base,train,api}.txt
    (Passo 3) -- cada arquivo próprio (ignorando linhas "-r ...", que só
    referenciam outro arquivo) não deve ter pacote repetido dentro de si."""
    for caminho in (REPO_ROOT / "requirements").glob("*.txt"):
        linhas = caminho.read_text(encoding="utf-8").splitlines()
        pacotes = [
            linha.split("==")[0].strip().lower()
            for linha in linhas
            if linha.strip() and not linha.strip().startswith(("#", "-r"))
        ]
        duplicados = {p for p in pacotes if pacotes.count(p) > 1}
        assert not duplicados, f"Pacotes duplicados em {caminho.name}: {duplicados}"


def test_dockerfile_referenciado_pelo_dvc_yaml_existe():
    """O elo dvc.yaml -> dockerfile deixou de ser direto (`docker build -f
    dockerfile`) e passou a ter o compose no meio: os stages rodam
    `docker compose run ... train`, e é o serviço `train` que aponta para o
    dockerfile (Passo 9, decisão 5 -- `%cd%` não expandia no runner Linux).
    A checagem segue a mesma: o dockerfile que o pipeline usa existe de fato,
    agora percorrendo os dois saltos."""
    dvc_yaml = yaml.safe_load((REPO_ROOT / "dvc.yaml").read_text(encoding="utf-8"))
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    servico_train = compose["services"]["train"]
    dockerfile = REPO_ROOT / servico_train["build"]["dockerfile"]
    assert dockerfile.exists(), f"{dockerfile.name} referenciado pelo compose não existe"

    for nome, stage in dvc_yaml["stages"].items():
        assert "docker compose run" in stage["cmd"], (
            f"stage '{nome}' não usa `docker compose run` -- um `docker run` com mount "
            "montado à mão volta a quebrar no runner Linux (Passo 9, decisão 5)"
        )
        # Sem estas deps, mudar a imagem ou o mount não invalidaria o stage.
        for dep in ("dockerfile", "docker-compose.yml"):
            assert dep in stage["deps"], f"stage '{nome}' não declara '{dep}' em deps"


def test_migrations_sql_sem_coluna_proibida_de_pii():
    """Minimização de PII por design (BRIEFING.md, architecture.md §4.1):
    `pacientes` guarda só id_paciente_externo + demografia não identificável.
    Guarda automatizável desde o schema, não só por convenção de código.

    `telefone` não está mais na lista (Passo 7): é a exceção deliberada --
    sem contato de envio o disparo de lembrete real (Infobip) não existe --
    documentada na migration `20260920020000_telefone_paciente.sql`. Segue
    proibido gravar nome/CPF/email, que servem só para identificar/exibir,
    não para o produto funcionar."""
    colunas_proibidas = ("nome", "cpf", "email")
    migrations_dir = REPO_ROOT / "supabase" / "migrations"
    assert migrations_dir.exists(), "supabase/migrations/ não existe (Passo 5)"

    for migration in migrations_dir.glob("*.sql"):
        linhas_sem_comentario = (
            linha.split("--", 1)[0]
            for linha in migration.read_text(encoding="utf-8").lower().splitlines()
        )
        texto = "\n".join(linhas_sem_comentario)
        encontradas = [c for c in colunas_proibidas if c in texto]
        assert not encontradas, (
            f"{migration.name} referencia coluna(s) proibida(s) por LGPD: {encontradas}"
        )


def test_export_treino_sem_coluna_proibida_de_pii():
    """`src/export_treino.py` (Passo 9.0) monta um CSV a partir de dados reais
    de produção -- mesmo critério de PII do schema (`test_migrations_sql_...`),
    aplicado agora ao código que gera o dataset de treino. `telefone` é a
    exceção deliberada no schema (Passo 7, contato de envio), mas o export
    nunca deveria emiti-la -- por isso ela some da lista de proibidas do
    schema e volta a ser proibida aqui."""
    colunas_proibidas = ("nome", "cpf", "email", "telefone")
    caminho = REPO_ROOT / "src" / "export_treino.py"
    assert caminho.exists(), "src/export_treino.py não existe (Passo 9.0)"

    texto = caminho.read_text(encoding="utf-8").lower()
    # COLUNAS_SAIDA é a lista literal de colunas que o CSV final carrega --
    # verificação estrutural, não um grep frágil sobre o arquivo inteiro
    # (que teria "telefone" nos comentários explicando por que ele não entra).
    inicio = texto.index("colunas_saida = [")
    fim = texto.index("]", inicio)
    bloco_colunas = texto[inicio:fim]

    encontradas = [c for c in colunas_proibidas if c in bloco_colunas]
    assert not encontradas, (
        f"COLUNAS_SAIDA de export_treino.py referencia coluna(s) proibida(s) por LGPD: "
        f"{encontradas}"
    )


_METODOS_DE_LOG = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)
# Espelha src/logging_config.py::CHAVES_PROIBIDAS. Duplicado de propósito: esta
# guarda é estática e precisa falhar mesmo que alguém quebre o import do módulo
# de logging -- é a evidência do SLO §6 (ADR-006), não pode depender de o código
# que ela audita estar importável.
_IDENTIFICADORES_PROIBIDOS_EM_LOG = frozenset(
    {"sobrenome", "nome", "name", "cpf", "email", "e_mail", "telefone", "celular", "phone"}
)


def _e_chamada_de_log(no: ast.Call) -> bool:
    """`logger.info(...)`, `logging.warning(...)`, `log.error(...)` -- reconhece
    pelo nome do objeto, que é a convenção usada em todo `src/`."""
    func = no.func
    if not isinstance(func, ast.Attribute) or func.attr not in _METODOS_DE_LOG:
        return False
    alvo = func.value
    nome_do_objeto = alvo.id if isinstance(alvo, ast.Name) else getattr(alvo, "attr", "")
    return nome_do_objeto in {"logger", "logging", "log", "_logger"}


def _identificadores_da_chamada(no: ast.Call):
    """Nomes de variável, atributos, chaves de dict e índices de subscript que
    aparecem nos argumentos da chamada.

    Só identificadores -- não o texto das mensagens. `logger.warning("telefone
    inválido")` é uma mensagem sobre o campo, não o valor dele, e flagrar isso
    treinaria a autora a ignorar a guarda. O que pega PII de verdade é
    `paciente["telefone"]`, `paciente.telefone`, `telefone` e
    `extra={"telefone": ...}`.
    """
    for filho in ast.walk(no):
        if isinstance(filho, ast.Name):
            yield filho.id
        elif isinstance(filho, ast.Attribute):
            yield filho.attr
        elif isinstance(filho, ast.Subscript) and isinstance(filho.slice, ast.Constant):
            if isinstance(filho.slice.value, str):
                yield filho.slice.value
        elif isinstance(filho, ast.Dict):
            for chave in filho.keys:
                if isinstance(chave, ast.Constant) and isinstance(chave.value, str):
                    yield chave.value


def _proibido(identificador: str) -> bool:
    alvo = identificador.lower()
    return any(
        alvo == proibido or alvo.startswith(f"{proibido}_") or alvo.endswith(f"_{proibido}")
        for proibido in _IDENTIFICADORES_PROIBIDOS_EM_LOG
    )


def test_nenhuma_chamada_de_log_em_src_referencia_campo_de_pii():
    """Guarda preventiva do SLO §6 ("0 ocorrências de PII em log"), na forma que
    o [ADR-006](../docs/adr/adr-006-observabilidade.md) definiu: o log do HF
    Space é efêmero, então não existe varredura a posteriori que sirva de
    evidência -- a prova tem de ser sobre o código.

    Complementa o filtro de `src/logging_config.py` em vez de duplicá-lo: o
    filtro é a rede de segurança para o log de terceiros (uvicorn/httpx), que
    não temos como reescrever; esta guarda barra o caso que não deveria nem
    chegar lá, que é código nosso passando PII para uma chamada de log.

    Mesmo espírito de `test_migrations_sql_sem_coluna_proibida_de_pii`: a
    proibição vale desde a estrutura, não por convenção de revisão.
    """
    encontrados = []
    for arquivo in sorted((REPO_ROOT / "src").rglob("*.py")):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call) or not _e_chamada_de_log(no):
                continue
            for identificador in _identificadores_da_chamada(no):
                if _proibido(identificador):
                    encontrados.append(
                        f"{arquivo.relative_to(REPO_ROOT)}:{no.lineno} -> {identificador}"
                    )

    assert not encontrados, (
        "Chamada(s) de log em src/ referenciando campo proibido por LGPD "
        f"(BRIEFING.md: 'Sem PII em logs. Nunca.'): {encontrados}"
    )


def test_a_guarda_de_log_realmente_detecta_pii():
    """Controle negativo da guarda acima. Uma varredura estática que passa
    porque deixou de reconhecer as chamadas seria pior que não existir: daria
    verde sem verificar nada, e é ela que sustenta o SLO §6 no pitch.

    Os casos "ok" são igualmente parte do contrato -- mensagem *sobre* o campo e
    identificador interno (uuid, nome de classe de exceção) não podem virar
    falso positivo, ou a guarda passa a ser ignorada."""
    deve_flagrar = [
        'logger.info(f"enviando para {telefone}")',
        'logger.warning("falha", extra={"telefone": t})',
        'logger.info("agendamento %s", paciente["telefone"])',
        'logger.info("cadastro", extra={"paciente_nome": n})',
        'logger.error("erro %s", paciente.cpf)',
        'logging.info("contato %s", email)',
    ]
    nao_deve_flagrar = [
        'logger.info("telefone inválido -- confira os números")',
        'logger.info("ok", extra={"id_agendamento": a, "excecao": e})',
        'print(f"{telefone}")',
    ]

    for codigo in deve_flagrar:
        no = ast.parse(codigo).body[0].value
        achados = [i for i in _identificadores_da_chamada(no) if _proibido(i)]
        assert _e_chamada_de_log(no) and achados, f"guarda deixou passar: {codigo}"

    for codigo in nao_deve_flagrar:
        no = ast.parse(codigo).body[0].value
        achados = (
            [i for i in _identificadores_da_chamada(no) if _proibido(i)]
            if _e_chamada_de_log(no)
            else []
        )
        assert not achados, f"falso positivo da guarda: {codigo}"


def test_architecture_md_sem_placeholder_generico():
    """docs/architecture.md é um template genérico -- este teste falha se algum
    placeholder tipo '[e.g., ...]' ainda não foi preenchido com conteúdo real
    do projeto (ver Passo 0 do PLANO-IMPLEMENTACAO.md)."""
    texto = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "[e.g.," not in texto, (
        "docs/architecture.md ainda contém placeholder(s) '[e.g., ...]' não preenchido(s)"
    )
