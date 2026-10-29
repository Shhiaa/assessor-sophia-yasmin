# Mudanças da Etapa 8 — o juiz e a escrita

Decisão: **transacionar**. Cada turno usa uma conexão Postgres própria. As tools de escrita não confirmam; o fechamento do turno (`graph.py`) confirma só se o juiz autorizou e desfaz em qualquer outro caso. Detalhes e alternativas descartadas estão em `decisao_tecnica.md`.

Nada de rota, API, agente ou banco foi alterado. Fora de um turno (servidor MCP, scripts) as tools mantêm o `commit()` imediato.

## Fluxo

Antes: tool grava e dá `commit()` → orquestrador → guardrail → juiz troca a frase → a linha continua no banco.

Depois: tool grava na conexão do turno (sem commit) → orquestrador → guardrail → juiz decide e marca `escrita_autorizada` → fechamento do turno dá `COMMIT` (aprovado) ou `ROLLBACK` (qualquer outro caso).

## Arquivos criados

| Arquivo | O que é |
| --- | --- |
| `app/unidade_trabalho.py` | Conexão do turno (guardada num `ContextVar`), `confirmar`, `desfazer`, `liberar` e `encerrar_turno_db`. Cada chamada de tool abre um `SAVEPOINT`, então um erro numa tool desfaz só aquela chamada. A conexão tem `idle_in_transaction_session_timeout` de 30 s. |
| `testes/prova_depois.py` | Prova, com marcador único `ZZ-<hora>-<n>` e uma conexão separada, que o turno recusado não deixa linha e o aprovado grava uma vez, nas três tools. Não usa LLM nem JEV. |
| `testes/reproduz_desencontro.py` | Já existia da etapa anterior: reproduz o "antes". |
| `decisao_tecnica.md`, `MUDANCAS.md` | Documentação. |

## Arquivos modificados

| Arquivo | O que mudou |
| --- | --- |
| `app/db.py` | `get_pgsql_conn()` devolve a conexão do turno quando há turno aberto. Sem turno, devolve a global como antes. |
| `app/tools/financeiro.py` | `conn.commit()` virou `confirmar(conn)` e `conn.rollback()` virou `desfazer(conn)`. `update_transaction` entrou na lista `TOOLS` (antes só o MCP a usava). |
| `app/tools/agenda.py` | Mesma troca em `add_event`. `conn.close()` virou `liberar(conn)` em `add_event` e `query_events`, e `query_events` ganhou `desfazer(conn)` no erro. |
| `app/juiz.py` | `_publicar` devolve `escrita_autorizada` (verdadeiro só no veredito `apoiada` com noul ≥ 0,70). Na recusa, remove do histórico as tool calls do turno (`RemoveMessage`). Timeout do JEV de 30 s para 5 s (`JEV_TIMEOUT_S`). |
| `app/graph.py` | Campo `escrita_autorizada` no `Estado` (iniciado em `False` a cada turno). `executar_fluxo_assessor` abre o turno no banco antes do `invoke` e chama `encerrar_turno_db(autorizado)` no `finally`. Se o `COMMIT` falhar, o turno conta como `erro` no monitor. |

Mudanças da etapa anterior (já no repositório): `app/tools/agenda.py` (tool `add_google_event`), `app/config.py` (`GOOGLE_OAUTH_CREDENTIALS`) e `app/prompts.py` (regra de quando usar `add_event` ou `add_google_event`).

## Garantias e limites

- **Recusado, juiz sem resposta, sem chave, `sem_resposta` ou exceção:** `ROLLBACK`. O banco fica como antes do turno.
- **Aprovado:** `COMMIT` único no fechamento. Dentro do turno a tool e as leituras enxergam a própria escrita.
- **Queda do processo antes do `COMMIT`:** o Postgres descarta a transação.
- **Repetição do pedido:** sem deduplicação. Recusa seguida de repetição aprovada grava uma linha.
- **`add_google_event`:** continua **fora** da invariante (o evento no Google é criado na hora da tool e o `ROLLBACK` não o alcança). Foi mantido assim para não aumentar a mudança; está registrado em `decisao_tecnica.md`.
- **`salvar_mensagem` (Mongo):** inalterado, continua depois do juiz.
- **Duas tool calls paralelas no mesmo turno** compartilham a conexão e o nome do savepoint. Se uma falhar, o `ROLLBACK TO SAVEPOINT` pode desfazer também a outra.

## Verificação

- Sintaxe de todos os arquivos alterados: ok.
- Juiz testado offline (`sem_chave` recusa e remove `2` e `3` do histórico; veredito `apoiada` autoriza e não remove nada): ok.
- `testes/prova_depois.py` **não foi executado**: o ambiente em que as mudanças foram feitas não resolve o host do Postgres (`aivencloud.com`). Rodar na máquina com acesso: `python testes/prova_depois.py`.
- Não foi rodado o fluxo completo pelo `/chat`.
