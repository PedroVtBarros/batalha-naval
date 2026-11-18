"""Testes do cliente gráfico (cliente_gui.py).

Três níveis:
    - ModeloCliente: mensagens do servidor -> estado, sem rede nem janela;
    - ConexaoServidor + ModeloCliente contra um Servidor real (sem janela);
    - duas janelas InterfaceGrafica reais jogando uma partida completa por cliques.
      São puladas se não houver tela disponível (tkinter.TclError).

Executar a partir da raiz do projeto:
    python -m unittest discover -s tests -p 'test_cliente_gui.py' -v
"""

import queue
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cliente_gui import (  # noqa: E402
    CONECTADO,
    ENCERRADO,
    MINHA_VEZ,
    VEZ_ADVERSARIO,
    ConexaoServidor,
    InterfaceGrafica,
    ModeloCliente,
)
from servidor import Servidor  # noqa: E402
from tabuleiro import Tabuleiro  # noqa: E402

NAVIOS_J1 = [("A1", "B1"), ("D4", "D5")]
NAVIOS_J2 = [("D1", "E1"), ("B3", "B4")]
VAZIO = ["~~~~~"] * 5
# Jogadas da partida da seção 10 em que o jogador 1 vence.
JOGADAS_VITORIA_J1 = [(1, "D1"), (2, "C1"), (1, "E1"), (2, "C2"), (1, "B3"), (2, "C3"), (1, "B4")]


def inicio(jogador, sua_vez):
    return {
        "tipo": "INICIO", "jogador": jogador, "sua_vez": sua_vez,
        "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
        "tabuleiro_adversario": VAZIO,
    }


def atualizacao(atirador, coordenada, resultado, sua_vez, adversario=VAZIO, restantes=(2, 2)):
    return {
        "tipo": "ATUALIZACAO", "atirador": atirador, "coordenada": coordenada,
        "resultado": resultado, "sua_vez": sua_vez,
        "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
        "tabuleiro_adversario": adversario,
        "navios_restantes": {"proprio": restantes[0], "adversario": restantes[1]},
    }


class TestModelo(unittest.TestCase):
    def test_aguarde_nao_muda_o_estado(self):
        modelo = ModeloCliente()
        texto = modelo.processar({"tipo": "AGUARDE", "mensagem": "Aguardando..."})
        self.assertEqual(texto, "Aguardando...")
        self.assertEqual(modelo.estado, CONECTADO)
        self.assertEqual(modelo.status(), "Aguardando o segundo jogador...")

    def test_inicio_define_jogador_tabuleiros_e_vez(self):
        for jogador, sua_vez, estado in ((1, True, MINHA_VEZ), (2, False, VEZ_ADVERSARIO)):
            with self.subTest(jogador=jogador):
                modelo = ModeloCliente()
                modelo.processar(inicio(jogador, sua_vez))
                self.assertEqual((modelo.jogador, modelo.estado), (jogador, estado))
                self.assertEqual(modelo.tabuleiro_proprio[0], "NN~~~")

    def test_atualizacao_troca_a_vez_e_libera_novo_disparo(self):
        modelo = ModeloCliente()
        modelo.processar(inicio(1, True))
        modelo.aguardando_resposta = True
        texto = modelo.processar(atualizacao(1, "B3", "ACERTO", False, ["~~~~~", "~~~~~", "~X~~~", "~~~~~", "~~~~~"]))
        self.assertEqual(texto, "Você atirou em B3: acertou um navio")
        self.assertEqual(modelo.estado, VEZ_ADVERSARIO)
        self.assertFalse(modelo.aguardando_resposta)
        self.assertEqual(modelo.ultimo_disparo, (1, "B3"))
        texto = modelo.processar(atualizacao(2, "C1", "AGUA", True))
        self.assertEqual(texto, "O adversário atirou em C1: água")
        self.assertEqual(modelo.estado, MINHA_VEZ)

    def test_erro_mantem_o_estado_e_libera_novo_disparo(self):
        modelo = ModeloCliente()
        modelo.processar(inicio(1, True))
        modelo.aguardando_resposta = True
        texto = modelo.processar({"tipo": "ERRO", "codigo": "DISPARO_REPETIDO", "mensagem": "Você já atirou em B3."})
        self.assertEqual(texto, "Erro: Você já atirou em B3.")
        self.assertEqual(modelo.estado, MINHA_VEZ)
        self.assertFalse(modelo.aguardando_resposta)

    def test_servidor_cheio_encerra(self):
        modelo = ModeloCliente()
        modelo.processar({"tipo": "ERRO", "codigo": "SERVIDOR_CHEIO", "mensagem": "Cheio."})
        self.assertEqual((modelo.estado, modelo.desfecho), (ENCERRADO, "interrompida"))

    def test_fim_vitoria_derrota_e_interrompida(self):
        casos = [
            ({"motivo": "VITORIA", "vencedor": 1}, "vitoria", "Você venceu!"),
            ({"motivo": "VITORIA", "vencedor": 2}, "derrota", "Você perdeu."),
            ({"motivo": "ADVERSARIO_SAIU", "vencedor": None}, "interrompida", "Partida encerrada."),
        ]
        for campos, desfecho, status in casos:
            with self.subTest(desfecho=desfecho):
                modelo = ModeloCliente()
                modelo.processar(inicio(1, True))
                modelo.processar({"tipo": "FIM", "mensagem": "texto", **campos})
                self.assertEqual((modelo.estado, modelo.desfecho, modelo.status()), (ENCERRADO, desfecho, status))
                self.assertIsNone(modelo.conexao_perdida())  # queda depois do FIM não é aviso

    def test_conexao_perdida_durante_a_partida(self):
        modelo = ModeloCliente()
        modelo.processar(inicio(2, False))
        self.assertEqual(modelo.conexao_perdida(), "A conexão com o servidor foi perdida.")
        self.assertEqual(modelo.estado, ENCERRADO)

    def test_pode_disparar(self):
        modelo = ModeloCliente()
        self.assertFalse(modelo.pode_disparar("A1")[0])  # partida não começou
        modelo.processar(inicio(1, True))
        self.assertEqual(modelo.pode_disparar("A1"), (True, ""))
        self.assertEqual(modelo.pode_disparar("Z9"), (False, "Coordenada inválida. Use de A1 a E5."))
        modelo.processar(atualizacao(1, "B3", "ACERTO", True, ["~~~~~", "~~~~~", "~X~~~", "~~~~~", "~~~~~"]))
        self.assertEqual(modelo.pode_disparar("B3"), (False, "Você já atirou em B3."))
        modelo.aguardando_resposta = True
        self.assertFalse(modelo.pode_disparar("C3")[0])
        modelo.processar(atualizacao(1, "C3", "AGUA", False))
        self.assertEqual(modelo.pode_disparar("D3"), (False, "Espere a sua vez."))

    def test_tipo_desconhecido_nao_quebra(self):
        modelo = ModeloCliente()
        self.assertIn("desconhecida", modelo.processar({"tipo": "PING"}))
        self.assertEqual(modelo.estado, CONECTADO)


class BaseComServidor(unittest.TestCase):
    def setUp(self):
        navios = iter([NAVIOS_J1, NAVIOS_J2] * 10)
        self.servidor = Servidor("127.0.0.1", 0, criar_tabuleiro=lambda: Tabuleiro(next(navios)))
        _, self.porta = self.servidor.iniciar()
        self.thread = threading.Thread(target=self.servidor.servir_para_sempre, daemon=True)
        self.thread.start()
        self.addCleanup(self._encerrar)

    def _encerrar(self):
        self.servidor.encerrar()
        self.thread.join(5)


class JogadorSemJanela:
    """ConexaoServidor + ModeloCliente, exatamente como a janela os usa."""

    def __init__(self, porta):
        self.eventos = queue.Queue()
        self.conexao = ConexaoServidor.conectar("127.0.0.1", porta, self.eventos)
        self.modelo = ModeloCliente()
        self.conexao.iniciar_leitura()

    def proximo(self):
        origem, tipo, dado = self.eventos.get(timeout=5)
        assert origem is self.conexao
        if tipo == "mensagem":
            self.modelo.processar(dado)
        elif tipo == "encerrada":
            self.modelo.conexao_perdida()
        return tipo, dado


class TestConexaoComServidorReal(BaseComServidor):
    def test_partida_completa(self):
        j1 = JogadorSemJanela(self.porta)
        self.assertEqual(j1.proximo()[1]["tipo"], "AGUARDE")
        j2 = JogadorSemJanela(self.porta)
        j1.proximo(), j2.proximo()
        self.assertEqual((j1.modelo.estado, j2.modelo.estado), (MINHA_VEZ, VEZ_ADVERSARIO))
        jogadores = {1: j1, 2: j2}
        for atirador, coordenada in JOGADAS_VITORIA_J1:
            self.assertTrue(jogadores[atirador].modelo.pode_disparar(coordenada)[0])
            self.assertTrue(jogadores[atirador].conexao.disparar(coordenada))
            for jogador in (j1, j2):
                self.assertEqual(jogador.proximo()[1]["tipo"], "ATUALIZACAO")
        for jogador in (j1, j2):
            self.assertEqual(jogador.proximo()[1]["tipo"], "FIM")
            self.assertEqual(jogador.proximo()[0], "encerrada")
        self.assertEqual((j1.modelo.desfecho, j2.modelo.desfecho), ("vitoria", "derrota"))
        self.assertEqual(j1.modelo.navios_restantes, {"proprio": 2, "adversario": 0})

    def test_sair_avisa_o_adversario(self):
        j1 = JogadorSemJanela(self.porta)
        j1.proximo()
        j2 = JogadorSemJanela(self.porta)
        j1.proximo(), j2.proximo()
        j2.conexao.sair()
        self.assertEqual(j1.proximo()[1]["motivo"], "ADVERSARIO_SAIU")
        self.assertEqual(j1.modelo.desfecho, "interrompida")

    def test_enviar_depois_de_fechar_devolve_false(self):
        j1 = JogadorSemJanela(self.porta)
        j1.proximo()
        j1.conexao.fechar()
        self.assertFalse(j1.conexao.disparar("A1"))
        self.assertEqual(j1.proximo()[0], "encerrada")


class TestJanelas(BaseComServidor):
    """Duas janelas reais, numa só thread do Tk, jogando por cliques."""

    def setUp(self):
        try:
            import tkinter as tk

            self.raiz = tk.Tk()
        except Exception as erro:  # sem tela (ex.: servidor de CI)
            self.skipTest(f"tkinter indisponível: {erro}")
        self.raiz.withdraw()
        self.addCleanup(self.raiz.destroy)
        super().setUp()
        import tkinter as tk

        self.janelas = [InterfaceGrafica(tk.Toplevel(self.raiz), "127.0.0.1", self.porta) for _ in range(2)]
        for janela in self.janelas:
            janela.raiz.withdraw()

    def esperar(self, condicao, descricao):
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            for janela in self.janelas:
                janela.processar_eventos()
            self.raiz.update()
            if condicao():
                return
            time.sleep(0.01)
        estados = [(j.modelo.estado, j.conexao is not None, j.rotulo_status.cget("text"),
                    j.registro.get("1.0", "end").strip().splitlines()[-3:]) for j in self.janelas]
        self.fail(f"tempo esgotado esperando: {descricao}; janelas: {estados}")

    def texto_registro(self, janela):
        return janela.registro.get("1.0", "end")

    def test_partida_completa_por_cliques(self):
        g1, g2 = self.janelas
        g1.conectar()
        self.esperar(lambda: g1.modelo.recebeu_aguarde, "AGUARDE na janela 1")
        self.assertEqual(g1.rotulo_status.cget("text"), "Aguardando o segundo jogador...")
        g2.conectar()
        self.esperar(lambda: g1.modelo.estado == MINHA_VEZ and g2.modelo.estado == VEZ_ADVERSARIO, "INICIO")
        self.assertEqual(g1.rotulo_status.cget("text"), "Sua vez! Clique no tabuleiro do adversário.")
        self.assertEqual(g1.celulas_proprio["A1"].cget("bg"), "#8d99ae")  # navio próprio em cinza
        self.assertEqual(str(g1.celulas_adversario["B3"].cget("cursor")), "hand2")

        g2.clicar("A1")  # fora da vez: o cliente nem envia
        self.assertIn("Espere a sua vez.", self.texto_registro(g2))

        janelas = {1: g1, 2: g2}
        for numero, (atirador, coordenada) in enumerate(JOGADAS_VITORIA_J1, start=1):
            janelas[atirador].clicar(coordenada)
            self.esperar(
                lambda n=numero: all(
                    j.modelo.ultimo_disparo == (atirador, coordenada) for j in janelas.values()
                ) or g1.modelo.estado == ENCERRADO,
                f"ATUALIZACAO do disparo {coordenada}",
            )
        self.esperar(lambda: g1.modelo.estado == ENCERRADO and g2.modelo.estado == ENCERRADO, "FIM")
        self.assertEqual(g1.rotulo_status.cget("text"), "Você venceu!")
        self.assertEqual(g2.rotulo_status.cget("text"), "Você perdeu.")
        self.assertEqual(g1.celulas_adversario["B3"].cget("text"), "✕")
        self.assertEqual(g1.celulas_proprio["C1"].cget("bg"), "#5dade2")  # tiro do jogador 2 na água
        self.assertEqual(g1.celulas_proprio["A1"].cget("bg"), "#8d99ae")  # navio intacto continua cinza
        self.assertIsNone(g1.conexao)
        self.assertIn("!disabled", g1.botao_nova.state() or ["!disabled"])

    def test_digitar_coordenada_normaliza(self):
        g1, g2 = self.janelas
        g1.conectar()
        self.esperar(lambda: g1.modelo.recebeu_aguarde, "AGUARDE na janela 1")
        g2.conectar()
        self.esperar(lambda: g1.modelo.estado == MINHA_VEZ, "INICIO")
        g1.campo_coordenada.insert(0, " b3 ")
        g1.disparar_digitado()
        self.esperar(lambda: g2.modelo.ultimo_disparo == (1, "B3"), "ATUALIZACAO de B3")
        self.assertIn("Você atirou em B3: acertou um navio", self.texto_registro(g1))

    def test_fechar_a_janela_avisa_o_adversario(self):
        g1, g2 = self.janelas
        g1.conectar()
        self.esperar(lambda: g1.modelo.recebeu_aguarde, "AGUARDE na janela 1")
        g2.conectar()
        self.esperar(lambda: g2.modelo.estado == VEZ_ADVERSARIO, "INICIO")
        self.janelas = [g2]
        g1.fechar_janela()
        self.esperar(lambda: g2.modelo.estado == ENCERRADO, "FIM ADVERSARIO_SAIU")
        self.assertIn("O adversário saiu da partida.", self.texto_registro(g2))

    def test_porta_invalida_e_servidor_inexistente(self):
        g1 = self.janelas[0]
        g1.campo_porta.delete(0, "end")
        g1.campo_porta.insert(0, "abc")
        g1.conectar()
        self.assertIn("A porta deve ser um número", self.texto_registro(g1))
        g1.campo_porta.delete(0, "end")
        g1.campo_porta.insert(0, "1")  # porta onde ninguém escuta
        g1.conectar()
        self.esperar(lambda: "Falha ao conectar" in self.texto_registro(g1), "falha de conexão")
        self.assertEqual(g1.rotulo_status.cget("text"), "Não foi possível conectar.")


if __name__ == "__main__":
    unittest.main()
