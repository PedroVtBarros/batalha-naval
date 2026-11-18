"""Regras de uma partida de Batalha Naval (fase 5), sem sockets ou interface.

A criação da Partida representa o início do jogo com os dois jogadores prontos.
O jogador 1 começa; disparos válidos alternam a vez. Erros preservam o estado.
Cada jogador recebe sua própria visão dos tabuleiros, conforme o readme.md.

O servidor deve usar estas regras sob seu lock do estado, na fase 6. A espera
pelo segundo jogador, o envio das mensagens e as desconexões pertencem a ele.
"""

from typing import Any

from tabuleiro import Tabuleiro


class PartidaError(Exception):
    """Base dos erros de controle da partida; erros do tabuleiro são propagados."""


class ForaDeVez(PartidaError):
    """O jogador tentou disparar durante o turno do adversário."""

    codigo = "FORA_DE_VEZ"


class PartidaEncerrada(PartidaError):
    """A partida já tem vencedor e não aceita novos disparos."""


class JogadorInvalido(PartidaError):
    """O chamador não identificou o jogador como o inteiro 1 ou 2."""


class Partida:
    """Controla dois tabuleiros e a vez dos jogadores de uma partida iniciada.

    Sem argumentos, cria dois tabuleiros sorteados. Para testes ou integração,
    aceita dois tabuleiros recém-criados e distintos. Depois de entregá-los à
    partida, o chamador deve aplicar disparos somente por Partida.disparar().
    """

    def __init__(
        self,
        tabuleiro_1: Tabuleiro | None = None,
        tabuleiro_2: Tabuleiro | None = None,
    ) -> None:
        primeiro = tabuleiro_1 if tabuleiro_1 is not None else Tabuleiro()
        segundo = tabuleiro_2 if tabuleiro_2 is not None else Tabuleiro()
        if primeiro is segundo:
            raise ValueError("Cada jogador precisa ter seu próprio tabuleiro.")
        if any(set("".join(tabuleiro.como_lista())) & {"O", "X"}
               for tabuleiro in (primeiro, segundo)):
            raise ValueError("A partida deve começar com tabuleiros sem disparos.")
        self._tabuleiros = {1: primeiro, 2: segundo}
        self._vez: int | None = 1
        self._vencedor: int | None = None

    @property
    def vez(self) -> int | None:
        """Jogador que pode disparar (1 ou 2); None depois da vitória."""
        return self._vez

    @property
    def vencedor(self) -> int | None:
        """Jogador que afundou os dois navios do outro; None enquanto não venceu."""
        return self._vencedor

    @property
    def encerrada(self) -> bool:
        """True quando a partida terminou por vitória."""
        return self._vencedor is not None

    @staticmethod
    def _validar_jogador(jogador: int) -> None:
        # bool e float também comparam iguais a 1/2, mas não são identificadores.
        if type(jogador) is not int or jogador not in (1, 2):
            raise JogadorInvalido("O jogador deve ser o inteiro 1 ou 2.")

    def disparar(self, jogador: int, coordenada: Any) -> str:
        """Aplica um disparo no adversário e devolve AGUA, ACERTO ou AFUNDOU.

        Depois de validar a identidade, verifica encerramento e turno, e só
        então delega coordenada e repetição ao tabuleiro. Se houver erro, nada
        muda. O servidor já deve ter verificado se a partida foi iniciada.

        Levanta ForaDeVez, CoordenadaInvalida ou DisparoRepetido para os erros
        previstos pelo protocolo. JogadorInvalido e PartidaEncerrada indicam
        chamadas indevidas à API; não criam novos códigos de mensagem ERRO.
        """
        self._validar_jogador(jogador)
        if self.encerrada:
            raise PartidaEncerrada("A partida já terminou.")
        if jogador != self._vez:
            raise ForaDeVez("Não é a sua vez de jogar.")

        adversario = 3 - jogador
        alvo = self._tabuleiros[adversario]
        resultado = alvo.receber_disparo(coordenada)
        if alvo.navios_restantes == 0:
            self._vencedor = jogador
            self._vez = None
        else:
            self._vez = adversario
        return resultado

    def visao(self, jogador: int) -> dict[str, Any]:
        """Devolve uma cópia dos campos visíveis ao jogador, pronta para o JSON.

        Inclui sua_vez, tabuleiro_proprio, tabuleiro_adversario e
        navios_restantes com contagens relativas a quem recebe a mensagem.
        Não revela N no tabuleiro adversário, nem mesmo após a vitória.
        """
        self._validar_jogador(jogador)
        proprio = self._tabuleiros[jogador]
        adversario = self._tabuleiros[3 - jogador]
        return {
            "sua_vez": self._vez == jogador,
            "tabuleiro_proprio": proprio.como_lista(),
            "tabuleiro_adversario": adversario.como_lista(ocultar_navios=True),
            "navios_restantes": {
                "proprio": proprio.navios_restantes,
                "adversario": adversario.navios_restantes,
            },
        }
