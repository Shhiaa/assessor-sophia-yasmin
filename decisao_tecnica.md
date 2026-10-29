# Decisão técnica — Etapa 8: o juiz e a escrita

Status: decisão tomada e implementada (lista completa das mudanças em `MUDANCAS.md`); a prova com o Postgres (`testes/prova_depois.py`) ainda precisa ser rodada na máquina com acesso ao banco. `add_google_event` ficou fora da invariante nesta entrega. As seções 1 a 3 descrevem a solução escolhida; a seção 4 reúne as decisões complementares; a seção 5 lista os arquivos. A seção 6 é o registro de cada alteração feita no repositório, com o motivo de cada escolha e o motivo de cada alternativa que ficou de fora.

Nenhuma chave, token, `.env` ou URL com senha aparece neste arquivo.

## 1. A decisão tomada, e por quê

**O problema.** `add_transaction`, `update_transaction` (ambas em `app/tools/financeiro.py`) e `add_event` (`app/tools/agenda.py`) dão `commit()` dentro da própria tool. O juiz (`app/juiz.py`) só roda no fim do grafo, depois do orquestrador e do guardrail de saída. Quando o juiz recusa a frase, a linha já está permanente em `transactions` ou `events`. O chat diz "Não tenho evidência para afirmar isso." e o banco guarda o registro.

**A decisão.** Uma transação por turno. As três tools passam a escrever numa conexão aberta para o turno e não confirmam. Quem confirma é o fechamento do turno, em `executar_fluxo_assessor`, e só quando o juiz aprovou. Recusa, erro ou exceção fazem `rollback()`. A fronteira que autoriza a escrita deixa de ser o fim da tool e passa a ser o veredito do juiz.

Fluxo de antes:

1. O modelo escolhe a tool.
2. A tool grava e dá `commit()`.
3. O orquestrador redige e o guardrail de saída revisa.
4. O juiz pode trocar a frase.
5. O chat mostra o texto. A linha continua no banco.

Fluxo de depois:

1. O modelo escolhe a tool.
2. A tool grava na conexão do turno, sem `commit()`.
3. O orquestrador redige e o guardrail de saída revisa.
4. O juiz decide e deixa `escrita_autorizada` no estado.
5. O fechamento do turno confirma se `escrita_autorizada` for verdadeiro. Em qualquer outro caso desfaz. A conexão é fechada num `finally`.

**Por que essa decisão cabe neste grafo.**

- Cobre as três tools de uma vez, porque todas pedem a conexão ao mesmo ponto. Corrigir só uma deixaria o mesmo furo em outra tabela.
- Não cria rota, endpoint nem agente. Muda a tool, o acesso ao banco, o juiz e o fechamento do turno.
- Dentro do turno a tool enxerga a própria escrita (mesma conexão), então "registre e me diga o saldo" continua funcionando.
- Se o processo cair no meio do turno, o Postgres desfaz sozinho a transação não confirmada. A invariante vale mesmo numa queda.
- Sem `turno_id` no `config` (servidor MCP, scripts) as tools mantêm o `commit()` imediato. Nada fora do chat muda.

**Garantia dada nas três tools.** Turno recusado: nenhuma linha criada nem alterada em `transactions` ou `events`. Turno aprovado: a escrita é confirmada uma vez, no fechamento do turno.

**Dois fatos do código que exigem trabalho extra, e por quê.**

- `app/db.py` guarda uma única conexão Postgres global, compartilhada por todas as requisições. Com uma transação por turno, duas sessões simultâneas se misturariam e o `commit()` de uma confirmaria a escrita pendente da outra. Por isso o turno abre uma conexão própria. A global continua para leituras fora de turno e para o MCP.
- O histórico do LangGraph (`MemorySaver`) guarda o `ToolMessage` com `status: ok` e o `id` da linha. Depois de uma recusa, o modelo do turno seguinte acreditaria que o lançamento existe. Por isso, na recusa, o juiz remove do histórico as mensagens de tool daquele turno.

## 2. As decisões que o grupo não tomou, e por quê

| Caminho | Como funcionaria aqui | Vantagem real | Custo real | Por que ficou de fora |
| --- | --- | --- | --- | --- |
| Interceptar | Um julgamento antes da tool, comparando os argumentos com a fala do usuário | Nada chega a gravar; não precisa de `rollback()` | O juiz atual compara a frase pronta com a evidência da tool. Antes da tool não existem frase nem evidência. Exigiria uma segunda rubrica e uma chamada a mais por escrita | Não cobre o caso visto em aula: "Lancei R$ 30,00" nasce depois da tool, e os argumentos (`amount=30`) estavam corretos |
| Adiar | A tool devolve "pendente" e guarda a intenção no estado; um nó grava depois do veredito | Nenhuma conexão aberta durante o turno | A tool não devolve `id` real e não enxerga a escrita no mesmo turno; um `update` logo depois de um `add` quebra; o INSERT vira código duplicado fora da tool; o MCP precisaria de outro caminho | O Postgres já entrega esse adiamento com a transação, sem duplicar código |
| Compensar | A tool confirma; na recusa, uma ação inversa (`DELETE` ou `UPDATE` ao valor antigo) | Nenhuma mudança no caminho feliz | Entre o commit e a compensação a linha está visível (painel, MCP, outra sessão); uma queda nessa janela deixa a linha para sempre; o `update` precisa guardar o valor anterior; falha na compensação é um segundo efeito | A invariante diz "nenhuma escrita permanece". Compensar é limpeza posterior, não atomicidade |

Custo da decisão tomada, que também fica registrado: a conexão do turno fica aberta durante a chamada do orquestrador, do guardrail e do JEV, e uma linha alterada fica travada para outras sessões nesse intervalo. Mitigações: timeout baixo no JEV e `idle_in_transaction_session_timeout` na conexão do turno.

## 3. O efeito nas métricas do SRE, e como

O monitor (`app/observabilidade`) mede o turno de `abrir_turno()` a `fechar_turno()`, conta erro só quando há exceção e soma tokens e custo apenas das chamadas de chat model captadas pelo callback. A chamada ao JEV usa `urllib` e não entra em tokens nem em custo.

| Métrica | Decisão: transacionar | Interceptar (descartada) | Compensar (descartada) |
| --- | --- | --- | --- |
| Latência e p95 (SLO 8 s) | Nenhuma chamada a mais. Soma uma conexão nova por turno com escrita e um `COMMIT` ou `ROLLBACK` (milissegundos, a medir). O risco é o JEV: com o timeout atual de 30 s a transação fica aberta e o turno estoura o SLO | Uma chamada de julgamento antes de cada escrita, no caminho crítico | Nada no caminho feliz; na recusa, uma ida a mais ao banco |
| Taxa de erro (SLO 5%) | Recusa termina em frase segura sem exceção: não conta como erro. Falha de `COMMIT` ou de conexão levanta exceção e conta | Falha do pré-julgamento vira exceção | Falha da compensação é um segundo efeito e sobe a taxa |
| Status ok, erro, bloqueado | Recusa do juiz fica `ok`. `bloqueado` continua só para o guardrail de entrada. Queda do JEV com falha fechada vira `ok` com escritas recusadas, e não aparece na taxa de erro | Igual, com um motivo a mais de erro | Recusa compensada fica `ok`, mas a linha existiu por uma janela |
| Tokens e custo | Sem mudança nos chat models | Uma chamada de modelo a mais por escrita, se for LLM | Sem mudança |
| Conexões | Uma conexão presa durante o turno com escrita | Nenhuma | Nenhuma extra |

Um contador dos rótulos `[juiz]` no `/monitor` tornaria visível a recusa por indisponibilidade. Fica fora desta entrega.

## 4. As outras decisões

| Ponto | Decisão | Por quê | Alternativa descartada e por quê |
| --- | --- | --- | --- |
| `update_transaction` fora da lista `TOOLS` do agente financeiro | Ligar a tool ao agente em `app/agents.py` | O enunciado trata a alteração como escrita obrigatória, e o prompt do financeiro já manda "atualizar" ao remover um registro. Sem a tool ligada, o cenário de alteração não roda no chat e a garantia ficaria sem prova | Provar só pelo MCP: o MCP não passa pelo grafo nem pelo juiz, então não prova a invariante do chat. Deixar sem cobrir: contradiz o enunciado |
| Juiz sem resposta (timeout, exceção, chave ausente) | Falha fechada: frase fixa e `rollback()` | É o comportamento que o `no_juiz` já tem para a frase, e mantém a invariante: sem conferência, nada permanece. O usuário vê a frase segura e o banco fica como estava | Falha aberta (confirmar sem veredito): aprova uma escrita que ninguém conferiu. Repetir a chamada ao JEV: soma latência contra o SLO de 8 s com a transação aberta. Fila de reprocessamento: complexidade desproporcional para a atividade |
| `add_google_event` | Entra no projeto como tool nova da agenda, criada pelo servidor MCP `@cocal/google-calendar-mcp`. **Nesta primeira versão fica fora da invariante**: o evento no Google é criado na hora da tool, antes do veredito | O grupo decidiu que o projeto tem Google Agenda, e o HTML (slide "Escopo") deixa os dois caminhos em aberto, exigindo a explicação. A tool não existia neste repositório e o FAQ (seção 6.1.2) ainda diz que não há calendário externo. Um efeito fora do Postgres não participa da transação do turno, então o `rollback()` não o alcança. Cobri-lo exige uma decisão própria (ver Alteração 4) | Não incluir: contradiz a decisão do grupo. Usar a API do Google direto (`google-api-python-client`): é síncrona e mais rápida, mas adiciona dependências e um fluxo de OAuth próprio, enquanto o `mcp` já está no `requirements.txt` e o MCP foi a escolha anterior do grupo. Substituir a tabela `events` pelo Google: tiraria o `add_event` do Postgres, que é uma das três escritas obrigatórias do enunciado |
| `salvar_mensagem` (Mongo) | Manter depois do juiz, como está | Grava a frase que o usuário efetivamente viu, não um efeito de domínio. A mensagem do usuário já é salva antes do turno, como registro de conversa | Colocar na mesma unidade de trabalho: o Mongo não compartilha transação com o Postgres, e duas fases seriam desproporcionais. Gravar antes do juiz: registraria uma frase que o usuário nunca viu |
| Leituras (`search_transactions`, `saldo_total`, `saldo_diario`, `query_events`) | Passam a usar a conexão do turno quando existe | Só a mesma conexão enxerga a escrita ainda não confirmada. Sem isso, "registre e diga o saldo" no mesmo turno não encontra a linha nova | Manter a conexão global nas leituras: o modelo confirmaria a escrita, a consulta não a acharia, e a resposta ficaria inconsistente |
| Timeout do JEV | 5 s (hoje 30 s) | O SLO de p95 é 8 s e a transação fica aberta enquanto o JEV responde. A TypeSafe publica latência de dezenas a centenas de milissegundos | Manter 30 s: estoura o SLO e prende a conexão. Valor de 1 a 2 s: transforma qualquer 429 ou 529 em recusa sem necessidade |
| Histórico na recusa | O juiz devolve `RemoveMessage` para as mensagens de tool e as `ai` com `tool_calls` do turno | Evita o `id` fantasma: sem isso, no turno seguinte o modelo acredita que a linha existe e tenta corrigi-la | Apagar a thread inteira: perde o contexto da conversa. Só orientar no prompt: prompt não é garantia |
| Repetição do mesmo pedido | Sem deduplicação. A garantia é no máximo uma confirmação por turno. Recusa seguida de repetição aprovada grava uma linha; dois turnos aprovados gravam duas | Dois lançamentos iguais podem ser legítimos (duas cocas no mesmo dia). A recusa não deixa rastro, então a repetição não duplica | Restrição única em `source_text` ou chave de idempotência por conteúdo: muda o esquema do banco e bloquearia compras repetidas legítimas. Limite conhecido: duas chamadas da mesma tool no mesmo turno gravam duas linhas |
| Queda do processo entre dois passos | Nenhuma ação extra | Antes do fechamento do turno, o Postgres descarta a transação aberta. A linha só existe se o `COMMIT` rodou | Janela restante, aceita e registrada: queda depois do `COMMIT` e antes de entregar a resposta deixa a linha sem o usuário ter visto o texto, e o Mongo pode ficar sem a mensagem do assistente |
| Evidência com marcador único | O script `testes/reproduz_desencontro.py` produz o "antes". O "depois" usa a matriz de testes da implementação | Marcador `ZZ-<hora>-<n>` isola cada tentativa. A camada 1 consulta o banco por uma conexão separada, então prova o commit antes do veredito sem depender do JEV | Confiar só na frase do chat: não mostra o que ficou no Postgres. Usar mocks do banco: não provaria nada sobre o commit real |

Pendência: o "antes" ainda não foi executado. O script foi escrito e a sintaxe validada, mas não pôde rodar no ambiente onde foi criado, que não tem as dependências nem as credenciais do projeto. A saída vai para a seção de evidência quando o grupo rodar na máquina com o `.env`.

## 5. Arquivos criados e arquivos modificados

Criados:

- `decisao_tecnica.md`: este arquivo.
- `testes/reproduz_desencontro.py`: reproduz o desencontro nas três tools, em duas camadas.

Modificados:

- `app/tools/agenda.py`: nova tool `add_google_event` e inclusão na lista `TOOLS_AGENDA`.
- `app/config.py`: lê `GOOGLE_OAUTH_CREDENTIALS` (caminho do arquivo de credenciais OAuth, sem o conteúdo).
- `app/prompts.py`: uma regra no prompt da agenda dizendo quando usar `add_event` e quando usar `add_google_event`.

Implementação da transação por turno:

- Criados: `app/unidade_trabalho.py` (conexão do turno, confirmar/desfazer), `testes/prova_depois.py` (prova com marcador único) e `MUDANCAS.md`.
- Modificados: `app/db.py` (devolve a conexão do turno), `app/tools/financeiro.py` (sem commit no turno; `update_transaction` ligada às tools do financeiro), `app/tools/agenda.py` (sem commit nem close no turno), `app/juiz.py` (`escrita_autorizada`, limpeza do histórico na recusa, timeout de 5 s) e `app/graph.py` (abre e encerra a transação do turno).
- `app/agents.py` não precisou mudar: a ligação de `update_transaction` foi feita na lista `TOOLS` de `app/tools/financeiro.py`.

## 6. Registro de alterações

### Alteração 1 — criação do `decisao_tecnica.md`

- **O que mudou.** Arquivo novo na raiz, com as cinco partes pedidas e este registro.
- **Por que foi feito assim.** O enunciado pede que a decisão e a lista de arquivos fiquem na raiz do repositório, e o grupo quer decidir antes de codar. Escrever primeiro obriga a fechar a fronteira da escrita e a pensar nas três tools antes de tocar no código.
- **Por que não foi feito de outro jeito.** Não reaproveitei `JUSTIFICATIVA.md` nem `JUSTIFICATIVA_v1.md`: são entregas de outra atividade (perfil do usuário) e misturar os assuntos tiraria a clareza de qual arquivo responde a esta prática. Também não deixei para escrever no fim: a decisão guia o código, e as listas de arquivos ficam mais fiéis quando atualizadas a cada alteração.

### Alteração 2 — `testes/reproduz_desencontro.py`

- **O que mudou.** Arquivo novo que reproduz o desencontro nas três tools, com marcador único por tentativa. A camada 1 chama a tool e consulta o banco por uma conexão separada. A camada 2 manda a frase ao `/chat` e compara a resposta com o banco.
- **Por que foi feito assim.** A camada 1 não usa LLM nem juiz: se a linha aparece numa conexão separada logo depois da tool, o commit aconteceu dentro da tool, antes de qualquer veredito. Isso prova o defeito de forma determinística e também cobre `update_transaction`, que não é alcançável pelo chat no estado atual. A camada 2 mostra o sintoma que o usuário vê. Usa só `urllib` e `psycopg2`, que já estão no projeto, para não criar dependência nova.
- **Por que não foi feito de outro jeito.** Não usei mocks do banco, porque não provariam nada sobre o commit real. Não confiei só no chat, porque o JEV não é determinístico e a recusa pode não acontecer. Não limpei as linhas de teste no script, para que o "antes" fique como evidência (apagar linha à mão é um dos itens que o enunciado exclui como solução). Não coloquei o arquivo em `app/`, para não misturar teste com código do Assessor.
- **Limite.** Não foi executado: o ambiente em que foi criado não tem as dependências nem o `.env`. Só a sintaxe foi validada.

### Alteração 3 — fechamento dos pontos em aberto (seções 4 e 5)

- **O que mudou.** A tabela da seção 4 passou de "Aberto" para decisão, motivo e alternativa descartada em cada ponto. A seção 5 ganhou o script e a lista do que está previsto.
- **Por que foi feito assim.** Cada decisão aponta para um fato do código: a lista `TOOLS` do financeiro, o timeout de 30 s em `app/juiz.py`, a conexão global em `app/db.py`, a ausência de `add_google_event` em `app/tools/agenda.py` e o `MemorySaver` do grafo.
- **Por que não foi feito de outro jeito.** Não deixei pontos para decidir durante o código: decidir com o código aberto tende a justificar o que já foi escrito. Também não marquei o "antes" como feito, porque ele depende de execução na máquina do grupo.

### Alteração 4 — tool `add_google_event`

- **O que mudou.** `app/tools/agenda.py` ganhou `add_google_event`, ligada ao agente de agenda. `app/config.py` ganhou a variável `GOOGLE_OAUTH_CREDENTIALS`. `app/prompts.py` ganhou uma regra: `add_event` para registrar compromissos, `add_google_event` só quando o usuário pedir o Google Agenda.
- **Por que foi feito assim.** A tool chama o servidor MCP do Google Calendar pelo cliente oficial do pacote `mcp`, que já estava no `requirements.txt`, então não há dependência nova. Ela lê o esquema real da tool `create-event` do servidor e só envia os campos que ele declara, porque os nomes dos parâmetros não puderam ser conferidos na documentação durante a criação. Se faltar um campo obrigatório ou o servidor não expuser a tool, devolve `status: error` em vez de falhar no meio do turno. O prazo de 20 s evita travar o turno. A regra no prompt evita criar o mesmo compromisso nos dois lugares sem o usuário pedir.
- **Por que não foi feito de outro jeito.** Não usei a API do Google diretamente, para não criar dependências e um fluxo de OAuth novos. Não substituí a tabela `events`, porque `add_event` é uma das três escritas que a invariante cobre. Não alterei rota, API nem outro fluxo da arquitetura, que o enunciado exclui. Não editei o PDF do FAQ, que ainda diz que não há calendário externo. Essa contradição precisa ser resolvida por vocês, porque o juiz vai confrontar o texto do FAQ.
- **Limites conhecidos.**
  - Não foi executada. O ambiente em que foi escrita não tem Node, as dependências nem as credenciais, então só a sintaxe foi validada. Falta autenticar uma vez (`npx @cocal/google-calendar-mcp auth` com `GOOGLE_OAUTH_CREDENTIALS` apontando para o arquivo de credenciais) e testar um evento real.
  - O servidor MCP é iniciado por chamada (`npx`), o que soma segundos ao turno e pressiona o SLO de 8 s.
  - O evento no Google não é desfeito por `rollback()`. Para cobri-lo há duas saídas: criar o evento só depois do veredito aprovado (Adiar), ou apagá-lo na recusa (Compensar). A escolha fica para a implementação do fechamento do turno.
