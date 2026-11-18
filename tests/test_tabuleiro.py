"""Testes da fase 2: posições dos navios e resultados dos disparos.

Executar a partir da raiz do projeto:
    python3 -m unittest discover -s tests -p 'test_tabuleiro.py' -v
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tabuleiro import (  # noqa: E402
    CoordenadaInvalida,
    DisparoRepetido,
    PosicionamentoInvalido,
    Tabuleiro,
    coordenada_para_indices,
)


class TestCoordenadas(unittest.TestCase):
    def test_converte_todas_as_25_coordenadas(self):
        for linha in range(1, 6):
            for coluna, letra in enumerate("ABCDE"):
                self.assertEqual(coordenada_para_indices(f"{letra}{linha}"), (linha - 1, coluna))

    def test_rejeita_formato_invalido_sem_normalizar(self):
        for coordenada in (None, 7, True, [], {}, "", "b3", " B3", "B3 ", "B3\n", "F1", "A0", "A6", "B10", "3B", "A１"):
            with self.subTest(coordenada=coordenada):
                with self.assertRaises(CoordenadaInvalida) as contexto:
                    coordenada_para_indices(coordenada)
                self.assertEqual(contexto.exception.codigo, "COORDENADA_INVALIDA")


class TestPosicionamento(unittest.TestCase):
    def test_cria_navios_horizontal_e_vertical_como_no_readme(self):
        tabuleiro = Tabuleiro([("A1", "B1"), ("D4", "D5")])
        self.assertEqual(tabuleiro.como_lista(), ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"])

    def test_aceita_navios_nas_bordas_e_coordenadas_em_ordem_inversa(self):
        tabuleiro = Tabuleiro([("E1", "D1"), ("A5", "A4")])
        self.assertEqual(tabuleiro.como_lista(), ["~~~NN", "~~~~~", "~~~~~", "N~~~~", "N~~~~"])

    def test_aceita_navios_encostados_sem_sobreposicao(self):
        tabuleiro = Tabuleiro([("A1", "B1"), ("A2", "B2")])
        self.assertEqual(tabuleiro.receber_disparo("A1"), "ACERTO")
        self.assertEqual(tabuleiro.receber_disparo("A2"), "ACERTO")
        self.assertEqual(tabuleiro.receber_disparo("B1"), "AFUNDOU")
        self.assertEqual(tabuleiro.receber_disparo("B2"), "AFUNDOU")

    def test_rejeita_posicionamentos_invalidos(self):
        casos = {
            "nenhum navio": [],
            "um navio": [("A1", "B1")],
            "tres navios": [("A1", "B1"), ("C2", "C3"), ("D5", "E5")],
            "uma casa": [("A1",), ("D4", "D5")],
            "tres casas": [("A1", "B1", "C1"), ("D4", "D5")],
            "diagonal": [("A1", "B2"), ("D4", "D5")],
            "casas separadas": [("A1", "C1"), ("D4", "D5")],
            "mesma casa": [("A1", "A1"), ("D4", "D5")],
            "sobreposicao": [("A1", "B1"), ("B1", "B2")],
            "fora do tabuleiro": [("E5", "F5"), ("D4", "D5")],
            "coordenada minuscula": [("a1", "B1"), ("D4", "D5")],
            "coordenada nao textual": [(None, "B1"), ("D4", "D5")],
        }
        for nome, navios in casos.items():
            with self.subTest(nome):
                with self.assertRaises(PosicionamentoInvalido):
                    Tabuleiro(navios)

    def test_alterar_entrada_nao_altera_navios(self):
        navios = [["A1", "B1"], ["D4", "D5"]]
        tabuleiro = Tabuleiro(navios)
        navios[0][0] = "C3"
        navios.clear()
        self.assertEqual(tabuleiro.receber_disparo("A1"), "ACERTO")
        self.assertEqual(tabuleiro.receber_disparo("B1"), "AFUNDOU")

    def test_sorteios_respeitam_tamanho_ocupacao_e_resultados(self):
        for semente in range(100):
            with self.subTest(semente=semente):
                tabuleiro = Tabuleiro(gerador=random.Random(semente))
                linhas = tabuleiro.como_lista()
                self.assertEqual(len(linhas), 5)
                self.assertTrue(all(len(linha) == 5 for linha in linhas))
                self.assertEqual("".join(linhas).count("N"), 4)
                self.assertEqual("".join(linhas).count("~"), 21)
                resultados = [
                    tabuleiro.receber_disparo(f"{coluna}{linha}")
                    for linha in range(1, 6) for coluna in "ABCDE"
                ]
                self.assertEqual(resultados.count("AGUA"), 21)
                self.assertEqual(resultados.count("ACERTO"), 2)
                self.assertEqual(resultados.count("AFUNDOU"), 2)
                self.assertNotIn("N", "".join(tabuleiro.como_lista()))

    def test_semente_permite_reproduzir_sorteio(self):
        primeiro = Tabuleiro(gerador=random.Random(42))
        segundo = Tabuleiro(gerador=random.Random(42))
        self.assertEqual(primeiro.como_lista(), segundo.como_lista())

    def test_construtor_sem_argumentos_sorteia_navios(self):
        tabuleiro = Tabuleiro()
        self.assertEqual("".join(tabuleiro.como_lista()).count("N"), 4)


class TestDisparos(unittest.TestCase):
    def setUp(self):
        self.tabuleiro = Tabuleiro([("A1", "B1"), ("D4", "D5")])

    def test_disparo_na_agua_marca_o(self):
        self.assertEqual(self.tabuleiro.receber_disparo("C2"), "AGUA")
        self.assertEqual(self.tabuleiro.como_lista()[1], "~~O~~")

    def test_acerto_marca_x_e_preserva_resto_do_navio(self):
        self.assertEqual(self.tabuleiro.receber_disparo("A1"), "ACERTO")
        self.assertEqual(self.tabuleiro.como_lista()[0], "XN~~~")

    def test_afunda_apenas_apos_atingir_ambas_as_casas_do_navio(self):
        for coordenada, resultado in (("D5", "ACERTO"), ("A1", "ACERTO"), ("D4", "AFUNDOU"), ("B1", "AFUNDOU")):
            self.assertEqual(self.tabuleiro.receber_disparo(coordenada), resultado)
        self.assertEqual(self.tabuleiro.como_lista(), ["XX~~~", "~~~~~", "~~~~~", "~~~X~", "~~~X~"])

    def test_disparo_repetido_na_agua_ou_navio_nao_altera_estado(self):
        for coordenada in ("C2", "A1", "B1"):
            self.tabuleiro.receber_disparo(coordenada)
            antes = self.tabuleiro.como_lista()
            with self.subTest(coordenada=coordenada):
                with self.assertRaises(DisparoRepetido) as contexto:
                    self.tabuleiro.receber_disparo(coordenada)
                self.assertEqual(contexto.exception.codigo, "DISPARO_REPETIDO")
                self.assertEqual(self.tabuleiro.como_lista(), antes)

    def test_coordenada_invalida_nao_altera_estado(self):
        antes = self.tabuleiro.como_lista()
        for coordenada in (None, [], "a1", "A6", "F5", "A1\n"):
            with self.subTest(coordenada=coordenada):
                with self.assertRaises(CoordenadaInvalida):
                    self.tabuleiro.receber_disparo(coordenada)
                self.assertEqual(self.tabuleiro.como_lista(), antes)
        self.assertEqual(self.tabuleiro.receber_disparo("A1"), "ACERTO")

    def test_tabuleiros_independentes(self):
        outro = Tabuleiro([("A1", "B1"), ("D4", "D5")])
        self.tabuleiro.receber_disparo("A1")
        self.assertEqual(outro.como_lista()[0], "NN~~~")
        self.assertEqual(outro.receber_disparo("A1"), "ACERTO")

    def test_reproduz_tabuleiro_com_disparos_do_readme(self):
        self.tabuleiro.receber_disparo("C2")
        self.tabuleiro.receber_disparo("D4")
        self.assertEqual(self.tabuleiro.como_lista(), ["NN~~~", "~~O~~", "~~~~~", "~~~X~", "~~~N~"])

    def test_lista_devolvida_nao_permite_alterar_estado_interno(self):
        linhas = self.tabuleiro.como_lista()
        linhas[0] = "~~~~~"
        linhas.append("NNNNN")
        self.assertEqual(self.tabuleiro.como_lista(), ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"])


class TestVisibilidadeEContagem(unittest.TestCase):
    def setUp(self):
        self.tabuleiro = Tabuleiro([("A1", "B1"), ("D4", "D5")])

    def test_visao_oculta_nao_revela_navios_antes_dos_disparos(self):
        self.assertEqual(self.tabuleiro.como_lista(ocultar_navios=True), ["~~~~~"] * 5)
        self.assertEqual(self.tabuleiro.como_lista()[0], "NN~~~")

    def test_visao_oculta_preserva_agua_e_acertos(self):
        self.tabuleiro.receber_disparo("C2")
        self.tabuleiro.receber_disparo("D4")
        self.assertEqual(
            self.tabuleiro.como_lista(ocultar_navios=True),
            ["~~~~~", "~~O~~", "~~~~~", "~~~X~", "~~~~~"],
        )
        self.assertEqual(self.tabuleiro.como_lista()[4], "~~~N~")

    def test_contagem_so_diminui_quando_navio_afunda(self):
        self.assertEqual(self.tabuleiro.navios_restantes, 2)
        for coordenada, quantidade in (("C2", 2), ("A1", 2), ("B1", 1),
                                       ("D5", 1), ("D4", 0)):
            self.tabuleiro.receber_disparo(coordenada)
            self.assertEqual(self.tabuleiro.navios_restantes, quantidade)

    def test_alterar_visao_oculta_nao_altera_tabuleiro(self):
        visao = self.tabuleiro.como_lista(ocultar_navios=True)
        visao[0] = "XXXXX"
        self.assertEqual(self.tabuleiro.como_lista(ocultar_navios=True), ["~~~~~"] * 5)
        self.assertEqual(self.tabuleiro.navios_restantes, 2)


if __name__ == "__main__":
    unittest.main()
