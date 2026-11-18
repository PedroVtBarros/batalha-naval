"""Cliente gráfico (Tkinter) da Batalha Naval.

Uso:
    python cliente_gui.py [endereco_do_servidor] [porta]   # padrão: 127.0.0.1 5050

O endereço pode ser IP ou domínio (inclusive o de um túnel TCP, como o Pinggy). Os
argumentos só preenchem os campos da tela de conexão.

Organização:
    nucleo_gui.py   lógica sem Tkinter: modelo da partida, sessão de rede, validação
    cliente_gui.py  somente a janela (este arquivo)

Threads: a conexão e a leitura da rede rodam em threads de `Sessao`, que só colocam
eventos numa fila. A janela consome a fila com `after()` na thread principal do
Tkinter, a única que mexe em widgets. Cada conexão tem um número de "geração"; eventos
de uma geração antiga (por exemplo, depois de sair e reconectar) são descartados.
O servidor continua decidindo tudo: a janela só mostra o que ele informa.
"""

from __future__ import annotations

import queue
import sys

try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk
except ImportError:  # Python sem Tkinter (ex.: Linux sem o pacote python3-tk)
    tk = None  # type: ignore[assignment]

from nucleo_gui import (
    AFUNDOU,
    AGUA,
    AGUARDANDO,
    ACERTO,
    CONECTADO,
    DESCONHECIDA,
    ENDERECO_PADRAO,
    ENCERRADA,
    EM_ANDAMENTO,
    LETRAS,
    NAVIO,
    NAVIOS_INICIAIS,
    PORTA_PADRAO,
    SEM_DISPARO,
    SIMBOLOS,
    TAMANHO,
    ModeloPartida,
    Sessao,
    interpretar_destino,
    nome_da_casa,
)

INTERVALO_FILA_MS = 50

# ---------------------------------------------------------------------------
# Aparência: fundo claro, azul como cor principal
# ---------------------------------------------------------------------------

FUNDO = "#f3f7fb"
CARTAO = "#ffffff"
AZUL = "#1565c0"
AZUL_ESCURO = "#0d47a1"
AZUL_CLARO = "#e3f0fc"
TEXTO = "#1b2a3a"
CINZA = "#5f7388"
BORDA = "#9db7d1"
VERMELHO = "#c62828"
VERDE_ESCURO = "#1b5e20"

# tipo de casa -> (fundo, texto, fundo ao passar o mouse, fundo desabilitada, texto desabilitada)
CORES_CASA = {
    DESCONHECIDA: ("#cfe3f7", "#3f6d9c", "#a9cdf0", "#e1ecf7", "#9bb5cf"),
    SEM_DISPARO: ("#eef5fc", "#8fb0cf", "#eef5fc", "#eef5fc", "#8fb0cf"),
    NAVIO: ("#546e8a", "#ffffff", "#546e8a", "#546e8a", "#ffffff"),
    AGUA: ("#ffffff", AZUL, "#ffffff", "#ffffff", AZUL),
    ACERTO: ("#e8613c", "#ffffff", "#e8613c", "#e8613c", "#ffffff"),
    AFUNDOU: ("#8e1b1b", "#ffffff", "#8e1b1b", "#8e1b1b", "#ffffff"),
}

# nível do painel de status -> (fundo, texto)
CORES_STATUS = {
    "espera": (AZUL_CLARO, AZUL_ESCURO),
    "minha": ("#dff3e2", VERDE_ESCURO),
    "adversario": ("#fff1d6", "#8a5200"),
    "vitoria": ("#c8e6c9", VERDE_ESCURO),
    "derrota": ("#ffd9d6", "#8e1b1b"),
    "interrompida": ("#ffe8c2", "#8a5200"),
    "erro": ("#ffd9d6", "#8e1b1b"),
}

LEGENDA = (
    (DESCONHECIDA, "Casa ainda não atacada"),
    (NAVIO, "Meu navio"),
    (AGUA, "Tiro na água"),
    (SEM_DISPARO, "Água sem disparo (meu tabuleiro)"),
    (ACERTO, "Acerto em navio"),
    (AFUNDOU, "Golpe que afundou um navio"),
)


class App:
    """Janela do jogo: tela de conexão e tela da partida."""

    def __init__(self, root: "tk.Tk", endereco: str = ENDERECO_PADRAO, porta: int = PORTA_PADRAO) -> None:
        self.root = root
        self.modelo = ModeloPartida()
        self._eventos: "queue.Queue" = queue.Queue()
        self._geracao = 0            # muda a cada nova conexão ou ao abandonar uma
        self._sessao: Sessao | None = None
        self._fechando = False
        self._partida_aberta = False  # a tela da partida está à frente?
        self._after_id: str | None = None

        root.title("Batalha Naval")
        root.configure(background=FUNDO)
        self._escala = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self._lado = int((48 if root.winfo_screenheight() < 900 else 56) * self._escala)
        self._criar_estilos()
        self._criar_widgets(endereco, str(porta))
        self._mostrar_conexao()
        self._renderizar()

        root.update_idletasks()
        largura, altura = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry(f"{largura}x{altura}")  # tamanho fixo: textos novos não mexem na janela
        root.minsize(largura, altura)
        root.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self._agendar()

    # ------------------------------------------------------------------ estilos

    def _criar_estilos(self) -> None:
        estilo = ttk.Style(self.root)
        if "clam" in estilo.theme_names():
            estilo.theme_use("clam")  # permite cores de fundo em botões nativos ttk
        base = tkfont.nametofont("TkDefaultFont").actual("family")
        self.fonte = tkfont.Font(family=base, size=10)
        self.fonte_negrito = tkfont.Font(family=base, size=10, weight="bold")
        self.fonte_pequena = tkfont.Font(family=base, size=9)
        self.fonte_titulo = tkfont.Font(family=base, size=20, weight="bold")
        self.fonte_secao = tkfont.Font(family=base, size=13, weight="bold")
        self.fonte_status = tkfont.Font(family=base, size=12, weight="bold")
        self.fonte_casa = tkfont.Font(family=base, size=14, weight="bold")

        estilo.configure(".", font=self.fonte, background=FUNDO, foreground=TEXTO)
        estilo.configure("TFrame", background=FUNDO)
        estilo.configure("Cartao.TFrame", background=CARTAO)
        estilo.configure("TLabel", background=FUNDO, foreground=TEXTO)
        estilo.configure("Cartao.TLabel", background=CARTAO)
        estilo.configure("Faixa.TFrame", background=AZUL)
        estilo.configure("Faixa.TLabel", background=AZUL, foreground="#ffffff", font=self.fonte_titulo)
        estilo.configure("FaixaSub.TLabel", background=AZUL, foreground="#cfe3f7", font=self.fonte)
        estilo.configure("Secao.TLabel", background=CARTAO, foreground=AZUL_ESCURO, font=self.fonte_secao)
        estilo.configure("Sub.TLabel", background=CARTAO, foreground=CINZA, font=self.fonte_pequena)
        estilo.configure("Eixo.TLabel", background=CARTAO, foreground=AZUL_ESCURO, font=self.fonte_negrito, anchor="center")
        estilo.configure("Info.TLabel", font=self.fonte_status, foreground=AZUL_ESCURO)
        estilo.configure("Contagem.TLabel", background=CARTAO, foreground=TEXTO, font=self.fonte_negrito)
        estilo.configure("Mensagem.TLabel", background=CARTAO, font=self.fonte_negrito)
        estilo.configure("Aviso.TLabel", background=CARTAO, foreground=CINZA)
        estilo.configure("AvisoErro.TLabel", background=CARTAO, foreground=VERMELHO, font=self.fonte_negrito)
        estilo.configure("Erro.TLabel", background=CARTAO, foreground=VERMELHO)
        estilo.configure("Status.TLabel", background=CARTAO, foreground=AZUL_ESCURO)
        estilo.configure("Legenda.TLabel", background=CARTAO, font=self.fonte_pequena)
        estilo.configure("TLabelframe", background=CARTAO, bordercolor=BORDA)
        estilo.configure("TLabelframe.Label", background=FUNDO, foreground=AZUL_ESCURO, font=self.fonte_negrito)
        estilo.configure("TEntry", fieldbackground="#ffffff", bordercolor=BORDA, padding=5)
        estilo.map("TEntry", bordercolor=[("focus", AZUL)], fieldbackground=[("disabled", "#e8eef4")],
                   foreground=[("disabled", "#8a99a8")])
        estilo.configure("TProgressbar", background=AZUL, troughcolor=AZUL_CLARO, bordercolor=AZUL_CLARO)

        # Botões: azul = ação principal; cinza-azulado = secundária; esmaecido = desabilitado.
        estilo.configure("Primario.TButton", background=AZUL, foreground="#ffffff", bordercolor=AZUL_ESCURO,
                         lightcolor=AZUL, darkcolor=AZUL, padding=(18, 7), font=self.fonte_negrito,
                         focuscolor="#ffffff")
        estilo.map("Primario.TButton",
                   background=[("disabled", "#a9c4e4"), ("pressed", AZUL_ESCURO), ("active", "#1976d2")],
                   foreground=[("disabled", "#eef4fb")],
                   bordercolor=[("disabled", "#a9c4e4")])
        estilo.configure("Secundario.TButton", background="#e3ecf6", foreground=AZUL_ESCURO, bordercolor=BORDA,
                         lightcolor="#e3ecf6", darkcolor="#e3ecf6", padding=(16, 7), font=self.fonte_negrito,
                         focuscolor=AZUL)
        estilo.map("Secundario.TButton",
                   background=[("disabled", "#f0f3f6"), ("pressed", "#c9dbee"), ("active", "#d3e3f4")],
                   foreground=[("disabled", "#a3b0bd")],
                   bordercolor=[("disabled", "#d5dde5")])

        for tipo, (fundo, texto, ativo, fundo_des, texto_des) in CORES_CASA.items():
            estilo.configure(f"{tipo}.Celula.TLabel", background=fundo, foreground=texto, font=self.fonte_casa,
                             anchor="center")
            estilo.configure(f"{tipo}.Celula.TButton", background=fundo, foreground=texto, font=self.fonte_casa,
                             bordercolor=fundo, lightcolor=fundo, darkcolor=fundo, padding=0, anchor="center",
                             focuscolor=AZUL, relief="flat")
            estilo.map(f"{tipo}.Celula.TButton",
                       background=[("disabled", fundo_des), ("pressed", AZUL), ("active", ativo)],
                       foreground=[("disabled", texto_des), ("pressed", "#ffffff")],
                       bordercolor=[("disabled", fundo_des), ("active", AZUL)],
                       lightcolor=[("disabled", fundo_des)], darkcolor=[("disabled", fundo_des)])
        for nivel, (fundo, texto) in CORES_STATUS.items():
            estilo.configure(f"{nivel}.Status.TLabel", background=fundo, foreground=texto, font=self.fonte_status,
                             padding=(14, 8), anchor="center")

    # ------------------------------------------------------------------ construção

    def _criar_widgets(self, endereco: str, porta: str) -> None:
        raiz = self.root
        raiz.columnconfigure(0, weight=1)
        raiz.rowconfigure(1, weight=1)

        faixa = ttk.Frame(raiz, style="Faixa.TFrame", padding=(22, 10))
        faixa.grid(row=0, column=0, sticky="ew")
        ttk.Label(faixa, text="Batalha Naval", style="Faixa.TLabel").pack(side="left")
        ttk.Label(faixa, text="Jogo em rede para dois jogadores", style="FaixaSub.TLabel").pack(
            side="left", padx=(16, 0), pady=(8, 0))

        corpo = ttk.Frame(raiz)
        corpo.grid(row=1, column=0, sticky="nsew")
        corpo.columnconfigure(0, weight=1)
        corpo.rowconfigure(0, weight=1)
        self._tela_conexao = ttk.Frame(corpo)
        self._tela_partida = ttk.Frame(corpo, padding=(18, 12))
        for tela in (self._tela_conexao, self._tela_partida):
            tela.grid(row=0, column=0, sticky="nsew")
        self._criar_tela_conexao(endereco, porta)
        self._criar_tela_partida()

    def _criar_tela_conexao(self, endereco: str, porta: str) -> None:
        cartao = ttk.Frame(self._tela_conexao, style="Cartao.TFrame", padding=(32, 26))
        cartao.place(relx=0.5, rely=0.46, anchor="center")
        ttk.Label(cartao, text="Conectar ao servidor", style="Secao.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(cartao, text="Informe onde o servidor está aberto.", style="Sub.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))

        ttk.Label(cartao, text="Endereço do servidor", style="Cartao.TLabel").grid(row=2, column=0, columnspan=2, sticky="w")
        self.var_endereco = tk.StringVar(value=endereco)
        self.entrada_endereco = ttk.Entry(cartao, textvariable=self.var_endereco, width=38)
        self.entrada_endereco.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(3, 2))
        ttk.Label(cartao, text="IP ou domínio, p. ex. 192.168.0.10 ou nome.a.free.pinggy.link",
                  style="Sub.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(cartao, text="Porta", style="Cartao.TLabel").grid(row=5, column=0, sticky="w")
        self.var_porta = tk.StringVar(value=porta)
        self.entrada_porta = ttk.Entry(cartao, textvariable=self.var_porta, width=10)
        self.entrada_porta.grid(row=6, column=0, sticky="w", pady=(3, 2))
        ttk.Label(cartao, text="Padrão: 5050. Com túnel, use a porta indicada por ele.",
                  style="Sub.TLabel").grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 16))

        botoes = ttk.Frame(cartao, style="Cartao.TFrame")
        botoes.grid(row=8, column=0, columnspan=2, sticky="w")
        self.botao_conectar = ttk.Button(botoes, text="Conectar", style="Primario.TButton", command=self._conectar)
        self.botao_conectar.pack(side="left")
        self.botao_cancelar = ttk.Button(botoes, text="Cancelar", style="Secundario.TButton",
                                         command=self._cancelar_conexao, state="disabled")
        self.botao_cancelar.pack(side="left", padx=(10, 0))

        self.progresso = ttk.Progressbar(cartao, mode="indeterminate", length=330)
        self.progresso.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        self.progresso.grid_remove()
        self.rotulo_conexao = ttk.Label(cartao, text="", style="Status.TLabel", wraplength=int(340 * self._escala),
                                        justify="left")
        self.rotulo_conexao.grid(row=10, column=0, columnspan=2, sticky="w", pady=(10, 0))

        for entrada in (self.entrada_endereco, self.entrada_porta):
            entrada.bind("<Return>", lambda _e: self._conectar())
        self.entrada_endereco.focus_set()

    def _criar_tela_partida(self) -> None:
        tela = self._tela_partida
        tela.columnconfigure(0, weight=1)

        topo = ttk.Frame(tela)
        topo.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        topo.columnconfigure(1, weight=1)
        self.rotulo_jogador = ttk.Label(topo, text="", style="Info.TLabel")
        self.rotulo_jogador.grid(row=0, column=0, sticky="w")
        self.rotulo_status = ttk.Label(topo, text="", style="espera.Status.TLabel")
        self.rotulo_status.grid(row=0, column=1, sticky="e")

        tabuleiros = ttk.Frame(tela)
        tabuleiros.grid(row=1, column=0)
        tabuleiros.columnconfigure((0, 1), uniform="tabuleiro")  # os dois cartões com a mesma largura
        self.proprio = self._criar_tabuleiro(
            tabuleiros, 0, "Meu tabuleiro", "Meus navios e os disparos recebidos", clicavel=False)
        self.disparos = self._criar_tabuleiro(
            tabuleiros, 1, "Meus disparos", "Clique para atacar o adversário", clicavel=True)

        legenda = ttk.LabelFrame(tela, text="Legenda", padding=(12, 6))
        legenda.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for i, (tipo, texto) in enumerate(LEGENDA):
            item = ttk.Frame(legenda, style="Cartao.TFrame")
            item.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 18), pady=2)
            amostra = tk.Frame(item, width=int(24 * self._escala), height=int(24 * self._escala), background=BORDA)
            amostra.grid_propagate(False)
            amostra.columnconfigure(0, weight=1)
            amostra.rowconfigure(0, weight=1)
            amostra.pack(side="left")
            ttk.Label(amostra, text=SIMBOLOS[tipo], style=f"{tipo}.Celula.TLabel").grid(
                row=0, column=0, sticky="nsew", padx=1, pady=1)
            ttk.Label(item, text=texto, style="Legenda.TLabel").pack(side="left", padx=(6, 0))

        mensagens = ttk.LabelFrame(tela, text="Mensagens", padding=(12, 6))
        mensagens.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        mensagens.columnconfigure(0, weight=1)
        self.rotulo_jogada = ttk.Label(mensagens, text="", style="Mensagem.TLabel", wraplength=int(680 * self._escala))
        self.rotulo_jogada.grid(row=0, column=0, sticky="w")
        self.rotulo_aviso = ttk.Label(mensagens, text="", style="Aviso.TLabel", wraplength=int(680 * self._escala))
        self.rotulo_aviso.grid(row=1, column=0, sticky="w")

        rodape = ttk.Frame(tela)
        rodape.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.botao_sair = ttk.Button(rodape, text="Sair da partida", style="Secundario.TButton", command=self._sair)
        self.botao_sair.pack(side="left")
        self.botao_voltar = ttk.Button(rodape, text="Voltar à tela de conexão", style="Primario.TButton",
                                       command=self._voltar_para_conexao, state="disabled")
        self.botao_voltar.pack(side="right")

    def _criar_tabuleiro(self, pai, coluna: int, titulo: str, subtitulo: str, clicavel: bool) -> dict:
        cartao = ttk.Frame(pai, style="Cartao.TFrame", padding=(14, 10))
        cartao.grid(row=0, column=coluna, padx=(0 if coluna == 0 else 14, 0), sticky="nsew")
        rotulo_titulo = ttk.Label(cartao, text=titulo, style="Secao.TLabel")
        rotulo_titulo.grid(row=0, column=0, columnspan=TAMANHO + 1, sticky="w")
        ttk.Label(cartao, text=subtitulo, style="Sub.TLabel").grid(
            row=1, column=0, columnspan=TAMANHO + 1, sticky="w", pady=(0, 6))

        for c, letra in enumerate(LETRAS):
            ttk.Label(cartao, text=letra, style="Eixo.TLabel", width=2).grid(row=2, column=c + 1, pady=(0, 2))
        celulas: list[list] = []
        for linha in range(TAMANHO):
            ttk.Label(cartao, text=str(linha + 1), style="Eixo.TLabel", width=2).grid(
                row=linha + 3, column=0, padx=(0, 4))
            fila = []
            for c in range(TAMANHO):
                # Moldura de lado fixo: garante casas quadradas e com tamanho confortável.
                moldura = tk.Frame(cartao, width=self._lado, height=self._lado, background=BORDA)
                moldura.grid_propagate(False)
                moldura.columnconfigure(0, weight=1)
                moldura.rowconfigure(0, weight=1)
                moldura.grid(row=linha + 3, column=c + 1, padx=1, pady=1)
                nome = nome_da_casa(linha, c)
                if clicavel:
                    casa = ttk.Button(moldura, text="", takefocus=True, command=lambda n=nome: self._ao_clicar(n))
                else:
                    casa = ttk.Label(moldura, text="")
                casa.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
                fila.append(casa)
            celulas.append(fila)
        contagem = ttk.Label(cartao, text="", style="Contagem.TLabel")
        contagem.grid(row=TAMANHO + 3, column=0, columnspan=TAMANHO + 1, sticky="w", pady=(8, 0))
        return {"titulo": rotulo_titulo, "celulas": celulas, "contagem": contagem}

    # ------------------------------------------------------------------ telas

    def _mostrar_conexao(self) -> None:
        self._partida_aberta = False
        self._tela_conexao.tkraise()

    def _mostrar_partida(self) -> None:
        self._partida_aberta = True
        self._tela_partida.tkraise()

    def _definir_mensagem_conexao(self, texto: str, erro: bool = False) -> None:
        self.rotulo_conexao.configure(text=texto, style="Erro.TLabel" if erro else "Status.TLabel")

    def _modo_conectando(self, conectando: bool) -> None:
        estado = "disabled" if conectando else "normal"
        self.entrada_endereco.configure(state=estado)
        self.entrada_porta.configure(state=estado)
        self.botao_conectar.configure(state=estado)
        self.botao_cancelar.configure(state="normal" if conectando else "disabled")
        if conectando:
            self.progresso.grid()
            self.progresso.start(12)
        else:
            self.progresso.stop()
            self.progresso.grid_remove()

    # ------------------------------------------------------------------ ações do usuário

    def _conectar(self) -> None:
        if self._sessao is not None:
            return  # já conectando ou conectado
        try:
            host, porta = interpretar_destino(self.var_endereco.get(), self.var_porta.get())
        except ValueError as erro:
            self._definir_mensagem_conexao(str(erro), erro=True)
            return
        self.var_endereco.set(host)  # mostra o que foi realmente usado (ex.: sem "tcp://")
        self.var_porta.set(str(porta))
        self._geracao += 1
        self.modelo = ModeloPartida()
        self._sessao = Sessao(host, porta, self._eventos, self._geracao)
        self._modo_conectando(True)
        self._definir_mensagem_conexao(f"Conectando a {host}:{porta}...")
        self._sessao.iniciar()

    def _cancelar_conexao(self) -> None:
        self._abandonar_sessao(enviar_sair=False)
        self._modo_conectando(False)
        self._definir_mensagem_conexao("Conexão cancelada.")

    def _sair(self) -> None:
        """Botão "Sair da partida": SAIR do protocolo e volta à tela de conexão."""
        if self.modelo.fase == EM_ANDAMENTO and not self._confirmar(
            "Sair da partida", "Deseja sair da partida? O adversário será avisado e a partida termina."
        ):
            return
        self._abandonar_sessao(enviar_sair=True)
        self._voltar_para_conexao("Você saiu da partida.")

    def _voltar_para_conexao(self, aviso: str = "") -> None:
        """Volta à tela de conexão (depois do fim da partida ou de sair)."""
        self._abandonar_sessao(enviar_sair=False)
        self.modelo = ModeloPartida()
        self._modo_conectando(False)
        self._definir_mensagem_conexao(aviso)
        self._renderizar()
        self._mostrar_conexao()
        self.entrada_endereco.focus_set()

    def _ao_clicar(self, coordenada: str) -> None:
        if self._sessao is None or not self.modelo.pode_disparar(coordenada):
            return  # fora da vez, casa já atacada ou disparo ainda sem resposta
        self.modelo.registrar_disparo(coordenada)
        enviado = self._sessao.enviar({"tipo": "DISPARAR", "coordenada": coordenada})
        if not enviado:
            self.modelo.cancelar_disparo()
            self._conexao_perdida()
            return
        self._renderizar()  # bloqueia os cliques até a resposta do servidor

    def _ao_fechar(self) -> None:
        if self.modelo.fase == EM_ANDAMENTO and not self._confirmar(
            "Fechar o jogo", "A partida está em andamento. Fechar a janela abandona a partida. Deseja fechar?"
        ):
            return
        self._abandonar_sessao(enviar_sair=True)
        self._destruir()

    def _destruir(self) -> None:
        """Para os temporizadores (fila de eventos e barra de progresso) e fecha a janela."""
        self._fechando = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.progresso.stop()
        self.root.destroy()

    def _confirmar(self, titulo: str, texto: str) -> bool:
        return messagebox.askyesno(titulo, texto, parent=self.root)

    # ------------------------------------------------------------------ sessão e eventos

    def _abandonar_sessao(self, enviar_sair: bool) -> None:
        """Encerra a conexão atual e invalida os eventos que ainda chegarem dela."""
        sessao, self._sessao = self._sessao, None
        self._geracao += 1
        if sessao is None:
            return
        if enviar_sair:
            sessao.sair()  # SAIR é aceito em qualquer estado, inclusive enquanto aguarda
        else:
            sessao.encerrar()

    def _agendar(self) -> None:
        if not self._fechando:
            self._after_id = self.root.after(INTERVALO_FILA_MS, self._processar_eventos)

    def _processar_eventos(self) -> None:
        """Roda na thread principal do Tkinter: esvazia a fila de eventos de rede."""
        try:
            while True:
                try:
                    geracao, tipo, dados = self._eventos.get_nowait()
                except queue.Empty:
                    break
                if geracao != self._geracao:
                    continue  # evento atrasado de uma conexão anterior
                self._tratar_evento(tipo, dados)
        finally:
            self._agendar()

    def _tratar_evento(self, tipo: str, dados) -> None:
        if tipo == "conectado":
            self._definir_mensagem_conexao("Conectado. Aguardando resposta do servidor...")
        elif tipo == "falha_conexao":
            self._falha_antes_da_partida(dados)
        elif tipo == "mensagem":
            self._tratar_mensagem(dados)
        elif tipo == "protocolo":
            self._falha_de_protocolo(dados)
        elif tipo == "encerrada":
            self._conexao_perdida()

    def _tratar_mensagem(self, mensagem: dict) -> None:
        antes = self.modelo.fase
        try:
            self.modelo.aplicar(mensagem)
        except (KeyError, TypeError, ValueError) as erro:
            self._falha_de_protocolo(f"{type(erro).__name__}: {erro}")
            return
        if antes == CONECTADO and self.modelo.fase == ENCERRADA:
            # Recusado já na entrada (SERVIDOR_CHEIO): continua na tela de conexão.
            self._falha_antes_da_partida(self.modelo.desfecho.texto)
            return
        if antes == CONECTADO and self.modelo.fase != CONECTADO:
            self._mostrar_partida()
        self._renderizar()
        self._encerrar_rede_se_acabou()

    def _conexao_perdida(self) -> None:
        if self.modelo.fase == ENCERRADA:
            return  # o servidor fecha a conexão depois do FIM: é esperado
        self.modelo.conexao_perdida()
        self._depois_de_encerrar()

    def _falha_de_protocolo(self, detalhe: str) -> None:
        self.modelo.falha_de_protocolo(detalhe)
        self._depois_de_encerrar()

    def _depois_de_encerrar(self) -> None:
        if self.modelo.fase == ENCERRADA and not self._partida_aberta:
            self._falha_antes_da_partida(self.modelo.desfecho.texto)
            return
        self._renderizar()
        self._encerrar_rede_se_acabou()

    def _encerrar_rede_se_acabou(self) -> None:
        """Depois de FIM (ou erro), libera a conexão; a tela continua mostrando o desfecho."""
        if self.modelo.fase == ENCERRADA and self._sessao is not None:
            self._sessao.encerrar()

    def _falha_antes_da_partida(self, texto: str) -> None:
        """Erro ao conectar ou recusa na entrada: tela de conexão com a explicação."""
        self._abandonar_sessao(enviar_sair=False)
        self.modelo = ModeloPartida()
        self._modo_conectando(False)
        self._definir_mensagem_conexao(texto, erro=True)
        self._mostrar_conexao()

    # ------------------------------------------------------------------ desenho

    def _renderizar(self) -> None:
        m = self.modelo
        self.rotulo_jogador.configure(text=f"Você é o Jogador {m.jogador}" if m.jogador else "Conectado ao servidor")
        texto, nivel = m.situacao
        self.rotulo_status.configure(text=texto, style=f"{nivel}.Status.TLabel")

        for linha in range(TAMANHO):
            for coluna in range(TAMANHO):
                tipo = m.tipo_casa(True, linha, coluna)
                casa = self.proprio["celulas"][linha][coluna]
                casa.configure(text=SIMBOLOS[tipo], style=f"{tipo}.Celula.TLabel")

                tipo = m.tipo_casa(False, linha, coluna)
                habilitada = m.pode_disparar(nome_da_casa(linha, coluna))
                casa = self.disparos["celulas"][linha][coluna]
                casa.configure(text=SIMBOLOS[tipo], style=f"{tipo}.Celula.TButton",
                               state="normal" if habilitada else "disabled",
                               cursor="hand2" if habilitada else "")

        self.proprio["contagem"].configure(
            text=f"Meus navios: {m.navios_proprio} de {NAVIOS_INICIAIS} restantes")
        self.disparos["contagem"].configure(
            text=f"Navios do adversário: {m.navios_adversario} de {NAVIOS_INICIAIS} restantes")
        self.disparos["titulo"].configure(foreground=AZUL if m.sua_vez else AZUL_ESCURO)

        self.rotulo_jogada.configure(text=m.ultima_jogada)
        if m.fase == ENCERRADA and m.desfecho is not None:
            self.rotulo_aviso.configure(text=m.desfecho.texto, style="Aviso.TLabel")
        else:
            self.rotulo_aviso.configure(text=m.aviso, style="AvisoErro.TLabel" if m.aviso_e_erro else "Aviso.TLabel")

        em_jogo = m.fase in (AGUARDANDO, EM_ANDAMENTO)
        self.botao_sair.configure(state="normal" if em_jogo else "disabled")
        self.botao_voltar.configure(state="normal" if m.fase == ENCERRADA else "disabled")


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main(argumentos: list[str]) -> int:
    if tk is None:
        print(
            "O Tkinter não está instalado neste Python. No Windows e no macOS ele vem com o "
            "instalador oficial do Python; no Debian/Ubuntu instale com: sudo apt install python3-tk",
            file=sys.stderr,
        )
        return 1
    if len(argumentos) > 2:
        print("Uso: python cliente_gui.py [endereco_do_servidor] [porta]", file=sys.stderr)
        return 2
    endereco = argumentos[0] if argumentos else ENDERECO_PADRAO
    porta = argumentos[1] if len(argumentos) == 2 else str(PORTA_PADRAO)
    try:
        endereco, numero_porta = interpretar_destino(endereco, porta)
    except ValueError as erro:
        print(erro, file=sys.stderr)
        return 2
    try:
        raiz = tk.Tk()
    except tk.TclError as erro:
        print(f"Não foi possível abrir a janela (há uma tela disponível?): {erro}", file=sys.stderr)
        return 1
    App(raiz, endereco, numero_porta)
    raiz.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
