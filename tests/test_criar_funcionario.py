"""
SaúdeJá — smoke de `scripts/criar_funcionario.py` (docs/architecture.md §7).

É a única porta de entrada de conta da equipe e roda uma vez por funcionário,
na configuração do primeiro deploy -- contra o projeto de produção, sem
ensaio. Sem teste até aqui. O Supabase Auth é trocado por um falso: o que se
trava é o contrato do script (senha nunca por argumento, mínimo de 8, e-mail
normalizado, conta já confirmada) e que toda recusa do Supabase vira mensagem
legível em vez de traceback.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from supabase_auth.errors import AuthApiError, AuthUnknownError, AuthWeakPasswordError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import criar_funcionario
from db.client import ConfiguracaoSupabaseAusente


class _AdminFalso:
    def __init__(self, erro=None):
        self.erro = erro
        self.criados = []

    def create_user(self, atributos):
        if self.erro:
            raise self.erro
        self.criados.append(atributos)
        return SimpleNamespace(user=SimpleNamespace(id="uuid-do-funcionario"))


@pytest.fixture
def rodar(monkeypatch):
    def _rodar(email, senhas, erro=None):
        admin = _AdminFalso(erro)
        respostas = iter(senhas)
        monkeypatch.setattr(criar_funcionario.getpass, "getpass", lambda prompt: next(respostas))
        monkeypatch.setattr(
            criar_funcionario,
            "obter_client",
            lambda: SimpleNamespace(auth=SimpleNamespace(admin=admin)),
        )
        monkeypatch.setattr(sys, "argv", ["criar_funcionario.py", email])
        criar_funcionario.main()
        return admin

    return _rodar


def test_cria_conta_confirmada_com_email_normalizado(rodar, capsys):
    admin = rodar("  Recepcao@Clinica.com.br ", ["senha-forte", "senha-forte"])

    assert admin.criados == [
        {"email": "recepcao@clinica.com.br", "password": "senha-forte", "email_confirm": True}
    ]
    assert "uuid-do-funcionario" in capsys.readouterr().out


def test_senha_curta_para_antes_de_chamar_o_supabase(rodar):
    with pytest.raises(SystemExit, match="pelo menos 8"):
        rodar("a@b.com", ["curta"])


def test_senhas_diferentes_param_antes_de_chamar_o_supabase(rodar):
    with pytest.raises(SystemExit, match="não conferem"):
        rodar("a@b.com", ["senha-forte", "senha-outra"])


@pytest.mark.parametrize(
    "erro, mensagem",
    [
        (AuthApiError("User already registered", 422, "email_exists"), "Já existe uma conta"),
        (AuthWeakPasswordError("fraca", 422, ["pwned"]), "senha por ser fraca"),
        (AuthApiError("boom", 500, "unexpected_failure"), r"\(500, unexpected_failure\)"),
        (ConfiguracaoSupabaseAusente("defina SUPABASE_URL"), "SUPABASE_URL"),
    ],
)
def test_recusa_do_supabase_vira_mensagem_e_nao_traceback(rodar, erro, mensagem):
    with pytest.raises(SystemExit, match=mensagem):
        rodar("a@b.com", ["senha-forte", "senha-forte"], erro=erro)


def test_resposta_que_nao_e_json_mostra_status_url_e_corpo(rodar):
    # Mesmo encadeamento do `_request` do supabase_auth: HTTPStatusError com
    # corpo não-JSON, convertido em AuthUnknownError dentro do `except`.
    requisicao = httpx.Request("POST", "https://exemplo.supabase.co/rest/v1/auth/v1/admin/users")
    resposta = httpx.Response(400, text="<html>Bad Request</html>", request=requisicao)
    try:
        try:
            resposta.raise_for_status()
        except httpx.HTTPStatusError as http_erro:
            raise AuthUnknownError(str(http_erro), ValueError("não é JSON"))
    except AuthUnknownError as erro_da_lib:
        erro = erro_da_lib

    with pytest.raises(SystemExit, match=r"(?s)respondeu 400.*rest/v1/auth.*<html>Bad Request"):
        rodar("a@b.com", ["senha-forte", "senha-forte"], erro=erro)
