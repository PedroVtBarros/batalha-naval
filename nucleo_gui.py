"""Núcleo da interface gráfica da Batalha Naval, sem nenhuma dependência de Tkinter.

Este módulo reúne tudo o que o `cliente_gui.py` precisa e que pode ser testado sem
abrir janela:

    interpretar_destino()   valida o endereço e a porta digitados (IP, domínio, túnel)
    traduzir_erro_conexao() transforma exceções de rede em mensagens compreensíveis
    ModeloPartida           estado visto pelo jogador, atualizado SOMENTE a partir das
                            mensagens reais do servidor (INICIO, ATUALIZACAO, ERRO, FIM...)
    Sessao                  uma conexão TCP: conecta e lê a rede em threads próprias e
                            entrega eventos a uma fila; usa o protocolo.py

O servidor continua sendo a única fonte da verdade: o modelo não valida jogadas nem
decide turnos, apenas guarda o que o servidor informou e impede cliques inúteis
(fora da vez, casa já atacada, disparo ainda sem resposta).

Threads: `Sessao` nunca toca em widgets. Cada evento vai para a fila com o número da
"geração" da conexão, e a interface descarta os de gerações antigas, de modo que
eventos atrasados de uma conexão anterior não afetam uma nova partida.
"""

from __future__ import annotations

import queue
import re
import socket
import threading
from dataclasses import dataclass
from typing import Any

from cliente import ENDERECO_PADRAO, PORTA_PADRAO  # noqa: F401  (reexportados para a GUI)
from protocolo import (
    ConexaoEncerrada,
    MensagemInvalida,
    enviar_mensagem,
    receber_mensagem,
)

LETRAS = "ABCDE"
TAMANHO = 5
NAVIOS_INICIAIS = 2  # seção 2 do readme: 2 navios de 2 casas por jogador
PRAZO_CONEXAO = 10.0  # segundos para o TCP conectar

# ---------------------------------------------------------------------------
# Coordenadas
# ---------------------------------------------------------------------------


def nome_da_casa(linha: int, coluna: int) -> str:
    """(linha, coluna) a partir de zero -> "B3" (letra = coluna, número = linha)."""
    return f"{LETRAS[coluna]}{linha + 1}"


def indices_da_casa(coordenada: str) -> tuple[int, int]:
    """"B3" -> (2, 1): (linha, coluna) a partir de zero. ValueError se for inválida."""
    if not isinstance(coordenada, str) or not re.fullmatch(r"[A-E][1-5]", coordenada):
        raise ValueError(f"coordenada inválida: {coordenada!r}")
    return int(coordenada[1]) - 1, LETRAS.index(coordenada[0])


# ---------------------------------------------------------------------------
# Destino da conexão (IP, domínio ou túnel TCP)
# ---------------------------------------------------------------------------


def interpretar_destino(endereco: str, porta: str) -> tuple[str, int]:
    """Valida o que foi digitado e devolve (host, porta).

    Aceita IP, nome de domínio e também o formato que um túnel costuma mostrar
    (`tcp://nome.a.free.pinggy.link:12345`, ou só `nome...:12345`): nesse caso a
    porta embutida no endereço vale mais que a do campo "Porta".

    Levanta ValueError com uma mensagem em português própria para o usuário.
    """
    host = endereco.strip()
    if host.lower().startswith("tcp://"):
        host = host[len("tcp://"):]
    host = host.rstrip("/")

    porta_texto = porta.strip()
    colchetes = re.fullmatch(r"\[(.+)\]:(\d+)", host)  # IPv6 com porta: [::1]:5050
    if colchetes:
        host, porta_texto = colchetes.group(1), colchetes.group(2)
    elif host.count(":") == 1:  # nome:porta ou IPv4:porta
        nome, _, embutida = host.partition(":")
        if not re.fullmatch(r"[0-9]+", embutida):
            raise ValueError("A porta que acompanha o endereço deve ser um número.")
        host, porta_texto = nome, embutida

    if not host:
        raise ValueError("Informe o endereço do servidor (IP ou domínio).")
    if re.search(r"\s", host) or "/" in host:
        raise ValueError(
            "O endereço não pode ter espaços nem barras. Informe só o IP ou o domínio."
        )
    if not porta_texto:
        raise ValueError("Informe a porta do servidor (o padrão é 5050).")
    if not re.fullmatch(r"[0-9]+", porta_texto) or not 0 < int(porta_texto) < 65536:
        raise ValueError("A porta deve ser um número entre 1 e 65535.")
    return host, int(porta_texto)


def traduzir_erro_conexao(erro: BaseException, host: str, porta: int) -> str:
    """Mensagem compreensível para uma falha ao conectar."""
    destino = f"{host}:{porta}"
    if isinstance(erro, socket.gaierror):
        return (
            f"Não foi possível encontrar o servidor «{host}». "
            "Confira se o endereço está certo e se há conexão com a internet/rede."
        )
    if isinstance(erro, TimeoutError):
        return (
            f"Tempo esgotado ao tentar conectar a {destino}. Confira o endereço e a porta "
            "e se o firewall ou o túnel permitem a conexão."
        )
    if isinstance(erro, ConnectionRefusedError):
        return (
            f"Conexão recusada por {destino}. Verifique se o servidor está aberto "
            "e se a porta está correta."
        )
    if isinstance(erro, UnicodeError):
        return f"O endereço «{host}» não é um nome de servidor válido."
    detalhe = getattr(erro, "strerror", None) or str(erro) or type(erro).__name__
    return f"Não foi possível conectar a {destino}: {detalhe}"


# ---------------------------------------------------------------------------
# Modelo da partida (visão do jogador, alimentada só pelas mensagens do servidor)
# ---------------------------------------------------------------------------

# Fases do ponto de vista da interface.
CONECTADO = "CONECTADO"          # TCP aberto; ainda não veio AGUARDE nem INICIO
AGUARDANDO = "AGUARDANDO"        # recebeu AGUARDE: esperando o segundo jogador
EM_ANDAMENTO = "EM_ANDAMENTO"    # recebeu INICIO
ENCERRADA = "ENCERRADA"          # FIM, SERVIDOR_CHEIO, conexão perdida ou erro de protocolo

# Tipos de casa (decidem cor e símbolo na interface).
DESCONHECIDA = "desconhecida"    # tabuleiro do adversário, casa ainda não atacada
SEM_DISPARO = "sem_disparo"      # meu tabuleiro, água que o adversário não atacou
NAVIO = "navio"                  # meu navio intacto
AGUA = "agua"                    # tiro na água
ACERTO = "acerto"                # parte de navio atingida
AFUNDOU = "afundou"              # casa do golpe que afundou um navio

SIMBOLOS = {
    DESCONHECIDA: "?",
    SEM_DISPARO: "~",
    NAVIO: "■",
    AGUA: "○",
    ACERTO: "✕",
    AFUNDOU: "✖",
}

_FRASE_PROPRIA = {
    "AGUA": "{} atirou em {}: água.",
    "ACERTO": "{} atirou em {} e acertou um navio!",
    "AFUNDOU": "{} atirou em {} e afundou um navio!",
}
_FRASE_ADVERSARIO = {
    "AGUA": "{} atirou em {}: água.",
    "ACERTO": "{} atirou em {} e acertou um dos seus navios.",
    "AFUNDOU": "{} atirou em {} e afundou um dos seus navios.",
}


@dataclass(frozen=True)
class Desfecho:
    """Como a partida (ou a tentativa de entrar) terminou."""

    tipo: str    # "vitoria", "derrota", "interrompida" ou "erro"
    titulo: str  # texto curto para o painel de status
    texto: str   # explicação completa


def _exigir(condicao: bool, campo: str) -> None:
    if not condicao:
        raise ValueError(f"campo inválido na mensagem do servidor: {campo}")


def _tabuleiro(valor: Any, campo: str) -> list[str]:
    _exigir(
        isinstance(valor, list)
        and len(valor) == TAMANHO
        and all(isinstance(l, str) and len(l) == TAMANHO and not set(l) - set("~ONX") for l in valor),
        campo,
    )
    return list(valor)


def _inteiro(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class ModeloPartida:
    """O que o jogador sabe da partida, sempre a partir de mensagens do servidor.

    `aplicar(mensagem)` interpreta uma mensagem; mensagem malformada levanta
    ValueError (a interface trata como falha de protocolo). Mensagens recebidas
    depois do fim são ignoradas, assim como tipos desconhecidos (readme, seção 5).
    """

    def __init__(self) -> None:
        self.fase = CONECTADO
        self.jogador: int | None = None
        self.sua_vez = False
        self.proprio: list[str] = ["~" * TAMANHO] * TAMANHO
        self.adversario: list[str] = ["~" * TAMANHO] * TAMANHO
        self.navios_proprio = NAVIOS_INICIAIS
        self.navios_adversario = NAVIOS_INICIAIS
        # O protocolo só informa a coordenada do golpe que afunda um navio; as demais
        # casas desse navio continuam aparecendo como acerto.
        self.afundou_proprio: set[str] = set()
        self.afundou_adversario: set[str] = set()
        self.pendente: str | None = None   # DISPARAR enviado e ainda sem ATUALIZACAO/ERRO
        self.ultima_jogada = ""
        self.aviso = ""
        self.aviso_e_erro = False
        self.desfecho: Desfecho | None = None

    # ------------------------------------------------------------------ consultas

    def pode_disparar(self, coordenada: str) -> bool:
        """True só na minha vez, sem disparo pendente e em casa ainda não atacada."""
        if self.fase != EM_ANDAMENTO or not self.sua_vez or self.pendente is not None:
            return False
        try:
            linha, coluna = indices_da_casa(coordenada)
        except ValueError:
            return False
        return self.adversario[linha][coluna] == "~"

    def tipo_casa(self, meu_tabuleiro: bool, linha: int, coluna: int) -> str:
        """Tipo visual de uma casa de um dos dois tabuleiros."""
        tabuleiro = self.proprio if meu_tabuleiro else self.adversario
        afundados = self.afundou_proprio if meu_tabuleiro else self.afundou_adversario
        caractere = tabuleiro[linha][coluna]
        if caractere == "X":
            return AFUNDOU if nome_da_casa(linha, coluna) in afundados else ACERTO
        if caractere == "O":
            return AGUA
        if caractere == "N":
            return NAVIO
        return SEM_DISPARO if meu_tabuleiro else DESCONHECIDA

    @property
    def situacao(self) -> tuple[str, str]:
        """(texto, nível) do painel de status. Níveis: minha, adversario, espera e os
        tipos de Desfecho (vitoria, derrota, interrompida, erro)."""
        if self.fase == CONECTADO:
            return "Conectando ao servidor...", "espera"
        if self.fase == AGUARDANDO:
            return "Aguardando outro jogador...", "espera"
        if self.fase == ENCERRADA and self.desfecho is not None:
            return self.desfecho.titulo, self.desfecho.tipo
        if self.pendente is not None:
            return f"Disparo em {self.pendente} enviado. Aguardando o servidor...", "espera"
        if self.sua_vez:
            return "Sua vez! Clique numa casa de “Meus disparos”.", "minha"
        return "Vez do adversário. Aguarde a jogada dele.", "adversario"

    # ------------------------------------------------------------------ ações locais

    def registrar_disparo(self, coordenada: str) -> None:
        """Marca um DISPARAR como enviado (bloqueia novos cliques até a resposta)."""
        if not self.pode_disparar(coordenada):
            raise ValueError(f"disparo não permitido agora: {coordenada!r}")
        self.pendente = coordenada
        self.aviso = ""
        self.aviso_e_erro = False

    def cancelar_disparo(self) -> None:
        """O envio falhou: libera o bloqueio (a conexão será tratada como perdida)."""
        self.pendente = None

    def conexao_perdida(self) -> None:
        if self.fase == ENCERRADA:
            return
        if self.fase == CONECTADO:
            self._encerrar(Desfecho(
                "erro",
                "Sem resposta do servidor",
                "O servidor encerrou a conexão sem iniciar a partida. Confira se o "
                "servidor está aberto (e, num túnel, se ele aponta para a porta do servidor).",
            ))
        else:
            self._encerrar(Desfecho(
                "erro", "Conexão perdida", "A conexão com o servidor foi perdida."
            ))

    def falha_de_protocolo(self, detalhe: str) -> None:
        if self.fase != ENCERRADA:
            self._encerrar(Desfecho(
                "erro", "Erro de comunicação",
                f"O servidor enviou uma mensagem inválida ({detalhe}). A partida foi encerrada.",
            ))

    # ------------------------------------------------------------------ mensagens

    def aplicar(self, mensagem: dict[str, Any]) -> None:
        """Atualiza o modelo com uma mensagem do servidor (ValueError se malformada)."""
        if self.fase == ENCERRADA:
            return  # atrasadas ou repetidas depois do fim
        tratador = {
            "AGUARDE": self._aguarde,
            "INICIO": self._inicio,
            "ATUALIZACAO": self._atualizacao,
            "ERRO": self._erro,
            "FIM": self._fim,
        }.get(mensagem.get("tipo"))
        if tratador is not None:
            tratador(mensagem)

    def _encerrar(self, desfecho: Desfecho) -> None:
        self.fase = ENCERRADA
        self.sua_vez = False
        self.pendente = None
        self.desfecho = desfecho

    def _aguarde(self, mensagem: dict[str, Any]) -> None:
        if self.fase == CONECTADO:
            self.fase = AGUARDANDO
            self.ultima_jogada = "Você está conectado. A partida começa quando o outro jogador entrar."

    def _inicio(self, mensagem: dict[str, Any]) -> None:
        jogador = mensagem["jogador"]
        _exigir(jogador in (1, 2) and _inteiro(jogador), "jogador")
        _exigir(isinstance(mensagem["sua_vez"], bool), "sua_vez")
        proprio = _tabuleiro(mensagem["tabuleiro_proprio"], "tabuleiro_proprio")
        adversario = _tabuleiro(mensagem["tabuleiro_adversario"], "tabuleiro_adversario")

        self.jogador = jogador
        self.sua_vez = mensagem["sua_vez"]
        self.proprio, self.adversario = proprio, adversario
        self.navios_proprio = self.navios_adversario = NAVIOS_INICIAIS
        self.afundou_proprio, self.afundou_adversario = set(), set()
        self.pendente = None
        self.fase = EM_ANDAMENTO
        self.aviso, self.aviso_e_erro = "", False
        self.ultima_jogada = "A partida começou."

    def _atualizacao(self, mensagem: dict[str, Any]) -> None:
        _exigir(self.jogador is not None, "ATUALIZACAO antes de INICIO")
        atirador = mensagem["atirador"]
        coordenada = mensagem["coordenada"]
        resultado = mensagem["resultado"]
        _exigir(atirador in (1, 2) and _inteiro(atirador), "atirador")
        indices_da_casa(coordenada)  # ValueError se inválida
        _exigir(resultado in _FRASE_PROPRIA, "resultado")
        _exigir(isinstance(mensagem["sua_vez"], bool), "sua_vez")
        proprio = _tabuleiro(mensagem["tabuleiro_proprio"], "tabuleiro_proprio")
        adversario = _tabuleiro(mensagem["tabuleiro_adversario"], "tabuleiro_adversario")
        navios = mensagem["navios_restantes"]
        _exigir(isinstance(navios, dict), "navios_restantes")
        navios_proprio, navios_adversario = navios["proprio"], navios["adversario"]
        _exigir(_inteiro(navios_proprio) and _inteiro(navios_adversario), "navios_restantes")

        euatirei = atirador == self.jogador
        self.proprio, self.adversario = proprio, adversario
        self.navios_proprio, self.navios_adversario = navios_proprio, navios_adversario
        self.sua_vez = mensagem["sua_vez"]
        if resultado == "AFUNDOU":
            (self.afundou_adversario if euatirei else self.afundou_proprio).add(coordenada)
        if euatirei:
            self.pendente = None  # a resposta ao meu disparo chegou
        frases = _FRASE_PROPRIA if euatirei else _FRASE_ADVERSARIO
        self.ultima_jogada = frases[resultado].format(
            "Você" if euatirei else "O adversário", coordenada
        )
        self.aviso, self.aviso_e_erro = "", False
        if self.fase != EM_ANDAMENTO:
            self.fase = EM_ANDAMENTO

    def _erro(self, mensagem: dict[str, Any]) -> None:
        codigo = str(mensagem.get("codigo", ""))
        texto = str(mensagem.get("mensagem") or "O servidor recusou o pedido.")
        self.pendente = None  # o disparo pendente (se houve) foi recusado: libera os cliques
        if codigo == "SERVIDOR_CHEIO":
            self._encerrar(Desfecho("erro", "Servidor ocupado", texto))
            return
        self.aviso, self.aviso_e_erro = texto, True

    def _fim(self, mensagem: dict[str, Any]) -> None:
        motivo = mensagem.get("motivo")
        vencedor = mensagem.get("vencedor")
        texto = str(mensagem.get("mensagem") or "")
        if motivo == "VITORIA":
            if vencedor == self.jogador:
                desfecho = Desfecho("vitoria", "Vitória!", "Você afundou todos os navios do adversário!")
            else:
                desfecho = Desfecho("derrota", "Derrota", "O adversário afundou todos os seus navios.")
        elif motivo == "ADVERSARIO_SAIU":
            desfecho = Desfecho(
                "interrompida", "O adversário saiu", "O adversário saiu da partida. Não há vencedor."
            )
        elif motivo == "ADVERSARIO_DESCONECTOU":
            desfecho = Desfecho(
                "interrompida", "O adversário desconectou", "A conexão do adversário caiu. Não há vencedor."
            )
        else:
            desfecho = Desfecho("interrompida", "Partida encerrada", texto or "A partida foi encerrada.")
        self._encerrar(desfecho)


# ---------------------------------------------------------------------------
# Sessão de rede (uma conexão; threads próprias, sem tocar em widgets)
# ---------------------------------------------------------------------------

Evento = tuple[int, str, Any]  # (geração, tipo, dados)


class Sessao:
    """Uma conexão com o servidor.

    `iniciar()` dispara uma thread que conecta (sem travar a interface) e depois lê o
    socket até o fim. Tudo chega em `eventos` como (geracao, tipo, dados):

        "conectado"      TCP aberto
        "falha_conexao"  não conectou; dados = mensagem para o usuário
        "mensagem"       mensagem do servidor; dados = dict
        "protocolo"      linha inválida vinda do servidor; dados = detalhe
        "encerrada"      a conexão acabou (FIN, reset ou encerramento nosso)

    Cuidados herdados do cliente de terminal (readme, seções 5 e 11): um único leitor
    `makefile("rb")` por conexão e socket bloqueante após conectar. No Windows nada
    desbloqueia uma leitura em curso a partir de outra thread, então só a thread de
    leitura fecha o leitor e o socket; `encerrar()` faz apenas `shutdown()`, o servidor
    percebe o fim da conexão e fecha o lado dele, o que termina a leitura. Como não
    existe `input()` bloqueado aqui, o `os._exit` do cliente de terminal não é necessário.
    """

    def __init__(
        self,
        host: str,
        porta: int,
        eventos: "queue.Queue[Evento]",
        geracao: int,
        prazo_conexao: float = PRAZO_CONEXAO,
    ) -> None:
        self.host = host
        self.porta = porta
        self.geracao = geracao
        self._eventos = eventos
        self._prazo = prazo_conexao
        self._sock: socket.socket | None = None
        self._encerrada = threading.Event()
        self._lock_envio = threading.Lock()
        self._thread = threading.Thread(target=self._executar, daemon=True, name=f"sessao-{geracao}")

    # ------------------------------------------------------------------ API (thread da interface)

    def iniciar(self) -> None:
        self._thread.start()

    @property
    def encerrada(self) -> bool:
        return self._encerrada.is_set()

    def enviar(self, mensagem: dict[str, Any]) -> bool:
        """Envia uma mensagem; False se a conexão não existe mais (nada é levantado)."""
        with self._lock_envio:
            sock = self._sock
            if sock is None or self._encerrada.is_set():
                return False
            try:
                enviar_mensagem(sock, mensagem)
                return True
            except (ConexaoEncerrada, OSError):
                return False

    def sair(self) -> None:
        """SAIR do protocolo (sem resposta; vale em qualquer estado) e encerramento."""
        self.enviar({"tipo": "SAIR"})
        self.encerrar()

    def encerrar(self) -> None:
        """Encerra a conexão (idempotente, não bloqueia). Cancela também uma conexão em curso."""
        self._encerrada.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # ------------------------------------------------------------------ thread de rede

    def _postar(self, tipo: str, dados: Any = None) -> None:
        self._eventos.put((self.geracao, tipo, dados))

    def _executar(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.porta), timeout=self._prazo)
        except Exception as erro:  # noqa: BLE001 - fronteira de thread: nada pode escapar
            if not self._encerrada.is_set():
                self._postar("falha_conexao", traduzir_erro_conexao(erro, self.host, self.porta))
            return

        leitor = None
        try:
            sock.settimeout(None)  # socket com makefile deve ser bloqueante (readme, seção 11)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            leitor = sock.makefile("rb")  # UMA vez por conexão
            self._sock = sock
            if self._encerrada.is_set():  # cancelada enquanto conectava
                return
            self._postar("conectado")
            self._ler(leitor)
        except Exception:  # noqa: BLE001 - falha inesperada com a conexão já aberta
            self._postar("encerrada")
        finally:
            self._encerrada.set()
            for recurso in (leitor, sock):
                try:
                    if recurso is not None:
                        recurso.close()
                except (OSError, ValueError):
                    pass

    def _ler(self, leitor) -> None:
        while True:
            try:
                mensagem = receber_mensagem(leitor)
            except MensagemInvalida as erro:
                self._postar("protocolo", str(erro))
                return
            except (ConexaoEncerrada, OSError, ValueError):
                self._postar("encerrada")
                return
            self._postar("mensagem", mensagem)
