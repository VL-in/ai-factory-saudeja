## Relatório de rollback RB-001: ensaio de rollback de código em dev
Ambiente: `dev`
Tipo: ensaio planejado. Nenhum incidente, nenhum usuário afetado.
Data: 05/10/2026
Tempo de rollback: 6 min 56 s, do push do revert até o Space de dev saudável no commit novo
Resultado: sucesso. O Space voltou byte a byte ao estado anterior.

# Resumo
| | |
|---|---|
| Deploy revertido | `2468d21`, comentário marcador em `src/config_projeto.py` |
| Commit de rollback | `be9cbc0` (`git revert 2468d21`) |
| Runs do Actions | `37393444276` (deploy revertido), `37394040519` (rollback) |
| Deployments (environment `dev`) | `6872531293` (deploy revertido), `6872641664` (rollback) |
| Commits do Space de dev | `3b95c574c3` (antes), `861ea8f834` (deploy revertido), `0f57df8d39` (rollback) |
| Produção | não foi tocada |

# Objetivo
Provar que um deploy ruim em `dev` volta ao estado anterior pelo caminho que o `deploy.yml` oferece, e medir quanto tempo isso leva.

O `deploy.yml` não tem botão de rollback. Disparar o `workflow_dispatch` numa tag antiga muda o `github.ref_name` para a tag, o job cai no environment `dev`, e a regra de branch do environment recusa a execução. Por isso, rollback aqui é sempre um *roll forward*: um `git revert` que passa pelo mesmo CI → migration → sync → smoke de qualquer deploy.

# Escopo: a ação de menor impacto
- **Mudança simulada:** um comentário marcador em `src/config_projeto.py`. Não altera comportamento, não toca a UI nem o banco. Mas está em `src/`, que entra no staging, e por isso dispara o deploy. Uma mudança só em `docs/` cairia no `paths-ignore`.
- **Fora do escopo, de propósito:** migration. O `supabase db push` só aplica migrations para frente, então uma migration de ensaio deixaria o schema de dev alterado para sempre. Fica para o RB-002 (ver Próximos passos).
- **Produção:** não foi tocada. O commit e o revert se anulam (`git diff 2468d21~1 be9cbc0` vazio), então chegam a `main` como alteração nula.

# Linha do tempo (horário de Brasília, relógio do GitHub)
| Hora | Evento |
|---|---|
| 21:20:04 | Começa o run `37393444276` (commit `2468d21`, marcador). CI em 4 min 55 s |
| 21:25:02–21:26:19 | Job `deploy`. O Space de dev vai para o commit `861ea8f834`: `RUNNING`, health 200, marcador presente |
| 21:26 | Decisão de reverter: `git revert 2468d21` gera o commit `be9cbc0`, enviado em `dev` |
| 21:26:45 | Começa o run `37394040519`. CI em 5 min 29 s |
| 21:32:14–21:33:40 | Job `deploy`. O Space de dev vai para o commit `0f57df8d39`: `RUNNING`, health 200, marcador ausente |

# Verificação
| Momento | Commit do Space | Arquivos | Digest da árvore (path + oid) |
|---|---|---|---|
| Antes do ensaio | `3b95c574c3` | 37 | `620a2d8bc287` |
| Deploy revertido | `861ea8f834` | 37 | `8a6c53fe38fa` |
| Depois do rollback | `0f57df8d39` | 37 | `620a2d8bc287` |

A árvore depois do rollback é idêntica à de antes: mesmos arquivos, mesmos oids. O commit do Space é novo porque o `hub-sync` sempre cria um, mas o conteúdo é exatamente o anterior. Para saber o que está no ar, compare o digest da árvore, não o SHA do commit do Space.

# Como rastrear este rollback
A cadeia de rastreio vai do git ao Space. Cada elo aponta para o seguinte:

```
commit be9cbc0 ("This reverts commit 2468d21…")
  └─ run 37394040519 do Deploy (HF Space)
       ├─ deployment 6872641664 (environment dev, status success com link para o job)
       └─ passo "Sync do staging para o Space": linha `url:` com o commit 0f57df8d39 do Space
            └─ histórico do Space de dev (Files → History)
```

Nos comandos abaixo, o id do Space vem da variável do environment e nunca fica escrito no repositório:

```bash
REPO=VL-in/ai-factory-saudeja
HF_SPACE_ID=$(gh variable get HF_SPACE_ID --env dev -R $REPO)
```

## 1. Git: o que foi revertido
O commit de revert carrega o SHA do commit original no corpo da mensagem.

```bash
git show --stat be9cbc0               # corpo: "This reverts commit 2468d218…"
git log --oneline 2468d21~1..be9cbc0  # o par: marcador e revert
git diff 2468d21~1 be9cbc0            # vazio: o rollback anulou a mudança
git log --oneline --grep "^(revert)" dev  # todos os rollbacks (convenção do tipo no assunto)
```

Em `main`, o PR de rollback e a Release `vX.Y.Z` com o item no CHANGELOG também viram registro. Não se aplica a este ensaio, que ficou em `dev`.

## 2. GitHub Actions: o log de cada etapa
Na interface: *Actions → Deploy (HF Space)*, com filtro pelo branch `dev`.
- https://github.com/VL-in/ai-factory-saudeja/actions/runs/37393444276
- https://github.com/VL-in/ai-factory-saudeja/actions/runs/37394040519

```bash
gh run list -R $REPO --workflow deploy.yml --branch dev -L 5
gh run view 37394040519 -R $REPO --json headSha,conclusion,createdAt,updatedAt
gh run view 37394040519 -R $REPO --log | grep -E "url: https://huggingface.co|\[ok\] Space"
```

O último comando mostra o elo que falta no Space. O passo "Sync do staging para o Space" imprime a URL do commit criado no Space (`.../commit/0f57df8d39…`), e o smoke imprime `[ok] Space no commit novo e saudável`.

**Atenção:** os logs do Actions expiram pela retenção do repositório (90 dias por padrão). Os deployments, os commits e este relatório não expiram.

## 3. GitHub Deployments: o histórico oficial do environment
Na interface: *Code → Deployments → dev*, em https://github.com/VL-in/ai-factory-saudeja/deployments/dev

```bash
gh api "repos/$REPO/deployments?environment=dev&per_page=5" \
  -q '.[]|"\(.id) \(.sha[:7]) \(.created_at)"'
gh api repos/$REPO/deployments/6872641664/statuses \
  -q '.[]|"\(.state) \(.created_at) \(.log_url)"'
```

Cada deployment guarda o SHA do git. Os status (`waiting → queued → in_progress → success`) apontam para o job do run.

## 4. Hugging Face: o que o Space recebeu
Na interface: Space de dev → *Files* → *History*.

```bash
# Commits do Space (a mensagem é sempre "Sync from … via hub-sync")
curl -s "https://huggingface.co/api/spaces/$HF_SPACE_ID/commits/main" \
  | python -c "import json,sys;[print(c['id'][:10],c['date']) for c in json.load(sys.stdin)[:5]]"

# Commit em execução agora (runtime) e estágio
curl -s "https://huggingface.co/api/spaces/$HF_SPACE_ID" \
  | python -c "import json,sys;d=json.load(sys.stdin);print(d['sha'][:10],d['runtime']['stage'],d['runtime'].get('sha','')[:10])"
```

O commit do Space não diz de qual commit do GitHub veio. A ligação passa pelo log do passo de sync (item 2) ou pelo horário do deployment (item 3).

## 5. Digest da árvore: prova de que o conteúdo voltou
Compara o conteúdo de duas revisões do Space, independente do SHA do commit:

```bash
for r in 3b95c574c3 861ea8f834 0f57df8d39; do
  printf "%s " $r
  curl -s "https://huggingface.co/api/spaces/$HF_SPACE_ID/tree/$r?recursive=true" \
    | python -c "import json,sys,hashlib;t=sorted((f['path'],f.get('oid','')) for f in json.load(sys.stdin) if f['type']=='file');print(len(t),'arquivos',hashlib.sha1(repr(t).encode()).hexdigest()[:12])"
done
```

Digests iguais significam conteúdo igual. É o critério de sucesso do rollback.

# O que foi bem
- O revert passou pelo mesmo pipeline de um deploy normal, sem caminho especial para manter.
- O `smoke_deploy.py` só deixou o run verde com o runtime no commit novo do Space. Então "run verde" significou de fato "rollback no ar".
- O rastreio ficou completo, do git ao Space, sem nenhum passo manual de registro.

# O que pode melhorar
- **O tempo do rollback é quase todo de CI.** Foram 5 min 29 s de CI e 1 min 26 s de deploy. Em produção, o caminho é revert em `dev` → PR → merge em `main`. Isso soma pelo menos dois CIs (o do PR e o do deploy), a aprovação em *Review deployments* e um PATCH novo no CHANGELOG, porque o `versao_release.py conferir` recusa versão igual ou menor que a última tag. Estimativa: 15 min ou mais, a confirmar.
- **O elo entre o Space e o git depende de um log que expira.** O `hub-sync` grava a mensagem fixa `Sync from ${GITHUB_REPO_ID} via hub-sync`, sem o SHA do GitHub. Passados os 90 dias de retenção, sobra só a correlação por horário com o deployment.

# Próximos passos
| Ação | Prioridade |
|---|---|
| RB-002: rollback de código com migration aditiva em commit separado. Confirmar que o código revertido funciona com o schema novo e que reverter o arquivo da migration faz o `db push` recusar | média |
| Documentar no runbook a regra "reverter o código, nunca o arquivo de migration; para desfazer schema, migration nova" | média |
| Levar o SHA do GitHub ao Space sem depender do log, por exemplo gravando-o num arquivo do staging | baixa |
| Ensaio do rollback do modelo (canário) | média |
