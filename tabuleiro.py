"""Tabuleiro de um jogador de Batalha Naval (fase 2).

Cria um tabuleiro 5x5 com dois navios de duas casas e aplica os disparos
recebidos. Não usa sockets nem controla jogadores, turnos ou vitória.
Os resultados e os símbolos seguem o contrato do readme.md.
"""

import random
import re
from collections.abc import Sequence
from typing import Any

TAMANHO = 5
COLUNAS = "ABCDE"
QUANTIDADE_NAVIOS = 2
TAMANHO_NAVIO = 2


class TabuleiroError(Exception):
    """Base dos erros deste módulo."""


class CoordenadaInvalida(TabuleiroError):
    """A coordenada não é um texto no formato A1 a E5."""

    codigo = "COORDENADA_INVALIDA"


class DisparoRepetido(TabuleiroError):
    """A coordenada já recebeu um disparo neste tabuleiro."""

    codigo = "DISPARO_REPETIDO"


class PosicionamentoInvalido(TabuleiroError):
    """Os navios informados não respeitam o tamanho, a direção ou a ocupação."""


def coordenada_para_indices(coordenada: Any) -> tuple[int, int]:
    """Valida a coordenada e devolve (linha, coluna), com índices de 0 a 4.

    Não normaliza a entrada: espaços e minúsculas devem ser tratados pelo
    cliente, conforme o protocolo. Por exemplo, B3 corresponde a (2, 1).
    """
    if not isinstance(coordenada, str) or not re.fullmatch(r"[A-E][1-5]", coordenada):
        raise CoordenadaInvalida("Coordenada inválida. Use de A1 a E5.")
    return int(coordenada[1]) - 1, COLUNAS.index(coordenada[0])


class Tabuleiro:
    """Guarda os navios e os disparos recebidos por um único jogador.

    Sem argumentos, sorteia dois navios sem sobreposição. É possível fornecer
    posições explícitas para testes e exemplos: [("A1", "B1"), ("D4", "D5")].
    `gerador` permite usar random.Random(semente) para repetir um sorteio.
    """

    def __init__(
        self,
        navios: Sequence[Sequence[str]] | None = None,
        *,
        gerador: random.Random | None = None,
    ) -> None:
        if navios is None:
            navios = self._sortear_navios(gerador if gerador is not None else random.Random())
        self._navios = self._validar_navios(navios)
        self._disparos: set[str] = set()

    @staticmethod
    def _sortear_navios(gerador: random.Random) -> list[tuple[str, str]]:
        # Lista finita de todas as posições horizontais e verticais de 2 casas.
        # Após cada escolha, elimina posições que sobrepõem um navio já colocado.
        posicoes = []
        for linha in range(1, TAMANHO + 1):
            for coluna in range(TAMANHO):
                inicio = f"{COLUNAS[coluna]}{linha}"
                if coluna + 1 < TAMANHO:
                    posicoes.append((inicio, f"{COLUNAS[coluna + 1]}{linha}"))
                if linha < TAMANHO:
                    posicoes.append((inicio, f"{COLUNAS[coluna]}{linha + 1}"))

        navios = []
        for _ in range(QUANTIDADE_NAVIOS):
            navio = gerador.choice(posicoes)
            navios.append(navio)
            posicoes = [posicao for posicao in posicoes if set(navio).isdisjoint(posicao)]
        return navios

    @staticmethod
    def _validar_navios(navios: Sequence[Sequence[str]]) -> tuple[frozenset[str], ...]:
        if len(navios) != QUANTIDADE_NAVIOS:
            raise PosicionamentoInvalido("O tabuleiro deve ter exatamente dois navios.")

        validados = []
        ocupadas: set[str] = set()
        for navio in navios:
            if len(navio) != TAMANHO_NAVIO:
                raise PosicionamentoInvalido("Cada navio deve ocupar exatamente duas casas.")
            try:
                (linha_a, coluna_a), (linha_b, coluna_b) = [
                    coordenada_para_indices(casa) for casa in navio
                ]
            except CoordenadaInvalida as erro:
                raise PosicionamentoInvalido(str(erro)) from erro
            if abs(linha_a - linha_b) + abs(coluna_a - coluna_b) != 1:
                raise PosicionamentoInvalido(
                    "As casas de um navio devem ser vizinhas na horizontal ou na vertical."
                )
            if not ocupadas.isdisjoint(navio):
                raise PosicionamentoInvalido("Os navios não podem se sobrepor.")
            validados.append(frozenset(navio))
            ocupadas.update(navio)
        # Copia as posições para que mudanças na lista original não afetem o jogo.
        return tuple(validados)

    def receber_disparo(self, coordenada: Any) -> str:
        """Aplica um disparo e devolve AGUA, ACERTO ou AFUNDOU.

        Levanta CoordenadaInvalida ou DisparoRepetido sem alterar o tabuleiro.
        O futuro servidor deve verificar início da partida e turno antes de
        chamar este método, preservando a ordem de validação do protocolo.
        """
        coordenada_para_indices(coordenada)
        if coordenada in self._disparos:
            raise DisparoRepetido(f"Você já atirou em {coordenada}.")

        self._disparos.add(coordenada)
        for navio in self._navios:
            if coordenada in navio:
                return "AFUNDOU" if navio.issubset(self._disparos) else "ACERTO"
        return "AGUA"

    def como_lista(self) -> list[str]:
        """Devolve uma cópia do tabuleiro completo, em 5 strings de 5 caracteres.

        Usa ~ (água), N (navio intacto), O (tiro na água) e X (navio atingido).
        Contém posições ocultas: destina-se ao servidor ou ao próprio dono.
        A visão do adversário será implementada na fase 5.
        """
        casas_navios = set().union(*self._navios)
        linhas = []
        for linha in range(1, TAMANHO + 1):
            casas = []
            for coluna in COLUNAS:
                coordenada = f"{coluna}{linha}"
                if coordenada in self._disparos:
                    casas.append("X" if coordenada in casas_navios else "O")
                else:
                    casas.append("N" if coordenada in casas_navios else "~")
            linhas.append("".join(casas))
        return linhas
