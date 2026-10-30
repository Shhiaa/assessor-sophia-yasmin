# Decisão técnica — Etapa 8: o juiz e a escrita

Aqui está o que decidimos fazer com o problema do juiz que fala tarde demais, o que a gente descartou e como conferimos que funciona. A lista completa das mudanças está no `MUDANCAS.md`.

## 1. A decisão tomada, e por quê

O problema era simples de explicar. Quando o usuário pedia "registre uma compra", a tool gravava no banco e já dava `commit()`. O juiz só olhava a frase depois disso. Se ele recusava e trocava a resposta por "Não tenho evidência para afirmar isso.", a linha continuava lá. O chat dizia não e o banco dizia sim. Isso valia para as três tools que escrevem: `add_transaction`, `update_transaction` e `add_event`.

A fronteira que passou a autorizar a escrita é o veredito do juiz. Antes, quem autorizava era o fim da tool. Agora a escrita só vira permanente se o juiz aprovar.

Para isso, cada turno (cada mensagem do usuário) abre uma transação do Postgres que serve de rascunho. As tools escrevem nela, mas não confirmam. O juiz decide e deixa `escrita_autorizada` no estado. No fim do turno, em `executar_fluxo_assessor` (`app/graph.py`), se o juiz aprovou a gente dá `COMMIT`. Em qualquer outro caso dá `ROLLBACK`: juiz recusou, juiz deu erro ou demorou, faltou a chave dele, ou deu exceção no meio.

Como era: o modelo escolhe a tool, a tool grava e confirma, o orquestrador escreve a frase, o guardrail revisa, o juiz pode trocar a frase, e o chat mostra o texto. A linha continua no banco.

Como ficou: o modelo escolhe a tool, a tool grava no rascunho, o orquestrador escreve a frase, o guardrail revisa, o juiz decide, e só então o fim do turno confirma ou apaga.

Isso cabe bem neste grafo por alguns motivos. Todas as tools pegam a conexão no mesmo lugar (`get_pgsql_conn()` em `app/db.py`), então um ponto só cobre as três. A tool enxerga o que ela mesma acabou de gravar, então "registre e me diga o saldo" continua funcionando e o `id` real chega ao juiz como evidência. Não criamos rota, endpoint nem agente. E se o programa cair no meio do turno, o Postgres joga fora a transação aberta sozinho. Fora do chat (servidor MCP, scripts) nada muda: as tools continuam com a conexão normal e o commit na hora.

O código novo fica em `app/unidade_trabalho.py`. As tools passaram a chamar `confirmar`, `desfazer` e `liberar` no lugar de `commit`, `rollback` e `close`. Dentro de um turno essas funções não confirmam nada; fora dele fazem o que sempre fizeram. Cada chamada de tool roda dentro de um `SAVEPOINT`, para que uma tool que falha desfaça só ela. O decorador `@serializado` faz uma tool de cada vez, porque o LangGraph pode rodar várias em paralelo.

A garantia em cada tool:

- `add_transaction`: se o turno for recusado, nenhuma linha nova em `transactions`. Se for aprovado, uma linha, confirmada uma vez.
- `update_transaction`: recusado, o registro continua com os valores antigos. Aprovado, a alteração é confirmada uma vez.
- `add_event`: recusado, nenhum evento novo em `events`. Aprovado, um evento, confirmado uma vez.

Uma coisa que a gente só percebeu depois: o histórico da conversa também precisava de limpeza. O LangGraph guarda as mensagens do turno, e se depois de uma recusa ele ainda dissesse "Registrei sua despesa de 30 reais", o modelo do turno seguinte acreditaria numa linha que não existe. Por isso, na recusa, o juiz tira do histórico as mensagens de IA e de tool daquele turno (`rastro_do_turno`, em `app/juiz.py`). Numa exceção, o `graph.py` faz a mesma limpeza. Sobra a fala do usuário e a frase segura.

Mais um detalhe: "Não tenho evidência para afirmar isso." não diz ao usuário que nada foi salvo, e ele poderia achar que o registro foi feito. Por isso, quando o turno recusado tinha tentado escrever (chamou `add_transaction`, `update_transaction`, `add_event` ou `add_google_event`), o juiz responde "Não consegui confirmar esse pedido, então nada foi registrado." Nos turnos sem escrita a frase continua a de antes. As respostas das tabelas de evidência pelo chat abaixo foram geradas antes dessa mudança, por isso ainda aparecem com a frase antiga.

## 2. As decisões que o grupo não tomou, e por quê

| Caminho | Como funcionaria aqui | Uma vantagem | Um custo | Por que ficou de fora |
| --- | --- | --- | --- | --- |
| Interceptar (julgar antes da tool) | Um julgamento antes de a tool escrever | Nada chega a gravar | O juiz atual compara a frase pronta com o retorno da tool, e antes da tool não existe nenhum dos dois. Precisaria de um segundo juiz e de uma chamada a mais por escrita | Não pega o caso da aula: o "R$ 30,00" errado só nasce depois da tool, com os argumentos certos |
| Adiar (gravar só depois do veredito) | A tool só anota a intenção e outro passo grava depois | Nenhuma conexão fica aberta durante o turno | A tool não devolve o `id` real nem enxerga a própria escrita. "Corrija o gasto que acabei de registrar" quebra e o INSERT teria que ser duplicado | O Postgres já faz esse adiamento com a transação, sem duplicar código |
| Compensar (gravar e desfazer se recusar) | A tool confirma e, na recusa, outra ação apaga a linha ou devolve o valor antigo | Quase não muda o caminho feliz | A linha fica visível por um tempo (painel, MCP, outra sessão). Se o programa cair nesse intervalo, o lixo fica para sempre. E apagar também pode falhar | A regra é "nenhuma escrita permanece". Compensar é limpar depois, não impedir |

O custo da solução que escolhemos também precisa ficar claro: a conexão fica aberta enquanto o orquestrador, o guardrail e o juiz trabalham, e uma linha alterada fica travada para outras sessões nesse tempo. Para limitar isso, o juiz tem no máximo 5 segundos para responder (`JEV_TIMEOUT_S` em `app/juiz.py`, eram 30) e o Postgres derruba a conexão do turno se ela ficar parada mais de 60 segundos. Com 30 segundos ele derrubou um turno em que o modelo demorou entre duas tools. Nesse caso nada foi salvo.

## 3. O efeito nas métricas do SRE, e como

O monitor (`app/observabilidade`) mede o turno de `abrir_turno()` até `fechar_turno()`. Ele só conta erro quando há exceção e só soma tokens e custo das chamadas aos modelos de chat. A chamada ao juiz usa `urllib` e fica fora dessa conta.

O que medimos de verdade foi o custo da transação sozinha, com `testes/mede_latencia.py` (100 escritas de cada tipo, sem modelo, Postgres local). Um turno com escrita levava de 8 a 11 ms antes, com o `commit` dentro da tool. Depois, levou de 35 a 41 ms aprovado e de 33 a 38 ms recusado. Dá uns 27 a 30 ms a mais, e quase tudo isso é abrir a conexão do turno. Esse banco é local, sem rede; num banco remoto cada ida e volta soma. O p95 de um turno completo, com modelos e juiz, a gente não conseguiu tirar: tivemos só dois turnos reais pelo monitor, e a latência deles foi dominada pelo modelo (veja a seção 4). O resto da tabela abaixo é raciocínio sobre o mecanismo.

| Métrica | Transacionar (a decisão) | Interceptar (descartada) | Compensar (descartada) |
| --- | --- | --- | --- |
| Latência do turno e p95 (SLO de 8 s) | Nenhuma chamada a mais ao modelo; soma uma conexão e um `COMMIT` ou `ROLLBACK`, uns 30 ms medidos. O risco é a conexão ficar aberta esperando o veredito, e o limite de 5 s do juiz protege o p95 | Uma chamada a mais antes de cada escrita, no caminho principal | Nada no caminho feliz; na recusa, uma ida a mais ao banco |
| Taxa de erro (SLO de 5%) | Recusa termina numa frase segura, sem exceção, então não conta como erro. Falha de conexão ou de `COMMIT` é exceção e conta. O bloqueio do guardrail de entrada também não conta como erro | A falha do pré-julgamento viraria exceção | A falha ao apagar é um segundo efeito e aumenta a taxa |
| Status (`ok`, `erro`, `bloqueado`) | Recusa do juiz fica `ok`; `bloqueado` continua só para o guardrail de entrada. Se o juiz cair com falha fechada, vira `ok` com as escritas recusadas | Igual, com mais um motivo de erro | Recusa compensada fica `ok`, mas a linha existiu por um tempo |
| Tokens e custo | Não muda | Uma chamada de modelo a mais por escrita, se o julgamento for um LLM | Não muda |

Um contador dos rótulos `[juiz]` no `/monitor` mostraria as recusas causadas por falha do juiz. Ficou fora desta entrega.

## 4. As outras decisões

O que entrou no escopo e o que ficou de fora:

| Peça | O que fizemos | Por quê |
| --- | --- | --- |
| `add_transaction`, `update_transaction`, `add_event` | Entram. Escrevem no rascunho do turno | São as três escritas obrigatórias |
| `update_transaction` | Entrou na lista `TOOLS` do agente financeiro (`app/tools/financeiro.py`; o `app/agents.py` não mudou) | Ela existia, mas só o servidor MCP usava. Sem isso, "alterar um lançamento" nem rodava no chat |
| Leituras (`search_transactions`, `saldo_total`, `saldo_diario`, `query_events`) | Continuam, usando a conexão do turno | Só a mesma conexão enxerga a escrita que ainda não foi confirmada |
| `add_google_event` | Entra, mas adiada. No chat a tool só anota o pedido e responde "pendente"; o evento é criado no Google depois do `COMMIT` aprovado. Se recusar ou der exceção, o pedido é descartado. Fora do chat, cria na hora | Um evento no Google não participa da transação do Postgres, então o `ROLLBACK` não o apagaria. Adiar evita que o Google veja uma escrita recusada. Poderíamos apagar com o `delete-event`, mas precisaríamos do id do evento, num formato que não dá para conferir sem as credenciais |
| `salvar_mensagem` (Mongo) | Fica de fora, continua depois do juiz | Grava a frase que o usuário de fato viu, não um efeito de domínio. O Mongo não participa da transação do Postgres |
| Compras em dólar ou euro | Entram. `add_transaction` aceita `currency` (BRL, USD ou EUR) e converte para reais (`app/cambio.py`, com a AwesomeAPI e uma cotação de reserva no `.env`). A tool devolve valor original, cotação e fonte | Era o caso da aula: gravar `amount=2` e responder "R$ 2" fazia o juiz recusar por moeda trocada. Agora a conversão vira evidência. Não usamos biblioteca de câmbio porque todas embrulham uma API ou um dado velho |
| Tools ao mesmo tempo | `@serializado`: uma por vez no rascunho | Sem a trava, 4 `add_transaction` simultâneas perderam linhas em 8 de 12 turnos. Com a trava, em 0 de 12 |

Sobre o Google, vale dizer os limites. A resposta ao usuário diz que o evento "será criado"; se o Google falhar depois do `COMMIT`, a falha só aparece no log. A chamada real ao Google nunca foi testada, porque aqui não tem Node.js nem credenciais. Os testes usam um Google falso. E o PDF do FAQ ainda diz que não existe calendário externo, o que contradiz a tool.

Se o juiz não responde, ele falha fechado: timeout, erro HTTP, falta de chave ou resposta vazia viram recusa. O usuário vê a frase segura e o banco fica como estava. Confirmar sem veredito aprovaria algo que ninguém conferiu, e repetir a chamada somaria tempo com a transação aberta.

Se o processo cair entre dois passos, antes do `COMMIT` não resta nada, porque o Postgres joga a transação fora. A única janela é cair depois do `COMMIT` e antes de entregar a resposta: a linha existe, mas o usuário não viu o texto, e o Mongo pode ficar sem a mensagem do assistente. É um risco que aceitamos.

Se o mesmo pedido for repetido, não há bloqueio de duplicados. A garantia é de no máximo uma confirmação por turno. Um pedido recusado e repetido depois, se aprovado, grava uma linha, porque a recusa não deixa rastro. Dois turnos aprovados gravam duas linhas. Duas compras iguais podem ser legítimas (duas cocas no mesmo dia), e uma regra de unicidade mudaria o banco.

### Evidência, com marcador único

O banco do `.env` não existe mais (o endereço sumiu do DNS), então testamos num Postgres 17 local no Docker, com um esquema que deduzimos do código (`testes/schema_local.sql`). Cada tentativa tem um marcador único `ZZ-...`, e a leitura é feita por outra conexão, que só enxerga o que foi confirmado.

Antes da correção, no código que veio da Etapa 7 (`testes/reproduz_desencontro.py`, camada das tools, rodada `125324`). A leitura por outra conexão, logo depois da tool e antes de qualquer veredito, já enxerga a escrita:

| Tool | Marcador | Antes | Depois (outra conexão) |
| --- | --- | --- | --- |
| `add_transaction` | `ZZ-125324-1` | 0 linhas | 1 linha |
| `update_transaction` | `ZZ-125324-2` | valor 30,00 | valor 50,00 |
| `add_event` | `ZZ-125324-3` | 0 eventos | 1 evento |

Depois da correção, nas tools (`testes/prova_depois.py`, sem modelo e sem juiz, rodada `124507`):

| Tool | Recusa sem efeito | Aprovação uma única vez |
| --- | --- | --- |
| `add_transaction` | `ZZ-124507-1`: 0 linhas | `ZZ-124507-2`: 1 linha (id 911) |
| `add_event` | `ZZ-124507-3`: 0 eventos | `ZZ-124507-4`: 1 evento (id 24) |
| `update_transaction` | descrição `ZZ-124507-6` recusada: o registro 912 manteve a antiga | a mesma descrição aprovada: aplicada uma vez |
| `add_google_event` (Google falso) | `ZZ-124507-g1`: 0 chamadas ao Google | `ZZ-124507-g2`: 1 chamada, depois do `COMMIT` |
| Duas escritas no mesmo turno | `ZZ-124507-7`: 0 linhas | — |
| 4 tools em paralelo | — | `ZZ-124507-p0` a `p11`: 12 turnos, 48 linhas (4 em cada) |

Deu 14 de 14 verificações passando (as duas últimas são da frase da recusa, descrita na seção 1).

Pelo chat, com o juiz de verdade e o código final (`testes/serie_testes.py`, rodada `agfecj`). O marcador usa letras, porque o guardrail troca números por `[PII_CONTA_...]`:

| Pergunta | Resposta | Banco |
| --- | --- | --- |
| "Comprei um lanche por 15,50 dólares hoje, descrição `ZZ-agfecj-a`…" | "Lancei R$ 83,47 em ALIMENTAÇÃO 30/10/2026 (USD 15,50)." | 1 linha de R$ 83,47 (id 1022) |
| "Paguei 20 euros num jantar hoje, descrição `ZZ-agfecj-b`…" | "Não consegui confirmar esse pedido, então nada foi registrado." (recusa) | 0 linhas |
| "Gastei 30 reais com um lanche hoje, descrição `ZZ-agfecj-c`…" | "Lancei R$ 30,00 em ALIMENTAÇÃO …" | 1 linha de R$ 30,00 (id 1024) |
| "Gastei 20 reais no mercado hoje, descrição `ZZ-agfecj-d`…" | "Lançamento de R$20,00 em ALIMENTAÇÃO registrado com sucesso." | 1 linha de R$ 20,00 |
| "Corrija o gasto `ZZ-agfecj-d` para 25 reais." (mesma conversa) | "Despesa 'ZZ-agfecj-d' atualizada para R$ 25,00." | a mesma linha (id 1025) com R$ 25,00, sem linha nova |
| "Registre na agenda o compromisso `ZZ-agfecj-e` em 02/01/2030 às 15:00…" | "Compromisso 'ZZ-agfecj-e' agendado para 02/01/2030 às 15:00 na sala 3." | 1 evento (id 31) |

Em execuções anteriores, quando o juiz recusou um pedido de transação ou de evento, o banco ficou com 0 linhas e o contador de `id` subiu mesmo assim, o que mostra que a tool chegou a gravar e o `ROLLBACK` desfez. Um erro da Groq no meio do turno também deixou 0 linhas. Num turno simulado com o juiz recusando, o histórico ficou só com a fala do usuário e a frase segura. Em 2 dólares (`ZZ-aidcaj-a`), a conversão deu R$ 10,69 (cotação 5,3462).

Consulta de saldo pelo chat: com o `gpt-oss-20b` no especialista, "Qual meu saldo total?" devolveu o saldo (R$ -1.156,14 no banco local) em 2 de 3 tentativas, e na terceira o juiz recusou com a frase segura. Com o `gpt-oss-120b` a Groq devolveu erro 400 de tool call inválida em 4 de 4 tentativas no código novo e em 3 de 4 no código antigo, então esse erro já existia antes da nossa mudança.

Monitor (`montar_painel`, `app/routes/monitor.py`), com dois turnos reais depois da correção: os dois ficaram `ok`, rota `financeiro`, 7,5 s e 52,8 s, uns 11,5 mil tokens e US$ 0,0014 cada. O segundo turno estourou os 8 s, e acreditamos que foi a espera de nova tentativa da Groq, mas não conseguimos separar isso do resto. Com duas amostras o p95 não significa nada. Numa rodada em que a Groq estava no limite, o monitor também registrou os turnos que deram exceção, com status `erro`, como esperado.


O que não conseguimos provar:

- A criação real de um evento no Google (aqui não tem Node.js nem credenciais).
- Consulta de agenda pelo chat: na única tentativa, a Groq deu erro 400 e o sistema respondeu "Desculpe, tive um problema ao processar sua mensagem". A consulta funciona dentro de um turno quando chamada direto (a busca acha o evento ainda não confirmado).
- O p95 de um turno completo, por falta de amostras.
- O veredito do juiz muda de uma execução para outra com o mesmo pedido (o euro, por exemplo, foi recusado). A regra vale dos dois lados: aprovado grava uma vez, recusado não deixa nada.

## 5. Arquivos criados e arquivos modificados

Criados:

- `app/unidade_trabalho.py`: o rascunho do turno (`abrir_turno_db`, `confirmar`, `desfazer`, `liberar`, `@serializado`, `depois_do_commit`, `encerrar_turno_db`).
- `app/cambio.py`: converte dólar e euro para reais.
- `testes/prova_depois.py`: prova nas tools, com tools em paralelo e o Google falso.
- `testes/serie_testes.py`: série de pedidos pelo chat completo (`--sem-juiz` força a recusa).
- `testes/mede_latencia.py`: mede o custo da transação por turno, sem modelo.
- `testes/reproduz_desencontro.py`: reproduz o problema original, o "antes". Ainda não foi executado.
- `testes/schema_local.sql`: esquema mínimo para o Postgres local.
- `decisao_tecnica.md` e `MUDANCAS.md`: documentação.

Modificados:

- `app/db.py`: `get_pgsql_conn()` entrega a conexão do turno quando há um turno aberto.
- `app/tools/financeiro.py`: usa `confirmar`, `desfazer` e `@serializado`; `update_transaction` entrou na lista `TOOLS`; `add_transaction` ganhou `currency`.
- `app/tools/agenda.py`: usa `confirmar`, `desfazer`, `liberar` e `@serializado`; `add_google_event` cria o evento só depois do `COMMIT`.
- `app/juiz.py`: marca `escrita_autorizada`, limpa o histórico na recusa (`rastro_do_turno`) e o timeout caiu de 30 s para 5 s e a recusa de um turno que tentou escrever tem frase própria (`FRASE_SEM_ESCRITA`).
- `app/graph.py`: abre e fecha o rascunho do turno e limpa o histórico se der exceção.
- `app/config.py`: cotação de reserva (`USD_BRL`, `EUR_BRL`) e `GOOGLE_OAUTH_CREDENTIALS`.
- `app/prompts.py`: regra de moeda no financeiro; regra de `add_event` e `add_google_event` na agenda.
- `.gitignore`: passou a ignorar logs, `historico.json` e saídas de teste locais, que deixaram de ser versionados.
