"""
Cria a conta de um funcionário da clínica no Supabase Auth (ADR-008).

É a única porta de entrada de conta: o cadastro aberto fica desligado no
projeto (`enable_signup = false`), então a tela de login da interface não
oferece "criar conta" -- quem cria é o administrador, rodando este script.

Usa a chave secreta do backend (`SUPABASE_URL`/`SUPABASE_SECRET_KEY` do `.env`)
pela API de admin do Supabase Auth, que ignora a trava de cadastro. A conta
nasce com o e-mail já confirmado: o projeto não tem SMTP próprio configurado, e
um e-mail de confirmação que não chega deixaria a conta inutilizável.

A senha é pedida no terminal, sem eco, e nunca passa por argumento de linha de
comando -- argumento fica no histórico do shell e na lista de processos.

Uso:
    python scripts/criar_funcionario.py recepcao@clinica.com.br

Para desativar alguém: Supabase Dashboard -> Authentication -> Users ->
"Delete user" (ou "Ban user", que preserva o histórico). A sessão aberta na
interface cai no próximo login; a que já está aberta dura até a inatividade
(30 min) ou até a pessoa clicar em "Sair".
"""
import argparse
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from supabase_auth.errors import AuthApiError, AuthWeakPasswordError  # noqa: E402

from db.client import ConfiguracaoSupabaseAusente, obter_client  # noqa: E402

TAMANHO_MINIMO_SENHA = 8  # mesmo mínimo de `supabase/config.toml` (auth)


def _pedir_senha() -> str:
    senha = getpass.getpass("Senha do funcionário: ")
    if len(senha) < TAMANHO_MINIMO_SENHA:
        sys.exit(f"Senha curta demais: use pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")
    if getpass.getpass("Repita a senha: ") != senha:
        sys.exit("As senhas não conferem.")
    return senha


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("email", help="e-mail de login do funcionário")
    args = parser.parse_args()

    email = args.email.strip().lower()
    senha = _pedir_senha()

    try:
        resposta = obter_client().auth.admin.create_user(
            {"email": email, "password": senha, "email_confirm": True}
        )
    except ConfiguracaoSupabaseAusente as exc:
        sys.exit(str(exc))
    except AuthWeakPasswordError as exc:
        sys.exit(f"O Supabase recusou a senha por ser fraca: {exc.message}")
    except AuthApiError as exc:
        if exc.code == "email_exists":
            sys.exit("Já existe uma conta com esse e-mail.")
        sys.exit(f"O Supabase recusou a criação da conta ({exc.status}, {exc.code}): {exc.message}")

    print(f"Conta criada. id do usuário no Supabase Auth: {resposta.user.id}")


if __name__ == "__main__":
    main()
