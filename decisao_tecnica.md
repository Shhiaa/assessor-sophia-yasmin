# Decisão técnica — Etapa 8: o juiz e a escrita

Status: decisão tomada, implementação ainda não feita. As seções 1 a 3 descrevem a solução escolhida; a seção 4 reúne as decisões complementares; a seção 5 lista os arquivos. A seção 6 é o registro de cada alteração feita no repositório, com o motivo de cada escolha e o motivo de cada alternativa que ficou de fora.

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

Pontos ainda abertos neste commit. Cada um será fechado, com escolha e motivo, no próximo registro da seção 6.

| Ponto | Estado |
| --- | --- |
| `update_transaction` fora da lista `TOOLS` do agente financeiro | Aberto |
| Juiz sem resposta (timeout, exceção, sem chave) | Aberto |
| `add_google_event` | Aberto |
| `salvar_mensagem` (Mongo) | Aberto |
| Leituras na conexão do turno | Aberto |
| Timeout do JEV | Aberto |
| Limpeza do histórico na recusa | Aberto |
| Repetição do mesmo pedido | Aberto |
| Evidência com marcador único, nas três tools | Pendente: o "antes" ainda não foi reproduzido |

## 5. Arquivos criados e arquivos modificados

Criados:

- `decisao_tecnica.md`: este arquivo.

Modificados:

- Nenhum até agora.

## 6. Registro de alterações

### Alteração 1 — criação do `decisao_tecnica.md`

- **O que mudou.** Arquivo novo na raiz, com as cinco partes pedidas e este registro.
- **Por que foi feito assim.** O enunciado pede que a decisão e a lista de arquivos fiquem na raiz do repositório, e o grupo quer decidir antes de codar. Escrever primeiro obriga a fechar a fronteira da escrita e a pensar nas três tools antes de tocar no código.
- **Por que não foi feito de outro jeito.** Não reaproveitei `JUSTIFICATIVA.md` nem `JUSTIFICATIVA_v1.md`: são entregas de outra atividade (perfil do usuário) e misturar os assuntos tiraria a clareza de qual arquivo responde a esta prática. Também não deixei para escrever no fim: a decisão guia o código, e as listas de arquivos ficam mais fiéis quando atualizadas a cada alteração.
