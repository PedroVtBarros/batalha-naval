"""Cliente da Batalha Naval (fases 4 e 7): conecta ao servidor e joga.

Uso:
    python cliente.py [endereco_do_servidor] [porta]   # padrao: 127.0.0.1 5050

Fase 4 - camada de rede e ciclo de recebimento:
    - Conecta ao servidor via TCP.
    - Cria sock.makefile("rb") uma unica vez por conexao; fecha leitor e socket
      no finally, conforme a secao 11 do readme.md.
    - Mantem a maquina de estados do cliente (secao 9.3):
        CONECTADO, MINHA_VEZ, VEZ_ADVERSARIO, ENCERRADO
    - Trata todas as mensagens das fases 1 a 3:
        AGUARDE, INICIO, ERRO (codigo decide a acao), FIM.
    - Aceita ATUALIZACAO (chegara nas fases 5/6) com logica de exibicao.
    - Permite envio de SAIR e trata Ctrl+C, ConexaoEncerrada e MensagemInvalida.
    - Deixa hooks bem definidos para a Fase 7:
        ao_minha_vez()   -- ponto de entrada para leitura de coordenada/SAIR
        exibir_tabuleiros() -- ponto de exibicao dos tabuleiros

Fase 7 - interface completa e loop de disparos:
    ao_minha_vez() le a coordenada digitada pelo jogador, normaliza com
    strip().upper(), envia DISPARAR ao servidor ou SAIR se o jogador digitar
    "SAIR". Trata EOFError e KeyboardInterrupt durante o input().

Fase 8 - recepção enquanto se espera a digitação:
    a leitura da rede (thread de leitura) e a do teclado (thread de entrada,
    só na vez do jogador) são separadas e entregam eventos a uma fila que a
    thread principal consome. Assim um FIM do servidor encerra o cliente mesmo
    com o jogador parado no prompt, e o Ctrl+C continua funcionando no Windows.
"""

from __future__ import annotations

import os
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

# ---------------------------------------------------------------------------
# Constantes de estado do cliente (seção 9.3)
# ---------------------------------------------------------------------------

CONECTADO = "CONECTADO"        # conectou; ainda não recebeu INICIO
MINHA_VEZ = "MINHA_VEZ"       # sua_vez=true no último INICIO ou ATUALIZACAO
VEZ_ADVERSARIO = "VEZ_ADVERSARIO"  # sua_vez=false no último INICIO ou ATUALIZACAO
ENCERRADO = "ENCERRADO"        # partida encerrada ou conexão perdida

PORTA_PADRAO = 5050
ENDERECO_PADRAO = "127.0.0.1"


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------

class Cliente:
    """Conexão com o servidor e máquina de estados da partida.

    Atributos públicos (preenchidos após receber INICIO):
        jogador    -- número do jogador (1 ou 2)
        estado     -- estado atual (CONECTADO, MINHA_VEZ, VEZ_ADVERSARIO, ENCERRADO)

    Hooks para a Fase 7:
        ao_minha_vez()     -- chamado quando o estado entra em MINHA_VEZ;
                              retorne True para encerrar o loop antecipadamente
                              (p.ex. após enviar SAIR), False/None para continuar.
        exibir_tabuleiros() -- chamado ao receber INICIO ou ATUALIZACAO.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._leitor = sock.makefile("rb")  # UMA vez por conexão (seção 5, regra 6)
        self.estado: str = CONECTADO
        self.jogador: int | None = None
        # Tabuleiros são armazenados após INICIO/ATUALIZACAO (Fase 7 vai usá-los)
        self.tabuleiro_proprio: list[str] | None = None
        self.tabuleiro_adversario: list[str] | None = None
        # Fase 8: a recepção da rede e a leitura do teclado rodam em threads
        # próprias (ver _loop_recepcao). O envio pode vir de duas threads (a da
        # entrada do usuário e a principal, no SAIR por Ctrl+C), então tem lock.
        self._lock_envio = threading.Lock()
        self._disparos_sem_resposta = 0  # DISPARAR enviados que ainda não tiveram ATUALIZACAO/ERRO
        self._thread_leitura: threading.Thread | None = None
        self._thread_entrada: threading.Thread | None = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def enviar(self, mensagem: dict[str, Any]) -> None:
        """Envia uma mensagem ao servidor."""
        with self._lock_envio:
            if mensagem.get("tipo") == "DISPARAR":
                # Contado antes do envio: a resposta nunca chega antes da contagem.
                self._disparos_sem_resposta += 1
            enviar_mensagem(self._sock, mensagem)

    @property
    def entrada_pendente(self) -> bool:
        """True se ainda há uma thread bloqueada esperando o jogador digitar."""
        return self._thread_entrada is not None and self._thread_entrada.is_alive()

    def enviar_sair(self) -> None:
        """Envia SAIR e marca o estado como ENCERRADO."""
        try:
            self.enviar({"tipo": "SAIR"})
        except (ConexaoEncerrada, OSError):
            pass
        self.estado = ENCERRADO

    def executar(self) -> None:
        """Roda o loop principal: recebe mensagens até o estado ser ENCERRADO.

        Esse é o ponto de entrada para o uso interativo (main) e para os testes
        que querem exercitar o ciclo completo num thread separado.
        """
        try:
            self._loop_recepcao()
        except KeyboardInterrupt:
            self._on_ctrl_c()
        finally:
            self._fechar()

    # ------------------------------------------------------------------
    # Hooks para a Fase 7 (sobrescreva ou substitua por lambdas nos testes)
    # ------------------------------------------------------------------

    def ao_minha_vez(self) -> bool | None:
        """Chamado sempre que o estado entra em MINHA_VEZ.

        Fase 7: le a coordenada digitada pelo jogador, normaliza com
        strip().upper() e envia DISPARAR ao servidor. Se o jogador
        digitar "SAIR", chama enviar_sair() e retorna True para encerrar
        o loop imediatamente.

        Retorno:
            True  -> encerra o loop principal agora (apos SAIR ou EOF).
            False | None -> continua aguardando respostas do servidor.
        """
        try:
            entrada = input("Sua vez! Digite a coordenada (A1-E5) ou SAIR: ")
        except (EOFError, KeyboardInterrupt):
            print()
            self.enviar_sair()
            return True

        entrada = entrada.strip().upper()

        if entrada == "SAIR":
            self.enviar_sair()
            return True

        self.enviar({"tipo": "DISPARAR", "coordenada": entrada})
        return None

    def exibir_tabuleiros(self) -> None:
        """Chamado ao receber INICIO ou ATUALIZACAO com os tabuleiros atualizados.

        Fase 4: exibição básica (texto simples).
        Fase 7: redesenhará o terminal com cores e coordenadas.
        """
        if self.tabuleiro_proprio is None:
            return
        LARGURA = 13           # len(" N  A B C D E")
        SEPARADOR = " " * 11   # empurra o 2º tabuleiro para alinhar à direita com o título
        cabecalho = "    A B C D E"   # 13 chars
        print()
        print("Seu tabuleiro:          Tabuleiro do adversário:")
        print(f"{cabecalho}{SEPARADOR}{cabecalho}")
        for i, (linha_p, linha_a) in enumerate(
            zip(self.tabuleiro_proprio, self.tabuleiro_adversario or ["~~~~~"] * 5),
            start=1,
        ):
            esquerda = f" {i}  " + " ".join(linha_p)   # 13 chars
            direita  = f" {i}  " + " ".join(linha_a)   # 13 chars
            print(f"{esquerda:{LARGURA}}{SEPARADOR}{direita}")
        print()

    # ------------------------------------------------------------------
    # Loop principal de recepção de mensagens
    # ------------------------------------------------------------------

    def _loop_recepcao(self) -> None:
        """Laço principal (fase 8): uma fila recebe eventos de duas threads.

        - thread de leitura: bloqueia em receber_mensagem e enfileira o que o
          servidor envia (ATUALIZACAO, ERRO, FIM...);
        - thread de entrada: roda ao_minha_vez() (input()) só na vez do jogador.

        Assim o cliente continua recebendo do servidor enquanto espera o
        jogador digitar: um FIM (adversário saiu/caiu) encerra o cliente na
        hora, sem exigir uma tecla. A thread principal só espera na fila com
        timeout curto, o que mantém o Ctrl+C funcionando também no Windows.
        """
        eventos: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thread_leitura = threading.Thread(
            target=self._ler_rede, args=(eventos,), daemon=True, name="cliente-leitura"
        )
        self._thread_leitura.start()

        while self.estado != ENCERRADO:
            # Na vez do jogador, pede a coordenada. Só depois de receber a resposta
            # a um DISPARAR (ex.: um ERRO de coordenada inválida) pede de novo.
            if (
                self.estado == MINHA_VEZ
                and not self.entrada_pendente
                and self._disparos_sem_resposta == 0
            ):
                self._thread_entrada = threading.Thread(
                    target=self._ler_entrada, args=(eventos,), daemon=True, name="cliente-entrada"
                )
                self._thread_entrada.start()

            try:
                tipo, valor = eventos.get(timeout=0.2)
            except queue.Empty:
                continue

            if tipo == "mensagem":
                if valor.get("tipo") in ("ATUALIZACAO", "ERRO"):
                    with self._lock_envio:
                        self._disparos_sem_resposta = max(0, self._disparos_sem_resposta - 1)
                self._despachar(valor)
            elif tipo == "entrada":
                if valor:  # ao_minha_vez() pediu para encerrar (SAIR, EOF, Ctrl+C)
                    self.estado = ENCERRADO
            elif tipo == "protocolo":
                # Linha inválida *vinda do servidor* → falha de protocolo.
                print(f"[ERRO DE PROTOCOLO] Mensagem inválida do servidor: {valor}")
                self.estado = ENCERRADO
            else:  # "conexao_encerrada"
                self._on_conexao_perdida()

    def _ler_rede(self, eventos: "queue.Queue[tuple[str, Any]]") -> None:
        """Thread de leitura: dona do leitor; lê até o fim da conexão e o libera.

        No Windows nada desbloqueia uma leitura em andamento a partir de outra
        thread (nem shutdown nem close), por isso só esta thread fecha o leitor.
        """
        try:
            while True:
                try:
                    eventos.put(("mensagem", receber_mensagem(self._leitor)))
                except MensagemInvalida as erro:
                    eventos.put(("protocolo", erro))
                    return
                except (ConexaoEncerrada, OSError, ValueError):
                    eventos.put(("conexao_encerrada", None))
                    return
        finally:
            self._liberar_recursos()

    def _ler_entrada(self, eventos: "queue.Queue[tuple[str, Any]]") -> None:
        """Thread de entrada: executa o hook ao_minha_vez() e avisa o resultado."""
        try:
            encerrar = bool(self.ao_minha_vez())
        except (ConexaoEncerrada, OSError):
            eventos.put(("conexao_encerrada", None))
            return
        eventos.put(("entrada", encerrar))

    def _despachar(self, mensagem: dict[str, Any]) -> None:
        """Roteia a mensagem recebida para o tratador correto."""
        tipo = mensagem.get("tipo", "")
        tratadores = {
            "AGUARDE": self._tratar_aguarde,
            "INICIO": self._tratar_inicio,
            "ATUALIZACAO": self._tratar_atualizacao,
            "ERRO": self._tratar_erro,
            "FIM": self._tratar_fim,
        }
        tratador = tratadores.get(tipo)
        if tratador:
            tratador(mensagem)
        else:
            # Tipo desconhecido vindo do servidor — tratar como falha de protocolo.
            print(f"[AVISO] Tipo de mensagem desconhecido recebido do servidor: {tipo!r}")

    # ------------------------------------------------------------------
    # Tratadores de mensagem
    # ------------------------------------------------------------------

    def _tratar_aguarde(self, mensagem: dict[str, Any]) -> None:
        """AGUARDE: exibe a mensagem e permanece em CONECTADO."""
        texto = mensagem.get("mensagem", "Aguardando o segundo jogador...")
        print(f"[AGUARDE] {texto}")
        # estado permanece CONECTADO

    def _tratar_inicio(self, mensagem: dict[str, Any]) -> None:
        """INICIO: registra o número do jogador e os tabuleiros; atualiza o estado."""
        self.jogador = mensagem.get("jogador")
        sua_vez: bool = mensagem.get("sua_vez", False)
        self.tabuleiro_proprio = mensagem.get("tabuleiro_proprio")
        self.tabuleiro_adversario = mensagem.get("tabuleiro_adversario")

        print(f"\n[INICIO] Você é o jogador {self.jogador}.")
        self.exibir_tabuleiros()

        self.estado = MINHA_VEZ if sua_vez else VEZ_ADVERSARIO
        if self.estado == VEZ_ADVERSARIO:
            print("[AGUARDE] Aguardando o adversário atirar...")

    def _tratar_atualizacao(self, mensagem: dict[str, Any]) -> None:
        """ATUALIZACAO: redesenha tabuleiros e atualiza o estado de vez."""
        atirador: int = mensagem.get("atirador")
        coordenada: str = mensagem.get("coordenada", "?")
        resultado: str = mensagem.get("resultado", "?")
        sua_vez: bool = mensagem.get("sua_vez", False)
        self.tabuleiro_proprio = mensagem.get("tabuleiro_proprio", self.tabuleiro_proprio)
        self.tabuleiro_adversario = mensagem.get("tabuleiro_adversario", self.tabuleiro_adversario)
        navios = mensagem.get("navios_restantes", {})

        quem = "Você" if atirador == self.jogador else "Adversário"
        print(f"\n[ATUALIZACAO] {quem} atirou em {coordenada}: {resultado}")
        if navios:
            print(f"  Navios restantes — seus: {navios.get('proprio', '?')} | adversário: {navios.get('adversario', '?')}")
        self.exibir_tabuleiros()

        self.estado = MINHA_VEZ if sua_vez else VEZ_ADVERSARIO
        if self.estado == VEZ_ADVERSARIO and atirador == self.jogador:
            print("[AGUARDE] Aguardando o adversário atirar...")
        elif self.estado == VEZ_ADVERSARIO:
            pass  # adversário acabou de atirar, ainda é a vez dele? não deveria acontecer.

    def _tratar_erro(self, mensagem: dict[str, Any]) -> None:
        """ERRO: decide pelo código e exibe a mensagem; estado não muda."""
        codigo: str = mensagem.get("codigo", "")
        texto: str = mensagem.get("mensagem", "")

        if codigo == "SERVIDOR_CHEIO":
            # O servidor vai fechar a conexão após este ERRO.
            print(f"[ERRO] {texto} (servidor cheio — encerrando)")
            self.estado = ENCERRADO
            return

        if codigo == "PARTIDA_NAO_INICIADA":
            print(f"[ERRO] {texto}")
            # Estado permanece CONECTADO; aguardamos o INICIO.
            return

        # Erros durante a partida (FORA_DE_VEZ, COORDENADA_INVALIDA,
        # DISPARO_REPETIDO, MENSAGEM_INVALIDA, TIPO_DESCONHECIDO…):
        # o estado não muda — se era nossa vez, continuamos em MINHA_VEZ.
        print(f"[ERRO] {texto} (código: {codigo})")

    def _tratar_fim(self, mensagem: dict[str, Any]) -> None:
        """FIM: exibe o desfecho e encerra."""
        motivo: str = mensagem.get("motivo", "")
        vencedor: int | None = mensagem.get("vencedor")
        texto: str = mensagem.get("mensagem", "")

        print()
        if motivo == "VITORIA":
            if vencedor == self.jogador:
                print(f"[FIM] Você venceu! {texto}")
            else:
                print(f"[FIM] O jogador {vencedor} venceu. {texto}")
        else:
            # ADVERSARIO_SAIU, ADVERSARIO_DESCONECTOU ou motivo desconhecido:
            # o campo "mensagem" do servidor já contém a frase completa.
            print(f"[FIM] {texto}")

        self.estado = ENCERRADO

    # ------------------------------------------------------------------
    # Eventos especiais
    # ------------------------------------------------------------------

    def _on_conexao_perdida(self) -> None:
        """Conexão caiu sem receber FIM — exibe aviso e encerra."""
        if self.estado != ENCERRADO:
            print("\n[ERRO] Conexão perdida com o servidor.")
        self.estado = ENCERRADO

    def _on_ctrl_c(self) -> None:
        """Ctrl+C: envia SAIR educadamente e encerra."""
        print("\n[INFO] Interrompido pelo usuário. Enviando SAIR...")
        self.enviar_sair()

    def fechar(self) -> None:
        """Encerra a conexão (idempotente); útil para quem não usa executar()."""
        self._fechar()

    def _fechar(self) -> None:
        """Encerra a conexão. Sempre chamado no finally de executar().

        Se a thread de leitura está bloqueada no leitor, fechar o leitor daqui
        travaria; então só se faz shutdown() do socket (o servidor percebe o fim
        da conexão e fecha o lado dele) e a própria thread de leitura libera
        leitor e socket quando a leitura terminar.
        """
        leitura = self._thread_leitura
        if leitura is not None and leitura.is_alive():
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        self._liberar_recursos()

    def _liberar_recursos(self) -> None:
        for recurso in (self._leitor, self._sock):
            try:
                recurso.close()
            except (OSError, ValueError):
                pass


# ---------------------------------------------------------------------------
# Fábrica de conexão
# ---------------------------------------------------------------------------

def conectar(endereco: str, porta: int) -> Cliente:
    """Abre a conexão TCP e devolve um Cliente pronto para usar.

    Levanta OSError se não conseguir conectar.
    """
    sock = socket.create_connection((endereco, porta))
    sock.setblocking(True)  # sockets com makefile devem ser bloqueantes (seção 11)
    return Cliente(sock)


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main(argumentos: list[str]) -> int:
    if len(argumentos) > 2:
        print("Uso: python cliente.py [endereco_do_servidor] [porta]", file=sys.stderr)
        return 2

    endereco = argumentos[0] if len(argumentos) >= 1 else ENDERECO_PADRAO
    try:
        porta = int(argumentos[1]) if len(argumentos) >= 2 else PORTA_PADRAO
        if not 0 < porta < 65536:
            raise ValueError
    except ValueError:
        print("A porta deve ser um número entre 1 e 65535.", file=sys.stderr)
        return 2

    print(f"Conectando a {endereco}:{porta}...")
    try:
        cliente = conectar(endereco, porta)
    except OSError as erro:
        print(f"Não foi possível conectar a {endereco}:{porta}: {erro}", file=sys.stderr)
        return 1

    print("Conectado.")
    cliente.executar()  # o finally de executar() já encerra o socket
    if cliente.entrada_pendente:
        # O FIM chegou com a thread do teclado ainda bloqueada no input(). No Linux,
        # com o stdin ligado a um terminal, o interpretador fica travado ao finalizar
        # esperando essa leitura (reproduzido com Python 3.12 em um pseudo-terminal);
        # no Windows termina normalmente. Como não há como interromper o input()
        # de outra thread, saímos direto, mas só depois de fechar a conexão e
        # descarregar a saída. Sem leitura pendente, a saída é a normal.
        cliente.fechar()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
