# Perfil financeiro do usuário - justificativa

## 1. Quais arquivos você criou ou modificou?

Criei do zero perfil, tools/perfil, routes/perfil_route, perfil.html/css/js e o md
só adicionei a tool no agents.

## 2. Por onde o perfil entra, e onde cada parte dele é gravada?

Entra pelo POST /perfil, já validado ali. O salvar_perfil grava renda/objetivo/tolerância no Mongo e as preferências como embeddings no Qdrant, na mesma chamada.

## 3. Como as preferências são guardadas e consultadas, e por que não é busca por palavra?

Cada frase vira um ponto no Qdrant e a busca compara por similaridade em embedding, não por texto.
Isso acha "não quero nada agressivo" em ua pergunta sobre cripto mesmo sem a palavra existir no cadastro,
o que um regex não acha.

## 4. Você criou uma tool ou duas? Por quê?

Uma, consultar_perfil_financeiro, porque qualquer conselho precisa dos dois dados (número/objetivo e preferências) juntos.
Duas tools seriam sempre chamadas juntas mesmo assim, então não ganhava nada em separar.

## 5. O que garante que o perfil de um usuário não apareceria para outro?

Mongo filtra por user_id e o Qdrant guarda user_id no payload e também filtra por ele, mesmo esquema que o memory já usa.

## 6. Sua tool consulta o banco diretamente ou faz uma chamada HTTP na própria API? Por quê?

Direto no banco, pra não fazer o processo chamar a si mesmo via HTTP à toa.
A tool já roda no mesmo processo que mantém a conexão com Mongo e Qdrant aberta.

## 7. Por que não existe um agente "perfil"?

Porque perfil não é algo que alguém pergunta direto, é um apoio que o financeiro usa, igual saldo ou transações.
Um agente novo exigiria uma rota no roteador pra uma intenção que ninguém digita.

## 8. Por que o chat não altera o cadastro?

Porque não existe nenhuma tool de escrita, a consultar_perfil_financeiro só lê.
Não é uma instrução pro modelo não mexer, é que essa porta não existe em nenhum lugar do código.
