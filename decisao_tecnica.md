# Decisão técnica — Etapa 8: o juiz e a escrita

Este arquivo conta, em linguagem simples, o que decidimos, por que, o que ficou de fora e como provamos que funciona. A lista completa de mudanças está no `MUDANCAS.md`. Não há chaves, tokens, `.env` nem senhas aqui.

## 1. O que decidimos, e por quê

### O problema

Quando o usuário pede "registre uma compra", a tool grava no banco e confirma na hora (`commit()`). Só depois o juiz confere a frase que vai aparecer no chat. Se o juiz não gostar e trocar a frase por "Não tenho evidência para afirmar isso.", a linha **já está salva**. O chat diz "não" e o banco diz "sim".

Isso acontecia nas três tools que escrevem: `add_transaction`, `update_transaction` (em `app/tools/financeiro.py`) e `add_event` (em `app/tools/agenda.py`).

### A decisão: uma transação por turno

Pense num rascunho a lápis. Cada pergunta do usuário (um "turno") ganha o seu próprio rascunho no banco:

1. As tools escrevem no rascunho, mas **não confirmam**.
2. O juiz dá o veredito.
3. Se o juiz aprovou, o fim do turno **passa a caneta** (`COMMIT`). Em qualquer outro caso, **apaga o rascunho** (`ROLLBACK`).

"Qualquer outro caso" inclui: juiz recusou, juiz deu erro, juiz demorou demais, faltou a chave do juiz ou deu exceção no meio do turno. Na dúvida, nada fica salvo.

**Como era:** o modelo escolhe a tool → a tool grava e confirma → o orquestrador escreve a frase → o juiz pode trocar a frase → o chat mostra o texto (a linha continua no banco).

**Como ficou:** o modelo escolhe a tool → a tool grava no rascunho → o orquestrador escreve a frase → **o juiz decide** → o fim do turno confirma ou apaga.

### Como isso funciona no código

Tudo mora em `app/unidade_trabalho.py`:

- No começo do turno, o `graph.py` abre o rascunho (`abrir_turno_db`). A conexão com o banco só é criada quando uma tool precisa dela.
- A função `get_pgsql_conn()` (em `app/db.py`) entrega a conexão do turno quando existe um turno aberto. Fora do chat (servidor MCP, scripts) tudo continua como antes: conexão normal e confirmação imediata.
- As tools usam `confirmar`, `desfazer` e `liberar` no lugar de `commit`, `rollback` e `close`. Dentro de um turno essas funções não confirmam nada; fora dele, fazem o que sempre fizeram.
- Cada chamada de tool usa um `SAVEPOINT` (um "marcador" dentro do rascunho). Se uma tool falha, só ela é desfeita, e as outras do mesmo turno continuam.
- O decorador `@serializado` faz **uma tool de cada vez** no rascunho. O LangGraph pode rodar várias tools ao mesmo tempo, e sem essa trava elas se atropelavam.
- No fim, `encerrar_turno_db(autorizado)` confirma ou apaga e fecha a conexão.

### Por que essa solução cabe neste projeto

- Cobre as três tools de uma vez, porque todas pegam a conexão no mesmo lugar. Corrigir só uma deixaria o mesmo furo nas outras.
- Não criou rota, endpoint nem agente novo.
- A tool enxerga o que ela mesma acabou de gravar. Por isso "registre e me diga o saldo" funciona, e o número de identificação (`id`) da linha volta de verdade para o juiz usar como evidência.
- Se o programa cair no meio do turno, o Postgres joga fora o rascunho sozinho.

### A garantia

- **Turno recusado:** nenhuma linha criada ou alterada em `transactions` ou `events`.
- **Turno aprovado:** a escrita é confirmada uma única vez, no fim do turno.

### O histórico da conversa também é limpo

O LangGraph guarda as mensagens do turno. Se, depois de uma recusa, o histórico ainda dissesse "Registrei sua despesa de 30 reais", o modelo do turno seguinte acreditaria numa linha que não existe. Por isso:

- **Na recusa**, o juiz remove do histórico todas as mensagens de IA e de tool daquele turno (`rastro_do_turno`). Sobra a fala do usuário e a frase segura.
- **Numa exceção**, o `graph.py` faz a mesma limpeza antes de repassar o erro.

## 2. O que não escolhemos, e por quê

| Caminho | Como seria aqui | O lado bom | O lado ruim | Por que ficou de fora |
| --- | --- | --- | --- | --- |
| **Interceptar** (julgar antes da tool) | Um julgamento antes de a tool escrever | Nada chega a gravar | O juiz compara a frase pronta com o que a tool devolveu. Antes da tool não existe nem uma coisa nem outra. Ainda custaria uma chamada a mais por escrita | Não pega o caso da aula: o "R$ 30,00" errado nasce depois da tool, com os argumentos certos |
| **Adiar** (gravar só depois do veredito) | A tool só anota a intenção e outro passo grava depois | Nenhuma conexão fica aberta durante o turno | A tool não devolve o `id` real, não enxerga a própria escrita e quebra "corrija o gasto que acabei de registrar". O INSERT teria que ser duplicado | O Postgres já faz esse adiamento com a transação, sem duplicar código |
| **Compensar** (gravar e desfazer na recusa) | A tool confirma e, se o juiz recusar, outra ação apaga a linha | Quase não muda o caminho feliz | A linha fica visível por um tempo (painel, MCP, outra sessão). Se o programa cair nesse intervalo, o lixo fica para sempre. Apagar também pode falhar | A regra diz "nenhuma escrita permanece". Compensar é limpar depois, não impedir |

**Custo da solução que escolhemos:** a conexão fica aberta enquanto o orquestrador, o guardrail e o juiz trabalham, e uma linha alterada fica travada para outras sessões nesse tempo. Para limitar isso:

- o juiz tem no máximo **5 segundos** para responder (`JEV_TIMEOUT_S` em `app/juiz.py`; antes eram 30);
- o Postgres derruba a conexão do turno se ela ficar parada por mais de **60 segundos** (com 30 s ele derrubou um turno em que o modelo de linguagem demorou entre duas tools; nesse caso nada foi salvo).

## 3. O que muda nas métricas do SRE

O monitor (`app/observabilidade`) mede o turno do começo ao fim, conta erro só quando há exceção e soma tokens e custo só das chamadas aos modelos de chat. A chamada ao juiz usa `urllib` e não entra nessa conta. Abaixo está o raciocínio sobre o mecanismo. Não medimos latência com carga real.

| Métrica | Solução escolhida (transacionar) | Interceptar (descartada) | Compensar (descartada) |
| --- | --- | --- | --- |
| **Latência e p95** (limite: 8 s) | Nenhuma chamada a mais ao modelo. Soma uma conexão e um `COMMIT` ou `ROLLBACK`, que levam milissegundos. O risco é o juiz demorar, e o limite de 5 s protege | Uma chamada de julgamento antes de cada escrita, bem no caminho principal | Nada no caminho feliz. Na recusa, uma ida a mais ao banco |
| **Taxa de erro** (limite: 5%) | Recusa termina numa frase segura, sem exceção: **não conta como erro**. Falha de conexão ou de `COMMIT` é exceção e conta | A falha do pré-julgamento viraria exceção | A falha ao apagar é um segundo problema e aumenta a taxa |
| **Status** (`ok`, `erro`, `bloqueado`) | Recusa do juiz fica `ok`. `bloqueado` continua só para o guardrail de entrada. Se o juiz cair, vira `ok` com escritas recusadas | Igual, com mais um motivo de erro | Recusa compensada fica `ok`, mas a linha existiu por um tempo |
| **Tokens e custo** | Nada muda | Uma chamada de modelo a mais por escrita | Nada muda |
| **Conexões** | Uma conexão presa durante o turno que escreve | Nenhuma | Nenhuma extra |

Um contador dos rótulos `[juiz]` no `/monitor` mostraria as recusas por falha do juiz. Ficou fora desta entrega.

## 4. As outras decisões

### O que acontece se o juiz não responde

Ele **falha fechado**: o usuário vê a frase segura e o banco volta ao que era. Vale para timeout, erro e falta de chave. Confirmar sem veredito aprovaria algo que ninguém conferiu. Repetir a chamada somaria tempo com a transação aberta.

### `update_transaction` agora funciona no chat

Ela existia, mas não estava na lista de tools do agente financeiro (só o servidor MCP a usava). Coloquei na lista `TOOLS` de `app/tools/financeiro.py`. O `app/agents.py` não precisou mudar. Sem isso, o cenário de "alterar um lançamento" nem rodaria no chat.

### Leituras

`search_transactions`, `saldo_total`, `saldo_diario` e `query_events` também usam a conexão do turno. Só ela enxerga o que acabou de ser gravado e ainda não confirmado.

### Tools ao mesmo tempo

O LangGraph pode rodar várias tools em paralelo. Sem a trava, quatro `add_transaction` simultâneas perderam linhas em **8 de 12 turnos**. Com a trava (`@serializado`), **0 de 12**.

### Google Agenda (`add_google_event`)

Um evento no Google não faz parte do Postgres, então o `ROLLBACK` não o apagaria. A solução foi **não criar o evento antes do veredito**:

- No chat, a tool só anota o pedido e responde "pendente" (`depois_do_commit` em `app/unidade_trabalho.py`).
- Depois do `COMMIT` aprovado, o fim do turno cria o evento no Google.
- Se o juiz recusar ou houver exceção, o pedido é descartado e o Google nunca é chamado.
- Fora do chat (MCP, scripts), a tool continua criando na hora.

**Limites, sem esconder:** a resposta ao usuário diz que o evento "será criado". Se o Google falhar depois do `COMMIT`, a falha só aparece no log. A chamada **real** ao Google nunca foi testada (aqui não há Node.js nem credenciais): os testes usam um Google falso. O PDF do FAQ ainda diz que não há calendário externo, e isso contradiz a tool.

**Alternativa que não usamos:** apagar o evento com o `delete-event` do servidor MCP. Daria para fazer, mas exigiria o id do evento, num formato que não conseguimos conferir sem as credenciais.

### Salvar a mensagem no Mongo (`salvar_mensagem`)

Fica como está, **depois** do juiz. Ela guarda a frase que o usuário de fato viu. O Mongo não participa da transação do Postgres.

### Compras em dólar ou euro

Este era o caso visto em aula: a tool gravava `amount=2` sem moeda, o chat respondia "R$ 2" e o juiz recusava por moeda trocada. Agora `add_transaction` aceita `currency` (BRL, USD ou EUR), converte para reais (`app/cambio.py`) e devolve valor original, cotação e fonte. Esses dados servem de evidência para o juiz.

- A cotação vem da AwesomeAPI (sem chave, sem biblioteca nova). Se ela falhar, usa a cotação de reserva `USD_BRL` ou `EUR_BRL` do `.env`.
- Descartamos bibliotecas de câmbio: todas embrulham uma API ou um dado velho, e `python-bcb` traz o `pandas`.
- A tabela não mudou: o valor em reais fica em `amount` e o texto original (com os dólares) fica em `source_text`.

### Repetir o mesmo pedido

Não há bloqueio de duplicados. A garantia é no máximo **uma confirmação por turno**. Se um pedido for recusado e o usuário repetir e for aprovado, grava uma linha. Duas compras iguais podem ser legítimas (duas cocas no mesmo dia), e uma regra de unicidade mudaria o banco.

### Se o programa cair no meio

Antes do `COMMIT`, o Postgres joga o rascunho fora. A única janela que sobra é cair **depois** do `COMMIT` e antes de entregar a resposta: a linha existe, mas o usuário não viu o texto.

## 5. Como provamos que funciona

**Onde testamos:** o banco do `.env` não existe mais (o endereço sumiu do DNS). Usamos um Postgres 17 local no Docker, com um esquema que deduzimos do código (`testes/schema_local.sql`). Se o esquema real for diferente, vale rodar de novo no banco certo. Nos testes pelo chat, o modelo do especialista foi a Groq (a cota do Gemini acabou) e a memória de longo prazo ficou desligada (o Qdrant recusa conexão aqui). Isso não afeta as escritas.

### Teste direto nas tools (`testes/prova_depois.py`)

Sem modelo de linguagem e sem juiz. A leitura é feita por outra conexão, então só aparece o que foi confirmado. Cada tentativa tem um marcador único (`ZZ-<hora>-<número>`).

| Tool | Turno recusado | Turno aprovado |
| --- | --- | --- |
| `add_transaction` | 0 linhas | 1 linha |
| `add_event` | 0 eventos | 1 evento |
| `update_transaction` | valor antigo mantido | valor novo, uma vez |
| `add_google_event` (Google falso) | 0 chamadas ao Google | 1 chamada, depois do `COMMIT` |
| Duas escritas no mesmo turno | 0 linhas | — |
| Quatro tools ao mesmo tempo | — | 12 turnos, 4 linhas em todos |

**Resultado: 12 de 12 verificações passaram.**

### Pelo chat, com o juiz real (`testes/serie_testes.py`)

O marcador usa letras, porque o anonimizador do guardrail troca números por `[PII_CONTA_...]`.

| Pergunta | Resposta | Banco |
| --- | --- | --- |
| "Comprei um lanche por 15,50 dólares hoje…" | "Lancei R$ 82,83 em ALIMENTAÇÃO … (USD 15,50)." | 1 linha de R$ 82,83 |
| "Gastei 30 reais com um lanche hoje…" | "Lancei R$30,00 em ALIMENTAÇÃO registrado com sucesso." | 1 linha de R$ 30,00 |
| "Gastei 20 reais no mercado hoje…" | "Registro de R$ 20,00 … adicionado com sucesso." | 1 linha |
| "Registre na agenda o compromisso … em 02/01/2030 às 15:00…" | "Não tenho evidência para afirmar isso." (recusa) | 0 eventos |
| "Paguei 20 euros num jantar hoje…" | "Não tenho evidência para afirmar isso." (recusa) | 0 linhas |
| "Corrija o gasto … para 25 reais." | "Não tenho evidência para afirmar isso." (recusa) | continua R$ 20,00 |

Em outras execuções: "2 dólares" virou R$ 10,69 (cotação 5,3462) e foi gravado uma vez. Quando o juiz recusou um pedido, o banco ficou com 0 linhas e o contador de `id` mesmo assim subiu, o que mostra que a tool **chegou a gravar** e o `ROLLBACK` desfez. Um erro da Groq no meio do turno também deixou 0 linhas.

### Histórico

Num turno simulado (fala do usuário, tool, resposta "Registrei sua despesa de 30 reais") com o juiz recusando, o histórico ficou só com a fala do usuário e "Não tenho evidência para afirmar isso." Depois de exceções, ficou só com as falas do usuário.

### O que ainda não foi provado

- A criação **real** de evento no Google (sem Node.js nem credenciais).
- Alterar valor, consultar saldo e consultar agenda **pelo chat**: a Groq bateu o limite de uso. A alteração de valor está provada só nas tools.
- Nos casos recusados pelo chat (euro, evento, correção), vimos o resultado, mas não conferimos o motivo exato do veredito.
- O chat completo não foi repetido depois das últimas correções (trava e limpeza do histórico).
- O veredito do juiz varia entre execuções para o mesmo pedido. A regra vale nos dois lados: aprovado grava uma vez, recusado não deixa nada.

## 6. Arquivos

### Criados

| Arquivo | Para que serve |
| --- | --- |
| `app/unidade_trabalho.py` | O rascunho do turno: conexão, `confirmar`, `desfazer`, `liberar`, `@serializado`, `depois_do_commit`, `encerrar_turno_db` |
| `app/cambio.py` | Converte dólar e euro para reais |
| `testes/prova_depois.py` | Prova nas tools, incluindo tools em paralelo e o Google falso |
| `testes/serie_testes.py` | Série de pedidos pelo chat completo (`--sem-juiz` força a recusa) |
| `testes/reproduz_desencontro.py` | Reproduz o problema original ("o chat disse não, o banco disse sim"). Ainda não foi executado |
| `testes/schema_local.sql` | Esquema mínimo para o Postgres local |
| `decisao_tecnica.md`, `MUDANCAS.md` | Documentação |

### Modificados

| Arquivo | O que mudou |
| --- | --- |
| `app/db.py` | `get_pgsql_conn()` entrega a conexão do turno quando há turno |
| `app/tools/financeiro.py` | Usa `confirmar`, `desfazer` e `@serializado`; `update_transaction` entrou na lista `TOOLS`; `add_transaction` ganhou `currency` |
| `app/tools/agenda.py` | Usa `confirmar`, `desfazer`, `liberar` e `@serializado`; `add_google_event` cria o evento só depois do `COMMIT` |
| `app/juiz.py` | Marca `escrita_autorizada`; limpa o histórico na recusa (`rastro_do_turno`); timeout de 5 s |
| `app/graph.py` | Abre e fecha o rascunho do turno; limpa o histórico em caso de exceção |
| `app/config.py` | Cotação de reserva (`USD_BRL`, `EUR_BRL`) e `GOOGLE_OAUTH_CREDENTIALS` |
| `app/prompts.py` | Regra de moeda no financeiro; regra de `add_event` e `add_google_event` na agenda |
| `.gitignore` | Passou a ignorar logs, `historico.json` e saídas de teste locais |

### Saíram do repositório (continuam no disco)

`_uvicorn.log`, `_uvicorn8001.log`, `historico.json`, `r1_antes.txt`, `r2_depois.txt`, `r3_item7.txt`, `r4_item8.txt`, `resp6c.txt` e `resp6d.txt`.

Rotas, API, frontend, guardrail, memória, monitor e servidor MCP ficaram como estavam.

## 7. Atenção: chave exposta

A chave do juiz (TypeSafe) continua no histórico do commit `042bead`. Apagar o arquivo não resolve: é preciso **revogar a chave** no painel do provedor e gerar outra.
