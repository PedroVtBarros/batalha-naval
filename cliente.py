"""Cliente da Batalha Naval (fase 4): conecta ao servidor e recebe mensagens.

Uso:
    python cliente.py [endereco_do_servidor] [porta]   # padrão: 127.0.0.1 5050

Fase 4 — camada de rede e ciclo de recebimento:
    - Conecta ao servidor via TCP.
    - Cria sock.makefile("rb") uma única vez por conexão; fecha leitor e socket
      no finally, conforme a seção 11 do readme.md.
    - Mantém a máquina de estados do cliente (seção 9.3):
        CONECTADO, MINHA_VEZ, VEZ_ADVERSARIO, ENCERRADO
    - Trata todas as mensagens das fases 1 a 3:
        AGUARDE, INICIO, ERRO (código decide a ação), FIM.
    - Aceita ATUALIZACAO (chegará nas fases 5/6) com lógica de exibição.
    - Permite envio de SAIR e trata Ctrl+C, ConexaoEncerrada e MensagemInvalida.
    - Deixa hooks bem definidos para a Fase 7:
        ao_minha_vez()   — ponto de entrada para leitura de coordenada/SAIR
        exibir_tabuleiros() — ponto de exibição dos tabuleiros

Fase 7 (ainda não implementada): interface completa e loop de disparos.
O método ao_minha_vez() atualmente apenas retorna None (nenhuma ação),
sinalizando que o cliente continua aguardando. Substitua-o na Fase 7 pelo
prompt de coordenada e pelo envio de DISPARAR.
"""

from __future__ import annotations

import socket
import sys
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

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def enviar(self, mensagem: dict[str, Any]) -> None:
        """Envia uma mensagem ao servidor."""
        enviar_mensagem(self._sock, mensagem)

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

        Fase 4: não faz nada (retorna None).
        Fase 7: lê a coordenada do jogador, envia DISPARAR ou SAIR e retorna
                True se quiser encerrar o loop imediatamente (p.ex. após SAIR).

        Retorno:
            True  → encerra o loop principal agora (usado pela Fase 7 após SAIR).
            False | None → continua aguardando respostas do servidor.
        """
        return None  # Fase 7 substituirá este comportamento

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
        while self.estado != ENCERRADO:
            try:
                mensagem = receber_mensagem(self._leitor)
            except MensagemInvalida as erro:
                # Linha incompleta/inválida *vinda do servidor* → falha de protocolo.
                print(f"[ERRO DE PROTOCOLO] Mensagem inválida do servidor: {erro}")
                self.estado = ENCERRADO
                return
            except ConexaoEncerrada:
                self._on_conexao_perdida()
                return

            self._despachar(mensagem)

            # Se acabamos de entrar em MINHA_VEZ, chama o hook da Fase 7.
            if self.estado == MINHA_VEZ:
                encerrar = self.ao_minha_vez()
                if encerrar:
                    self.estado = ENCERRADO

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
                print(f"[FIM] 🎉 Você venceu! {texto}")
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

    def _fechar(self) -> None:
        """Fecha leitor e socket. Sempre chamado no finally de executar()."""
        for recurso in (self._leitor, self._sock):
            try:
                recurso.close()
            except OSError:
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
    cliente.executar()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
