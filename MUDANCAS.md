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
| `testes/mede_latencia.py` | Mede o custo da transação por turno sem LLM: cerca de +27 a 30 ms por turno que escreve, quase tudo abrir a conexão (Postgres local, sem rede). |
| `testes/serie_testes.py` | Série de pedidos pelo grafo completo (dólar, euro, reais, correção de valor, evento, consultas): mostra a pergunta, a resposta e as linhas do banco. `--sem-juiz` força a recusa. |
| `testes/reproduz_desencontro.py` | Já existia da etapa anterior: reproduz o "antes". |
| `decisao_tecnica.md`, `MUDANCAS.md` | Documentação. |

## Arquivos modificados

| Arquivo | O que mudou |
| --- | --- |
| `app/db.py` | `get_pgsql_conn()` devolve a conexão do turno quando há turno aberto. Sem turno, devolve a global como antes. |
| `app/tools/financeiro.py` | `conn.commit()` virou `confirmar(conn)` e `conn.rollback()` virou `desfazer(conn)`. `update_transaction` entrou na lista `TOOLS` (antes só o MCP a usava). `add_transaction` ganhou `currency` (BRL, USD ou EUR): converte para reais antes de gravar e devolve `amount_brl`, `moeda_original`, `cotacao_brl` e `fonte_cotacao`. O valor e a moeda originais ficam no `source_text`; a tabela não mudou. |
| `app/tools/agenda.py` | Mesma troca em `add_event`. `conn.close()` virou `liberar(conn)` em `add_event` e `query_events`, e `query_events` ganhou `desfazer(conn)` no erro. |
| `app/config.py` | `COTACAO_RESERVA` (lê `USD_BRL` e `EUR_BRL` do `.env`, padrão 5,50 e 6,00). |
| `app/prompts.py` | Regra de `add_google_event` (devolve "pendente") e, no financeiro: valor em dólar ou euro vai com `currency` e a resposta informa valor original, cotação e valor em reais. |
| `app/juiz.py` | `_publicar` devolve `escrita_autorizada` (verdadeiro só no veredito `apoiada` com noul ≥ 0,70). Na recusa, remove do histórico as tool calls do turno (`RemoveMessage`). Timeout do JEV de 30 s para 5 s (`JEV_TIMEOUT_S`). Quando o turno recusado tinha tentado escrever, a resposta é "Não consegui confirmar esse pedido, então nada foi registrado." (`FRASE_SEM_ESCRITA`); sem escrita, continua a frase segura antiga. |
| `app/graph.py` | Campo `escrita_autorizada` no `Estado` (iniciado em `False` a cada turno). `executar_fluxo_assessor` abre o turno no banco antes do `invoke` e chama `encerrar_turno_db(autorizado)` no `finally`. Se o `COMMIT` falhar, o turno conta como `erro` no monitor. |

Mudanças da etapa anterior (já no repositório): `app/tools/agenda.py` (tool `add_google_event`), `app/config.py` (`GOOGLE_OAUTH_CREDENTIALS`) e `app/prompts.py` (regra de quando usar `add_event` ou `add_google_event`).

## Garantias e limites

- **Recusado, juiz sem resposta, sem chave, `sem_resposta` ou exceção:** `ROLLBACK`. O banco fica como antes do turno.
- **Aprovado:** `COMMIT` único no fechamento. Dentro do turno a tool e as leituras enxergam a própria escrita.
- **Queda do processo antes do `COMMIT`:** o Postgres descarta a transação.
- **Repetição do pedido:** sem deduplicação. Recusa seguida de repetição aprovada grava uma linha.
- **`add_google_event`:** dentro da regra. No chat a tool só guarda o pedido (`status: pendente`) e o Google é chamado depois do `COMMIT` aprovado; recusa ou exceção descartam o pedido. Fora de turno cria na hora. A chamada real ao Google nunca foi testada (sem Node nem credenciais); a prova usa um Google falso.
- **`salvar_mensagem` (Mongo):** inalterado, continua depois do juiz.
- **Tools em paralelo no mesmo turno:** `@serializado` faz uma tool por vez na conexão do turno (sem a trava, 8 de 12 turnos com 4 `add_transaction` simultâneas perdiam linha; com ela, 0).

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

## Correções da análise do revisor (PDF "Etapa 8 — Análise da solução")

| Ponto do revisor | O que foi feito |
| --- | --- |
| 1. Tools em paralelo perdem escrita | `@serializado` (em `app/unidade_trabalho.py`) nas 7 tools do Postgres: trava por turno (`RLock` no dicionário do turno) e `RELEASE SAVEPOINT` ao fim de cada chamada. `testes/prova_depois.py` ganhou o teste de 4 tools em paralelo em 12 turnos: 0 perdas com a trava, 8 perdas sem ela. |
| 2. Histórico sujo depois da recusa | `rastro_do_turno` (`app/juiz.py`) remove todas as mensagens de IA e de tool do turno na recusa. Verificado com um turno simulado: sobram a fala do usuário e a frase segura. |
| 3. Exceção não limpa o histórico | `executar_fluxo_assessor` remove o mesmo rastro do checkpoint (`update_state`) antes de relançar a exceção. Verificado: depois de exceções seguidas o histórico tem só as falas do usuário. |
| 4. Google fora da invariante | Resolvido: `add_google_event` guarda o pedido no turno e só cria o evento no Google depois do `COMMIT` aprovado (`depois_do_commit`). Provado com Google falso: recusa, 0 chamadas; aprovação, 1. |
| 5. Evidência incompleta | `decisao_tecnica.md` agora tem a evidência por tool com marcador único, a tabela do chat e os limites (correção de valor, consultas e motivo das recusas ainda a repetir quando a cota da Groq voltar). `prova_depois.py` foi executado (8 de 8). |
| 6. `decisao_tecnica.md` inconsistente | Reescrito alinhado ao código: `ContextVar` no lugar de `turno_id`, timeouts de 5 s (juiz) e 60 s (transação), `update_transaction` ligada em `financeiro.TOOLS`, log de implementação. |
| 7. Sujeira e segurança | `_uvicorn*.log`, `historico.json`, `r*.txt` e `resp*.txt` saíram do repositório (`git rm --cached`; os arquivos continuam no disco) e entraram no `.gitignore`. **A chave TypeSafe segue no histórico do commit `042bead`: é preciso revogá-la no provedor.** |

## Provas pelo chat com o código final (rodada `agfecj`, juiz real)

| Pedido | Resultado |
| --- | --- |
| 15,50 dólares | R$ 83,47 gravado uma vez |
| 20 euros | Recusado com "Não consegui confirmar esse pedido, então nada foi registrado."; 0 linhas |
| 30 reais | R$ 30,00 gravado uma vez |
| 20 reais e, na mesma conversa, "Corrija … para 25 reais" | A mesma linha passou a R$ 25,00, sem linha nova (`update_transaction` aprovado pelo chat) |
| Evento 02/01/2030 às 15:00 | 1 evento gravado |
| Saldo total | Responde com o `gpt-oss-20b`; com o 120b a Groq dá erro 400, também no código antigo |
| "Antes" (código da Etapa 7, `reproduz_desencontro.py`, rodada `125324`) | As três tools gravaram antes do veredito |
| Monitor | 2 turnos `ok`, rota `financeiro`, 7,5 s e 52,8 s (a espera de nova tentativa da Groq pesa); exceções aparecem como `erro` |

Continua sem prova: criação real de evento no Google e consulta de agenda pelo chat.
