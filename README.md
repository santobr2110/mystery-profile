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

- Uma **rodada** tem uma carta para cada jogador: todos leem uma vez, e a rodada inteira é da **mesma categoria**. Na rodada seguinte a categoria muda para todos (sem repetir a anterior).
- A cada carta, um jogador é o **reader** e só ele vê a resposta secreta e as 10 pistas.
- Os outros jogam em turnos: escolhem um número de 1 a 10, o reader **lê a pista em voz alta** (em inglês) e a pista também aparece nas telas.
- Quem escolheu pode dar **um palpite**, digitado ou falado (o reader marca ✓ Correct), ou pode passar.
- O palpite digitado **perdoa erros de ortografia**: maiúsculas, acentos, singular/plural e espaços não contam ("Pele", "boot", "tooth brush"); cabe cerca de um erro a cada quatro letras ("umbrela", "elefant", "hamburguer"); e palavras escritas do jeito que soam também valem ("sizors" para *scissors*, "pinguin" para *penguin*). O jogo não aceita, porém, outra palavra real parecida ("house" não vale por *mouse*), nem anos vizinhos (1888 não vale por 1889). Se ainda assim recusar algo certo, o reader tem o botão "Actually, that's right — accept it".
- Pontos: acertar com 1 pista vale 10, com 10 pistas vale 1. O reader ganha +2 quando alguém acerta.
- **Cada jogador escolhe o próprio nível** no lobby (A1, A2, B1 ou B2). Em cada carta, quem começa adivinhando é o "dono" dela: **70% das cartas dele saem no nível que ele escolheu** e os outros 30% são sorteados entre os níveis marcados pelo anfitrião. Como o leitor muda a cada carta, todos são donos na mesma proporção. A tela mostra "🎯 Card at your level" ou "🎲 Mixed card".
- Se o nível do jogador não existir na categoria da rodada (Famous e Year só têm B1 e B2), o jogo usa o nível mais próximo.
- O anfitrião escolhe as **categorias** e os **níveis da sala**, que valem para os 30% sorteados. O lobby mostra quantas cartas o baralho tem.
- O primeiro jogador a atingir a meta (25/40/60/80) vence, **mas a partida só acaba quando todos tiverem jogado o mesmo número de turnos**, então quem joga por último sempre tem a sua chance. Se der empate na liderança, o jogo segue por até duas rodadas extras; persistindo o empate, a vitória é compartilhada.
- No fim de cada carta aparecem todas as pistas com 🔊 para ouvir a pronúncia (revisão de vocabulário).
- Nos níveis **A1 e A2**, o botão **🇧🇷 PT** no topo mostra a tradução de cada pista em português, e no fim da carta aparece também a tradução da resposta. A escolha fica salva no celular de cada jogador. B1 e B2 não têm tradução, de propósito.

## Adicionar cartas

Edite `cards.json`. Cada carta tem `category` (Person, Place, Thing, Food, Animal, Famous ou Year), `level` (A1, A2, B1 ou B2), `answer`, `aliases` (outras respostas aceitas) e exatamente **10** `clues`. As cartas de A1 e A2 têm ainda `pt` com `answer` e as 10 traduções, na mesma ordem das pistas. Categorias e níveis novos aparecem sozinhos no lobby. Reinicie o servidor depois de editar.

Total atual: **940 cartas** — 150 no A1, 180 no A2, 280 no B1 e 330 no B2.

| Categoria | Cartas | Observação |
|---|---|---|
| Thing | 224 | objetos do dia a dia |
| Place | 168 | lugares |
| Person | 156 | profissões e tipos de pessoa |
| Food | 120 | comidas e bebidas |
| Animal | 112 | animais |
| Famous | 100 | pessoas reais e famosas, só em B1 e B2 |
| Year | 60 | anos históricos, só em B1 e B2 |

As pistas de B1 e B2 são propositalmente indiretas: no B2 nenhuma pista entrega a resposta sozinha, e no B1 no máximo uma. A1 e A2 continuam diretos.

## Jogar pela internet (sem Wi-Fi em comum)

O servidor é um único arquivo Python sem dependências. Dá para publicar em serviços como Render ou Railway (comando de start: `python3 server.py`; a porta vem da variável `PORT`).

## Arquivos

| Arquivo | O que é |
|---|---|
| `server.py` | Servidor e regras do jogo (salas, turnos, pontuação) |
| `cards.json` | As 940 cartas nos níveis A1, A2, B1 e B2 |
| `cards_classic_backup.json` | Backup das 55 cartas antigas (pessoas e lugares famosos), não é usado pelo jogo |
| `static/` | O app do celular (HTML/CSS/JS) |
