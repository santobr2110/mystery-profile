# Mystery Profile 🃏

Jogo de pistas multiplayer para celular, inspirado no *Perfil*, todo em inglês, para praticar o idioma.
Vale para 2 a 8 jogadores, cada um no seu celular.

## Como rodar

Não precisa instalar nada, porque usa o Python que já vem no Mac.

1. Dê dois cliques em **`Start Game.command`** (ou rode `python3 server.py` no terminal).
2. O terminal mostra um endereço como `http://172.16.2.226:8000`.
3. Todos os celulares precisam estar **no mesmo Wi-Fi** do computador e abrir esse endereço.
4. Uma pessoa toca em **Create a new game**, e as outras digitam o código de 4 letras (ou usam o link de convite).

> Na primeira vez o macOS pode perguntar se aceita conexões de entrada para o Python. Responda **Permitir**.

## Como jogar

- A cada rodada, um jogador é o **reader** e só ele vê a resposta secreta e as 10 pistas.
- Os outros jogam em turnos: escolhem um número de 1 a 10, o reader **lê a pista em voz alta** (em inglês) e a pista também aparece nas telas.
- Quem escolheu pode dar **um palpite**, digitado (erros pequenos de ortografia são aceitos) ou falado (o reader marca ✓ Correct), ou pode passar.
- Pontos: acertar com 1 pista vale 10, com 10 pistas vale 1. O reader ganha +2 quando alguém acerta.
- O primeiro jogador a atingir a meta (25/40/60/80) vence.
- No fim de cada carta aparecem todas as pistas com 🔊 para ouvir a pronúncia (revisão de vocabulário).

## Adicionar cartas

Edite `cards.json`. Cada carta tem `category` (Person, Place, Thing ou Animal; se usar um nome novo, ele vira uma nova categoria), `answer`, `aliases` (outras respostas aceitas) e exatamente **10** `clues`. Reinicie o servidor depois de editar.

## Jogar pela internet (sem Wi-Fi em comum)

O servidor é um único arquivo Python sem dependências. Dá para publicar em serviços como Render ou Railway (comando de start: `python3 server.py`; a porta vem da variável `PORT`).

## Arquivos

| Arquivo | O que é |
|---|---|
| `server.py` | Servidor e regras do jogo (salas, turnos, pontuação) |
| `cards.json` | As 55 cartas com pistas em inglês |
| `static/` | O app do celular (HTML/CSS/JS) |
