"""
Checagens de coerência estrutural do repositório: consistência entre
.gitignore, arquivos rastreados pelo git, e referências do dvc.yaml.
Não testam lógica de negócio -- servem para pegar "drift" de configuração
(ex.: artefato binário commitado por engano, dependência referenciada mas
ausente).
"""
import ast
import re
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
            # Dataset versionado por DVC (`<arquivo>.dvc` no git, conteúdo no
            # remote): no CI só o model.pkl é baixado, e o que
            # garante a existência do dado é o próprio `.dvc`.
            if (REPO_ROOT / f"{dep}.dvc").exists():
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
    -- cada arquivo próprio (ignorando linhas "-r ...", que só
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
    dockerfile (`%cd%`, que o mount antigo usava, não expandia no runner
    Linux).
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
            "montado à mão volta a quebrar no runner Linux"
        )
        # Sem estas deps, mudar a imagem ou o mount não invalidaria o stage.
        for dep in ("dockerfile", "docker-compose.yml"):
            assert dep in stage["deps"], f"stage '{nome}' não declara '{dep}' em deps"


# Exceções deliberadas à minimização de PII, cada uma amarrada à migration que
# a declara: a coluna é permitida NAQUELE arquivo e em nenhum outro. Uma lista
# solta de "colunas permitidas" deixaria qualquer migration futura acrescentar
# `nome_completo` em outra tabela sem ninguém notar -- o que é exatamente o
# tipo de drift que esta guarda existe para pegar.
COLUNAS_DE_PII_PERMITIDAS_POR_MIGRATION = {
    # Sem contato de envio não existe lembrete pago, que é o produto.
    "telefone": "20260920020000_telefone_paciente.sql",
    # 2026-09-28 (ADR-007): a fila do dia é operada por uma pessoa, que precisa
    # chamar o paciente pelo nome -- o hash sha256 não serve para isso.
    "nome_completo": "20260928000000_nome_paciente.sql",
}


def test_migrations_sql_sem_coluna_proibida_de_pii():
    """Minimização de PII por design (BRIEFING.md, architecture.md §4.1):
    `pacientes` guarda só id_paciente_externo + demografia não identificável,
    mais as exceções deliberadas de `COLUNAS_DE_PII_PERMITIDAS_POR_MIGRATION`.
    Guarda automatizável desde o schema, não só por convenção de código.

    **CPF e e-mail seguem proibidos sem exceção** -- servem só para
    identificar, nunca para o produto funcionar, e o CPF já está representado
    pelo hash em `id_paciente_externo`.

    A guarda é por par (coluna, arquivo), não por lista de colunas: `telefone`
    só pode aparecer na migration que o introduziu, e `nome_completo` só na
    dele. Em qualquer outro arquivo, `nome_completo` volta a cair na proibição
    de `nome` (a checagem é por substring, então a variação composta não
    escapa)."""
    colunas_proibidas = ("nome", "cpf", "email", "telefone")
    migrations_dir = REPO_ROOT / "supabase" / "migrations"
    assert migrations_dir.exists(), "supabase/migrations/ não existe"

    for migration in migrations_dir.glob("*.sql"):
        linhas_sem_comentario = (
            linha.split("--", 1)[0]
            for linha in migration.read_text(encoding="utf-8").lower().splitlines()
        )
        texto = "\n".join(linhas_sem_comentario)
        # Retira do texto só as exceções que ESTE arquivo tem direito de
        # declarar, antes de procurar o que sobrou.
        for coluna, arquivo in COLUNAS_DE_PII_PERMITIDAS_POR_MIGRATION.items():
            if migration.name == arquivo:
                texto = texto.replace(coluna, "")

        encontradas = [c for c in colunas_proibidas if c in texto]
        assert not encontradas, (
            f"{migration.name} referencia coluna(s) proibida(s) por LGPD: {encontradas}. "
            "Exceção nova exige entrada em COLUNAS_DE_PII_PERMITIDAS_POR_MIGRATION, "
            "ADR próprio e atualização de docs/LGPD.md §2."
        )


def test_excecoes_de_pii_apontam_para_migrations_que_existem():
    """Se a migration da exceção for renomeada ou removida, a entrada vira um
    passe livre silencioso: a coluna deixaria de ser retirada de qualquer
    arquivo e, pior, nada acusaria a inconsistência."""
    migrations_dir = REPO_ROOT / "supabase" / "migrations"
    for coluna, arquivo in COLUNAS_DE_PII_PERMITIDAS_POR_MIGRATION.items():
        caminho = migrations_dir / arquivo
        assert caminho.exists(), (
            f"exceção de PII para '{coluna}' aponta para {arquivo}, que não existe"
        )
        texto = caminho.read_text(encoding="utf-8").lower()
        assert f"add column {coluna}" in texto, (
            f"{arquivo} não declara a coluna '{coluna}' que a exceção autoriza"
        )


def test_export_treino_sem_coluna_proibida_de_pii():
    """`src/export_treino.py` monta um CSV a partir de dados reais
    de produção -- mesmo critério de PII do schema (`test_migrations_sql_...`),
    aplicado agora ao código que gera o dataset de treino. `telefone` é a
    exceção deliberada no schema (contato de envio), mas o export
    nunca deveria emiti-la -- por isso ela some da lista de proibidas do
    schema e volta a ser proibida aqui."""
    colunas_proibidas = ("nome", "cpf", "email", "telefone")
    caminho = REPO_ROOT / "src" / "export_treino.py"
    assert caminho.exists(), "src/export_treino.py não existe"

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


def test_nome_do_paciente_nao_vaza_pelas_portas_de_saida():
    """`pacientes.nome_completo` passou a ser gravado em 2026-09-28 (ADR-007)
    para a "Fila do dia" ser operável por quem atende. A autorização é para
    aquela tela, e só para ela -- este teste guarda as portas que levariam o
    nome para fora, cada uma com um mecanismo diferente de saída:

    - **select do job D-2**: traria o nome para o processo que monta a mensagem
      enviada à Infobip, um processador externo;
    - **schema da API**: `/predict` é a porta de entrada para integrações
      externas ao Saúde Já (diagrama C2 de architecture.md);
    - **export de treino**: o dataset vai para o remote do DVC, fora do Brasil
      (LGPD.md §4).

    O export já tem guarda própria (`test_export_treino_...`), por lista de
    colunas; aqui a checagem é por ausência do termo nos arquivos que definem
    as outras duas fronteiras."""
    portas = {
        "src/db/repositories.py": ("_COLUNAS_AGENDAMENTO_PARA_INFERENCIA",),
        "src/api/schemas.py": None,  # arquivo inteiro: nenhum campo de nome
    }

    for caminho, trechos in portas.items():
        texto = (REPO_ROOT / caminho).read_text(encoding="utf-8")
        alvos = []
        if trechos is None:
            alvos = [texto]
        else:
            for nome_do_trecho in trechos:
                inicio = texto.index(nome_do_trecho)
                alvos.append(texto[inicio : texto.index(")", inicio)])

        for alvo in alvos:
            # Comentário fora: os dois arquivos explicam por que o nome NÃO
            # entra, e um grep cru sobre o texto inteiro acusaria a explicação.
            sem_comentario = "\n".join(
                linha.split("#", 1)[0] for linha in alvo.splitlines()
            )
            assert "nome" not in sem_comentario.lower(), (
                f"{caminho} referencia 'nome' numa porta de saída -- o nome do paciente "
                "é autorizado só na 'Fila do dia' (ADR-007)"
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
    do projeto."""
    texto = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "[e.g.," not in texto, (
        "docs/architecture.md ainda contém placeholder(s) '[e.g., ...]' não preenchido(s)"
    )


# --- workflows do GitHub Actions ----------------------------------------------

WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _passos(workflow: str) -> list[dict]:
    conteudo = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return [passo for job in conteudo["jobs"].values() for passo in job.get("steps", [])]


def test_actions_de_terceiros_fixadas_por_sha():
    """A action de deploy recebe o HF_TOKEN; uma tag pode ser
    movida para outro código, um SHA não. O Dependabot atualiza os SHAs."""
    soltas = [
        f"{arquivo.name}: {passo['uses']}"
        for arquivo in WORKFLOWS.glob("*.yml")
        for passo in _passos(arquivo.name)
        if "uses" in passo
        and not passo["uses"].startswith("./")
        and not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", passo["uses"])
    ]
    assert soltas == [], f"actions sem SHA fixo: {soltas}"


def test_dvc_pull_sempre_com_alvo_fora_do_re_treino():
    """`dvc pull` sem alvo traria o dataset de treino -- e, no
    deploy, ele iria para o Space junto. Só o re-treino precisa do dado."""
    sem_alvo = [
        f"{arquivo.name}: {linha.strip()}"
        for arquivo in WORKFLOWS.glob("*.yml")
        if arquivo.name != "retrain.yml"
        for linha in arquivo.read_text(encoding="utf-8").splitlines()
        if re.search(r"\bdvc pull\s*$", linha)
    ]
    assert sem_alvo == [], f"dvc pull sem alvo: {sem_alvo}"


def test_deploy_sobe_o_staging_e_nao_o_checkout():
    sync = [p for p in _passos("deploy.yml") if "hub-sync" in p.get("uses", "")]
    assert len(sync) == 1
    assert sync[0]["with"]["subdirectory"] == "build/space"


def test_deploy_migra_o_banco_antes_do_sync_e_depois_da_guarda_do_campeao():
    """Migration antes do sync (o Space rebuilda ao receber os
    arquivos). Guarda do campeão antes da migration: deploy que não vai
    acontecer não deixa o banco migrado."""
    nomes = [p.get("name", p.get("uses", "")) for p in _passos("deploy.yml")]
    guarda = next(i for i, n in enumerate(nomes) if "campeao" in n)
    migracao = next(i for i, n in enumerate(nomes) if "Migrations" in n)
    sync = next(i for i, n in enumerate(nomes) if "hub-sync" in n or "Sync" in n)
    assert guarda < migracao < sync


def test_dependencias_do_dvc_estao_em_lf_no_checkout():
    """O DVC 3 hasheia os BYTES das dependências dos stages. Com CRLF no
    checkout de Windows e LF no runner Linux, o md5 do dvc.lock nunca batia
    no GitHub Actions -- o re-treino reexecutava tudo mesmo sem dado novo e o
    código 2 do gate não disparava. O
    `.gitattributes` força LF no checkout; este teste pega o arquivo novo que
    um editor de Windows tenha gravado com CRLF antes de ele virar commit."""
    dvc_yaml = yaml.safe_load((REPO_ROOT / "dvc.yaml").read_text(encoding="utf-8"))
    com_crlf = sorted(
        {
            dep
            for stage in dvc_yaml["stages"].values()
            for dep in stage.get("deps", [])
            if (REPO_ROOT / dep).is_file()
            and not (REPO_ROOT / f"{dep}.dvc").exists()
            and b"\r\n" in (REPO_ROOT / dep).read_bytes()
        }
    )
    assert com_crlf == [], (
        f"dependências do dvc.yaml com CRLF: {com_crlf} -- converta para LF "
        "(o .gitattributes já faz isso no checkout)"
    )


# --- canário do modelo (ADR-009) ------------------------------------------------


def test_canario_nao_aciona_deploy_mas_a_promocao_aciona():
    """Abrir ou reverter um canário não muda nada no Space (o canário não
    entra no staging); promover muda champion_metrics.json e dvc.lock, que
    precisam continuar disparando o deploy."""
    conteudo = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text(encoding="utf-8"))
    # PyYAML lê a chave `on:` como o booleano True.
    ignorados = conteudo[True]["push"]["paths-ignore"]
    assert {"data/canario.json", "data/canario/**", "data/canario_historico.json"} <= set(
        ignorados
    )
    assert not any(
        re.fullmatch(p.replace("**", ".*").replace("*", "[^/]*"), alvo)
        for p in ignorados
        for alvo in ("data/champion_metrics.json", "dvc.lock", "data/consultas-treino.csv.dvc")
    )


def test_job_d2_nao_morre_se_o_modelo_do_canario_nao_baixar():
    """Um canário inalcançável não pode custar o lembrete de ninguém: o job
    segue com o campeão e falha no fim (src/canario.py::preparar_para_job)."""
    texto = (WORKFLOWS / "job_d2.yml").read_text(encoding="utf-8")
    linha = next(
        i for i, t in enumerate(texto.splitlines()) if "dvc pull data/canario/model.pkl" in t
    )
    seguinte = texto.splitlines()[linha + 1]
    assert "||" in texto.splitlines()[linha] or seguinte.strip().startswith("||")


def test_canario_e_encerrado_sempre_por_pr_nunca_por_push_em_main():
    texto = (WORKFLOWS / "canario.yml").read_text(encoding="utf-8")
    assert "gh pr create" in texto
    assert 'git push origin "$BRANCH"' in texto
    assert "git push origin main" not in texto


# --- ambientes dev/production e versão da release ---------------------------------

# Secrets que só existem nos environments: um job que os lê sem declarar o
# environment receberia vazio (ou, pior, um secret de repositório esquecido).
_SECRETS_DE_AMBIENTE = re.compile(
    r"secrets\.(SUPABASE_URL|SUPABASE_SECRET_KEY|SUPABASE_DB_PASSWORD|SUPABASE_PROJECT_REF"
    r"|SUPABASE_ACCESS_TOKEN|INFOBIP_\w+|HF_TOKEN|HEALTHCHECKS_\w+"
    r"|AZURE_STORAGE_CONNECTION_STRING)\b"
)


def test_jobs_com_secret_de_ambiente_declaram_o_environment():
    """Separação dev/prod: o Supabase, o Space, a Infobip e a chave de
    escrita do Azure ficam nos environments. Os jobs agendados usam
    `production` sem criar deploy (`deployment: false`); o deploy escolhe o
    environment pelo branch."""
    for arquivo in sorted(WORKFLOWS.glob("*.yml")):
        conteudo = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        for nome, job in conteudo["jobs"].items():
            if not _SECRETS_DE_AMBIENTE.search(yaml.safe_dump(job)):
                continue
            ambiente = job.get("environment")
            assert ambiente, f"{arquivo.name}::{nome} lê secret de ambiente sem environment"
            if arquivo.name == "deploy.yml":
                assert ambiente == "${{ github.ref_name == 'main' && 'production' || 'dev' }}"
            else:
                assert ambiente == {"name": "production", "deployment": False}, (
                    f"{arquivo.name}::{nome}"
                )


def test_filtro_de_deploy_do_ci_espelha_o_paths_ignore_do_deploy():
    """O job `versao` do CI só exige versão nova quando o merge vai deployar.
    Se o filtro e o `paths-ignore` divergirem, ou um PR que deploya passa sem
    versão (e o deploy falha depois do merge), ou um PR só de docs é barrado."""
    deploy = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text(encoding="utf-8"))
    ignorados = deploy[True]["push"]["paths-ignore"]
    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    passo = next(p for p in ci["jobs"]["mudancas"]["steps"] if p.get("id") == "deploya")
    filtro = re.compile(passo["env"]["IGNORADOS_PELO_DEPLOY"])

    def glob_para_regex(glob: str) -> str:
        return re.escape(glob).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")

    def ignorado_pelo_deploy(caminho: str) -> bool:
        return any(re.fullmatch(glob_para_regex(p), caminho) for p in ignorados)

    for caminho in (
        "docs/logs/CHANGELOG.md",
        "docs/adr/adr-009-canario-do-modelo.md",
        "data/canario.json",
        "data/canario/model.pkl.dvc",
        "data/canario_historico.json",
        "data/champion_metrics.json",
        "dvc.lock",
        "src/jobs/inferencia_diaria.py",
        "README.md",
        "data/canario.json.salvo",
    ):
        assert bool(filtro.search(caminho)) == ignorado_pelo_deploy(caminho), caminho


def test_release_so_depois_do_space_no_ar_e_so_em_main():
    deploy = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text(encoding="utf-8"))
    release = deploy["jobs"]["release"]
    assert release["needs"] == "deploy"
    assert "github.ref_name == 'main'" in release["if"]
    nomes = [p.get("name", "") for p in deploy["jobs"]["deploy"]["steps"]]
    versao = next(i for i, n in enumerate(nomes) if "Versao da release" in n)
    migracao = next(i for i, n in enumerate(nomes) if "Migrations" in n)
    assert versao < migracao, "versão inválida tem de parar o deploy antes de migrar o banco"
