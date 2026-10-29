# Decisão técnica — Etapa 8: o juiz e a escrita

Status: decisão tomada e implementada. Lista de todas as mudanças em `MUDANCAS.md`. Nenhuma chave, token, `.env` ou URL com senha aparece neste arquivo.

## 1. A decisão tomada, e por quê

**O problema.** `add_transaction`, `update_transaction` (`app/tools/financeiro.py`) e `add_event` (`app/tools/agenda.py`) davam `commit()` dentro da própria tool. O juiz (`app/juiz.py`) só roda no fim do grafo, depois do orquestrador e do guardrail de saída. Quando o juiz recusava a frase, a linha já estava permanente em `transactions` ou `events`: o chat dizia "Não tenho evidência para afirmar isso." e o banco guardava o registro.

**A decisão: transacionar.** Uma transação por turno. As três tools escrevem numa conexão aberta para o turno e não confirmam. Quem confirma é o fechamento do turno, em `executar_fluxo_assessor` (`app/graph.py`), e só quando o juiz aprovou (`escrita_autorizada`). Recusa, erro, exceção, juiz sem resposta ou sem chave fazem `ROLLBACK`.

Como funciona (`app/unidade_trabalho.py`):

- `abrir_turno_db()` cria, num `ContextVar`, um dicionário do turno com a conexão (aberta na primeira tool que precisar) e uma trava (`RLock`). As threads das tools copiam o contexto e enxergam o mesmo dicionário.
- `get_pgsql_conn()` (`app/db.py`) devolve a conexão do turno quando há turno aberto. Fora de turno (servidor MCP, scripts) devolve a conexão global, e `confirmar`, `desfazer` e `liberar` se comportam como `commit`, `rollback` e `close` de antes.
- `@serializado` envolve as sete tools do Postgres: uma tool por vez na conexão do turno. Cada chamada abre um `SAVEPOINT`; um erro na tool desfaz só aquela chamada (`ROLLBACK TO SAVEPOINT`) e o fim da chamada libera o savepoint.
- `encerrar_turno_db(autorizado)` dá `COMMIT` ou `ROLLBACK` e fecha a conexão num `finally`. A conexão do turno tem `idle_in_transaction_session_timeout` de 60 s.

Fluxo de antes: modelo escolhe a tool → **tool grava e dá `commit()`** → orquestrador redige → guardrail revisa → juiz pode trocar a frase → chat mostra o texto (a linha continua no banco).

Fluxo de depois: modelo escolhe a tool → tool grava na conexão do turno, sem `commit()` → orquestrador redige → guardrail revisa → **juiz decide e marca `escrita_autorizada`** → fechamento do turno confirma se verdadeiro, desfaz em qualquer outro caso.

**Por que cabe neste grafo.**

- Cobre as três tools de uma vez, porque todas pedem a conexão ao mesmo ponto.
- Não cria rota, endpoint nem agente. Muda a tool, o acesso ao banco, o juiz e o fechamento do turno.
- A tool enxerga a própria escrita (mesma conexão): "registre e me diga o saldo" funciona e o `id` real volta para o juiz como evidência.
- Se o processo cair no meio do turno, o Postgres descarta a transação não confirmada.

**Garantia nas três tools.** Turno recusado: nenhuma linha criada nem alterada em `transactions` ou `events`. Turno aprovado: a escrita é confirmada uma vez, no fechamento do turno.

**Histórico depois da recusa ou da exceção.** O `MemorySaver` guarda as mensagens do turno. Se elas ficassem, o modelo do turno seguinte leria "Registrei sua despesa de 30 reais" e a tool com `status: ok` de uma linha que não existe. Por isso:

- Na recusa, o juiz devolve `RemoveMessage` para todas as mensagens de IA e de tool do turno (`rastro_do_turno`); sobra a fala do usuário e a frase segura.
- Numa exceção em qualquer nó, `executar_fluxo_assessor` remove o mesmo rastro do checkpoint (`update_state`) antes de relançar a exceção.

## 2. As decisões que o grupo não tomou, e por quê

| Caminho | Como funcionaria aqui | Vantagem real | Custo real | Por que ficou de fora |
| --- | --- | --- | --- | --- |
| Interceptar | Um julgamento antes da tool, comparando os argumentos com a fala do usuário | Nada chega a gravar | O juiz compara a frase pronta com a evidência da tool; antes da tool não existem frase nem evidência. Exigiria uma segunda rubrica e uma chamada a mais por escrita | Não cobre o caso visto em aula: "Lancei R$ 30,00" nasce depois da tool, com os argumentos corretos |
| Adiar | A tool devolve "pendente" e um nó grava depois do veredito | Nenhuma conexão aberta durante o turno | A tool não devolve `id` real e não enxerga a escrita no mesmo turno; `update` logo depois de `add` quebra; o INSERT vira código duplicado; o MCP precisaria de outro caminho | O Postgres já entrega esse adiamento com a transação, sem duplicar código |
| Compensar | A tool confirma; na recusa, uma ação inversa (`DELETE` ou `UPDATE` ao valor antigo) | Nenhuma mudança no caminho feliz | A linha fica visível por uma janela (painel, MCP, outra sessão); queda nessa janela deixa a linha para sempre; falha da compensação é um segundo efeito | A invariante diz "nenhuma escrita permanece"; compensar é limpeza posterior, não atomicidade |

Custo da decisão tomada: a conexão do turno fica aberta durante orquestrador, guardrail e juiz, e uma linha alterada fica travada para outras sessões nesse intervalo. Mitigações: timeout do juiz em 5 s (`JEV_TIMEOUT_S`) e `idle_in_transaction_session_timeout` de 60 s na conexão do turno (com 30 s o Postgres derrubou um turno em que o LLM demorou entre duas tool calls; o turno falhou fechado, sem linha).

## 3. O efeito nas métricas do SRE, e como

O monitor (`app/observabilidade`) mede o turno de `abrir_turno()` a `fechar_turno()`, conta erro só quando há exceção e soma tokens e custo só das chamadas de chat model. A chamada ao JEV usa `urllib` e não entra em tokens nem em custo. Os números abaixo são o raciocínio sobre o mecanismo; latência real não foi medida com carga.

| Métrica | Decisão: transacionar | Interceptar (descartada) | Compensar (descartada) |
| --- | --- | --- | --- |
| Latência e p95 (SLO 8 s) | Nenhuma chamada a mais ao modelo. Soma uma conexão por turno com escrita e um `COMMIT` ou `ROLLBACK`. O risco é o JEV: o timeout de 5 s limita o tempo com a transação aberta | Uma chamada de julgamento antes de cada escrita, no caminho crítico | Nada no caminho feliz; na recusa, uma ida a mais ao banco |
| Taxa de erro (SLO 5%) | Recusa termina em frase segura sem exceção: não conta como erro. Falha de `COMMIT` ou de conexão levanta exceção e conta (`status = erro`) | Falha do pré-julgamento vira exceção | Falha da compensação é um segundo efeito e sobe a taxa |
| Status ok, erro, bloqueado | Recusa do juiz fica `ok`; `bloqueado` continua só para o guardrail de entrada. Queda do JEV com falha fechada vira `ok` com escritas recusadas | Igual, com um motivo a mais de erro | Recusa compensada fica `ok`, mas a linha existiu por uma janela |
| Tokens e custo | Sem mudança nos chat models | Uma chamada de modelo a mais por escrita, se for LLM | Sem mudança |
| Conexões | Uma conexão presa durante o turno com escrita | Nenhuma | Nenhuma extra |

Um contador dos rótulos `[juiz]` no `/monitor` tornaria visível a recusa por indisponibilidade. Fica fora desta entrega.

## 4. As outras decisões

| Ponto | Decisão | Por quê | Alternativa descartada e por quê |
| --- | --- | --- | --- |
| `update_transaction` fora do agente financeiro | Incluída na lista `TOOLS` de `app/tools/financeiro.py`, que o agente já importa (`app/agents.py` não mudou) | O enunciado trata a alteração como escrita obrigatória; sem a tool ligada o cenário de alteração não roda no chat | Provar só pelo MCP: ele não passa pelo grafo nem pelo juiz |
| Juiz sem resposta (timeout, exceção, chave ausente) | Falha fechada: frase fixa e `ROLLBACK` | Mantém a invariante: sem conferência, nada permanece | Falha aberta (confirmar sem veredito): aprova escrita que ninguém conferiu. Repetir a chamada: soma latência com a transação aberta |
| `add_google_event` | **Fora da invariante, como decisão consciente.** O evento no Google é criado na hora da tool, antes do veredito | Um efeito fora do Postgres não participa da transação, e o `ROLLBACK` não o alcança. Risco aceito: se o juiz recusar, o evento continua no Google e não é apagado | Compensar com `delete-event` do servidor MCP do Google: é possível (a tool existe) mas exige guardar o id do evento, apagar na recusa e registrar a falha do delete. Fica como melhoria. Criar o evento só depois da aprovação: tira o id da resposta da tool |
| `salvar_mensagem` (Mongo) | Mantido depois do juiz | Grava a frase que o usuário efetivamente viu; o Mongo não compartilha transação com o Postgres | Gravar antes do juiz: registraria uma frase que o usuário nunca viu |
| Leituras (`search_transactions`, `saldo_total`, `saldo_diario`, `query_events`) | Usam a conexão do turno quando existe | Só a mesma conexão enxerga a escrita ainda não confirmada | Conexão global nas leituras: "registre e diga o saldo" não acharia a linha |
| Tools em paralelo no mesmo turno | `@serializado`: uma por vez na conexão do turno | O ToolNode do LangGraph roda tool calls em paralelo. Sem a trava, SAVEPOINTs e rollbacks parciais se misturam. Medido: sem a trava, 8 de 12 turnos com 4 `add_transaction` simultâneas perderam linha; com a trava, 0 de 12 | Uma conexão por tool: quebraria a transação única do turno |
| Moeda estrangeira | `add_transaction` aceita `currency` (BRL, USD ou EUR) e converte para reais (`app/cambio.py`: AwesomeAPI, com cotação de reserva `USD_BRL` e `EUR_BRL` no `.env`). A tool devolve valor original, cotação e fonte | O caso dos dólares: gravar `amount=2` e responder "R$ 2" fazia o juiz recusar por moeda trocada. Agora o valor convertido e a cotação são evidência | Biblioteca de câmbio: todas embrulham uma API ou dado velho e `python-bcb` traz o pandas |
| Timeout do JEV | 5 s | O SLO de p95 é 8 s e a transação fica aberta enquanto o JEV responde | Manter 30 s: estoura o SLO e prende a conexão |
| Repetição do mesmo pedido | Sem deduplicação. No máximo uma confirmação por turno; recusa seguida de repetição aprovada grava uma linha | Dois lançamentos iguais podem ser legítimos | Restrição única em `source_text`: muda o esquema e bloquearia compras repetidas legítimas |
| Queda do processo entre dois passos | Nenhuma ação extra | Antes do `COMMIT`, o Postgres descarta a transação. Janela aceita: queda depois do `COMMIT` e antes de entregar a resposta | — |

### Evidência, com marcador único

Ambiente: Postgres 17 num contêiner local (o banco do `.env` não existe mais no DNS), esquema inferido em `testes/schema_local.sql`. Nos testes pelo chat o especialista usou a Groq (cota do Gemini esgotada) e a memória de longo prazo estava desligada (o Qdrant recusa conexão). Isso não interfere nas escritas.

**Camada das tools** (`testes/prova_depois.py`, sem LLM e sem JEV; lê o banco por outra conexão):

| Tool | Recusado | Aprovado |
| --- | --- | --- |
| `add_transaction` | 0 linhas (`ZZ-<rodada>-1`) | 1 linha (`ZZ-<rodada>-2`) |
| `add_event` | 0 eventos (`ZZ-<rodada>-3`) | 1 evento (`ZZ-<rodada>-4`) |
| `update_transaction` | valor antigo mantido (`ZZ-<rodada>-6`) | valor novo gravado uma vez |
| 2 escritas no mesmo turno | 0 linhas (`ZZ-<rodada>-7`) | — |
| 4 `add_transaction` em paralelo | — | 12 turnos, 4 linhas em todos |

Resultado: 8 de 8 verificações ok.

**Pelo chat, com o juiz real** (marcadores em letras, porque o anonimizador troca dígitos por `[PII_CONTA_…]`):

| Pergunta | Resposta | Banco |
| --- | --- | --- |
| "Comprei um lanche por 15,50 dólares hoje, descrição ZZ-ajfjdb-a…" | "Lancei R$ 82,83 em ALIMENTAÇÃO … (USD 15,50)." | 1 linha de R$ 82,83 |
| "Gastei 30 reais com um lanche hoje, descrição ZZ-ajfjdb-c…" | "Lancei R$30,00 em ALIMENTAÇÃO registrado com sucesso." | 1 linha de R$ 30,00 |
| "Gastei 20 reais no mercado hoje, descrição ZZ-ajfjdb-d…" | "Registro de R$ 20,00 … adicionado com sucesso." | 1 linha |
| "Registre na agenda o compromisso ZZ-ajfjdb-e em 02/01/2030 às 15:00…" | "Não tenho evidência para afirmar isso." (recusa) | 0 eventos |
| "Paguei 20 euros num jantar hoje, descrição ZZ-ajfjdb-b…" | "Não tenho evidência para afirmar isso." (recusa) | 0 linhas |
| "Corrija o gasto ZZ-ajfjdb-d para 25 reais." | "Não tenho evidência para afirmar isso." (recusa) | valor continua R$ 20,00 |

Em execuções anteriores, com marcadores como `ZZ-aidcaj-a` e `ZZ-aidcaj-b`, a compra "2 dólares" virou R$ 10,69 (cotação 5,3462) e foi gravada uma vez; com o juiz `sem_apoio` (0,15) a linha desfeita deixou 0 linhas e a sequência do `id` subiu, o que prova que o INSERT aconteceu e foi desfeito. O mesmo vale para um erro 400 da Groq no meio do turno: 0 linhas.

**Histórico**: com um turno simulado (fala do usuário, tool call, `ToolMessage`, frase "Registrei sua despesa de 30 reais.") e o juiz recusando, o histórico ficou só com a fala do usuário e "Não tenho evidência para afirmar isso." Numa exceção no meio do turno, o histórico ficou só com as falas do usuário.

**Limites da evidência (a repetir quando a cota da Groq voltar):**

- A correção de valor pelo chat (caso `ZZ-ajfjdb-d`) foi recusada pelo juiz; a aprovação de `update_transaction` está provada só na camada das tools.
- As consultas de saldo e de agenda pelo chat ficaram sem resposta por erro 400 e limite de tokens da Groq.
- Nos casos recusados pelo chat (euro, evento, correção) não conferi o motivo do veredito nem se a tool chegou a rodar; o banco ficou sem linha em todos.
- O veredito do JEV varia entre execuções para o mesmo pedido; a invariante vale nos dois casos.

## 5. Arquivos criados e arquivos modificados

Criados:

- `app/unidade_trabalho.py`: conexão do turno, `confirmar`, `desfazer`, `liberar`, `@serializado`, `encerrar_turno_db`.
- `app/cambio.py`: conversão de USD e EUR para reais.
- `testes/prova_depois.py`: prova nas três tools, incluindo tools em paralelo.
- `testes/prova_chat.py`, `testes/prova_dolar.py`, `testes/serie_testes.py`: provas pelo grafo completo.
- `testes/reproduz_desencontro.py`: reproduz o "antes" (ainda não executado).
- `testes/schema_local.sql`: esquema mínimo para o Postgres local.
- `decisao_tecnica.md`, `MUDANCAS.md`.

Modificados:

- `app/db.py`: `get_pgsql_conn()` devolve a conexão do turno.
- `app/tools/financeiro.py`: `confirmar`, `desfazer` e `@serializado` nas tools; `update_transaction` na lista `TOOLS`; `add_transaction` com `currency`.
- `app/tools/agenda.py`: `confirmar`, `desfazer`, `liberar` e `@serializado`; tool `add_google_event`.
- `app/juiz.py`: `escrita_autorizada`, `rastro_do_turno`, timeout de 5 s.
- `app/graph.py`: campo `escrita_autorizada`, abertura e encerramento da transação do turno, limpeza do histórico na exceção.
- `app/config.py`: `COTACAO_RESERVA` e `GOOGLE_OAUTH_CREDENTIALS`.
- `app/prompts.py`: regra de moeda no financeiro e regra de `add_event` e `add_google_event` na agenda.
- `.gitignore`: logs, `historico.json` e saídas de teste locais (`r*.txt`, `resp*.txt`), que deixaram de ser versionados.

## 6. Registro de alterações

1. **`decisao_tecnica.md` e `testes/reproduz_desencontro.py`.** A decisão foi escrita antes do código, e o script reproduz o desencontro nas três tools (camada das tools, sem LLM, e camada do chat). Limite: o "antes" ainda não foi executado.
2. **Tool `add_google_event`** (`app/tools/agenda.py`, `app/config.py`, `app/prompts.py`), pelo cliente `mcp` que já estava no `requirements.txt`. Fora da invariante (seção 4).
3. **Transação por turno** (`app/unidade_trabalho.py`, `app/db.py`, tools, `app/juiz.py`, `app/graph.py`). A fronteira da escrita passou a ser o veredito do juiz.
4. **Testes com Postgres local e timeout de 60 s.** O Postgres derrubou uma conexão do turno por ficar parada mais de 30 s entre duas tool calls.
5. **Conversão de moeda** (`app/cambio.py`, `add_transaction`). `currency` com BRL, USD e EUR; cotação da API, com reserva no `.env`.
6. **Correções da análise do revisor.** Trava por turno e `RELEASE SAVEPOINT` (`@serializado`); limpeza de todas as mensagens de IA e de tool do turno na recusa e na exceção; este arquivo alinhado ao código (mecanismo `ContextVar`, timeouts de 5 s e 60 s, `update_transaction` ligada em `financeiro.TOOLS`); logs, `historico.json` e `r*.txt` tirados do repositório (continuam no disco). A chave do JEV (TypeSafe) continua no histórico do commit `042bead`: é preciso revogá-la no painel do provedor, apagar o arquivo não resolve.
