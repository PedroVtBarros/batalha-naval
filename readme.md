# Batalha Naval em Rede

Jogo de Batalha Naval para dois jogadores, por turnos, com arquitetura cliente-servidor, escrito em Python e comunicando-se por sockets TCP. Trabalho da disciplina de Redes de Computadores.

Este arquivo é a documentação única da aplicação: propósito, funcionamento, arquitetura, requisitos, como executar, justificativa do TCP e o **protocolo da camada de aplicação** (mensagens, estados e eventos) que cliente e servidor seguem.

**Para jogar agora**, a partir da raiz do projeto, em três terminais (detalhes na [seção 3](#3-requisitos-mínimos-e-como-executar)):

```powershell
py -3 servidor.py          # terminal 1: servidor (porta 5050)
py -3 cliente.py           # terminal 2: jogador 1
py -3 cliente.py           # terminal 3: jogador 2
```

No Linux/macOS, troque `py -3` por `python3`.

## Sumário

1. [Estado do projeto](#1-estado-do-projeto)
2. [Propósito, funcionamento e arquitetura](#2-propósito-funcionamento-e-arquitetura)
3. [Requisitos mínimos e como executar](#3-requisitos-mínimos-e-como-executar)
4. [Por que TCP](#4-por-que-tcp)
5. [Formato das mensagens no fio](#5-formato-das-mensagens-no-fio)
6. [Tabuleiro e coordenadas](#6-tabuleiro-e-coordenadas)
7. [Catálogo de mensagens](#7-catálogo-de-mensagens)
8. [Códigos de erro](#8-códigos-de-erro)
9. [Estados e eventos](#9-estados-e-eventos)
10. [Exemplo de turno completo](#10-exemplo-de-turno-completo)
11. [Usando o protocolo.py](#11-usando-o-protocolopy)
12. [Testes](#12-testes)
13. [Guia de leitura por integrante](#13-guia-de-leitura-por-integrante)
14. [Decisões de projeto a confirmar](#14-decisões-de-projeto-a-confirmar)
15. [Roteiro de demonstração (cerca de 3 minutos)](#15-roteiro-de-demonstração-cerca-de-3-minutos)

---

## 1. Estado do projeto

| Fase | Entrega | Responsável | Situação |
|------|---------|-------------|----------|
| 1 | Protocolo: `protocolo.py`, este `readme.md` e `tests/test_protocolo.py` | Pedro Vitor Moura de Araujo Barros | **Implementado** |
| 2 | Tabuleiro: criar, posicionar navios e verificar disparos | Henrique Sousa Brito | **Implementado** |
| 3 | Servidor: aceitar os dois jogadores e aguardar o segundo | JuanBarrer4 (identificação no GitHub) | **Implementado** |
| 4 | Cliente: conectar ao servidor e receber mensagens | Ariel Pina | **Implementado** |
| 5 | Regras da partida: turnos, vitória e visão de cada jogador | Henrique Sousa Brito | **Implementado** |
| 6 | Servidor: integrar as regras, receber disparos e enviar resultados | JuanBarrer4 (identificação no GitHub) | **Implementado** |
| 7 | Cliente: interface e entrada dos disparos | Ariel Pina | **Implementado** |
| 8 | Testes de integração e fechamento da documentação | Pedro Vitor Moura de Araujo Barros | **Implementado** |

Todas as fases estão implementadas: protocolo de comunicação, tabuleiro de um jogador, regras completas em `partida.py` (turnos, vitória e visão de cada jogador), servidor que recebe os dois jogadores, processa os disparos com essas regras e envia `ATUALIZACAO` e `FIM`, e cliente com interface de terminal. A fase 8 acrescentou `tests/test_integracao.py` (servidor real e clientes reais por TCP, inclusive como processos) e corrigiu um defeito do cliente: ele ficava bloqueado esperando a digitação e só percebia o `FIM` do servidor depois de o jogador apertar Enter (ver [seção 2.1](#21-arquitetura)). As mensagens, estados e eventos abaixo são o contrato.

Estrutura do projeto:

```
.
├── readme.md                  documentação única (este arquivo)
├── protocolo.py               envio e recebimento de mensagens (compartilhado)
├── tabuleiro.py               navios, disparos, contagem e visão oculta
├── partida.py                 turnos, vitória e visão de cada jogador
├── servidor.py                servidor TCP: recebe os jogadores e conduz a partida
├── cliente.py                 cliente TCP: interface de terminal e envio dos disparos
├── .gitignore                 ignora __pycache__ e *.pyc
└── tests/
    ├── test_protocolo.py      testes do protocolo.py
    ├── test_tabuleiro.py      testes do tabuleiro.py
    ├── test_partida.py        testes das regras da partida
    ├── test_servidor.py       testes do servidor.py
    ├── test_cliente.py        testes do cliente.py
    └── test_integracao.py     jogo completo: servidor real + clientes reais (fase 8)
```

## 2. Propósito, funcionamento e arquitetura

### Propósito

Permitir que duas pessoas, em computadores diferentes, joguem Batalha Naval pela rede, cada uma no seu terminal. O projeto é também um exercício de aplicação distribuída: definir um protocolo da camada de aplicação sobre TCP, com mensagens, estados e eventos bem especificados.

### Papéis

- **Servidor** (um processo). Aceita as conexões, sorteia a posição dos navios, valida todos os disparos, decide de quem é a vez, calcula os resultados e detecta o fim da partida. É a **única fonte da verdade**: guarda os dois tabuleiros completos.
- **Cliente** (um programa, executado uma vez por jogador). Mostra o jogo no terminal, lê a coordenada digitada pelo jogador e a envia ao servidor. O cliente **não decide nada**: só mostra o que o servidor informa. Mesmo que valide o formato da coordenada para dar resposta rápida ao usuário, o servidor sempre valida de novo.

### Regras do jogo

- Dois jogadores, 1 servidor. O primeiro a conectar aguarda o segundo. Quando os dois estão conectados, a partida começa e o **jogador 1** joga primeiro.
- Cada jogador tem um tabuleiro **5×5** (coordenadas de `A1` a `E5`) com **2 navios de 2 casas**, na horizontal ou na vertical, sem sobreposição. O servidor posiciona os navios automaticamente.
- Na sua vez, o jogador envia uma coordenada. O servidor responde com `AGUA`, `ACERTO` ou `AFUNDOU`. Todo disparo válido passa a vez ao adversário, exceto o que encerra a partida.
- Coordenada inválida, disparo repetido ou disparo fora da vez geram um `ERRO` **só para quem tentou**. Tabuleiros e turno não mudam.
- Cada jogador vê seus próprios navios e o resultado dos próprios disparos. **O servidor nunca envia posições ocultas dos navios do adversário.**
- Vence quem afundar os dois navios do adversário. Se alguém sair ou desconectar durante a partida, o servidor avisa o outro e encerra o jogo.

### Visão geral do fluxo

```
Jogador 1                    Servidor                    Jogador 2
    |--- conecta ------------->|                              |
    |<-- AGUARDE --------------|                              |
    |                          |<------------- conecta -------|
    |<-- INICIO (sua_vez=true) |  INICIO (sua_vez=false) ---->|
    |--- DISPARAR B3 --------->|                              |
    |                          | (valida, aplica, troca a vez)|
    |<-- ATUALIZACAO ----------|---------- ATUALIZACAO ------>|
    |                          |<---------- DISPARAR C1 ------|
    |          ...             |            ...               |
    |<-- ATUALIZACAO + FIM ----|---- ATUALIZACAO + FIM ------>|
```

### 2.1 Arquitetura

```
 Computador do jogador 1            Computador do servidor            Computador do jogador 2
 +--------------------+            +----------------------+          +--------------------+
 |    cliente.py      |   TCP      |     servidor.py      |   TCP    |    cliente.py      |
 |  (terminal, I/O)   |<---------->|  Servidor + Partida  |<-------->|  (terminal, I/O)   |
 +--------------------+  conexão 1 |  + Tabuleiro x 2     | conexão 2+--------------------+
                                    +----------------------+
              protocolo.py (JSON + LF) é usado pelos dois lados
```

Um servidor, dois clientes, **uma conexão TCP por jogador**. Toda a comunicação é cliente ↔ servidor; os clientes nunca falam entre si.

| Módulo | Responsabilidade | Conhece sockets? |
|--------|------------------|------------------|
| `protocolo.py` | Formato no fio: um objeto JSON UTF-8 por linha, terminado em LF. `enviar_mensagem` e `receber_mensagem` e as exceções `ConexaoEncerrada`, `MensagemIncompleta` e `MensagemInvalida`. Não conhece as regras do jogo. | sim |
| `tabuleiro.py` | Um tabuleiro 5×5: sorteia os 2 navios, aplica disparos, conta navios restantes, gera a visão com ou sem os navios intactos. | não |
| `partida.py` | Uma partida: de quem é a vez, validação do disparo (vez, coordenada, repetição), vitória e **a visão de cada jogador** (nunca revela `N` do adversário). | não |
| `servidor.py` | Aceita as conexões, decide quem é jogador 1 e 2, traduz cada mensagem recebida em uma chamada a `Partida`, envia a cada jogador o **seu** resultado, trata `SAIR` e desconexões, recusa o terceiro cliente e volta a aceitar outra dupla ao fim. **É a única fonte da verdade.** | sim |
| `cliente.py` | Conecta ao servidor, lê a coordenada digitada, envia `DISPARAR`/`SAIR` e desenha o que o servidor informa. Não decide nada sobre o jogo. | sim |

**Threads do servidor.** A thread principal só aceita conexões; **cada conexão tem sua thread**, que bloqueia em `receber_mensagem`. Por isso o servidor percebe a saída de um jogador **mesmo quando ele está só esperando a sua vez** (ou o segundo jogador). Todo o estado (`_estado`, `_jogadores`, `_partida`) é protegido por **um único `Lock`**, então os eventos dos dois jogadores são tratados um por vez, e cada conexão tem ainda um lock de envio, porque a thread de um jogador também escreve no socket do outro.

**Threads do cliente.** Ler o teclado e ler a rede são duas esperas que não podem se bloquear mutuamente. O cliente usa:

- a **thread principal**, que só espera eventos numa fila (com prazo curto, o que mantém o `Ctrl+C` funcionando no Windows) e executa a máquina de estados;
- a **thread de leitura da rede**, dona do leitor do socket, que enfileira cada mensagem do servidor;
- a **thread de entrada**, que executa o `input()` **somente na vez do jogador**.

Assim, se o adversário sair ou cair enquanto o jogador está no prompt `Sua vez!`, o `FIM` chega, o cliente mostra o desfecho e encerra **sem esperar uma tecla**.

## 3. Requisitos mínimos e como executar

### 3.1 Requisitos

- **Python 3.10 ou superior** (compatibilidade **estimada**, ver abaixo), somente biblioteca padrão (`socket`, `json`, `threading`, `unittest`...). Nada para instalar com `pip`.
- **Versões efetivamente testadas** (a suíte completa, 157 testes, passou nas duas):
  - Windows 11, Python **3.14.10** (`py -3`), inclusive o cliente e o servidor em console real, com `Ctrl+C` real;
  - Ubuntu no WSL, Python **3.12.3** (`python3`), inclusive o cliente e o servidor num pseudo-terminal (`pty`) com `Ctrl+C`.
- **Compatibilidade estimada, não testada:** Python **3.10 e 3.11**. O mínimo de 3.10 vem da leitura do código (tipos `X | None` avaliados em tempo de execução em `partida.py` e `TimeoutError` como o erro de tempo esgotado de `socket`), não de uma execução. Também **não testados:** macOS e dois computadores físicos (roteiro na seção 3.4).
- **Três terminais** para uma partida: um para o servidor e um para cada jogador. Podem ser na mesma máquina (endereço `127.0.0.1`) ou em máquinas diferentes na mesma rede.
- **Rede**: o computador do servidor precisa ser alcançável pelos clientes (pelo IP) e a **porta TCP** escolhida precisa estar liberada no firewall dele. Porta padrão: `5050`.
- Terminal que exiba UTF-8, pois as mensagens de texto têm acentos. Os tabuleiros usam só caracteres ASCII.

### 3.2 Argumentos dos programas

Os argumentos são **posicionais** (não há opções como `--porta`):

| Comando | Argumentos | Padrões |
|---------|------------|---------|
| `servidor.py [porta]` | `porta`: 1 a 65535 | `5050`. Sempre escuta em **`0.0.0.0`** (todas as interfaces); o endereço de escuta não é configurável. |
| `cliente.py [endereco_do_servidor] [porta]` | `endereco_do_servidor`: IP ou nome; `porta`: 1 a 65535 | `127.0.0.1` e `5050`. Para informar a porta é preciso informar também o endereço. |

Códigos de saída: `0` ao terminar normalmente; `1` se o cliente não conseguiu conectar ou o servidor não conseguiu abrir a porta; `2` para argumentos inválidos.

### 3.3 Executar no mesmo computador

Abra três terminais na **raiz do projeto** (a pasta que contém `servidor.py`).

Windows PowerShell:

```powershell
py -3 servidor.py                 # terminal 1
py -3 cliente.py                  # terminal 2: vira o jogador 1
py -3 cliente.py                  # terminal 3: vira o jogador 2
```

Linux/macOS:

```bash
python3 servidor.py               # terminal 1
python3 cliente.py                # terminal 2
python3 cliente.py                # terminal 3
```

Outra porta (se a `5050` estiver ocupada), nos três terminais: `py -3 servidor.py 6000` e `py -3 cliente.py 127.0.0.1 6000`.

O primeiro cliente a conectar é o jogador 1 e fica em `[AGUARDE]`; quando o segundo conecta, a partida começa e o jogador 1 joga primeiro.

**Como jogar.** Na sua vez o programa mostra `Sua vez! Digite a coordenada (A1-E5) ou SAIR:`. Digite a coordenada, por exemplo `B3` (letra = coluna, número = linha; minúsculas e espaços são aceitos, o cliente normaliza). Uma coordenada inválida ou repetida mostra `[ERRO]` e pede outra, sem perder a vez. **Como sair:** na sua vez, digite `SAIR`; **enquanto espera o adversário** (ou o segundo jogador), `SAIR` não é lido, então use `Ctrl+C`, que vale a qualquer momento e envia `SAIR` ao servidor. Nos dois casos o adversário é avisado (`FIM ADVERSARIO_SAIU`) e, se você era o primeiro e ainda esperava, a vaga é liberada. No tabuleiro: `~` água/desconhecido, `O` tiro na água, `X` navio atingido, `N` navio seu intacto. Para parar o servidor, `Ctrl+C` no terminal dele.

### 3.4 Executar em dois computadores na mesma rede

O servidor ouve em `0.0.0.0`, isto é, **em todas as interfaces de rede** do computador (loopback, Wi-Fi, Ethernet), e não só em `127.0.0.1`. Por isso um cliente de **outra máquina** consegue conectar. O cliente, porém, precisa do **endereço IP real do computador do servidor** na rede local. **Nunca use `0.0.0.0` nem `127.0.0.1` no cliente de outra máquina:** `127.0.0.1` seria o próprio computador do cliente.

**Passo 1. Descobrir o IP do computador do servidor** (use o IPv4 da interface conectada à rede local, geralmente começa com `192.168.` ou `10.`):

```powershell
ipconfig                                   # Windows: "Endereço IPv4" do adaptador Wi-Fi/Ethernet
```
```bash
hostname -I                                # Linux
ipconfig getifaddr en0                     # macOS (Wi-Fi; use en1 se estiver vazio)
```

**Passo 2. Liberar a porta no firewall do computador do servidor.** Windows PowerShell **como administrador** (ou aceite o aviso do Firewall que aparece na primeira vez que o Python abre a porta):

```powershell
New-NetFirewallRule -DisplayName "Batalha Naval" -Direction Inbound -Protocol TCP -LocalPort 5050 -Action Allow -Profile Private
```

Linux com `ufw`: `sudo ufw allow 5050/tcp`. Para remover a regra do Windows depois: `Remove-NetFirewallRule -DisplayName "Batalha Naval"`.

**Passo 3. Abrir o servidor** no computador A e **os clientes** no A e/ou no B (exemplo com o IP `192.168.0.10`; troque pelo seu):

```powershell
py -3 servidor.py                          # computador A (servidor)
py -3 cliente.py 192.168.0.10              # computador A ou B: jogador 1
py -3 cliente.py 192.168.0.10              # computador B: jogador 2
```
(Linux/macOS: `python3` no lugar de `py -3`.) Com outra porta: `py -3 servidor.py 6000` e `py -3 cliente.py 192.168.0.10 6000`.

**Se não conectar:** confira que os dois computadores estão na mesma rede (Wi-Fi "de convidados" costuma isolar os dispositivos), teste a porta a partir do cliente com `Test-NetConnection -ComputerName 192.168.0.10 -Port 5050` (PowerShell) e confira se o servidor está rodando (`Servidor ouvindo em 0.0.0.0:5050`).

**O que foi verificado e o que não foi.** Verificado: o servidor em `0.0.0.0` aceitou dois clientes conectados pelo **IP de rede da própria máquina** (não só por `127.0.0.1`) e conduziu a partida. **Não verificado:** conexão entre dois computadores físicos, nem a regra de firewall acima. `Ctrl+C` foi verificado no cliente (esperando o adversário e parado no prompt) e no servidor, em console real no Windows e em pseudo-terminal no Linux. Roteiro curto para concluir: (1) no computador A, `ipconfig` e anote o IPv4; (2) no A, crie a regra de firewall e rode `py -3 servidor.py`; (3) no B, rode `Test-NetConnection -ComputerName <IP do A> -Port 5050` e confirme `TcpTestSucceeded : True`; (4) no B, `py -3 cliente.py <IP do A>` e, no A, `py -3 cliente.py <IP do A>`; (5) jogue até o `FIM` e confira que o servidor mostra `Partida encerrada`.

## 4. Por que TCP

Escolhemos TCP (e não UDP) porque o jogo depende de que **nenhuma mensagem se perca nem chegue fora de ordem**:

- **Entrega confiável.** Se um `DISPARAR` ou uma `ATUALIZACAO` fosse perdido, os dois lados teriam visões diferentes da partida. Com UDP teríamos de reinventar confirmação, retransmissão e ordenação na aplicação.
- **Ordem garantida em cada conexão.** O TCP entrega os bytes exatamente na ordem em que foram enviados. Dois disparos nunca se invertem e uma atualização nunca ultrapassa a anterior.
- **Conexão com início e fim.** Sabemos quando um jogador entra e quando sai ou cai: o fim da conexão é detectado e vira o evento "desconexão". Em UDP não há conexão e seria preciso inventar heartbeat e timeouts.
- **Não precisamos de baixa latência.** O jogo é por turnos e troca poucas mensagens pequenas; o custo do TCP é irrelevante aqui.

Duas consequências que o protocolo precisa tratar:

1. **O TCP é um fluxo de bytes, sem fronteiras de mensagem.** Uma mensagem pode chegar em pedaços e duas podem chegar coladas na mesma leitura. Por isso cada mensagem termina com uma **quebra de linha LF** (seção 5), que delimita onde uma acaba e a próxima começa.
2. **A ordem só vale dentro de uma conexão.** Os dois jogadores usam conexões diferentes, e o TCP não ordena eventos entre elas. Por isso **o servidor controla os turnos e a consistência da partida**: só ele decide quem joga, processa um disparo por vez (o estado da partida deve ser protegido contra acesso simultâneo) e envia a cada jogador o resultado já consolidado. Os clientes **não devem** supor em que ordem as mensagens de um mesmo evento chegam aos dois jogadores.

## 5. Formato das mensagens no fio

Regras para toda mensagem, em qualquer direção:

1. Uma mensagem é **um objeto JSON em uma única linha**, codificado em **UTF-8** e terminado por **uma quebra de linha LF** (`\n`, byte `0x0A`; não use CRLF).
2. Todo objeto tem o campo **`"tipo"`**, um texto não vazio que identifica a mensagem (seção 7).
3. Não há quebra de linha "crua" dentro do JSON: quebras em textos viram `\n` escapado (o `json.dumps` já faz isso). Por isso a LF é um delimitador seguro.
4. Campos além dos listados para um tipo são **ignorados** por quem recebe. Campos obrigatórios ausentes ou com valor fora do aceito tornam a mensagem inválida para o jogo (o servidor responde `ERRO`).
5. O corpo JSON tem no **máximo 64 KiB** (65 536 bytes, sem a LF). As mensagens reais têm menos de 1 KiB.
6. Cada conexão tem **um único leitor**, criado uma vez com `sock.makefile("rb")` e reutilizado em todas as leituras. O leitor guarda em buffer bytes que já podem pertencer à *próxima* mensagem; criar outro leitor, ou misturar com `sock.recv()`, perderia mensagens.

Os exemplos JSON deste documento aparecem formatados em várias linhas para leitura; **no fio, cada um é uma linha só**. Exemplo real de bytes enviados (`\n` ao final é a LF):

```
{"tipo":"DISPARAR","coordenada":"B3"}\n
```

### Comportamento da leitura (`receber_mensagem`)

| Situação | O que acontece |
|----------|----------------|
| Linha completa com JSON válido | Devolve o `dict`. |
| Mensagem chega em partes (inclusive no meio de um caractere acentuado) | Espera chegar a LF e devolve a mensagem inteira. |
| Duas mensagens chegam juntas | Devolve uma por chamada, na ordem de envio. |
| Conexão encerrada, sem bytes pendentes | Levanta `ConexaoEncerrada`. Vale também para conexão derrubada (reset). |
| Conexão encerrada com bytes recebidos mas **sem a LF final** | Levanta `MensagemIncompleta`, que **é uma** `ConexaoEncerrada`. Os bytes parciais são descartados, nunca viram mensagem. |
| Linha completa que não é mensagem válida: UTF-8 inválido, JSON inválido, linha vazia, JSON que não é objeto, objeto sem `"tipo"` textual, mais de 64 KiB | Levanta `MensagemInvalida`. A linha ruim já foi consumida e **a conexão continua utilizável**. |

Como reagir:

- `ConexaoEncerrada` / `MensagemIncompleta` → o outro lado foi embora. No servidor é o evento *desconexão* do jogador; no cliente, "conexão perdida". Feche o socket.
- `MensagemInvalida` → o servidor responde `ERRO` com código `MENSAGEM_INVALIDA` e segue; o cliente, se recebê-la do servidor, trata como falha de protocolo e encerra.

`enviar_mensagem` usa `sendall()`, valida o envelope antes de enviar (levanta `MensagemInvalida` e não envia nada se `mensagem` não for um `dict` com `"tipo"`, não for serializável ou passar de 64 KiB) e levanta `ConexaoEncerrada` se o outro lado já fechou.

## 6. Tabuleiro e coordenadas

### Coordenadas

Uma coordenada é um texto de **exatamente dois caracteres**: uma **letra maiúscula de `A` a `E`** (a **coluna**) seguida de **um dígito de `1` a `5`** (a **linha**). Expressão regular: `[A-E][1-5]` (use `re.fullmatch`). São 25 coordenadas válidas, de `A1` a `E5`. Minúsculas, espaços e `"b3"` **não** são aceitos pelo servidor; o cliente deve normalizar (`strip().upper()`) o que o usuário digitar antes de enviar.

### Representação do tabuleiro (única, para todos)

Um tabuleiro é uma **lista JSON de 5 textos**, cada um com **5 caracteres**:

- o texto de índice `0` é a **linha 1**, o de índice `4` é a linha 5;
- dentro de cada texto, o caractere de índice `0` é a **coluna A**, o de índice `4` é a coluna E;
- logo, a coordenada `B3` é `tabuleiro[2][1]` (linha 3 → índice 2; coluna B → índice 1).

| Caractere | Significado | Aparece em |
|-----------|-------------|------------|
| `~` | Água sem disparo (ou casa ainda desconhecida) | os dois tabuleiros |
| `O` | Disparo na água | os dois tabuleiros |
| `X` | Parte de navio atingida | os dois tabuleiros |
| `N` | Parte de navio **intacta** | **somente** em `tabuleiro_proprio` |

Exemplo: o tabuleiro com navios em `A1-B1` (horizontal) e `D4-D5` (vertical), com um disparo na água em `C2` e um acerto em `D4`:

```json
["NN~~~",
 "~~O~~",
 "~~~~~",
 "~~~X~",
 "~~~N~"]
```

```
    A B C D E
 1  N N ~ ~ ~
 2  ~ ~ O ~ ~
 3  ~ ~ ~ ~ ~
 4  ~ ~ ~ X ~
 5  ~ ~ ~ N ~
```

### Regras de privacidade

- `tabuleiro_adversario` mostra apenas o que o jogador já descobriu com os próprios disparos: **só contém `~`, `O` e `X`, nunca `N`**.
- O servidor **nunca** envia o tabuleiro próprio de um jogador ao outro.
- Um navio está **afundado** quando suas 2 casas foram atingidas. Isso aparece no `resultado` `AFUNDOU` e em `navios_restantes`.

### Usando o tabuleiro.py (fases 2 e 5)

Este módulo representa **um único tabuleiro**, que será mantido pelo servidor. Não usa sockets e não depende da interface: pode ser usado por um cliente de terminal ou por uma futura interface gráfica através do servidor.

| Item | Comportamento |
|------|---------------|
| `Tabuleiro()` | Sorteia 2 navios de 2 casas num tabuleiro 5×5, na horizontal ou vertical, sem sobreposição. |
| `Tabuleiro(navios)` | Recebe posições explícitas para testes e exemplos, como `[("A1", "B1"), ("D4", "D5")]`. Valida quantidade, tamanho, coordenadas, vizinhança e sobreposição. |
| `Tabuleiro(gerador=random.Random(42))` | Repete um sorteio com a mesma semente, útil para reproduzir testes. |
| `coordenada_para_indices(coordenada)` | Valida `A1` a `E5` e devolve `(linha, coluna)` começando em zero; `B3` vira `(2, 1)`. |
| `receber_disparo(coordenada)` | Registra o tiro e devolve `AGUA`, `ACERTO` ou `AFUNDOU`. |
| `como_lista()` | Devolve uma cópia do tabuleiro completo em 5 strings de 5 caracteres, usando `~`, `N`, `O` e `X`. |
| `como_lista(ocultar_navios=True)` | Devolve uma cópia sem navios intactos: troca `N` por `~`, mantendo `O` e `X`. |
| `navios_restantes` | Propriedade com a quantidade de navios que ainda têm pelo menos uma casa intacta, de 0 a 2. |
| `CoordenadaInvalida` | Disparo fora do formato aceito; tem `codigo = "COORDENADA_INVALIDA"`. |
| `DisparoRepetido` | A casa já recebeu um tiro; tem `codigo = "DISPARO_REPETIDO"`. |
| `PosicionamentoInvalido` | Os navios fornecidos ao construtor não respeitam as regras de posicionamento. |
| `TabuleiroError` | Classe-base dos erros deste módulo. |

Exemplo para executar no interpretador Python, a partir da raiz do projeto:

```python
from tabuleiro import Tabuleiro

tabuleiro = Tabuleiro([("A1", "B1"), ("D4", "D5")])
print(tabuleiro.receber_disparo("C2"))  # AGUA
print(tabuleiro.receber_disparo("D4"))  # ACERTO
print(tabuleiro.como_lista())
# ['NN~~~', '~~O~~', '~~~~~', '~~~X~', '~~~N~']
print(tabuleiro.receber_disparo("D5"))  # AFUNDOU
```

Um tiro na água marca `O`; um tiro num navio marca `X`. O resultado é `AFUNDOU` somente quando as duas casas **daquele navio** foram atingidas. Navios podem encostar, mas não podem ocupar a mesma casa. Erros de coordenada ou repetição não alteram o estado. Cada objeto mantém seus próprios navios e disparos, sem compartilhar estado com outro tabuleiro.

**Integração:** o método `receber_disparo` continua operando em um único tabuleiro. Para controlar uma partida, use `Partida.disparar`, que valida a vez e aplica o tiro no tabuleiro **do alvo**. O servidor verifica antes se a partida começou (seção 8). As exceções de disparo fornecem `codigo` e texto (`str(erro)`) para montar a mensagem `ERRO`; esses módulos não enviam mensagens por conta própria.

`como_lista()` contém os navios intactos (`N`) e serve apenas para o servidor ou o próprio dono. **Não deve ser enviado ao adversário.** A fase 5 acrescenta `como_lista(ocultar_navios=True)` e `navios_restantes`; o controle de turnos, vitória e composição da visão de cada jogador está em `partida.py`.

### Usando o partida.py (fase 5)

`Partida` representa uma partida **já iniciada**, com dois jogadores. O jogador 1 começa. Todo disparo válido passa a vez, mesmo um acerto ou afundamento, exceto o que encerra a partida. Coordenada inválida, tiro repetido ou tentativa fora da vez não alteram tabuleiros, turno ou vencedor.

| Item | Comportamento |
|------|---------------|
| `Partida()` | Cria uma partida com dois tabuleiros sorteados. |
| `Partida(tabuleiro_1, tabuleiro_2)` | Usa dois tabuleiros recém-criados, distintos e sem disparos anteriores. Útil para testes e para reaproveitar a fábrica de tabuleiros do servidor. |
| `disparar(jogador, coordenada)` | Verifica a vez, aplica o disparo no adversário e devolve `AGUA`, `ACERTO` ou `AFUNDOU`. |
| `vez` | Propriedade com o jogador da vez (`1` ou `2`); `None` depois da vitória. |
| `vencedor` | Propriedade com `1` ou `2` depois da vitória; `None` enquanto ninguém venceu. |
| `encerrada` | Propriedade booleana que informa se a partida terminou por vitória. |
| `visao(jogador)` | Devolve cópias de `sua_vez`, `tabuleiro_proprio`, `tabuleiro_adversario` e `navios_restantes`, do ponto de vista de quem recebe. |
| `ForaDeVez` | Tentativa de disparo fora do turno; tem `codigo = "FORA_DE_VEZ"`. |
| `PartidaEncerrada` | Chamada de disparo depois da vitória. É uma proteção interna; não acrescenta código de erro ao protocolo. |
| `JogadorInvalido` | Identificador diferente dos inteiros `1` e `2`. É um erro de uso da API; a identidade deve vir da conexão do servidor. |
| `PartidaError` | Classe-base dos erros de controle. `CoordenadaInvalida` e `DisparoRepetido`, de `tabuleiro.py`, são propagados. |

A vitória é identificada quando o tabuleiro alvo fica com zero navios restantes. Nesse disparo final, `vez` passa a `None` e `sua_vez` fica `false` **para os dois jogadores**. Mesmo depois da vitória, a visão do adversário nunca revela navios intactos (`N`). As listas e contagens devolvidas são cópias: modificar uma visão não altera a partida, e novas jogadas não modificam visões antigas.

Exemplo de uma partida completa local, sem servidor, usando os navios da seção 10. Execute o bloco inteiro no terminal, a partir da raiz do projeto:

```bash
python3 - <<'PY'
from partida import Partida
from tabuleiro import Tabuleiro

partida = Partida(
    Tabuleiro([("A1", "B1"), ("D4", "D5")]),
    Tabuleiro([("D1", "E1"), ("B3", "B4")]),
)
jogadas = [(1, "D1"), (2, "C1"), (1, "E1"), (2, "C2"),
           (1, "B3"), (2, "C3"), (1, "B4")]
for jogador, coordenada in jogadas:
    resultado = partida.disparar(jogador, coordenada)
    print(f"Jogador {jogador} -> {coordenada}: {resultado}; próxima vez: {partida.vez}")
print("Vencedor:", partida.vencedor)  # 1
print("Visão do jogador 2:", partida.visao(2))
PY
```

**Integração com o servidor (implementada na fase 6, ver "Usando o servidor.py"):**

1. Quando o segundo jogador entrar, criar `Partida` com os dois tabuleiros produzidos pela fábrica do servidor. A espera pelo adversário continua sendo responsabilidade do servidor.
2. Em `Servidor._disparo_em_partida`, com o lock do estado adquirido, chamar `partida.disparar(conexao.numero, mensagem.get("coordenada"))`. O número deve vir da conexão, nunca de um campo informado pelo cliente. `ForaDeVez`, `CoordenadaInvalida` e `DisparoRepetido` fornecem os códigos já definidos; o servidor responde `ERRO` somente ao autor.
3. Após um disparo válido, obter `visao(1)` e `visao(2)` e usar cada visão para compor a respectiva `ATUALIZACAO`, junto com `atirador`, `coordenada` e `resultado`. Não enviar uma mesma visão aos dois jogadores.
4. Se `partida.encerrada`, enviar primeiro as atualizações finais, depois `FIM` com `motivo: "VITORIA"` e `partida.vencedor`; fechar as conexões e liberar a partida. Caso contrário, atualizar o estado do servidor a partir de `partida.vez`.

A classe não tem sockets, não envia `FIM` e não trata `SAIR` ou desconexões. Esses eventos e a sincronização continuam sob responsabilidade do servidor. Ao entregar os tabuleiros a `Partida`, aplique os disparos somente por `Partida.disparar`, evitando alterações que ignorem os turnos.

### Usando o servidor.py (fases 3 e 6)

```
python servidor.py          # porta 5050, escuta em 0.0.0.0
python servidor.py 6000     # outra porta
```

(No Windows, `py -3 servidor.py ...`; ver a [seção 3](#3-requisitos-mínimos-e-como-executar).)

O servidor mostra no terminal cada evento (conexões, início, disparos e resultados, saídas). Para encerrá-lo, use Ctrl+C.

Todas as linhas da tabela 9.2 estão implementadas: `AGUARDE` ao primeiro jogador (1); `INICIO` aos dois quando o segundo conecta, com tabuleiros sorteados pelo `tabuleiro.py` (2); `ERRO PARTIDA_NAO_INICIADA` para `DISPARAR` enquanto aguarda (3); saída ou queda do jogador 1 enquanto aguarda, voltando a `SEM_JOGADORES` (4 e 5); disparo válido com `ATUALIZACAO` aos dois e troca da vez (6); vitória com `ATUALIZACAO` final e `FIM VITORIA` aos dois (7); `ERRO COORDENADA_INVALIDA`, `DISPARO_REPETIDO` e `FORA_DE_VEZ` só para quem tentou (8 e 9); `ERRO MENSAGEM_INVALIDA` e `TIPO_DESCONHECIDO` (10); `FIM ADVERSARIO_SAIU` e `FIM ADVERSARIO_DESCONECTOU` (11 e 12); `ERRO SERVIDOR_CHEIO` a um terceiro cliente (13). Depois de um `FIM`, o servidor aceita uma nova partida.

**Como um disparo é processado (fase 6).** Quando o segundo jogador entra, o servidor cria uma `Partida` com os dois tabuleiros da fábrica e monta cada `INICIO` a partir de `partida.visao(n)`. Ao chegar `{"tipo": "DISPARAR", "coordenada": "B3"}`, a thread da conexão:

1. adquire o lock do estado e confere se a conexão ainda é um jogador da partida (eventos de conexões já encerradas são ignorados);
2. se a partida não começou, responde `ERRO PARTIDA_NAO_INICIADA`;
3. chama `partida.disparar(conexao.numero, "B3")`, com o número **vindo da conexão**, nunca da mensagem. `Partida` verifica, nessa ordem, vez, coordenada e repetição (seção 8); `ForaDeVez`, `CoordenadaInvalida` ou `DisparoRepetido` viram `ERRO` com o `codigo` e o texto da exceção, enviado só ao autor;
4. se o disparo é válido, envia a cada jogador a **sua** `ATUALIZACAO`: `atirador`, `coordenada` e `resultado` iguais para os dois, mais `partida.visao(n)` de quem recebe (`sua_vez`, tabuleiros e `navios_restantes`). O tabuleiro do adversário nunca contém `N`;
5. se `partida.encerrada`, envia `FIM` com `motivo: "VITORIA"` e `vencedor` aos dois, fecha as duas conexões e volta a `SEM_JOGADORES`; senão, o estado passa a `VEZ_J1` ou `VEZ_J2` conforme `partida.vez`.

Como o servidor está organizado:

| Peça | Papel |
|------|-------|
| `Servidor` | Guarda o estado (seção 9.1), os jogadores (`{1: Conexao, 2: Conexao}`) e a `Partida` em andamento. `Servidor(criar_tabuleiro=...)` permite tabuleiros fixos nos testes. |
| Thread principal | Só aceita conexões (`servir_para_sempre`). |
| Uma thread por conexão | Bloqueia em `receber_mensagem` e trata cada mensagem; assim o servidor percebe a saída do jogador 1 mesmo enquanto ele aguarda. |
| `Servidor._lock` | Um único lock protege todo o estado: os eventos dos dois jogadores são tratados um de cada vez. |
| `Conexao` | Socket, leitor único (`makefile("rb")`), número do jogador e um lock de envio por socket (seção 11). `fechar()` encerra a conexão; leitor e socket são liberados pela própria thread da conexão. |

Eventos de uma conexão que o servidor já encerrou são ignorados. Se o jogador 1 tiver caído no exato momento em que o segundo chega (o envio de `INICIO` falha), a partida não começa e quem chegou vira o jogador 1 (observação da seção 9.2).

### Usando o cliente.py (fases 4, 7 e 8)

```
python cliente.py                  # conecta em 127.0.0.1:5050
python cliente.py 192.168.0.10     # conecta em outro IP na porta 5050
python cliente.py 127.0.0.1 6000   # endereço e porta personalizados
```

(No Windows, `py -3 cliente.py ...`; ver a [seção 3](#3-requisitos-mínimos-e-como-executar).)

O que o cliente faz (linhas da tabela 9.3): conexão TCP com leitor único (`makefile("rb")`), transição entre `CONECTADO`, `MINHA_VEZ`, `VEZ_ADVERSARIO` e `ENCERRADO`, exibição de `AGUARDE`, desenho dos dois tabuleiros ao receber `INICIO` e `ATUALIZACAO`, decisão de `ERRO` pelo campo `codigo` (encerrando em `SERVIDOR_CHEIO`), encerramento limpo ao receber `FIM` (`VITORIA`, `ADVERSARIO_SAIU`, `ADVERSARIO_DESCONECTOU`), envio de `SAIR` e tratamento de queda de conexão (`ConexaoEncerrada` / `MensagemInvalida`).

**Entrada (fase 7).** `ao_minha_vez()` lê a coordenada digitada pelo jogador (`input()`), normaliza com `.strip().upper()` e envia `{"tipo": "DISPARAR", "coordenada": entrada}`; se o jogador digitar `SAIR`, chama `enviar_sair()` e encerra. `EOFError` e `KeyboardInterrupt` durante o `input()` também encerram via `enviar_sair()`. O cliente **não valida** a coordenada: quem decide é o servidor, que responde `ERRO` e o cliente pede outra.

**Recepção durante a digitação (fase 8).** Antes, o cliente chamava `input()` na mesma thread que lia o socket; se o adversário saísse enquanto o jogador estava no prompt, o `FIM` ficava esperando até ele apertar Enter. Agora a leitura da rede e a do teclado são threads separadas, ligadas à thread principal por uma fila (seção 2.1). O `FIM` encerra o cliente imediatamente. Como a thread do teclado só existe na vez do jogador, **`SAIR` só pode ser digitado na vez dele**; enquanto espera o adversário, a saída é `Ctrl+C`, que envia `SAIR`. Linhas digitadas fora da vez ficam no buffer do terminal e serão lidas no próximo `input()`.

**Encerramento com o teclado ocupado.** Não existe como interromper um `input()` de outra thread. Se o `FIM` chega enquanto a thread do teclado ainda espera uma linha, o `main()` fecha o socket, descarrega a saída e usa `os._exit(0)`: no Linux, com o `stdin` num terminal, a finalização normal do interpretador ficou travada nessa leitura (reproduzido com Python 3.12 e `pty`). No Windows a finalização normal funciona, mas o comportamento é o mesmo nos dois sistemas. Sem leitura pendente (por exemplo, `SAIR` digitado ou `Ctrl+C` fora do prompt), o programa termina normalmente.

## 7. Catálogo de mensagens

| Tipo | Quem envia | Quem recebe | Resumo |
|------|-----------|-------------|--------|
| `DISPARAR` | cliente | servidor | Disparo em uma coordenada, na vez do jogador |
| `SAIR` | cliente | servidor | O jogador abandona a partida |
| `AGUARDE` | servidor | cliente (jogador 1) | Conectado, esperando o segundo jogador |
| `INICIO` | servidor | os dois clientes | A partida começou |
| `ATUALIZACAO` | servidor | os dois clientes (cada um a sua versão) | Resultado de um disparo válido |
| `ERRO` | servidor | só o cliente que causou o erro | Pedido recusado; o estado não mudou |
| `FIM` | servidor | os clientes ainda conectados | A partida terminou |

Nas tabelas abaixo, "tipo JSON" é o tipo do valor no JSON (`string`, `integer`, `boolean`, `array`, `object`, `null`).

### 7.1 `DISPARAR` (cliente → servidor)

**Quando:** na vez do jogador, para atirar em uma casa do tabuleiro do adversário. O servidor aceita o disparo apenas se a partida estiver em andamento, for a vez desse jogador, a coordenada for válida e ainda não tiver sido usada por ele (ordem de verificação na seção 8).

| Campo | Tipo JSON | Obrigatório | Valores aceitos |
|-------|-----------|-------------|-----------------|
| `tipo` | string | sim | `"DISPARAR"` |
| `coordenada` | string | sim | `A1` a `E5` (seção 6) |

```json
{
  "tipo": "DISPARAR",
  "coordenada": "B3"
}
```

**Resposta:** se válido, `ATUALIZACAO` para os dois jogadores. Se não, `ERRO` só para quem enviou.

### 7.2 `SAIR` (cliente → servidor)

**Quando:** a qualquer momento (inclusive fora da vez ou enquanto aguarda o segundo jogador) em que o jogador queira abandonar. Não tem resposta ao remetente: o servidor fecha a conexão dele. Depois de enviar, o cliente pode fechar o socket.

| Campo | Tipo JSON | Obrigatório | Valores aceitos |
|-------|-----------|-------------|-----------------|
| `tipo` | string | sim | `"SAIR"` |

```json
{
  "tipo": "SAIR"
}
```

**Efeito:** se havia partida em andamento, o adversário recebe `FIM` com `motivo` `ADVERSARIO_SAIU`. Se o jogador era o primeiro e ainda esperava o segundo, nenhuma partida existe (seção 9.2).

### 7.3 `AGUARDE` (servidor → cliente)

**Quando:** logo depois que o **primeiro** jogador conecta e ainda não há adversário. É enviada uma vez. O segundo jogador nunca a recebe: ao conectar, a partida começa e ele recebe `INICIO`.

| Campo | Tipo JSON | Obrigatório | Valores aceitos |
|-------|-----------|-------------|-----------------|
| `tipo` | string | sim | `"AGUARDE"` |
| `mensagem` | string | sim | Texto livre para exibir ao usuário |

```json
{
  "tipo": "AGUARDE",
  "mensagem": "Conectado. Aguardando o segundo jogador..."
}
```

### 7.4 `INICIO` (servidor → cliente)

**Quando:** quando o segundo jogador conecta. O servidor envia **uma `INICIO` a cada jogador**, com o tabuleiro próprio daquele jogador. O jogador 1 recebe `sua_vez: true` e o jogador 2, `sua_vez: false`.

| Campo | Tipo JSON | Obrigatório | Valores aceitos |
|-------|-----------|-------------|-----------------|
| `tipo` | string | sim | `"INICIO"` |
| `jogador` | integer | sim | `1` ou `2`: o número do jogador que recebe a mensagem |
| `sua_vez` | boolean | sim | `true` para o jogador 1, `false` para o 2 |
| `tabuleiro_proprio` | array de 5 strings | sim | Tabuleiro do próprio jogador, **com os navios (`N`)** |
| `tabuleiro_adversario` | array de 5 strings | sim | Tabuleiro do adversário como o jogador o conhece: tudo `~` no início |

Para o jogador 1:

```json
{
  "tipo": "INICIO",
  "jogador": 1,
  "sua_vez": true,
  "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
  "tabuleiro_adversario": ["~~~~~", "~~~~~", "~~~~~", "~~~~~", "~~~~~"]
}
```

Para o jogador 2 (outro número, outro tabuleiro, sem a vez):

```json
{
  "tipo": "INICIO",
  "jogador": 2,
  "sua_vez": false,
  "tabuleiro_proprio": ["~~~NN", "~~~~~", "~N~~~", "~N~~~", "~~~~~"],
  "tabuleiro_adversario": ["~~~~~", "~~~~~", "~~~~~", "~~~~~", "~~~~~"]
}
```

### 7.5 `ATUALIZACAO` (servidor → cliente)

**Quando:** após **cada disparo válido**, inclusive o que encerra a partida. O servidor envia **uma `ATUALIZACAO` a cada jogador**: o `tabuleiro_proprio` e o `tabuleiro_adversario` são os daquele jogador; os demais campos descrevem o mesmo disparo para os dois. Depois do disparo vencedor, `sua_vez` é `false` para ambos e a `ATUALIZACAO` é seguida de `FIM`.

| Campo | Tipo JSON | Obrigatório | Valores aceitos |
|-------|-----------|-------------|-----------------|
| `tipo` | string | sim | `"ATUALIZACAO"` |
| `atirador` | integer | sim | `1` ou `2`: quem fez o disparo |
| `coordenada` | string | sim | `A1` a `E5`: onde o disparo caiu |
| `resultado` | string | sim | `"AGUA"`, `"ACERTO"` ou `"AFUNDOU"` |
| `sua_vez` | boolean | sim | `true` se o jogador que recebe joga a seguir |
| `tabuleiro_proprio` | array de 5 strings | sim | Tabuleiro do próprio jogador já atualizado (com `N`) |
| `tabuleiro_adversario` | array de 5 strings | sim | O que o jogador sabe do adversário (só `~`, `O`, `X`) |
| `navios_restantes` | object | sim | `{"proprio": n, "adversario": m}`, com `n` e `m` inteiros de `0` a `2` |

`resultado`: `AGUA` = nenhuma casa de navio; `ACERTO` = acertou um navio que ainda tem casa intacta; `AFUNDOU` = acertou a última casa intacta de um navio.

O exemplo completo, com as duas versões (jogador 1 e jogador 2) do mesmo disparo, está na [seção 10](#10-exemplo-de-turno-completo).

### 7.6 `ERRO` (servidor → cliente)

**Quando:** o servidor recusa um pedido. É enviado **apenas ao jogador que o causou**. Salvo `SERVIDOR_CHEIO`, a conexão continua aberta e **tabuleiros e turno permanecem como estavam**: o jogador pode tentar de novo.

| Campo | Tipo JSON | Obrigatório | Valores aceitos |
|-------|-----------|-------------|-----------------|
| `tipo` | string | sim | `"ERRO"` |
| `codigo` | string | sim | Um dos códigos da seção 8 |
| `mensagem` | string | sim | Texto livre para exibir ao usuário |

```json
{
  "tipo": "ERRO",
  "codigo": "FORA_DE_VEZ",
  "mensagem": "Não é a sua vez de jogar."
}
```

O cliente deve decidir o que fazer pelo `codigo` (estável), e mostrar a `mensagem` (que pode mudar de redação).

### 7.7 `FIM` (servidor → cliente)

**Quando:** a partida terminou. Depois de enviar, o servidor fecha as conexões. Em caso de vitória é enviada aos **dois** jogadores logo depois da última `ATUALIZACAO`. Em caso de saída ou desconexão, só o jogador que ficou a recebe (o outro já foi embora).

| Campo | Tipo JSON | Obrigatório | Valores aceitos |
|-------|-----------|-------------|-----------------|
| `tipo` | string | sim | `"FIM"` |
| `motivo` | string | sim | `"VITORIA"`, `"ADVERSARIO_SAIU"` ou `"ADVERSARIO_DESCONECTOU"` |
| `vencedor` | integer ou null | sim | `1` ou `2` quando `motivo` é `VITORIA`; `null` nos outros casos (partida interrompida, sem vencedor) |
| `mensagem` | string | sim | Texto livre para exibir ao usuário |

- `VITORIA`: um jogador afundou os dois navios do outro.
- `ADVERSARIO_SAIU`: o adversário enviou `SAIR`.
- `ADVERSARIO_DESCONECTOU`: a conexão do adversário caiu sem `SAIR`.

```json
{
  "tipo": "FIM",
  "motivo": "VITORIA",
  "vencedor": 1,
  "mensagem": "Jogador 1 afundou todos os navios do adversário."
}
```

```json
{
  "tipo": "FIM",
  "motivo": "ADVERSARIO_SAIU",
  "vencedor": null,
  "mensagem": "O adversário saiu da partida."
}
```

## 8. Códigos de erro

Valores do campo `codigo` da mensagem `ERRO`. Em todos (exceto `SERVIDOR_CHEIO`) o estado da partida **não muda**.

| Código | Quando o servidor envia |
|--------|-------------------------|
| `PARTIDA_NAO_INICIADA` | `DISPARAR` recebido enquanto ainda aguarda o segundo jogador |
| `FORA_DE_VEZ` | `DISPARAR` de um jogador que não é o da vez |
| `COORDENADA_INVALIDA` | `DISPARAR` sem `coordenada`, com valor que não é texto ou fora de `[A-E][1-5]` (inclui minúsculas, espaços, `F9`, `B10`) |
| `DISPARO_REPETIDO` | `DISPARAR` em uma coordenada que esse jogador já atingiu antes |
| `MENSAGEM_INVALIDA` | A linha recebida não é uma mensagem válida (`MensagemInvalida` do `protocolo.py`: JSON inválido, sem `tipo`, etc.) |
| `TIPO_DESCONHECIDO` | `tipo` válido como JSON, mas que o cliente não pode enviar (qualquer um que não seja `DISPARAR` ou `SAIR`) |
| `SERVIDOR_CHEIO` | Um terceiro cliente tentou conectar com a partida em andamento. **O servidor fecha essa conexão** logo depois |

**Ordem de verificação de um `DISPARAR`** (a primeira que falhar define o erro; os testes de integração vão depender disto):

1. Partida iniciada? Não → `PARTIDA_NAO_INICIADA`.
2. É a vez de quem enviou? Não → `FORA_DE_VEZ`.
3. `coordenada` presente e válida? Não → `COORDENADA_INVALIDA`.
4. Coordenada ainda não atingida por esse jogador? Se já foi → `DISPARO_REPETIDO`.
5. Tudo certo → aplica o disparo, troca a vez (ou encerra) e envia `ATUALIZACAO`.

Exemplos:

```json
{"tipo": "ERRO", "codigo": "COORDENADA_INVALIDA", "mensagem": "Coordenada inválida. Use de A1 a E5."}
```

```json
{"tipo": "ERRO", "codigo": "DISPARO_REPETIDO", "mensagem": "Você já atirou em B3."}
```

## 9. Estados e eventos

### 9.1 Estados do servidor

| Estado | Significado |
|--------|-------------|
| `SEM_JOGADORES` | Nenhum jogador conectado. Estado inicial. |
| `AGUARDANDO_SEGUNDO` | O jogador 1 está conectado e recebeu `AGUARDE`. |
| `VEZ_J1` | Partida em andamento; só o jogador 1 pode atirar. |
| `VEZ_J2` | Partida em andamento; só o jogador 2 pode atirar. |
| `ENCERRADA` | O servidor enviou `FIM` e está fechando as conexões. Transitório: em seguida volta a `SEM_JOGADORES`, pronto para outra partida (uma partida por vez). |

### 9.2 Tabela de estados e eventos do servidor

Em `VEZ_J1` e `VEZ_J2` as linhas são simétricas: "jogador da vez" e "adversário" referem-se a quem atira e a quem aguarda naquele estado.

| # | Estado atual | Evento | Ação do servidor | Próximo estado |
|---|--------------|--------|------------------|----------------|
| 1 | `SEM_JOGADORES` | Um cliente conecta | Registra como jogador 1; envia `AGUARDE` | `AGUARDANDO_SEGUNDO` |
| 2 | `AGUARDANDO_SEGUNDO` | Um cliente conecta (início da partida) | Registra como jogador 2; posiciona os navios; envia `INICIO` a cada jogador (jogador 1 com `sua_vez: true`) | `VEZ_J1` |
| 3 | `AGUARDANDO_SEGUNDO` | `DISPARAR` do jogador 1 | `ERRO` `PARTIDA_NAO_INICIADA` só para ele | `AGUARDANDO_SEGUNDO` |
| 4 | `AGUARDANDO_SEGUNDO` | `SAIR` do jogador 1 | Fecha a conexão dele; **nenhuma partida é criada**; nada é enviado a ninguém | `SEM_JOGADORES` |
| 5 | `AGUARDANDO_SEGUNDO` | Desconexão do jogador 1 (conexão cai) | Libera o jogador 1; nenhuma partida criada | `SEM_JOGADORES` |
| 6 | `VEZ_J1` / `VEZ_J2` | **Disparo válido** do jogador da vez (não encerra) | Aplica o disparo; envia `ATUALIZACAO` aos dois (`sua_vez: true` só para o adversário) | Vez do adversário |
| 7 | `VEZ_J1` / `VEZ_J2` | Disparo válido do jogador da vez que **afunda o último navio** do adversário (**vitória**) | Envia `ATUALIZACAO` aos dois (`sua_vez: false` para ambos), depois `FIM` `VITORIA` com `vencedor` aos dois; fecha as conexões | `ENCERRADA` → `SEM_JOGADORES` |
| 8 | `VEZ_J1` / `VEZ_J2` | **Tentativa inválida** do jogador da vez: `DISPARAR` com coordenada inválida ou repetida | `ERRO` (`COORDENADA_INVALIDA` ou `DISPARO_REPETIDO`) só para ele; tabuleiros e vez inalterados | Mesmo estado |
| 9 | `VEZ_J1` / `VEZ_J2` | `DISPARAR` do **adversário** (fora da vez) | `ERRO` `FORA_DE_VEZ` só para ele; nada muda | Mesmo estado |
| 10 | `VEZ_J1` / `VEZ_J2` | Linha inválida (`MensagemInvalida`) ou `tipo` não permitido, de qualquer jogador | `ERRO` `MENSAGEM_INVALIDA` ou `TIPO_DESCONHECIDO` só para ele; nada muda | Mesmo estado |
| 11 | `VEZ_J1` / `VEZ_J2` | `SAIR` de qualquer jogador | Envia `FIM` `ADVERSARIO_SAIU` (`vencedor: null`) ao outro; fecha as duas conexões | `ENCERRADA` → `SEM_JOGADORES` |
| 12 | `VEZ_J1` / `VEZ_J2` | Desconexão de qualquer jogador (`ConexaoEncerrada`, inclusive `MensagemIncompleta`) | Envia `FIM` `ADVERSARIO_DESCONECTOU` (`vencedor: null`) ao outro; fecha as conexões | `ENCERRADA` → `SEM_JOGADORES` |
| 13 | `VEZ_J1` / `VEZ_J2` | Um terceiro cliente conecta | `ERRO` `SERVIDOR_CHEIO` só para ele; fecha a conexão dele; a partida não é afetada | Mesmo estado |

Observações:

- O evento "disparo válido" inclui, na linha 6, os três resultados: `AGUA`, `ACERTO` e `AFUNDOU` (que não afunde o último navio).
- `SAIR` é aceito em qualquer estado, mesmo fora da vez.
- Em `AGUARDANDO_SEGUNDO`, uma linha inválida ou um `tipo` não permitido recebe o mesmo `ERRO` da linha 10 (`MENSAGEM_INVALIDA` / `TIPO_DESCONHECIDO`), sem mudar de estado.
- Se, ao iniciar a partida (linha 2), o servidor perceber que o jogador 1 já caiu (por exemplo, falha ao enviar `INICIO` a ele), a partida **não começa**: o jogador que acabou de chegar passa a ser o jogador 1 e recebe `AGUARDE` (como na linha 1).
- Eventos de um jogador cuja conexão já foi encerrada pelo servidor são ignorados.

### Se o primeiro jogador sair antes de o segundo entrar

Linhas 4 e 5 da tabela. Enquanto o servidor está em `AGUARDANDO_SEGUNDO` e o jogador 1 envia `SAIR` ou a conexão dele cai:

1. Não existe adversário a avisar, então **nenhuma mensagem** é enviada a ninguém e **não existe partida** (nem tabuleiros) para encerrar.
2. O servidor descarta a conexão e volta a `SEM_JOGADORES`.
3. O próximo cliente que conectar será **o jogador 1** e receberá `AGUARDE`, como se o anterior nunca tivesse existido. Quem chegar depois passa a ser o jogador 2.

Para detectar isso, o servidor precisa continuar lendo o socket do jogador 1 enquanto aguarda (por exemplo, com uma thread por conexão).

### 9.3 Estados do cliente

| Estado | Significado |
|--------|-------------|
| `CONECTADO` | Conectou ao servidor; ainda não recebeu `INICIO` (pode ter recebido `AGUARDE`). |
| `MINHA_VEZ` | Último `sua_vez` recebido foi `true`: o jogador digita a coordenada. |
| `VEZ_ADVERSARIO` | Último `sua_vez` recebido foi `false`: aguarda mensagens do servidor. |
| `ENCERRADO` | Partida terminada ou conexão perdida; o cliente fecha o socket e sai. |

| Estado atual | Evento | Ação do cliente | Próximo estado |
|--------------|--------|-----------------|----------------|
| `CONECTADO` | Recebe `AGUARDE` | Mostra a mensagem | `CONECTADO` |
| `CONECTADO` | Recebe `INICIO` | Guarda o número do jogador; desenha os dois tabuleiros | `MINHA_VEZ` se `sua_vez`, senão `VEZ_ADVERSARIO` |
| `MINHA_VEZ` | Usuário digita uma coordenada | Normaliza (`strip().upper()`); envia `DISPARAR` | `MINHA_VEZ` (aguarda a resposta do servidor) |
| `MINHA_VEZ` | Usuário digita `SAIR` (ou fim da entrada, `EOF`) | Envia `SAIR`; fecha o socket | `ENCERRADO` |
| `MINHA_VEZ` / `VEZ_ADVERSARIO` | Recebe `ATUALIZACAO` | Redesenha os tabuleiros; mostra coordenada e resultado | `MINHA_VEZ` se `sua_vez`, senão `VEZ_ADVERSARIO` |
| qualquer ativo | Recebe `ERRO` | Mostra a `mensagem`; **o estado não muda**; se era sua vez, pede outra coordenada | O mesmo |
| qualquer ativo | Recebe `FIM`, **inclusive enquanto o usuário ainda não digitou nada** | Mostra o desfecho (`VITORIA` com `vencedor` igual ou diferente do seu número, ou aviso de saída/desconexão do adversário) e encerra sem esperar a digitação | `ENCERRADO` |
| qualquer ativo | `Ctrl+C` | Envia `SAIR`; fecha o socket | `ENCERRADO` |
| qualquer ativo | `ConexaoEncerrada` / `MensagemIncompleta` sem ter recebido `FIM` | Mostra "conexão perdida" | `ENCERRADO` |
| qualquer ativo | `MensagemInvalida` vinda do servidor | Trata como falha de protocolo; fecha o socket | `ENCERRADO` |

O cliente sabe se foi ele quem atirou comparando `atirador` com o seu número de `jogador`, recebido no `INICIO`. Se o cliente for encerrado de forma brusca (processo morto, rede caída) o servidor percebe a conexão encerrada e trata como desconexão; com `Ctrl+C` o cliente ainda tem tempo de enviar `SAIR`.

### 9.4 Diagramas de estados

Servidor (os números são as linhas da tabela 9.2). `ENCERRADA` é transitório e não aparece:

```
                          1: um cliente conecta
        [SEM_JOGADORES] ---------------------------> [AGUARDANDO_SEGUNDO]
              ^  ^                                          |        |
              |  |        4/5: jogador 1 sai ou cai        |        | 2: o segundo cliente conecta
              |  +-----------------------------------------+        v
              |                                                  [VEZ_J1] <----+
              |                                                    |  |  6: disparo válido
              |        7 (vitória), 11 (SAIR), 12 (desconexão)     |  +----> [VEZ_J2]  (e de volta)
              +----------------------------------------------------+
                        fecha as conexões e volta a SEM_JOGADORES

     Não mudam o estado: 3 (DISPARAR sem partida), 8, 9 e 10 (erros) e 13 (terceiro cliente).
```

Cliente:

```
 [CONECTADO]        --- INICIO (sua_vez = true) ---------------->  [MINHA_VEZ]
 [CONECTADO]        --- INICIO (sua_vez = false) --------------->  [VEZ_ADVERSARIO]
 [MINHA_VEZ]        --- ATUALIZACAO (sua_vez = false) ---------->  [VEZ_ADVERSARIO]
 [VEZ_ADVERSARIO]   --- ATUALIZACAO (sua_vez = true) ----------->  [MINHA_VEZ]
 [MINHA_VEZ]        --- ERRO (coordenada inválida, repetida...) ->  [MINHA_VEZ]  (pede outra)
 [CONECTADO]        --- AGUARDE ou ERRO PARTIDA_NAO_INICIADA ---->  [CONECTADO]

 Qualquer estado ativo --- FIM, SAIR digitado, Ctrl+C, ERRO SERVIDOR_CHEIO,
                           conexão perdida ou mensagem inválida do servidor --->  [ENCERRADO]
```

## 10. Exemplo de turno completo

**Situação:** a partida está em `VEZ_J1`, sem disparos ainda. Os tabuleiros que **só o servidor** conhece por inteiro:

```
Jogador 1 (navios A1-B1 e D4-D5)     Jogador 2 (navios D1-E1 e B3-B4)
    A B C D E                            A B C D E
 1  N N ~ ~ ~                         1  ~ ~ ~ N N
 2  ~ ~ ~ ~ ~                         2  ~ ~ ~ ~ ~
 3  ~ ~ ~ ~ ~                         3  ~ N ~ ~ ~
 4  ~ ~ ~ N ~                         4  ~ N ~ ~ ~
 5  ~ ~ ~ N ~                         5  ~ ~ ~ ~ ~
```

**Passo 1. O jogador 1 envia o disparo** (cliente 1 → servidor):

```json
{
  "tipo": "DISPARAR",
  "coordenada": "B3"
}
```

**Passo 2. O servidor valida**, na ordem da seção 8: partida iniciada (sim); vez do jogador 1 (sim); `B3` casa com `[A-E][1-5]` (sim); jogador 1 nunca atirou em `B3` (sim). O disparo é válido.

**Passo 3. O servidor aplica o disparo.** Em `B3` há um navio do jogador 2, e a outra casa dele (`B4`) continua intacta: `resultado` = `ACERTO`. A vez passa ao jogador 2. Os navios restantes continuam 2 e 2.

**Passo 4. O servidor envia a cada jogador a sua versão** (servidor → cliente 1):

```json
{
  "tipo": "ATUALIZACAO",
  "atirador": 1,
  "coordenada": "B3",
  "resultado": "ACERTO",
  "sua_vez": false,
  "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
  "tabuleiro_adversario": ["~~~~~", "~~~~~", "~X~~~", "~~~~~", "~~~~~"],
  "navios_restantes": {"proprio": 2, "adversario": 2}
}
```

(servidor → cliente 2):

```json
{
  "tipo": "ATUALIZACAO",
  "atirador": 1,
  "coordenada": "B3",
  "resultado": "ACERTO",
  "sua_vez": true,
  "tabuleiro_proprio": ["~~~NN", "~~~~~", "~X~~~", "~N~~~", "~~~~~"],
  "tabuleiro_adversario": ["~~~~~", "~~~~~", "~~~~~", "~~~~~", "~~~~~"],
  "navios_restantes": {"proprio": 2, "adversario": 2}
}
```

Estas são exatamente as duas mensagens que `tests/test_integracao.py` (`test_disparo_valido_atualiza_os_dois_clientes`) confere campo a campo contra o servidor real, com esses tabuleiros fixos.

### O que cada lado pode ver

| Informação | Jogador 1 (atirador) | Jogador 2 (alvo) |
|------------|----------------------|------------------|
| Coordenada e resultado do disparo | `B3`, `ACERTO` | `B3`, `ACERTO` |
| Seus próprios navios | `A1-B1` e `D4-D5`, intactos | `D1-E1` intacto; `B3` atingido (`X`) e `B4` ainda intacto (`N`) |
| Tabuleiro do adversário | Só a casa descoberta: `X` em `B3`. **Não sabe** que `B4` é a outra metade do navio nem onde está o outro | Nada: ele ainda não atirou (tudo `~`) |
| De quem é a vez | Do jogador 2 (`sua_vez: false`) | Dele (`sua_vez: true`) |

O jogador 1 **não recebe** o `tabuleiro_proprio` do jogador 2, e o servidor nunca coloca `N` no `tabuleiro_adversario` de ninguém. O estado do servidor passa a `VEZ_J2`.

### Tentativas inválidas logo em seguida

Se o jogador 1, ainda sem ser a sua vez, tentar atirar de novo (servidor → só o cliente 1; o estado fica `VEZ_J2`, o jogador 2 não é avisado):

```json
{"tipo": "DISPARAR", "coordenada": "C2"}
```
```json
{"tipo": "ERRO", "codigo": "FORA_DE_VEZ", "mensagem": "Não é a sua vez de jogar."}
```

Se o jogador 2, na sua vez, enviar `{"tipo": "DISPARAR", "coordenada": "Z9"}`, recebe `ERRO` `COORDENADA_INVALIDA` e continua sendo a vez dele; o jogador 1 nada recebe.

### Ordem das mensagens

As duas `ATUALIZACAO` seguem por conexões diferentes; o servidor as envia em sequência, mas **os clientes não devem depender de qual chega primeiro**. Dentro de uma mesma conexão a ordem é garantida pelo TCP.

## 11. Usando o protocolo.py

`protocolo.py` está na raiz do projeto e é importado por cliente e servidor. Ele não conhece as regras do jogo.

| Item | Descrição |
|------|-----------|
| `enviar_mensagem(sock, mensagem)` | Envia um `dict` (com `"tipo"`) como uma linha JSON UTF-8 + LF, com `sendall`. |
| `receber_mensagem(leitor)` | Bloqueia até chegar uma mensagem completa e devolve o `dict`. |
| `ConexaoEncerrada` | A outra ponta fechou ou caiu. |
| `MensagemIncompleta` | Subclasse de `ConexaoEncerrada`: a conexão acabou no meio de uma mensagem. |
| `MensagemInvalida` | Linha completa que não é mensagem válida (ou, no envio, mensagem inválida). A conexão segue utilizável. |
| `ProtocoloError` | Classe-base das três exceções. |
| `TAMANHO_MAXIMO` | 65 536 bytes por mensagem (sem a LF). |

Exemplo de uso (esboço para o cliente; o servidor usa o socket devolvido por `accept()`):

```python
import socket
from protocolo import (
    enviar_mensagem, receber_mensagem, ConexaoEncerrada, MensagemInvalida,
)

sock = socket.create_connection((endereco, porta))
leitor = sock.makefile("rb")          # UMA vez por conexão; reutilize sempre

enviar_mensagem(sock, {"tipo": "DISPARAR", "coordenada": "B3"})

try:
    mensagem = receber_mensagem(leitor)
except MensagemInvalida:
    ...   # linha ruim já consumida; no servidor, responda ERRO MENSAGEM_INVALIDA
except ConexaoEncerrada:
    ...   # o outro lado se foi (inclui MensagemIncompleta)
finally:
    ...   # ao terminar: leitor.close(); sock.close()
```

Cuidados:

- Não use `sock.recv()` em uma conexão que tem leitor: o leitor já pode ter lido bytes da próxima mensagem.
- Não use `sock.settimeout()` em sockets com leitor `makefile`: se o tempo estourar, o buffer do leitor pode ficar inconsistente (aviso da documentação do Python). Use sockets bloqueantes.
- `sendall` não é atômico entre threads. Se mais de uma thread puder escrever no mesmo socket (por exemplo, a thread do jogador 1 enviando também ao jogador 2), proteja `enviar_mensagem` com um `threading.Lock` por socket.
- Para fechar uma conexão de verdade é preciso fechar o leitor **e** o socket (o `makefile` mantém o socket aberto enquanto existir).
- `receber_mensagem` bloqueia. O servidor, que precisa ouvir os dois jogadores ao mesmo tempo, precisa de uma thread por conexão ou de `selectors`; essa escolha é da fase 3.

## 12. Testes

A partir da raiz do projeto, para executar **todos** os testes (157, cerca de 7 s no Windows; no Linux o encerramento do servidor em cada teste espera até 0,5 s e a suíte leva mais tempo):

```powershell
py -3 -m unittest discover -s tests -v          # Windows PowerShell
```
```bash
python3 -m unittest discover -s tests -v        # Linux/macOS
```

Os testes não precisam de servidor aberto: cada um sobe o seu, numa porta livre de `127.0.0.1`. Para executar só os testes de integração: `py -3 -m unittest discover -s tests -p "test_integracao.py" -v`.

`tests/test_integracao.py` (fase 8) joga o **jogo completo** com o servidor real e clientes reais por TCP. Usa tabuleiros fixos (jogador 1: `A1-B1` e `D4-D5`; jogador 2: `D1-E1` e `B3-B4`) para que acerto, afundamento e vitória sejam previsíveis; o sorteio de posições do jogo normal continua sendo o padrão do `Servidor`. Cobre: primeiro jogador aguarda e a partida começa com o segundo; `INICIO` de cada um e quem começa; cada jogador vê só os próprios navios e nunca recebe `N` do adversário; disparo válido atualizando os dois (a mensagem da seção 10); coordenada inválida, disparo repetido e disparo fora da vez gerando o `ERRO` certo só para quem errou, sem mudar tabuleiros nem vez; água, acerto, afundamento e vitória dos dois jogadores; `ATUALIZACAO` final antes do `FIM` e fechamento das conexões; `SAIR` e queda brusca (RST) por quem joga e por quem espera a vez; saída e queda do primeiro jogador durante a espera liberando a vaga; terceiro cliente recebendo `SERVIDOR_CHEIO` sem afetar a partida; nova partida com estado novo depois de vitória e depois de saída. Também usa a classe `Cliente` real (o cliente parado no prompt encerra ao receber `FIM`; atualizações recebidas enquanto espera; `Ctrl+C` enviando `SAIR`) e **processos reais** de `servidor.py` e `cliente.py` com entrada pelo `stdin`: partida completa até a vitória com tabuleiros sorteados, `FIM` com o cliente esperando digitação, `SAIR` digitado, terceiro cliente recusado e cliente sem servidor (código de saída 1). Todo socket, processo e thread é encerrado ao fim de cada teste, e as esperas têm prazo de 5 s.

Para executar somente os testes do tabuleiro (sem sockets, servidor ou cliente):

```bash
python3 -m unittest discover -s tests -p 'test_tabuleiro.py' -v
```

`tests/test_servidor.py` sobe um servidor real numa porta livre de `127.0.0.1` e conecta clientes TCP de verdade. Cobre `AGUARDE`, `INICIO` para cada jogador, `PARTIDA_NAO_INICIADA`, saída e queda enquanto aguarda, mensagens inválidas e tipos desconhecidos, `SERVIDOR_CHEIO`, `FIM` por saída, queda ou mensagem incompleta, erros só para quem errou e nova partida depois do fim. Na fase 6, cobre também o turno completo da seção 10 (as duas `ATUALIZACAO` exatas), `FORA_DE_VEZ` antes de `COORDENADA_INVALIDA`, coordenadas inválidas e repetidas sem mudar a vez, partidas completas com vitória de cada jogador seguidas de `FIM VITORIA` e fechamento das conexões, ausência de `N` no tabuleiro do adversário e nova partida do zero depois de uma vitória.

`tests/test_tabuleiro.py` cobre coordenadas, posicionamentos explícitos válidos e inválidos, sorteios reproduzíveis, 100 sementes de sorteio, água, acerto, afundamento, disparos repetidos, preservação do estado após erros, independência entre tabuleiros, contagem de navios restantes e ocultação dos navios intactos. Reproduz também o exemplo da seção 6.

Para verificar somente as regras da fase 5, sem sockets:

```bash
python3 -m unittest discover -s tests -p 'test_partida.py' -v
```

`tests/test_partida.py` cobre jogador inicial, alternância de turnos, prioridade dos erros, tentativas inválidas sem mudança de estado, visões dos dois jogadores iguais às da seção 10, contagens relativas, cópias das visões e partidas independentes. Exercita partidas completas com vitória de cada jogador, impedimento de novos tiros após o fim e privacidade da frota do vencedor.

`tests/test_cliente.py` testa o `cliente.py` contra um servidor real em `127.0.0.1` e via `socket.socketpair()`. Cobre `AGUARDE`, `INICIO` e transição de turno, desenho dos tabuleiros, `SERVIDOR_CHEIO`, envio de `SAIR`, `ERRO` sem alterar estado, `ATUALIZACAO`, `FIM`, queda de conexão e argumentos de linha de comando.

(No Windows, se `python` abrir a Microsoft Store, use `py -3`.) Os testes de `tests/test_protocolo.py` usam `socket.socketpair()`: dois sockets reais ligados entre si, sem servidor nem cliente. Cobrem envio e recebimento de uma mensagem, duas mensagens juntas, mensagem em partes (inclusive byte a byte e cortada no meio de um caractere acentuado), JSON inválido e outras linhas inválidas, linha acima do limite, conexão encerrada, envio para conexão encerrada e mensagem interrompida antes da LF.

## 13. Guia de leitura por integrante

Todos: seções 2, 4, 5 e 11.

- **Tabuleiros e regras (fases 2 e 5):** seção 6 (representação, coordenadas e APIs de `tabuleiro.py` e `partida.py`) e 7.5 (`resultado` e `navios_restantes`). As duas fases estão implementadas: `Tabuleiro` posiciona navios, recebe tiros, conta os navios restantes e gera a visão oculta; `Partida` controla turnos, vitória e visão de cada jogador, **sem nunca incluir `N` no tabuleiro do adversário**. A integração com o servidor está feita (fase 6).
- **Servidor (fases 3 e 6):** seções 5, 7, 8 e 9.2, o exemplo da 10 e "Usando o servidor.py" na seção 6. Atenção à ordem de verificação do `DISPARAR`, a quem recebe cada mensagem (`ERRO` só para quem errou), à proteção do estado da partida contra acesso simultâneo e ao `Lock` por socket (seção 11).
- **Cliente (fases 4 e 7):** seções 6, 7, 8 e 9.3, e o exemplo da 10. Atenção a normalizar a coordenada (`strip().upper()`), a decidir a ação pelo `codigo` do `ERRO` e ao uso de `INICIO`/`ATUALIZACAO` para redesenhar tudo (os tabuleiros vêm completos a cada mensagem).

## 14. Decisões de projeto a confirmar

Pontos em que o enunciado deixava margem e o protocolo fixou uma escolha. Qualquer mudança deve ser combinada com todos e refletida neste documento.

1. A **letra é a coluna** (`A`–`E`) e o **dígito é a linha** (`1`–`5`).
2. Coordenadas só em **maiúsculas**; a normalização é do cliente.
3. O disparo que vence envia `ATUALIZACAO` **e depois** `FIM`; assim o cliente desenha o tabuleiro final pelo mesmo caminho de sempre.
4. `FIM` com saída ou desconexão tem `vencedor: null` (partida interrompida, sem vencedor).
5. `SAIR` não tem resposta ao remetente; o servidor apenas fecha a conexão.
6. Um terceiro cliente recebe `ERRO` `SERVIDOR_CHEIO` e é desconectado.
7. Após o `FIM`, o servidor fecha as duas conexões e volta a aceitar uma nova partida; "jogar de novo" exige reconectar os clientes.
8. Porta padrão sugerida: `5050`.
9. O servidor escuta em `0.0.0.0` (todas as interfaces) e o endereço de escuta não é configurável; só a porta é.
10. O servidor conduz **uma partida por vez**: com a partida em andamento, o terceiro cliente é recusado (`SERVIDOR_CHEIO`).
11. No cliente, `SAIR` só é lido na vez do jogador; fora da vez, a saída é `Ctrl+C` (que também envia `SAIR`).

## 15. Roteiro de demonstração (cerca de 3 minutos)

Preparação (antes de apresentar): três janelas de terminal lado a lado na raiz do projeto e o `readme.md` aberto na seção 7. Como os navios são sorteados, não se sabe de antemão onde estão; por isso o roteiro não depende de acertos.

| Tempo | O que fazer | O que mostrar e dizer |
|-------|-------------|-----------------------|
| 0:00 | Terminal 1: `py -3 servidor.py` | `Servidor ouvindo em 0.0.0.0:5050`. "Um servidor, uma conexão TCP por jogador; o servidor é a única fonte da verdade." |
| 0:20 | Terminal 2: `py -3 cliente.py` | O jogador 1 recebe `[AGUARDE]`; no log do servidor aparece `registrado; aguardando o segundo jogador`. |
| 0:35 | Terminal 3: `py -3 cliente.py` | Os dois recebem `INICIO` e o seu tabuleiro (com `N`); o adversário está todo em `~`. O jogador 1 mostra `Sua vez!`; o 2 mostra `Aguardando o adversário atirar...`. |
| 0:55 | Terminal 2 (jogador 1): digitar `z9` | `[ERRO] Coordenada inválida` e o prompt volta: **erro só para quem errou, a vez não muda**. |
| 1:10 | Terminal 2: digitar `b3` (minúsculo) | Resultado `AGUA`, `ACERTO` ou `AFUNDOU`. O jogador 1 vê `O`/`X` em B3 no tabuleiro do adversário; o jogador 2 vê o mesmo disparo no **seu** tabuleiro e **recebe a vez**. Explicar o caminho da jogada (seção 10): cliente → `DISPARAR` → servidor valida com `Partida` → uma `ATUALIZACAO` para cada jogador, cada uma com a sua visão. |
| 1:40 | Terminal 3 (jogador 2): digitar `b3`; terminal 2: digitar `c1`; terminal 3: digitar `b3` outra vez | Cada um só vê os próprios navios; nenhum `N` do adversário aparece. A segunda `b3` do jogador 2 gera `[ERRO] Você já atirou em B3` (`DISPARO_REPETIDO`) e ele continua com a vez. |
| 2:05 | Abrir um **quarto** terminal: `py -3 cliente.py` | `[ERRO] Já existe uma partida em andamento... (servidor cheio)` e o programa termina; a partida não é afetada. |
| 2:20 | No terminal do jogador que **não** está na vez: `Ctrl+C` | Esse cliente envia `SAIR`; o outro, **mesmo parado no prompt**, imprime `[FIM] O adversário saiu da partida.` e encerra sozinho. O log do servidor mostra `Partida encerrada (ADVERSARIO_SAIU)`. |
| 2:40 | Abrir dois clientes de novo | O servidor aceitou outra dupla: partida nova, tabuleiros novos. |
| 2:50 | Terminal 4: `py -3 -m unittest discover -s tests -v` | `Ran 157 tests ... OK`: protocolo, regras, servidor, cliente e integração com o servidor real. |

Se sobrar tempo, jogue até a vitória: o último disparo gera uma `ATUALIZACAO` e depois o `FIM` (`Você venceu!` / `O jogador N venceu.`), e o servidor fecha as conexões e volta a aceitar outra partida.
