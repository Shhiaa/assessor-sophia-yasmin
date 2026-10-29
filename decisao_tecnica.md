# Decisão técnica — Etapa 8: o juiz e a escrita

Este arquivo conta, em linguagem simples, a decisão que tomamos, o que ficou de fora e como provamos que funciona. Sem chave, token, `.env` ou URL com senha. O registro completo das mudanças está no `MUDANCAS.md`.

## 1. A decisão tomada, e por quê

### O problema

Quando o usuário pede "registre uma compra", a tool grava no banco e confirma na hora (`commit()`). Só depois o juiz confere a frase que vai aparecer no chat. Se o juiz recusar e trocar a frase por "Não tenho evidência para afirmar isso.", a linha **já está salva**: o chat diz "não" e o banco diz "sim". Acontecia nas três tools que escrevem: `add_transaction`, `update_transaction` e `add_event`.

### Qual fronteira passou a autorizar a escrita

**O veredito do juiz.** Antes, a escrita era autorizada pelo fim da tool. Agora ela só vira permanente depois que o juiz aprova.

Para isso, cada turno (cada pergunta do usuário) ganha um "rascunho a lápis" no banco, uma transação do Postgres:

1. As tools escrevem no rascunho e **não confirmam**.
2. O juiz dá o veredito e deixa `escrita_autorizada` no estado.
3. O fim do turno, em `executar_fluxo_assessor` (`app/graph.py`), **passa a caneta** (`COMMIT`) se o juiz aprovou. Em qualquer outro caso **apaga o rascunho** (`ROLLBACK`): juiz recusou, juiz deu erro ou demorou demais, faltou a chave do juiz, ou houve exceção no meio do turno.

### Fluxo de antes e de depois

**Antes:** o modelo escolhe a tool → **a tool grava e confirma** → o orquestrador escreve a frase → o guardrail revisa → o juiz pode trocar a frase → o chat mostra o texto (a linha continua no banco).

**Depois:** o modelo escolhe a tool → a tool grava no rascunho → o orquestrador escreve a frase → o guardrail revisa → **o juiz decide** → o fim do turno confirma ou apaga.

### Por que isso cabe neste grafo

- Todas as tools pegam a conexão no mesmo lugar (`get_pgsql_conn()` em `app/db.py`), então um único ponto cobre as três. Corrigir só uma deixaria o mesmo furo nas outras.
- Não criou rota, endpoint nem agente novo. Muda o acesso ao banco, as tools, o juiz e o fechamento do turno.
- A tool enxerga o que ela mesma acabou de gravar. Por isso "registre e me diga o saldo" funciona, e o `id` real da linha chega ao juiz como evidência.
- Se o programa cair no meio do turno, o Postgres joga fora o rascunho sozinho.
- Fora do chat (servidor MCP, scripts) nada muda: as tools seguem com a conexão normal e a confirmação imediata.

O código do rascunho está em `app/unidade_trabalho.py`: `abrir_turno_db` abre o turno; `confirmar`, `desfazer` e `liberar` entram no lugar de `commit`, `rollback` e `close`; cada tool usa um `SAVEPOINT` (se uma falha, só ela é desfeita); `@serializado` faz uma tool de cada vez, porque o LangGraph pode rodar várias em paralelo; `encerrar_turno_db` confirma ou apaga e fecha a conexão.

### A garantia, em cada tool

- **`add_transaction`:** turno recusado, nenhuma linha nova em `transactions`; aprovado, uma linha, confirmada uma vez no fim do turno.
- **`update_transaction`:** turno recusado, o registro continua com os valores antigos; aprovado, a alteração é confirmada uma vez.
- **`add_event`:** turno recusado, nenhum evento novo em `events`; aprovado, um evento, confirmado uma vez.

### O histórico da conversa também é limpo

O LangGraph guarda as mensagens do turno. Se, depois de uma recusa, o histórico ainda dissesse "Registrei sua despesa de 30 reais", o modelo do turno seguinte acreditaria numa linha que não existe. Por isso, na recusa o juiz remove do histórico todas as mensagens de IA e de tool daquele turno (`rastro_do_turno` em `app/juiz.py`), e numa exceção o `graph.py` faz a mesma limpeza. Sobra a fala do usuário e a frase segura.

## 2. As decisões que o grupo não tomou, e por quê

| Caminho | Como funcionaria aqui | Uma vantagem real | Um custo real | Por que ficou de fora |
| --- | --- | --- | --- | --- |
| **Interceptar** (julgar antes da tool) | Um julgamento antes de a tool escrever, comparando os argumentos com a fala do usuário | Nada chega a gravar | O juiz atual compara a frase pronta com o que a tool devolveu, e antes da tool não existe nem uma coisa nem outra. Exigiria um segundo juiz e uma chamada a mais por escrita | Não pega o caso da aula: o "R$ 30,00" errado nasce depois da tool, com os argumentos certos (`amount=30`) |
| **Adiar** (gravar só depois do veredito) | A tool só anota a intenção e outro passo grava depois | Nenhuma conexão fica aberta durante o turno | A tool não devolve o `id` real e não enxerga a própria escrita. "Corrija o gasto que acabei de registrar" quebra, e o INSERT teria que ser duplicado fora da tool | O Postgres já faz esse adiamento com a transação, sem duplicar código |
| **Compensar** (gravar e desfazer na recusa) | A tool confirma e, se o juiz recusar, outra ação apaga a linha ou devolve o valor antigo | Quase não muda o caminho feliz | A linha fica visível por um tempo (painel, MCP, outra sessão). Se o programa cair nesse intervalo, o lixo fica para sempre. Apagar também pode falhar, e isso é um segundo efeito | A regra diz "nenhuma escrita permanece". Compensar é limpar depois, não impedir |

Custo da solução que escolhemos: a conexão fica aberta enquanto o orquestrador, o guardrail e o juiz trabalham, e uma linha alterada fica travada para outras sessões nesse tempo. Para limitar isso, o juiz tem no máximo **5 segundos** para responder (`JEV_TIMEOUT_S` em `app/juiz.py`; antes eram 30), e o Postgres derruba a conexão do turno se ela ficar parada por mais de **60 segundos**. Com 30 s ele derrubou um turno em que o modelo demorou entre duas tools, e nesse caso nada foi salvo.

## 3. O efeito nas métricas do SRE, e como

O monitor (`app/observabilidade`) mede o turno de `abrir_turno()` a `fechar_turno()`. Conta erro só quando há exceção, e soma tokens e custo só das chamadas aos modelos de chat (a chamada ao juiz usa `urllib` e não entra nessa conta).

**O que foi medido:** o custo da transação sozinha (`testes/mede_latencia.py`, 100 escritas de cada tipo, sem modelo de linguagem, Postgres local). Um turno com escrita ficou de 8 a 11 ms (antes, com `commit` dentro da tool) para 35 a 41 ms (depois, aprovado) ou 33 a 38 ms (depois, recusado). É um acréscimo de **27 a 30 ms**, e quase tudo é abrir a conexão do turno. O Postgres local não tem rede; num banco remoto cada ida e volta soma. **O p95 de um turno completo, com modelos e juiz, não foi medido**: os modelos estavam no limite de uso. O resto da tabela é raciocínio sobre o mecanismo.

| Métrica | Decisão tomada: transacionar | Interceptar (descartada) | Compensar (descartada) |
| --- | --- | --- | --- |
| **Latência do turno e p95** (SLO de 8 s) | Nenhuma chamada a mais ao modelo. Soma uma conexão e um `COMMIT` ou `ROLLBACK`, cerca de 30 ms medidos. O risco é **uma conexão aberta à espera do veredito**: o limite de 5 s do juiz protege o p95 | **Uma chamada a mais** antes de cada escrita, no caminho principal, somando ao p95 | Nada no caminho feliz. Na recusa, uma ida a mais ao banco |
| **Taxa de erro** (SLO de 5%) | Recusa termina em **frase segura sem exceção**: não conta como erro. Falha de conexão ou de `COMMIT` é exceção e conta. O bloqueio do guardrail de entrada também não conta como erro | A falha do pré-julgamento viraria exceção e entraria na taxa | A falha ao apagar é **um segundo efeito** e aumenta a taxa |
| **Status** (`ok`, `erro`, `bloqueado`) | Recusa do juiz fica `ok`. `bloqueado` continua só para o guardrail de entrada. Se o juiz cair com falha fechada, o turno vira `ok` com escritas recusadas | Igual, com mais um motivo de erro | Recusa compensada fica `ok`, mas a linha existiu por um tempo |
| **Tokens e custo** | Nada muda nos modelos de chat | Uma chamada de modelo a mais por escrita, se o julgamento for um LLM | Nada muda |

Um contador dos rótulos `[juiz]` no `/monitor` mostraria as recusas causadas por falha do juiz. Ficou fora desta entrega.

## 4. As outras decisões

### O que entrou no escopo e o que ficou de fora

| Peça | O que fizemos | Por quê |
| --- | --- | --- |
| `add_transaction`, `update_transaction`, `add_event` | **Entram.** Escrevem no rascunho do turno | São as três escritas obrigatórias |
| `update_transaction` | Foi colocada na lista `TOOLS` do agente financeiro (`app/tools/financeiro.py`; o `app/agents.py` não mudou) | Ela existia, mas só o servidor MCP a usava. Sem isso, o cenário de "alterar um lançamento" nem rodaria no chat |
| Leituras (`search_transactions`, `saldo_total`, `saldo_diario`, `query_events`) | **Continuam**, usando a conexão do turno | Só a mesma conexão enxerga a escrita ainda não confirmada |
| `add_google_event` | **Entra, adiada.** No chat a tool só anota o pedido e responde "pendente"; o evento é criado no Google **depois do `COMMIT` aprovado**. Recusa ou exceção descartam o pedido. Fora do chat cria na hora | Um evento no Google não participa da transação do Postgres, então o `ROLLBACK` não o apagaria. Adiar evita que o Google veja uma escrita recusada. Alternativa não usada: apagar com o `delete-event`, que exigiria o id do evento, num formato que não conseguimos conferir sem as credenciais |
| `salvar_mensagem` (Mongo) | **Fica de fora**, continua depois do juiz | Grava a frase que o usuário de fato viu, não um efeito de domínio. O Mongo não participa da transação do Postgres |
| Compras em dólar ou euro | **Entram.** `add_transaction` aceita `currency` (BRL, USD ou EUR), converte para reais (`app/cambio.py`, com a AwesomeAPI e cotação de reserva no `.env`) e devolve valor original, cotação e fonte | Era o caso da aula: gravar `amount=2` e responder "R$ 2" fazia o juiz recusar por moeda trocada. Agora a conversão vira evidência. Não usamos biblioteca de câmbio: todas embrulham uma API ou um dado velho |
| Tools ao mesmo tempo | **`@serializado`**: uma de cada vez no rascunho | O LangGraph roda tools em paralelo. Sem a trava, 4 `add_transaction` simultâneas perderam linhas em **8 de 12 turnos**; com a trava, **0 de 12** |

**Limites do Google:** a resposta ao usuário diz que o evento "será criado". Se o Google falhar depois do `COMMIT`, a falha só aparece no log. A chamada **real** ao Google nunca foi testada (aqui não há Node.js nem credenciais): os testes usam um Google falso. O PDF do FAQ ainda diz que não há calendário externo, e isso contradiz a tool.

### O que acontece se o juiz não responde

Ele **falha fechado**: timeout, erro HTTP, falta de chave ou resposta vazia viram recusa. O usuário vê a frase segura e o banco volta ao que era. Confirmar sem veredito aprovaria algo que ninguém conferiu, e repetir a chamada somaria tempo com a transação aberta.

### O que resta se o processo cair entre dois passos

Antes do `COMMIT`, o Postgres joga o rascunho fora, então nada resta. A única janela é cair **depois** do `COMMIT` e antes de entregar a resposta: a linha existe, mas o usuário não viu o texto, e o Mongo pode ficar sem a mensagem do assistente. É um risco aceito.

### O que uma repetição do mesmo pedido faz

Não há bloqueio de duplicados. A garantia é no máximo **uma confirmação por turno**. Pedido recusado e repetido depois, se aprovado, grava uma linha, porque a recusa não deixa rastro. Dois turnos aprovados gravam duas linhas. Duas compras iguais podem ser legítimas (duas cocas no mesmo dia), e uma regra de unicidade mudaria o banco.

### A evidência, com marcador único

**Onde testamos:** o banco do `.env` não existe mais (o endereço sumiu do DNS). Usamos um Postgres 17 local no Docker, com um esquema que deduzimos do código (`testes/schema_local.sql`). Cada tentativa tem um marcador único `ZZ-...`, e a leitura é feita por uma conexão separada, que só enxerga o que foi confirmado.

**Nas tools** (`testes/prova_depois.py`, sem modelo e sem juiz; rodada `124507`):

| Tool | Recusa sem efeito | Aprovação uma única vez |
| --- | --- | --- |
| `add_transaction` | `ZZ-124507-1`: 0 linhas | `ZZ-124507-2`: 1 linha (id 911) |
| `add_event` | `ZZ-124507-3`: 0 eventos | `ZZ-124507-4`: 1 evento (id 24) |
| `update_transaction` | descrição `ZZ-124507-6` recusada: registro 912 manteve a antiga | a mesma descrição aprovada: aplicada uma vez |
| `add_google_event` (Google falso) | `ZZ-124507-g1`: 0 chamadas ao Google | `ZZ-124507-g2`: 1 chamada, depois do `COMMIT` |
| Duas escritas no mesmo turno | `ZZ-124507-7`: 0 linhas | — |
| 4 tools em paralelo | — | `ZZ-124507-p0` a `p11`: 12 turnos, 48 linhas (4 em cada) |

Resultado: **12 de 12 verificações passaram.**

**Pelo chat, com o juiz real** (`testes/serie_testes.py`; marcador em letras, porque o guardrail troca números por `[PII_CONTA_...]`):

| Pergunta | Resposta | Banco |
| --- | --- | --- |
| "Comprei um lanche por 15,50 dólares hoje, descrição `ZZ-ajfjdb-a`…" | "Lancei R$ 82,83 em ALIMENTAÇÃO … (USD 15,50)." | 1 linha de R$ 82,83 |
| "Gastei 30 reais com um lanche hoje, descrição `ZZ-ajfjdb-c`…" | "Lancei R$30,00 em ALIMENTAÇÃO registrado com sucesso." | 1 linha de R$ 30,00 |
| "Gastei 20 reais no mercado hoje, descrição `ZZ-ajfjdb-d`…" | "Registro de R$ 20,00 … adicionado com sucesso." | 1 linha |
| "Paguei 20 euros num jantar hoje, descrição `ZZ-ajfjdb-b`…" | "Não tenho evidência para afirmar isso." (recusa) | 0 linhas |
| "Registre na agenda o compromisso `ZZ-ajfjdb-e` em 02/01/2030 às 15:00…" | "Não tenho evidência para afirmar isso." (recusa) | 0 eventos |
| "Corrija o gasto `ZZ-ajfjdb-d` para 25 reais." | "Não tenho evidência para afirmar isso." (recusa) | continua R$ 20,00 |

Em outras execuções: "2 dólares" (`ZZ-aidcaj-a`) virou R$ 10,69, cotação 5,3462, gravado uma vez. Um evento aprovado pelo chat foi gravado uma vez (id 6; o marcador, escrito com números, foi trocado pelo guardrail, e por isso passamos a usar letras). Quando o juiz recusou um pedido, o banco ficou com 0 linhas e o contador de `id` mesmo assim subiu, o que mostra que a tool **chegou a gravar** e o `ROLLBACK` desfez. Um erro da Groq no meio do turno também deixou 0 linhas. Num turno simulado com o juiz recusando, o histórico ficou só com a fala do usuário e a frase segura.

**O que não foi provado:**

- A aprovação de `update_transaction` **pelo chat** (a correção foi recusada pelo juiz); ela está provada só nas tools.
- A criação **real** de evento no Google.
- Consulta de saldo e de agenda pelo chat (a Groq bateu o limite de uso).
- O motivo exato das recusas pelo chat (euro, evento e correção): vimos o resultado, não o veredito do juiz.
- O chat completo depois das últimas correções (trava e limpeza do histórico).
- O veredito do juiz varia entre execuções para o mesmo pedido. A regra vale nos dois lados: aprovado grava uma vez, recusado não deixa nada.

## 5. Arquivos criados e arquivos modificados

### Criados

- `app/unidade_trabalho.py`: o rascunho do turno (`abrir_turno_db`, `confirmar`, `desfazer`, `liberar`, `@serializado`, `depois_do_commit`, `encerrar_turno_db`).
- `app/cambio.py`: converte dólar e euro para reais.
- `testes/prova_depois.py`: prova nas tools, incluindo tools em paralelo e o Google falso.
- `testes/serie_testes.py`: série de pedidos pelo chat completo (`--sem-juiz` força a recusa).
- `testes/mede_latencia.py`: mede o custo da transação por turno, sem modelo.
- `testes/reproduz_desencontro.py`: reproduz o problema original (o "antes"); ainda não foi executado.
- `testes/schema_local.sql`: esquema mínimo para o Postgres local.
- `decisao_tecnica.md` e `MUDANCAS.md`: documentação.

### Modificados

- `app/db.py`: `get_pgsql_conn()` entrega a conexão do turno quando há turno.
- `app/tools/financeiro.py`: usa `confirmar`, `desfazer` e `@serializado`; `update_transaction` entrou na lista `TOOLS`; `add_transaction` ganhou `currency`.
- `app/tools/agenda.py`: usa `confirmar`, `desfazer`, `liberar` e `@serializado`; `add_google_event` cria o evento só depois do `COMMIT`.
- `app/juiz.py`: marca `escrita_autorizada`, limpa o histórico na recusa (`rastro_do_turno`) e o timeout caiu de 30 s para 5 s.
- `app/graph.py`: abre e fecha o rascunho do turno e limpa o histórico em caso de exceção.
- `app/config.py`: cotação de reserva (`USD_BRL`, `EUR_BRL`) e `GOOGLE_OAUTH_CREDENTIALS`.
- `app/prompts.py`: regra de moeda no financeiro; regra de `add_event` e `add_google_event` na agenda.
- `.gitignore`: passou a ignorar logs, `historico.json` e saídas de teste locais, que deixaram de ser versionados.
