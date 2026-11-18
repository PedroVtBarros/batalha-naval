"""Cliente com interface gráfica (tkinter) da Batalha Naval.

Uso:
    python cliente_gui.py [endereco_do_servidor] [porta]   # padrão: 127.0.0.1 5050

É uma alternativa ao cliente.py (terminal): usa o mesmo protocolo.py e conversa
com o mesmo servidor.py, então um jogador pode usar a interface gráfica e o outro
o terminal. Só usa a biblioteca padrão (tkinter já vem com o Python no Windows).

Organização em três partes, da mais simples de testar à mais visual:

    ModeloCliente     estado do jogador (seção 9.3 do readme.md): processa cada
                      mensagem do servidor e diz o que mostrar. Sem Tk e sem socket.
    ConexaoServidor   socket TCP + leitor único + thread que lê as mensagens e as
                      coloca numa fila. Nunca toca na janela.
    InterfaceGrafica  a janela. Consulta a fila a cada 50 ms (root.after) e só
                      então atualiza os widgets: o tkinter não pode ser usado por
                      outras threads.

Assim a janela nunca congela esperando a rede, e o FIM chega e aparece mesmo
quando não é a vez do jogador.
"""

from __future__ import annotations

import queue
import socket
import sys
import threading
from typing import Any

from protocolo import (
    ConexaoEncerrada,
    MensagemInvalida,
    enviar_mensagem,
    receber_mensagem,
)

PORTA_PADRAO = 5050
ENDERECO_PADRAO = "127.0.0.1"
COLUNAS = "ABCDE"
TAMANHO = 5
TABULEIRO_VAZIO = ["~~~~~"] * TAMANHO

# Estados do cliente (seção 9.3).
CONECTADO = "CONECTADO"
MINHA_VEZ = "MINHA_VEZ"
VEZ_ADVERSARIO = "VEZ_ADVERSARIO"
ENCERRADO = "ENCERRADO"

TEXTO_RESULTADO = {"AGUA": "água", "ACERTO": "acertou um navio", "AFUNDOU": "afundou um navio!"}


# ---------------------------------------------------------------------------- modelo


class ModeloCliente:
    """Estado de um jogador, atualizado só pelas mensagens do servidor.

    O cliente não decide nada sobre o jogo: as verificações de pode_disparar
    servem apenas para dar resposta rápida ao clique; o servidor valida de novo.
    """

    def __init__(self) -> None:
        self.estado = CONECTADO
        self.jogador: int | None = None
        self.tabuleiro_proprio = list(TABULEIRO_VAZIO)
        self.tabuleiro_adversario = list(TABULEIRO_VAZIO)
        self.navios_restantes = {"proprio": 2, "adversario": 2}
        self.aguardando_resposta = False  # DISPARAR enviado, resposta ainda não chegou
        self.ultimo_disparo: tuple[int, str] | None = None  # (atirador, coordenada)
        self.desfecho: str | None = None  # "vitoria", "derrota" ou "interrompida"
        self.recebeu_aguarde = False
        self.mensagem_final = ""

    @property
    def iniciado(self) -> bool:
        return self.jogador is not None

    def processar(self, mensagem: dict[str, Any]) -> str:
        """Aplica uma mensagem do servidor e devolve uma linha para o registro."""
        tipo = mensagem.get("tipo")
        tratador = {
            "AGUARDE": self._aguarde,
            "INICIO": self._inicio,
            "ATUALIZACAO": self._atualizacao,
            "ERRO": self._erro,
            "FIM": self._fim,
        }.get(tipo)
        if tratador is None:
            return f"Mensagem desconhecida do servidor: {tipo!r}"
        return tratador(mensagem)

    def _aguarde(self, mensagem: dict[str, Any]) -> str:
        self.recebeu_aguarde = True
        return mensagem.get("mensagem", "Aguardando o segundo jogador...")

    def _inicio(self, mensagem: dict[str, Any]) -> str:
        self.jogador = mensagem["jogador"]
        self.tabuleiro_proprio = list(mensagem["tabuleiro_proprio"])
        self.tabuleiro_adversario = list(mensagem["tabuleiro_adversario"])
        self.estado = MINHA_VEZ if mensagem["sua_vez"] else VEZ_ADVERSARIO
        return f"A partida começou! Você é o jogador {self.jogador}."

    def _atualizacao(self, mensagem: dict[str, Any]) -> str:
        self.tabuleiro_proprio = list(mensagem["tabuleiro_proprio"])
        self.tabuleiro_adversario = list(mensagem["tabuleiro_adversario"])
        self.navios_restantes = dict(mensagem["navios_restantes"])
        self.aguardando_resposta = False
        atirador, coordenada = mensagem["atirador"], mensagem["coordenada"]
        self.ultimo_disparo = (atirador, coordenada)
        self.estado = MINHA_VEZ if mensagem["sua_vez"] else VEZ_ADVERSARIO
        quem = "Você atirou" if atirador == self.jogador else "O adversário atirou"
        resultado = TEXTO_RESULTADO.get(mensagem["resultado"], mensagem["resultado"])
        return f"{quem} em {coordenada}: {resultado}"

    def _erro(self, mensagem: dict[str, Any]) -> str:
        self.aguardando_resposta = False
        codigo = mensagem.get("codigo", "")
        texto = mensagem.get("mensagem", "")
        if codigo == "SERVIDOR_CHEIO":
            self.encerrar("interrompida", texto)
        return f"Erro: {texto}"

    def _fim(self, mensagem: dict[str, Any]) -> str:
        texto = mensagem.get("mensagem", "")
        if mensagem.get("motivo") == "VITORIA":
            venceu = mensagem.get("vencedor") == self.jogador
            self.encerrar("vitoria" if venceu else "derrota", texto)
            return ("Você venceu! " if venceu else "Você perdeu. ") + texto
        self.encerrar("interrompida", texto)
        return texto

    def conexao_perdida(self) -> str | None:
        """A conexão caiu. Devolve o aviso, ou None se a partida já tinha terminado."""
        if self.estado == ENCERRADO:
            return None
        self.encerrar("interrompida", "A conexão com o servidor foi perdida.")
        return self.mensagem_final

    def encerrar(self, desfecho: str, texto: str) -> None:
        self.estado = ENCERRADO
        self.aguardando_resposta = False
        self.desfecho = desfecho
        self.mensagem_final = texto

    def pode_disparar(self, coordenada: str) -> tuple[bool, str]:
        """Diz se um clique em `coordenada` deve virar DISPARAR (e o motivo se não)."""
        if self.estado == ENCERRADO:
            return False, "A partida terminou."
        if self.estado != MINHA_VEZ:
            return False, "Espere a sua vez."
        if self.aguardando_resposta:
            return False, "Aguarde o resultado do disparo anterior."
        if len(coordenada) != 2 or coordenada[0] not in COLUNAS or coordenada[1] not in "12345":
            return False, "Coordenada inválida. Use de A1 a E5."
        linha, coluna = int(coordenada[1]) - 1, COLUNAS.index(coordenada[0])
        if self.tabuleiro_adversario[linha][coluna] != "~":
            return False, f"Você já atirou em {coordenada}."
        return True, ""

    def status(self) -> str:
        """Frase curta para o topo da janela."""
        if self.estado == ENCERRADO:
            return {
                "vitoria": "Você venceu!",
                "derrota": "Você perdeu.",
            }.get(self.desfecho or "", "Partida encerrada.")
        if not self.iniciado:
            return "Aguardando o segundo jogador..." if self.recebeu_aguarde else "Conectado."
        if self.estado == MINHA_VEZ:
            return "Aguardando resultado..." if self.aguardando_resposta else "Sua vez! Clique no tabuleiro do adversário."
        return "Vez do adversário..."


# ---------------------------------------------------------------------------- rede


class ConexaoServidor:
    """Conexão TCP com o servidor. A thread de leitura só coloca eventos na fila.

    Eventos colocados em `eventos`, como tuplas (conexao, tipo, dado):
        (self, "mensagem", dict)   mensagem recebida do servidor
        (self, "invalida", texto)  linha inválida vinda do servidor (falha de protocolo)
        (self, "encerrada", None)  conexão encerrada ou caída
    """

    def __init__(self, sock: socket.socket, eventos: queue.Queue) -> None:
        self._sock = sock
        self._leitor = sock.makefile("rb")  # UMA vez por conexão (seção 5)
        self._eventos = eventos
        self._lock_envio = threading.Lock()
        self._ativa = True

    @classmethod
    def conectar(cls, endereco: str, porta: int, eventos: queue.Queue, prazo: float = 10) -> "ConexaoServidor":
        """Conecta (com prazo para não esperar para sempre). Levanta OSError se falhar.

        A leitura só começa com iniciar_leitura(): quem conecta pode primeiro
        avisar "conectado" na fila e só então liberar as mensagens do servidor,
        garantindo que o AGUARDE nunca chegue antes do aviso de conexão.
        """
        sock = socket.create_connection((endereco, porta), timeout=prazo)
        sock.settimeout(None)  # sockets com leitor makefile devem ser bloqueantes (seção 11)
        return cls(sock, eventos)

    def iniciar_leitura(self) -> None:
        threading.Thread(target=self._ler, daemon=True, name="leitura-servidor").start()

    def _ler(self) -> None:
        try:
            while True:
                self._eventos.put((self, "mensagem", receber_mensagem(self._leitor)))
        except MensagemInvalida as erro:
            self._eventos.put((self, "invalida", str(erro)))
        except (ConexaoEncerrada, OSError, ValueError):
            self._eventos.put((self, "encerrada", None))
        finally:
            # Só esta thread fecha o leitor: fechá-lo enquanto ela lê travaria.
            self.fechar()
            for recurso in (self._leitor, self._sock):
                try:
                    recurso.close()
                except OSError:
                    pass

    def enviar(self, mensagem: dict[str, Any]) -> bool:
        """Envia uma mensagem; devolve False se a conexão já não existe."""
        with self._lock_envio:
            if not self._ativa:
                return False
            try:
                enviar_mensagem(self._sock, mensagem)
                return True
            except (ConexaoEncerrada, OSError):
                return False

    def disparar(self, coordenada: str) -> bool:
        return self.enviar({"tipo": "DISPARAR", "coordenada": coordenada})

    def sair(self) -> None:
        """Envia SAIR (sem resposta, seção 7.2) e encerra a conexão."""
        self.enviar({"tipo": "SAIR"})
        self.fechar()

    def fechar(self) -> None:
        """Encerra a conexão; a thread de leitura recebe o fim do fluxo e termina."""
        with self._lock_envio:
            if not self._ativa:
                return
            self._ativa = False
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


# ---------------------------------------------------------------------------- janela

CORES = {
    "fundo": "#0b2545",
    "painel": "#13315c",
    "texto": "#eef4ed",
    "suave": "#8da9c4",
    "agua": "#1b4f72",
    "desconhecido": "#134074",
    "navio": "#8d99ae",
    "tiro_agua": "#5dade2",
    "acerto": "#e63946",
    "mira": "#2e86c1",
    "ultimo": "#f4d35e",
    "vitoria": "#2a9d8f",
    "derrota": "#e63946",
    "neutro": "#f4a261",
}

SIMBOLO = {"~": "", "N": "", "O": "•", "X": "✕"}


class InterfaceGrafica:
    """Janela do jogo. Toda alteração de widget acontece na thread do tkinter."""

    INTERVALO_MS = 50

    def __init__(self, raiz, endereco: str = ENDERECO_PADRAO, porta: int = PORTA_PADRAO) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.raiz = raiz
        self.eventos: queue.Queue = queue.Queue()
        self.conexao: ConexaoServidor | None = None
        self.modelo = ModeloCliente()
        self._tentativa = 0  # identifica a tentativa de conexão atual
        self._encerrando = False
        self._id_consulta = None

        raiz.title("Batalha Naval")
        raiz.configure(bg=CORES["fundo"], padx=16, pady=12)
        raiz.resizable(False, False)
        raiz.protocol("WM_DELETE_WINDOW", self.fechar_janela)
        raiz.bind("<Destroy>", self._ao_destruir, add="+")

        fonte = ("Segoe UI", 10)
        estilo = {"bg": CORES["fundo"], "fg": CORES["texto"], "font": fonte}

        # --- linha de conexão
        topo = tk.Frame(raiz, bg=CORES["fundo"])
        topo.pack(fill="x")
        tk.Label(topo, text="Servidor", **estilo).pack(side="left")
        self.campo_endereco = tk.Entry(topo, width=34, font=fonte)
        self.campo_endereco.insert(0, endereco)
        self.campo_endereco.pack(side="left", padx=(6, 12))
        tk.Label(topo, text="Porta", **estilo).pack(side="left")
        self.campo_porta = tk.Entry(topo, width=7, font=fonte)
        self.campo_porta.insert(0, str(porta))
        self.campo_porta.pack(side="left", padx=(6, 12))
        self.botao_conectar = ttk.Button(topo, text="Conectar", command=self.conectar)
        self.botao_conectar.pack(side="left")
        self.campo_endereco.bind("<Return>", lambda _e: self.conectar())
        self.campo_porta.bind("<Return>", lambda _e: self.conectar())

        # --- faixa de status
        self.rotulo_status = tk.Label(
            raiz, text="Informe o servidor e clique em Conectar.", bg=CORES["painel"],
            fg=CORES["texto"], font=("Segoe UI", 14, "bold"), pady=10,
        )
        self.rotulo_status.pack(fill="x", pady=(12, 10))

        # --- tabuleiros
        area = tk.Frame(raiz, bg=CORES["fundo"])
        area.pack()
        self.celulas_proprio = self._criar_grade(area, "Seu tabuleiro", 0, clicavel=False)
        self.celulas_adversario = self._criar_grade(area, "Tabuleiro do adversário", 1, clicavel=True)
        self.rotulo_navios = tk.Label(raiz, text="", **estilo)
        self.rotulo_navios.pack(pady=(6, 0))

        # --- entrada por teclado (alternativa ao clique)
        linha = tk.Frame(raiz, bg=CORES["fundo"])
        linha.pack(pady=(8, 0))
        tk.Label(linha, text="Coordenada", **estilo).pack(side="left")
        self.campo_coordenada = tk.Entry(linha, width=6, font=("Consolas", 12), justify="center")
        self.campo_coordenada.pack(side="left", padx=6)
        self.campo_coordenada.bind("<Return>", lambda _e: self.disparar_digitado())
        self.botao_disparar = ttk.Button(linha, text="Disparar", command=self.disparar_digitado)
        self.botao_disparar.pack(side="left")

        # --- registro
        self.registro = tk.Text(
            raiz, height=7, width=72, state="disabled", bg=CORES["painel"], fg=CORES["texto"],
            font=("Consolas", 9), relief="flat", padx=8, pady=6,
        )
        self.registro.pack(pady=(10, 8))

        # --- botões finais
        rodape = tk.Frame(raiz, bg=CORES["fundo"])
        rodape.pack(fill="x")
        self.botao_sair = ttk.Button(rodape, text="Sair da partida", command=self.sair_da_partida)
        self.botao_sair.pack(side="right")
        self.botao_nova = ttk.Button(rodape, text="Nova partida", command=self.nova_partida)
        self.botao_nova.pack(side="right", padx=8)
        tk.Label(
            rodape, text="• tiro na água    ✕ navio atingido    cinza: seu navio",
            bg=CORES["fundo"], fg=CORES["suave"], font=("Segoe UI", 9),
        ).pack(side="left")

        self.redesenhar()
        self._id_consulta = self.raiz.after(self.INTERVALO_MS, self._consultar_eventos)

    def _criar_grade(self, pai, titulo: str, posicao: int, clicavel: bool):
        tk = self.tk
        quadro = tk.Frame(pai, bg=CORES["fundo"], padx=12)
        quadro.grid(row=0, column=posicao)
        tk.Label(quadro, text=titulo, bg=CORES["fundo"], fg=CORES["texto"],
                 font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=TAMANHO + 1, pady=(0, 4))
        for coluna, letra in enumerate(COLUNAS):
            tk.Label(quadro, text=letra, bg=CORES["fundo"], fg=CORES["suave"],
                     font=("Segoe UI", 10, "bold")).grid(row=1, column=coluna + 1)
        celulas = {}
        for linha in range(TAMANHO):
            tk.Label(quadro, text=str(linha + 1), bg=CORES["fundo"], fg=CORES["suave"],
                     font=("Segoe UI", 10, "bold"), width=2).grid(row=linha + 2, column=0)
            for coluna, letra in enumerate(COLUNAS):
                coordenada = f"{letra}{linha + 1}"
                celula = tk.Label(quadro, width=4, height=2, font=("Segoe UI", 13, "bold"),
                                  relief="flat", bd=0, highlightthickness=2,
                                  highlightbackground=CORES["fundo"])
                celula.grid(row=linha + 2, column=coluna + 1, padx=2, pady=2)
                if clicavel:
                    celula.bind("<Button-1>", lambda _e, c=coordenada: self.clicar(c))
                    celula.bind("<Enter>", lambda _e, c=coordenada: self._mirar(c, True))
                    celula.bind("<Leave>", lambda _e, c=coordenada: self._mirar(c, False))
                celulas[coordenada] = celula
        return celulas

    # ------------------------------------------------------------ ações do usuário

    def conectar(self) -> None:
        if self.conexao is not None:
            return
        endereco = self.campo_endereco.get().strip()
        try:
            porta = int(self.campo_porta.get().strip())
            if not 0 < porta < 65536:
                raise ValueError
        except ValueError:
            self.anotar("A porta deve ser um número entre 1 e 65535.")
            return
        if not endereco:
            self.anotar("Informe o endereço do servidor.")
            return

        self._tentativa += 1
        tentativa = self._tentativa
        self.modelo = ModeloCliente()
        self.botao_conectar.state(["disabled"])
        self._definir_status(f"Conectando a {endereco}:{porta}...", CORES["painel"])
        self.anotar(f"Conectando a {endereco}:{porta}...")

        def trabalho():  # thread própria: resolver nome e conectar pode demorar
            try:
                conexao = ConexaoServidor.conectar(endereco, porta, self.eventos)
            except OSError as erro:
                self.eventos.put((tentativa, "falha", str(erro)))
                return
            # Primeiro o aviso de conexão, depois as mensagens: a fila preserva a ordem.
            self.eventos.put((tentativa, "conectado", conexao))
            conexao.iniciar_leitura()

        threading.Thread(target=trabalho, daemon=True, name="conexao").start()

    def clicar(self, coordenada: str) -> None:
        if self.conexao is None:
            return
        pode, motivo = self.modelo.pode_disparar(coordenada)
        if not pode:
            if self.modelo.estado != ENCERRADO:
                self.anotar(motivo)
            return
        if self.conexao.disparar(coordenada):
            self.modelo.aguardando_resposta = True
            self.anotar(f"Disparando em {coordenada}...")
            self.redesenhar()

    def disparar_digitado(self) -> None:
        coordenada = self.campo_coordenada.get().strip().upper()  # normalização (seção 6)
        self.campo_coordenada.delete(0, "end")
        if coordenada:
            self.clicar(coordenada)

    def sair_da_partida(self) -> None:
        if self.conexao is not None and self.modelo.estado != ENCERRADO:
            self.conexao.sair()
            self.modelo.encerrar("interrompida", "Você saiu da partida.")
            self.anotar("Você saiu da partida.")
            self.conexao = None
            self.redesenhar()

    def nova_partida(self) -> None:
        """O servidor fecha a conexão após o FIM: jogar de novo exige reconectar."""
        if self.conexao is not None and self.modelo.estado != ENCERRADO:
            return  # ainda há uma partida em andamento
        if self.conexao is not None:
            self.conexao.fechar()
            self.conexao = None
        self.modelo = ModeloCliente()
        self.conectar()

    def fechar_janela(self) -> None:
        self._encerrando = True
        if self.conexao is not None:
            if self.modelo.estado != ENCERRADO:
                self.conexao.sair()  # avisa o servidor; o adversário recebe FIM
            else:
                self.conexao.fechar()
        self.raiz.destroy()

    # ------------------------------------------------------------ eventos da rede

    def _consultar_eventos(self) -> None:
        if self._encerrando:
            return
        self.processar_eventos()
        self._id_consulta = self.raiz.after(self.INTERVALO_MS, self._consultar_eventos)

    def _ao_destruir(self, evento) -> None:
        # Cancela a consulta agendada para não chamar um widget que já não existe.
        if evento.widget is self.raiz and self._id_consulta is not None:
            self._encerrando = True
            try:
                self.raiz.after_cancel(self._id_consulta)
            except Exception:
                pass
            self._id_consulta = None

    def processar_eventos(self) -> None:
        """Trata tudo o que as threads deixaram na fila (chamado só na thread do Tk)."""
        while True:
            try:
                origem, tipo, dado = self.eventos.get_nowait()
            except queue.Empty:
                break
            if tipo in ("conectado", "falha"):
                self._resultado_conexao(origem, tipo, dado)
            elif origem is self.conexao:  # ignora eventos de conexões antigas
                self._evento_da_conexao(tipo, dado)

    def _resultado_conexao(self, tentativa: int, tipo: str, dado) -> None:
        if tentativa != self._tentativa:  # tentativa antiga, já substituída
            if tipo == "conectado":
                dado.sair()
            return
        if tipo == "falha":
            self.botao_conectar.state(["!disabled"])
            self._definir_status("Não foi possível conectar.", CORES["derrota"])
            self.anotar(f"Falha ao conectar: {dado}")
            return
        self.conexao = dado
        self.anotar("Conectado.")
        self.redesenhar()

    def _evento_da_conexao(self, tipo: str, dado) -> None:
        if tipo == "mensagem":
            self.anotar(self.modelo.processar(dado))
        elif tipo == "invalida":
            self.anotar(f"Mensagem inválida do servidor: {dado}")
            self.modelo.conexao_perdida()
            self.conexao.fechar()
        elif tipo == "encerrada":
            aviso = self.modelo.conexao_perdida()
            if aviso:
                self.anotar(aviso)
        if self.modelo.estado == ENCERRADO:
            self.conexao.fechar()
            self.conexao = None
        self.redesenhar()

    # ------------------------------------------------------------ desenho

    def redesenhar(self) -> None:
        modelo = self.modelo
        conectado = self.conexao is not None
        ultimo = modelo.ultimo_disparo
        for coordenada, celula in self.celulas_proprio.items():
            simbolo = self._casa(modelo.tabuleiro_proprio, coordenada)
            cor = {"~": CORES["agua"], "N": CORES["navio"], "O": CORES["tiro_agua"], "X": CORES["acerto"]}[simbolo]
            destaque = ultimo is not None and ultimo[0] != modelo.jogador and ultimo[1] == coordenada
            self._pintar(celula, cor, SIMBOLO[simbolo], destaque)
        minha_vez = modelo.estado == MINHA_VEZ and not modelo.aguardando_resposta and conectado
        for coordenada, celula in self.celulas_adversario.items():
            simbolo = self._casa(modelo.tabuleiro_adversario, coordenada)
            cor = {"~": CORES["desconhecido"], "O": CORES["tiro_agua"], "X": CORES["acerto"]}.get(simbolo, CORES["desconhecido"])
            destaque = ultimo is not None and ultimo[0] == modelo.jogador and ultimo[1] == coordenada
            self._pintar(celula, cor, SIMBOLO.get(simbolo, ""), destaque)
            celula.configure(cursor="hand2" if minha_vez and simbolo == "~" else "arrow")

        if modelo.estado == ENCERRADO:
            cor = {"vitoria": CORES["vitoria"], "derrota": CORES["derrota"]}.get(modelo.desfecho or "", CORES["neutro"])
            self._definir_status(modelo.status(), cor)
        elif conectado:
            self._definir_status(modelo.status(), CORES["mira"] if minha_vez else CORES["painel"])

        if modelo.iniciado:
            restantes = modelo.navios_restantes
            self.rotulo_navios.configure(
                text=f"Jogador {modelo.jogador}   ·   seus navios: {restantes['proprio']}   ·   "
                     f"navios do adversário: {restantes['adversario']}"
            )
        else:
            self.rotulo_navios.configure(text="")

        self.botao_conectar.state(["disabled"] if conectado or modelo.estado == ENCERRADO else ["!disabled"])
        self.botao_sair.state(["!disabled"] if conectado and modelo.estado != ENCERRADO else ["disabled"])
        self.botao_nova.state(["!disabled"] if modelo.estado == ENCERRADO else ["disabled"])
        self.botao_disparar.state(["!disabled"] if minha_vez else ["disabled"])

    def _mirar(self, coordenada: str, entrando: bool) -> None:
        celula = self.celulas_adversario[coordenada]
        if entrando and str(celula.cget("cursor")) == "hand2":
            celula.configure(bg=CORES["mira"])
        elif not entrando:
            self.redesenhar()

    @staticmethod
    def _casa(tabuleiro: list[str], coordenada: str) -> str:
        return tabuleiro[int(coordenada[1]) - 1][COLUNAS.index(coordenada[0])]

    @staticmethod
    def _pintar(celula, cor: str, texto: str, destaque: bool) -> None:
        celula.configure(bg=cor, text=texto, fg=CORES["texto"],
                         highlightbackground=CORES["ultimo"] if destaque else CORES["fundo"])

    def _definir_status(self, texto: str, cor: str) -> None:
        self.rotulo_status.configure(text=texto, bg=cor)

    def anotar(self, texto: str) -> None:
        self.registro.configure(state="normal")
        self.registro.insert("end", texto + "\n")
        self.registro.see("end")
        self.registro.configure(state="disabled")


def ativar_nitidez_windows() -> None:
    """No Windows com escala de tela (125%, 150%...), evita que a janela fique borrada."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass  # Windows antigo: segue com a escala padrão


def main(argumentos: list[str]) -> int:
    if len(argumentos) > 2:
        print("Uso: python cliente_gui.py [endereco_do_servidor] [porta]", file=sys.stderr)
        return 2
    endereco = argumentos[0] if argumentos else ENDERECO_PADRAO
    try:
        porta = int(argumentos[1]) if len(argumentos) == 2 else PORTA_PADRAO
    except ValueError:
        print("A porta deve ser um número entre 1 e 65535.", file=sys.stderr)
        return 2

    import tkinter as tk

    ativar_nitidez_windows()
    raiz = tk.Tk()
    InterfaceGrafica(raiz, endereco, porta)
    raiz.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
