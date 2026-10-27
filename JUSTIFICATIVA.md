# JUSTIFICATIVA

## 1. Quais arquivos você criou ou modificou? Indique o caminho de cada um.

Arquivos criados ou modificados nesta implementação:

- app/perfil.py - módulo de serviço de perfil com escrita em MongoDB e upsert em Qdrant para o vetor semântico do perfil.
- app/routes/perfil.py - rota HTTP de escrita do perfil (POST /perfil).
- app/schemas.py - contrato Pydantic do payload e resposta de perfil.
- app/tools/memoria.py - tool de consulta de perfil e histórico de memória usada pelo especialista.
- app/graph.py - fluxo de assessor injeta o perfil como contexto, e agora orienta o usuário a usar a tela de perfil quando o perfil não existir.
- JUSTIFICATIVA.md - arquivo de entrega com a explicação do desenho.

## 2. Por onde o perfil entra, e onde cada parte dele é gravada?

O perfil entra pelo POST /perfil da API, enviado pela tela de Perfil com o contrato user_id, renda_mensal, objetivo, tolerancia_risco e preferencias. O dado estruturado é gravado em MongoDB na collection perfis do banco assessor. O texto semântico e o conteúdo do perfil ficam indexados em Qdrant na collection perfil_usuario. A escrita nos dois lugares é disparada no mesmo ponto de entrada, no módulo de perfil, pela função salvar_perfil.

## 3. Como as preferências são guardadas e consultadas, e por que não é busca por palavra?

As preferências entram como texto livre e são transformadas em embedding no mesmo momento em que o perfil é persistido. O vetor é enviado ao Qdrant com payload estruturado e o campo texto com a descrição completa feita a partir do perfil. Depois, o agente consulta esse mesmo índice com gerar_embedding da busca textual do usuário e faz query semântica em Qdrant com filtro de user_id. Isso não é busca por palavra, porque o sistema compara significado por similaridade vetorial e encontra restrições como 'não quero nada agressivo' mesmo quando a palavra cripto não aparece no texto do perfil.

## 4. Você criou uma tool ou duas? Por quê?

Criei uma tool buscar_perfil_usuario dentro do conjunto de tools de memória para dar acesso ao especialista financeiro ao perfil estruturado e ao texto livre. A intenção foi manter o perfil como complemento do especialista financeiro, sem criar um agente novo e sem duplicar o desenho da arquitetura atual. A tool usa user_id vindo do contexto de requisição e consulta o perfil e a busca semântica diretamente no módulo Python, sem ir pela própria API.

## 5. O que garante que o perfil de um usuário não apareceria para outro, se houvesse mais de um?

A garantia é o filtro por user_id aplicado no MongoDB e no Qdrant. A collection de Mongo usa user_id como chave única, e o VDB query usa must=[FieldCondition(key='user_id', match=MatchValue(value=user_id))]. Assim a consulta fica isolada por usuário.

## 6. Sua tool consulta o banco diretamente ou faz uma chamada HTTP na própria API? Por quê?

A tool consulta o modelo e o serviço de perfil diretamente no Python, sem chamar a API HTTP. Isso evita depender de uma segunda camada HTTP dentro do fluxo do agente e mantém a ferramenta conectada com o mesmo código de serviço que salva e recupera o perfil.

## 7. Por que não existe um agente 'perfil'?

Porque o perfil não é um domínio novo. Ele é dado de apoio dos agentes, e o documento exige que o perfil seja um suporte de contexto ao raciocínio financeiro do assessor, não um quarto agente ou especialista separado.

## 8. Por que o chat não altera o cadastro?

O chat apenas lê o perfil como contexto e o repassa ao especialista. A tela de Perfil é a única entrada de escrita. O documento exige que o chat não cadastre nem edite o perfil; essa separação é garantida pela ausência de uma rota de escrita no chat e pela imersão do cadastro no fluxo de formulário/POST da tela de perfil.
