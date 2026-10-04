"""
SaúdeJá — cliente único do Supabase.

Wrapper fino sobre `supabase-py`: nada aqui sabe sobre pacientes/agendamentos
/predições (isso é `src/db/repositories.py`) -- só resolve `SUPABASE_URL`/
`SUPABASE_SECRET_KEY` e devolve um client pronto.

`SUPABASE_SECRET_KEY` é a chave secreta (`sb_secret_...`, formato novo de API
key do Supabase que substitui o JWT `service_role`) -- nunca a `publishable`
(`sb_publishable_...`, equivalente à antiga `anon`). O backend (UI em
processo, job D-2) roda fora do navegador e precisa ignorar RLS (ver
comentário em `supabase/migrations/20260919000000_init.sql` -- todas as
tabelas têm RLS habilitado sem policies, então a chave publishable não
enxergaria nada). Nunca versionar a chave: só via `.env`/secret do ambiente.
"""
import os

from supabase import Client, ClientOptions, create_client

import config_projeto  # noqa: F401 -- side effect: carrega .env em os.environ

_cliente: Client | None = None


class ConfiguracaoSupabaseAusente(Exception):
    """SUPABASE_URL/SUPABASE_SECRET_KEY não configuradas no ambiente."""


def obter_client() -> Client:
    """Client único reusado entre chamadas -- `create_client` abre um
    `httpx.Client` interno, recriar a cada request seria desperdício."""
    global _cliente
    if _cliente is None:
        _cliente = create_client(_url_supabase(), _key_supabase())
    return _cliente


def criar_client_autenticacao() -> Client:
    """Client DESCARTÁVEL, um por tentativa de login (ADR-008) -- nunca o
    singleton de `obter_client`.

    O `supabase-py` escuta os próprios eventos de auth: depois de um
    `sign_in_with_password` bem-sucedido, ele troca o header `Authorization`
    do client pelo JWT do usuário logado. No singleton isso faria duas coisas
    erradas ao mesmo tempo: (1) todas as consultas seguintes do backend
    passariam a rodar como `authenticated` em vez da chave secreta, e o RLS
    sem policies devolveria tabela vazia; (2) como o processo do Streamlit é
    um só para todos os navegadores conectados, o login de um funcionário
    mudaria a identidade das consultas de todos os outros.

    `persist_session`/`auto_refresh_token` desligados: a sessão do Supabase
    Auth só serve para provar a senha, não para acessar dado -- a UI não
    guarda o token, então não há o que persistir nem thread de refresh para
    deixar rodando.
    """
    return create_client(
        _url_supabase(),
        _key_supabase(),
        options=ClientOptions(auto_refresh_token=False, persist_session=False),
    )


def _url_supabase() -> str:
    url = os.environ.get("SUPABASE_URL")
    if not url:
        raise ConfiguracaoSupabaseAusente(
            "SUPABASE_URL não definida -- configure no .env (ver .env.example)"
        )
    return url


def _key_supabase() -> str:
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not key:
        raise ConfiguracaoSupabaseAusente(
            "SUPABASE_SECRET_KEY não definida -- configure no .env (ver .env.example); "
            "use a chave secreta (sb_secret_...), não a publishable"
        )
    return key
