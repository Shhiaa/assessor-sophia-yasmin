# Mudanças da Etapa 8 — o juiz e a escrita

Decisão: **transacionar**. Cada turno usa uma conexão Postgres própria. As tools de escrita não confirmam; o fechamento do turno (`graph.py`) confirma só se o juiz autorizou e desfaz em qualquer outro caso. Detalhes e alternativas descartadas estão em `decisao_tecnica.md`.

Nada de rota, API, agente ou banco foi alterado. Fora de um turno (servidor MCP, scripts) as tools mantêm o `commit()` imediato.

## Fluxo

Antes: tool grava e dá `commit()` → orquestrador → guardrail → juiz troca a frase → a linha continua no banco.

Depois: tool grava na conexão do turno (sem commit) → orquestrador → guardrail → juiz decide e marca `escrita_autorizada` → fechamento do turno dá `COMMIT` (aprovado) ou `ROLLBACK` (qualquer outro caso).

## Arquivos criados

| Arquivo | O que é |
| --- | --- |
| `app/unidade_trabalho.py` | Conexão do turno (guardada num `ContextVar`), `confirmar`, `desfazer`, `liberar` e `encerrar_turno_db`. Cada chamada de tool abre um `SAVEPOINT`, então um erro numa tool desfaz só aquela chamada. A conexão tem `idle_in_transaction_session_timeout` de 60 s (com 30 s o Postgres derrubou um turno em que o LLM demorou entre duas tool calls). |
| `testes/prova_depois.py` | Prova, com marcador único `ZZ-<hora>-<n>` e uma conexão separada, que o turno recusado não deixa linha e o aprovado grava uma vez, nas três tools. Não usa LLM nem JEV. |
| `app/cambio.py` | Conversão de USD/EUR para reais: AwesomeAPI (sem chave, `urllib`, timeout 3 s) e, se falhar, a cotação de reserva do `.env` (`USD_BRL`, `EUR_BRL`). Sem dependência nova. |
| `testes/schema_local.sql` | Esquema mínimo (inferido das queries das tools) para testar num Postgres local no Docker. O banco Aiven do `.env` não existe mais no DNS. |
| `testes/prova_chat.py` | Prova pelo grafo completo (`executar_fluxo_assessor`): mostra mensagem enviada, resposta e linhas do banco; a sequence do `id` prova que o INSERT foi tentado mesmo quando o rollback o desfez. `--sem-juiz` força a recusa. |
| `testes/prova_dolar.py` | O caso do HTML (compra em dólares) pelo grafo completo, com o juiz real. |
| `testes/reproduz_desencontro.py` | Já existia da etapa anterior: reproduz o "antes". |
| `decisao_tecnica.md`, `MUDANCAS.md` | Documentação. |

## Arquivos modificados

| Arquivo | O que mudou |
| --- | --- |
| `app/db.py` | `get_pgsql_conn()` devolve a conexão do turno quando há turno aberto. Sem turno, devolve a global como antes. |
| `app/tools/financeiro.py` | `conn.commit()` virou `confirmar(conn)` e `conn.rollback()` virou `desfazer(conn)`. `update_transaction` entrou na lista `TOOLS` (antes só o MCP a usava). `add_transaction` ganhou `currency` (BRL, USD ou EUR): converte para reais antes de gravar e devolve `amount_brl`, `moeda_original`, `cotacao_brl` e `fonte_cotacao`. O valor e a moeda originais ficam no `source_text`; a tabela não mudou. |
| `app/tools/agenda.py` | Mesma troca em `add_event`. `conn.close()` virou `liberar(conn)` em `add_event` e `query_events`, e `query_events` ganhou `desfazer(conn)` no erro. |
| `app/config.py` | `COTACAO_RESERVA` (lê `USD_BRL` e `EUR_BRL` do `.env`, padrão 5,50 e 6,00). |
| `app/prompts.py` | Regra no prompt do financeiro: valor em dólar ou euro vai com `currency` e a resposta informa valor original, cotação e valor em reais. |
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

Ambiente de teste: Postgres 17 num contêiner Docker local (`pg-teste17`, porta 55433) com `testes/schema_local.sql`, apontado só por `DATABASE_URL` na linha de comando (o `.env` não foi alterado). Nos testes pelo chat o especialista usou o Groq direto (cota diária do Gemini esgotada) e a tool de memória devolveu "sem histórico" (o Qdrant recusa a conexão aqui). Isso não interfere nas escritas.

| Teste | Resultado |
| --- | --- |
| `testes/prova_depois.py` (3 tools, recusa e aprovação, duas escritas no mesmo turno) | 7 de 7 ok |
| Chat, criar transação, juiz real recusou (`sem_apoio`) | resposta "Não tenho evidência…", 0 linhas, sequence subiu (INSERT feito e desfeito) |
| Chat, criar evento, juiz real aprovou (`apoiada` 0,86) | 1 evento gravado, uma vez |
| Chat, criar transação em reais, juiz real aprovou (`apoiada` 0,80 a 0,94) | 1 linha gravada, uma vez |
| Chat, `--sem-juiz` (falha fechada) | recusa, 0 linhas |
| Chat, caso dos dólares com conversão ("Comprei uma coca por 2 dólares") | tool chamada com `currency="USD"`, cotação 5,3462 (API), gravou R$ 10,69 uma vez, juiz `apoiada` 0,78. Em outra execução o juiz ficou em 0,66 (abaixo do limiar de 0,70) e recusou: 0 linhas. |
| Conversão isolada (`app/cambio.py`) | USD e EUR pela API ok; com a API indisponível usa a reserva do `.env` (`fonte: reserva`) |
| Exceção no meio do turno (erro 400 da Groq depois da tool) | 0 linhas, sequence subiu: o rollback também vale para exceção |

Observações:
- O veredito do JEV varia entre execuções (o mesmo pedido em reais foi aprovado e depois recusado). A invariante vale nos dois casos.
- O `gpt-oss` da Groq às vezes devolve erro 400 de tool call inválida; o teste repete em outra sessão.
- Não foram testados pelo chat: alterar valor (`update_transaction`) e consulta de saldo (limite de tokens por minuto da Groq). `update_transaction` está coberto por `prova_depois.py`.
