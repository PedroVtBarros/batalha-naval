"""Servidor da Batalha Naval (fase 3): recebe os dois jogadores e inicia a partida.

Uso:
    python servidor.py [porta]          # porta padrão: 5050

O que esta fase faz (linhas da tabela 9.2 do readme.md):

    1   primeiro cliente conecta            -> jogador 1, recebe AGUARDE
    2   segundo cliente conecta             -> jogador 2, os dois recebem INICIO
    3   DISPARAR enquanto aguarda           -> ERRO PARTIDA_NAO_INICIADA
    4/5 jogador 1 sai/cai enquanto aguarda  -> volta a SEM_JOGADORES, sem avisar ninguém
    10  linha inválida / tipo não permitido -> ERRO MENSAGEM_INVALIDA / TIPO_DESCONHECIDO
    11  SAIR durante a partida              -> FIM ADVERSARIO_SAIU ao outro
    12  desconexão durante a partida        -> FIM ADVERSARIO_DESCONECTOU ao outro
    13  terceiro cliente conecta            -> ERRO SERVIDOR_CHEIO e fecha a conexão dele

O processamento de um DISPARAR durante a partida (turnos, resultado, ATUALIZACAO e
vitória) fica para as fases 5 e 6: ver Servidor._disparo_em_partida.

Modelo de concorrência:

    - a thread principal só aceita conexões;
    - cada conexão tem uma thread própria que bloqueia em receber_mensagem;
    - todo o estado da partida é protegido por um único Lock (self._lock), então
      os eventos dos dois jogadores são tratados um de cada vez;
    - cada conexão tem um Lock de envio, porque a thread de um jogador também
      envia mensagens ao outro (sendall não é atômico entre threads).
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
from collections.abc import Callable
from typing import Any

from protocolo import (
    ConexaoEncerrada,
    MensagemInvalida,
    enviar_mensagem,
    receber_mensagem,
)
from tabuleiro import Tabuleiro

PORTA_PADRAO = 5050

# Estados do servidor (seção 9.1). ENCERRADA é transitório e não fica guardado:
# ao terminar a partida o servidor fecha as conexões e volta a SEM_JOGADORES.
SEM_JOGADORES = "SEM_JOGADORES"
AGUARDANDO_SEGUNDO = "AGUARDANDO_SEGUNDO"
VEZ_J1 = "VEZ_J1"
VEZ_J2 = "VEZ_J2"

TABULEIRO_DESCONHECIDO = ["~~~~~"] * 5
TIPOS_DO_CLIENTE = {"DISPARAR", "SAIR"}

log = logging.getLogger("servidor")


class Conexao:
    """Um cliente conectado: socket, leitor único, lock de envio e número do jogador."""

    def __init__(self, sock: socket.socket, endereco: Any) -> None:
        self.sock = sock
        self.endereco = endereco
        self.leitor = sock.makefile("rb")  # UMA vez por conexão (seção 5, regra 6)
        self.numero: int | None = None  # 1 ou 2 quando vira jogador
        self.ativa = True
        self._lock_envio = threading.Lock()
        self._lock_fechar = threading.Lock()

    def enviar(self, mensagem: dict[str, Any]) -> bool:
        """Envia uma mensagem; devolve False se a conexão já caiu (sem levantar)."""
        with self._lock_envio:
            if not self.ativa:
                return False
            try:
                enviar_mensagem(self.sock, mensagem)
                return True
            except (ConexaoEncerrada, OSError):
                return False

    def fechar(self) -> None:
        """Encerra a conexão: o cliente recebe fim de fluxo. Idempotente.

        Pode ser chamado de qualquer thread. Não fecha o leitor: fechar um leitor
        enquanto outra thread está bloqueada nele trava (o BufferedReader tem um
        lock interno) e, no Windows, shutdown() não desbloqueia essa leitura. Quem
        libera leitor e socket é a própria thread da conexão (liberar()), quando
        a leitura termina; o cliente fecha o lado dele ao receber o fim do fluxo.
        """
        with self._lock_fechar:
            if not self.ativa:
                return
            self.ativa = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # o outro lado já pode ter fechado

    def liberar(self) -> None:
        """Fecha leitor e socket. Só a thread dona da leitura (ou ninguém lendo)."""
        self.fechar()
        for recurso in (self.leitor, self.sock):
            try:
                recurso.close()
            except OSError:
                pass

    def __str__(self) -> str:
        quem = f"jogador {self.numero}" if self.numero else "cliente"
        return f"{quem} {self.endereco}"


class Servidor:
    """Aceita conexões TCP e conduz uma partida por vez.

    `criar_tabuleiro` permite que os testes forneçam tabuleiros com navios fixos;
    por padrão cada jogador recebe um Tabuleiro() sorteado.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        porta: int = PORTA_PADRAO,
        *,
        criar_tabuleiro: Callable[[], Tabuleiro] = Tabuleiro,
    ) -> None:
        self.host = host
        self.porta = porta
        self._criar_tabuleiro = criar_tabuleiro
        self._lock = threading.Lock()  # protege tudo abaixo
        self._estado = SEM_JOGADORES
        self._jogadores: dict[int, Conexao] = {}
        self._tabuleiros: dict[int, Tabuleiro] = {}
        self._socket_escuta: socket.socket | None = None
        self._rodando = threading.Event()

    # ------------------------------------------------------------------ ciclo de vida

    def iniciar(self) -> tuple[str, int]:
        """Abre o socket de escuta. Devolve (host, porta) reais (porta 0 = porta livre)."""
        escuta = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":
            # No Windows, SO_REUSEADDR permite que outro processo use a mesma porta.
            escuta.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        escuta.bind((self.host, self.porta))
        escuta.listen()
        # Timeout só no socket de escuta (sem makefile): permite parar com Ctrl+C
        # no Windows, onde accept() bloqueante não é interrompido.
        escuta.settimeout(0.5)
        self._socket_escuta = escuta
        self.porta = escuta.getsockname()[1]
        self._rodando.set()
        return self.host, self.porta

    def servir_para_sempre(self) -> None:
        """Laço de aceitação. Termina quando encerrar() é chamado."""
        if self._socket_escuta is None:
            self.iniciar()
        assert self._socket_escuta is not None
        log.info("Servidor ouvindo em %s:%d", self.host, self.porta)
        while self._rodando.is_set():
            try:
                sock, endereco = self._socket_escuta.accept()
            except TimeoutError:
                continue
            except OSError:
                break  # socket de escuta fechado por encerrar()
            sock.setblocking(True)  # sockets com makefile devem ser bloqueantes
            conexao = Conexao(sock, endereco)
            log.info("Nova conexão de %s", endereco)
            self._ao_conectar(conexao)

    def encerrar(self) -> None:
        """Para de aceitar conexões e fecha todas as conexões abertas."""
        self._rodando.clear()
        if self._socket_escuta is not None:
            self._socket_escuta.close()
        with self._lock:
            conexoes = list(self._jogadores.values())
            self._resetar()
        for conexao in conexoes:
            conexao.fechar()

    @property
    def estado(self) -> str:
        with self._lock:
            return self._estado

    # ------------------------------------------------------------------ eventos

    def _ao_conectar(self, conexao: Conexao) -> None:
        with self._lock:
            if self._estado == SEM_JOGADORES:
                self._registrar_jogador_1(conexao)
            elif self._estado == AGUARDANDO_SEGUNDO:
                self._iniciar_partida(conexao)
            else:
                # Linha 13: partida em andamento, terceiro cliente recusado.
                log.info("Recusando %s: servidor cheio", conexao.endereco)
                conexao.enviar(
                    {
                        "tipo": "ERRO",
                        "codigo": "SERVIDOR_CHEIO",
                        "mensagem": "Já existe uma partida em andamento. Tente mais tarde.",
                    }
                )
                conexao.liberar()  # nenhuma thread lê esta conexão
                return
        threading.Thread(
            target=self._ler_conexao, args=(conexao,), daemon=True, name=f"conexao-{conexao.endereco}"
        ).start()

    def _registrar_jogador_1(self, conexao: Conexao) -> None:
        """Linha 1. Chamado com self._lock adquirido."""
        conexao.numero = 1
        self._jogadores = {1: conexao}
        self._estado = AGUARDANDO_SEGUNDO
        log.info("%s registrado; aguardando o segundo jogador", conexao)
        conexao.enviar(
            {"tipo": "AGUARDE", "mensagem": "Conectado. Aguardando o segundo jogador..."}
        )

    def _iniciar_partida(self, conexao: Conexao) -> None:
        """Linha 2. Chamado com self._lock adquirido."""
        jogador_1 = self._jogadores[1]
        conexao.numero = 2
        self._tabuleiros = {1: self._criar_tabuleiro(), 2: self._criar_tabuleiro()}

        if not jogador_1.enviar(self._mensagem_inicio(1)):
            # Observação da seção 9.2: o jogador 1 caiu antes do início. A partida
            # não começa; quem acabou de chegar passa a ser o jogador 1.
            log.info("%s caiu antes do início; %s vira jogador 1", jogador_1, conexao.endereco)
            jogador_1.fechar()
            self._tabuleiros = {}
            self._registrar_jogador_1(conexao)
            return

        self._jogadores[2] = conexao
        self._estado = VEZ_J1
        log.info("Partida iniciada: %s x %s", jogador_1, conexao)
        if not conexao.enviar(self._mensagem_inicio(2)):
            # O jogador 2 caiu no mesmo instante: a thread dele vai receber
            # ConexaoEncerrada e encerrar a partida pela linha 12.
            log.info("Falha ao enviar INICIO a %s", conexao)

    def _mensagem_inicio(self, numero: int) -> dict[str, Any]:
        return {
            "tipo": "INICIO",
            "jogador": numero,
            "sua_vez": numero == 1,
            "tabuleiro_proprio": self._tabuleiros[numero].como_lista(),
            "tabuleiro_adversario": list(TABULEIRO_DESCONHECIDO),
        }

    def _ler_conexao(self, conexao: Conexao) -> None:
        """Thread de uma conexão: lê mensagens até a conexão acabar."""
        try:
            while conexao.ativa:
                try:
                    mensagem = receber_mensagem(conexao.leitor)
                except MensagemInvalida as erro:
                    log.info("Mensagem inválida de %s: %s", conexao, erro)
                    self._enviar_erro(
                        conexao, "MENSAGEM_INVALIDA", "Mensagem inválida para o protocolo."
                    )
                    continue
                self._ao_receber(conexao, mensagem)
        except (ConexaoEncerrada, OSError, ValueError):
            pass  # ValueError: leitor já fechado
        try:
            self._ao_desconectar(conexao)
        finally:
            conexao.liberar()

    def _ao_receber(self, conexao: Conexao, mensagem: dict[str, Any]) -> None:
        tipo = mensagem["tipo"]
        if tipo == "SAIR":
            self._ao_sair(conexao)
        elif tipo == "DISPARAR":
            self._ao_disparar(conexao, mensagem)
        else:
            self._enviar_erro(
                conexao,
                "TIPO_DESCONHECIDO",
                f"Tipo de mensagem não permitido: {tipo}. Use DISPARAR ou SAIR.",
            )

    def _ao_disparar(self, conexao: Conexao, mensagem: dict[str, Any]) -> None:
        with self._lock:
            if not self._eh_jogador_atual(conexao):
                return  # evento de conexão já encerrada pelo servidor: ignora
            if self._estado == AGUARDANDO_SEGUNDO:
                # Linha 3 (passo 1 da ordem de verificação da seção 8).
                self._enviar_erro(
                    conexao, "PARTIDA_NAO_INICIADA", "A partida ainda não começou. Aguarde o adversário."
                )
                return
            self._disparo_em_partida(conexao, mensagem)

    def _disparo_em_partida(self, conexao: Conexao, mensagem: dict[str, Any]) -> None:
        """DISPARAR com a partida em andamento (linhas 6 a 9). Chamado com self._lock.

        A implementar nas fases 5 (regras) e 6 (integração): verificar a vez
        (FORA_DE_VEZ), aplicar o disparo no tabuleiro do adversário, enviar
        ATUALIZACAO aos dois e, em caso de vitória, FIM VITORIA e encerrar.
        Por enquanto o disparo é apenas registrado no log e nada é enviado.
        """
        log.info("%s disparou %r (processamento previsto para a fase 6)", conexao, mensagem.get("coordenada"))

    def _ao_sair(self, conexao: Conexao) -> None:
        log.info("%s enviou SAIR", conexao)
        self._encerrar_por_saida(conexao, "ADVERSARIO_SAIU", "O adversário saiu da partida.")

    def _ao_desconectar(self, conexao: Conexao) -> None:
        self._encerrar_por_saida(
            conexao, "ADVERSARIO_DESCONECTOU", "A conexão do adversário caiu."
        )

    def _encerrar_por_saida(self, conexao: Conexao, motivo: str, texto: str) -> None:
        """Linhas 4, 5, 11 e 12: um jogador saiu (SAIR) ou caiu (desconexão)."""
        with self._lock:
            if not self._eh_jogador_atual(conexao):
                conexao.fechar()
                return
            if self._estado == AGUARDANDO_SEGUNDO:
                # Linhas 4 e 5: não há partida nem adversário a avisar.
                log.info("%s saiu enquanto aguardava; voltando a %s", conexao, SEM_JOGADORES)
                self._resetar()
                conexao.fechar()
                return
            # Linhas 11 e 12: avisa o outro e encerra a partida.
            outro = self._jogadores[2 if conexao.numero == 1 else 1]
            log.info("Partida encerrada (%s): %s saiu", motivo, conexao)
            conexao.fechar()
            outro.enviar({"tipo": "FIM", "motivo": motivo, "vencedor": None, "mensagem": texto})
            outro.fechar()
            self._resetar()

    # ------------------------------------------------------------------ auxiliares

    def _eh_jogador_atual(self, conexao: Conexao) -> bool:
        return conexao.numero is not None and self._jogadores.get(conexao.numero) is conexao

    def _resetar(self) -> None:
        """Volta a SEM_JOGADORES (ENCERRADA é transitório). Chamado com self._lock."""
        self._estado = SEM_JOGADORES
        self._jogadores = {}
        self._tabuleiros = {}

    @staticmethod
    def _enviar_erro(conexao: Conexao, codigo: str, texto: str) -> None:
        conexao.enviar({"tipo": "ERRO", "codigo": codigo, "mensagem": texto})


def main(argumentos: list[str]) -> int:
    if len(argumentos) > 1:
        print("Uso: python servidor.py [porta]", file=sys.stderr)
        return 2
    try:
        porta = int(argumentos[0]) if argumentos else PORTA_PADRAO
        if not 0 < porta < 65536:
            raise ValueError
    except ValueError:
        print("A porta deve ser um número entre 1 e 65535.", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    servidor = Servidor(porta=porta)
    try:
        servidor.iniciar()
    except OSError as erro:
        print(f"Não foi possível abrir a porta {porta}: {erro}", file=sys.stderr)
        return 1
    try:
        servidor.servir_para_sempre()
    except KeyboardInterrupt:
        log.info("Encerrando o servidor...")
    finally:
        servidor.encerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
