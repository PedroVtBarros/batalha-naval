"""Testes da fase 5: turnos, vitória e privacidade, sem sockets ou interface.

Executar a partir da raiz do projeto:
    python3 -m unittest discover -s tests -p 'test_partida.py' -v
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from partida import (  # noqa: E402
    ForaDeVez,
    JogadorInvalido,
    Partida,
    PartidaEncerrada,
)
from tabuleiro import CoordenadaInvalida, DisparoRepetido, Tabuleiro  # noqa: E402

NAVIOS_J1 = [("A1", "B1"), ("D4", "D5")]
NAVIOS_J2 = [("D1", "E1"), ("B3", "B4")]
VAZIO = ["~~~~~"] * 5


def criar_partida() -> Partida:
    return Partida(Tabuleiro(NAVIOS_J1), Tabuleiro(NAVIOS_J2))


class BasePartida(unittest.TestCase):
    def setUp(self):
        self.partida = criar_partida()

    def estado_completo(self):
        return (self.partida.vez, self.partida.vencedor, self.partida.encerrada,
                self.partida.visao(1), self.partida.visao(2))


class TestInicio(BasePartida):
    def test_jogador_1_comeca(self):
        self.assertEqual(self.partida.vez, 1)
        self.assertTrue(self.partida.visao(1)["sua_vez"])
        self.assertFalse(self.partida.visao(2)["sua_vez"])
        self.assertIsNone(self.partida.vencedor)
        self.assertFalse(self.partida.encerrada)

    def test_construtor_sorteia_dois_tabuleiros_com_navios_ocultos(self):
        partida = Partida()
        for jogador in (1, 2):
            visao = partida.visao(jogador)
            self.assertEqual("".join(visao["tabuleiro_proprio"]).count("N"), 4)
            self.assertEqual(visao["tabuleiro_adversario"], VAZIO)
            self.assertEqual(visao["navios_restantes"], {"proprio": 2, "adversario": 2})

    def test_rejeita_mesmo_tabuleiro_para_ambos_os_jogadores(self):
        tabuleiro = Tabuleiro(NAVIOS_J1)
        with self.assertRaises(ValueError):
            Partida(tabuleiro, tabuleiro)

    def test_rejeita_tabuleiro_com_disparos_anteriores(self):
        for coordenada in ("A1", "C2"):
            for jogador in (1, 2):
                with self.subTest(coordenada=coordenada, jogador=jogador):
                    tabuleiros = [Tabuleiro(NAVIOS_J1), Tabuleiro(NAVIOS_J2)]
                    tabuleiros[jogador - 1].receber_disparo(coordenada)
                    with self.assertRaises(ValueError):
                        Partida(*tabuleiros)

    def test_rejeita_identidades_invalidas_sem_alterar_estado(self):
        antes = self.estado_completo()
        for jogador in (0, 3, -1, True, False, 1.0, "1", None, [], {}):
            with self.subTest(jogador=jogador):
                with self.assertRaises(JogadorInvalido):
                    self.partida.disparar(jogador, "B3")
                with self.assertRaises(JogadorInvalido):
                    self.partida.visao(jogador)
                self.assertEqual(self.estado_completo(), antes)


class TestTurnos(BasePartida):
    def test_todo_disparo_valido_troca_a_vez_inclusive_acerto_e_afundamento(self):
        jogadas = [(1, "B3", "ACERTO"), (2, "C2", "AGUA"), (1, "B4", "AFUNDOU")]
        for jogador, coordenada, esperado in jogadas:
            self.assertEqual(self.partida.disparar(jogador, coordenada), esperado)
            self.assertEqual(self.partida.vez, 3 - jogador)
            self.assertFalse(self.partida.visao(jogador)["sua_vez"])
            self.assertTrue(self.partida.visao(3 - jogador)["sua_vez"])

    def test_fora_de_vez_nao_muda_estado(self):
        antes = self.estado_completo()
        with self.assertRaises(ForaDeVez) as contexto:
            self.partida.disparar(2, "A1")
        self.assertEqual(contexto.exception.codigo, "FORA_DE_VEZ")
        self.assertEqual(self.estado_completo(), antes)

    def test_turno_e_verificado_antes_da_coordenada(self):
        antes = self.estado_completo()
        for coordenada in (None, [], "F9", "b3"):
            with self.subTest(coordenada=coordenada):
                with self.assertRaises(ForaDeVez):
                    self.partida.disparar(2, coordenada)
                self.assertEqual(self.estado_completo(), antes)

    def test_coordenada_invalida_nao_consume_turno(self):
        antes = self.estado_completo()
        for coordenada in (None, 3, [], {}, "", "B10", "F1", "b3", " B3", "B3\n"):
            with self.subTest(coordenada=coordenada):
                with self.assertRaises(CoordenadaInvalida):
                    self.partida.disparar(1, coordenada)
                self.assertEqual(self.estado_completo(), antes)
        self.assertEqual(self.partida.disparar(1, "B3"), "ACERTO")

    def test_disparo_repetido_nao_consume_turno(self):
        for coordenada in ("C1", "B3"):
            with self.subTest(coordenada=coordenada):
                self.partida = criar_partida()
                self.partida.disparar(1, coordenada)
                self.partida.disparar(2, "C2")
                antes = self.estado_completo()
                with self.assertRaises(DisparoRepetido):
                    self.partida.disparar(1, coordenada)
                self.assertEqual(self.estado_completo(), antes)

    def test_fora_de_vez_tem_prioridade_sobre_disparo_repetido(self):
        self.partida.disparar(1, "B3")
        antes = self.estado_completo()
        with self.assertRaises(ForaDeVez):
            self.partida.disparar(1, "B3")
        self.assertEqual(self.estado_completo(), antes)

    def test_mesma_coordenada_pode_ser_usada_pelos_dois_jogadores(self):
        self.assertEqual(self.partida.disparar(1, "B3"), "ACERTO")
        self.assertEqual(self.partida.disparar(2, "B3"), "AGUA")
        self.assertEqual(self.partida.visao(1)["tabuleiro_proprio"][2], "~O~~~")
        self.assertEqual(self.partida.visao(2)["tabuleiro_proprio"][2], "~X~~~")


class TestVisibilidade(BasePartida):
    def test_reproduz_exatamente_as_duas_visoes_do_exemplo_do_readme(self):
        self.assertEqual(self.partida.disparar(1, "B3"), "ACERTO")
        self.assertEqual(self.partida.visao(1), {
            "sua_vez": False,
            "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
            "tabuleiro_adversario": ["~~~~~", "~~~~~", "~X~~~", "~~~~~", "~~~~~"],
            "navios_restantes": {"proprio": 2, "adversario": 2},
        })
        self.assertEqual(self.partida.visao(2), {
            "sua_vez": True,
            "tabuleiro_proprio": ["~~~NN", "~~~~~", "~X~~~", "~N~~~", "~~~~~"],
            "tabuleiro_adversario": VAZIO,
            "navios_restantes": {"proprio": 2, "adversario": 2},
        })

    def test_disparos_dos_dois_lados_nao_revelam_navios_intactos(self):
        self.partida.disparar(1, "B3")
        self.partida.disparar(2, "A1")
        for jogador in (1, 2):
            visao = self.partida.visao(jogador)
            self.assertIn("N", "".join(visao["tabuleiro_proprio"]))
            self.assertNotIn("N", "".join(visao["tabuleiro_adversario"]))
        self.assertEqual(self.partida.visao(1)["tabuleiro_adversario"][2], "~X~~~")
        self.assertEqual(self.partida.visao(2)["tabuleiro_adversario"][0], "X~~~~")

    def test_contagens_sao_relativas_a_quem_recebe(self):
        self.partida.disparar(1, "B3")
        self.partida.disparar(2, "C2")
        self.partida.disparar(1, "B4")
        self.assertEqual(self.partida.visao(1)["navios_restantes"], {"proprio": 2, "adversario": 1})
        self.assertEqual(self.partida.visao(2)["navios_restantes"], {"proprio": 1, "adversario": 2})
        self.assertFalse(self.partida.encerrada)

    def test_visoes_nao_permitem_modificar_estado_interno(self):
        antes = self.estado_completo()
        visao = self.partida.visao(1)
        visao["sua_vez"] = False
        visao["tabuleiro_proprio"][0] = "XXXXX"
        visao["tabuleiro_adversario"].append("NNNNN")
        visao["navios_restantes"]["proprio"] = 0
        self.assertEqual(self.estado_completo(), antes)

    def test_visao_antiga_nao_muda_depois_de_novo_disparo(self):
        visao_anterior = self.partida.visao(1)
        self.partida.disparar(1, "B3")
        self.assertTrue(visao_anterior["sua_vez"])
        self.assertEqual(visao_anterior["tabuleiro_adversario"], VAZIO)
        self.assertNotEqual(visao_anterior, self.partida.visao(1))

    def test_visao_e_serializavel_em_json(self):
        self.partida.disparar(1, "B3")
        visao = self.partida.visao(1)
        self.assertEqual(json.loads(json.dumps(visao)), visao)

    def test_partidas_nao_compartilham_estado(self):
        outra = criar_partida()
        self.partida.disparar(1, "B3")
        self.assertEqual(outra.vez, 1)
        self.assertEqual(outra.visao(1)["tabuleiro_adversario"], VAZIO)


class TestVitoria(BasePartida):
    def vencer(self, jogador: int):
        jogadas = {
            1: [(1, "D1"), (2, "C1"), (1, "E1"), (2, "C2"),
                (1, "B3"), (2, "C3"), (1, "B4")],
            2: [(1, "C1"), (2, "A1"), (1, "C2"), (2, "B1"),
                (1, "C3"), (2, "D4"), (1, "C4"), (2, "D5")],
        }
        for atirador, coordenada in jogadas[jogador][:-1]:
            self.partida.disparar(atirador, coordenada)
            self.assertFalse(self.partida.encerrada)
            self.assertIsNone(self.partida.vencedor)
        atirador, coordenada = jogadas[jogador][-1]
        self.assertEqual(self.partida.disparar(atirador, coordenada), "AFUNDOU")

    def test_ambos_os_jogadores_podem_vencer_afundando_o_ultimo_navio(self):
        for vencedor in (1, 2):
            with self.subTest(vencedor=vencedor):
                self.partida = criar_partida()
                self.vencer(vencedor)
                self.assertTrue(self.partida.encerrada)
                self.assertEqual(self.partida.vencedor, vencedor)
                self.assertIsNone(self.partida.vez)
                self.assertEqual(self.partida.visao(vencedor)["navios_restantes"]["adversario"], 0)

    def test_disparo_final_deixa_sua_vez_false_para_os_dois(self):
        self.vencer(1)
        self.assertFalse(self.partida.visao(1)["sua_vez"])
        self.assertFalse(self.partida.visao(2)["sua_vez"])

    def test_nao_revela_frota_do_vencedor_ao_perdedor(self):
        self.vencer(1)
        self.assertIn("N", "".join(self.partida.visao(1)["tabuleiro_proprio"]))
        self.assertNotIn("N", "".join(self.partida.visao(2)["tabuleiro_adversario"]))

    def test_disparo_depois_da_vitoria_nao_muda_estado(self):
        self.vencer(1)
        antes = self.estado_completo()
        for jogador in (1, 2):
            for coordenada in ("C5", "B4", None):
                with self.subTest(jogador=jogador, coordenada=coordenada):
                    with self.assertRaises(PartidaEncerrada):
                        self.partida.disparar(jogador, coordenada)
                    self.assertEqual(self.estado_completo(), antes)


if __name__ == "__main__":
    unittest.main()
