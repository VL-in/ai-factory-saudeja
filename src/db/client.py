"""
SaúdeJá — cliente único do Supabase (Passo 5).

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

from supabase import Client, create_client

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
