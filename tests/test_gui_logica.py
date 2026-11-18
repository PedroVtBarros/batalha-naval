"""Testes do nucleo_gui.py (lógica da interface gráfica, sem Tkinter).

Cobrem a validação do destino da conexão (IP, domínio, túnel), as mensagens de erro de
conexão, o ModeloPartida alimentado com as mensagens reais do protocolo (as da seção 10
do readme) e a Sessao de rede contra o servidor real.
"""

from __future__ import annotations

import itertools
import queue
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import nucleo_gui  # noqa: E402
from nucleo_gui import (  # noqa: E402
    ACERTO,
    AFUNDOU,
    AGUA,
    AGUARDANDO,
    CONECTADO,
    DESCONHECIDA,
    ENCERRADA,
    EM_ANDAMENTO,
    NAVIO,
    SEM_DISPARO,
    ModeloPartida,
    Sessao,
    indices_da_casa,
    interpretar_destino,
    nome_da_casa,
    traduzir_erro_conexao,
)
from protocolo import enviar_mensagem, receber_mensagem  # noqa: E402
from servidor import SEM_JOGADORES, Servidor  # noqa: E402
from tabuleiro import Tabuleiro  # noqa: E402

PRAZO = 5.0
VAZIO = ["~~~~~"] * 5

INICIO_J1 = {
    "tipo": "INICIO", "jogador": 1, "sua_vez": True,
    "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
    "tabuleiro_adversario": VAZIO,
}
INICIO_J2 = {
    "tipo": "INICIO", "jogador": 2, "sua_vez": False,
    "tabuleiro_proprio": ["~~~NN", "~~~~~", "~N~~~", "~N~~~", "~~~~~"],
    "tabuleiro_adversario": VAZIO,
}
# Seção 10 do readme: o jogador 1 atira em B3 e acerta (versões de cada jogador).
ATUALIZACAO_J1 = {
    "tipo": "ATUALIZACAO", "atirador": 1, "coordenada": "B3", "resultado": "ACERTO",
    "sua_vez": False,
    "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
    "tabuleiro_adversario": ["~~~~~", "~~~~~", "~X~~~", "~~~~~", "~~~~~"],
    "navios_restantes": {"proprio": 2, "adversario": 2},
}
ATUALIZACAO_J2 = {
    "tipo": "ATUALIZACAO", "atirador": 1, "coordenada": "B3", "resultado": "ACERTO",
    "sua_vez": True,
    "tabuleiro_proprio": ["~~~NN", "~~~~~", "~X~~~", "~N~~~", "~~~~~"],
    "tabuleiro_adversario": VAZIO,
    "navios_restantes": {"proprio": 2, "adversario": 2},
}


class TestCoordenadas(unittest.TestCase):
    def test_letra_e_coluna_e_numero_e_linha(self) -> None:
        self.assertEqual(indices_da_casa("B3"), (2, 1))  # linha 3, coluna B
        self.assertEqual(nome_da_casa(2, 1), "B3")
        self.assertEqual(nome_da_casa(0, 0), "A1")
        self.assertEqual(nome_da_casa(4, 4), "E5")

    def test_ida_e_volta_das_25_casas(self) -> None:
        for linha, coluna in itertools.product(range(5), repeat=2):
            self.assertEqual(indices_da_casa(nome_da_casa(linha, coluna)), (linha, coluna))

    def test_coordenada_invalida(self) -> None:
        for ruim in ("F1", "A6", "a1", "B", "B10", "", None, 12):
            with self.assertRaises(ValueError):
                indices_da_casa(ruim)  # type: ignore[arg-type]


class TestInterpretarDestino(unittest.TestCase):
    def test_ip_e_porta(self) -> None:
        self.assertEqual(interpretar_destino("192.168.0.10", "5050"), ("192.168.0.10", 5050))

    def test_dominio_com_espacos_ao_redor(self) -> None:
        self.assertEqual(interpretar_destino("  jogo.exemplo.com  ", " 6000 "), ("jogo.exemplo.com", 6000))

    def test_endereco_de_tunel_com_prefixo_e_porta_embutida(self) -> None:
        self.assertEqual(
            interpretar_destino("tcp://abcde-1-2-3-4.a.free.pinggy.link:43125", "5050"),
            ("abcde-1-2-3-4.a.free.pinggy.link", 43125),
        )

    def test_dominio_com_porta_embutida_sem_prefixo(self) -> None:
        self.assertEqual(interpretar_destino("x.a.free.pinggy.link:9999", "5050"), ("x.a.free.pinggy.link", 9999))

    def test_ipv6(self) -> None:
        self.assertEqual(interpretar_destino("::1", "5050"), ("::1", 5050))
        self.assertEqual(interpretar_destino("[::1]:6000", "5050"), ("::1", 6000))

    def test_endereco_vazio(self) -> None:
        for vazio in ("", "   ", "tcp://"):
            with self.assertRaises(ValueError) as contexto:
                interpretar_destino(vazio, "5050")
            self.assertIn("endereço", str(contexto.exception))

    def test_porta_invalida(self) -> None:
        for ruim in ("", "abc", "0", "65536", "-1", "50.5", "٣"):
            with self.assertRaises(ValueError, msg=ruim) as contexto:
                interpretar_destino("127.0.0.1", ruim)
            self.assertIn("orta", str(contexto.exception))

    def test_porta_embutida_invalida(self) -> None:
        with self.assertRaises(ValueError):
            interpretar_destino("servidor:abc", "5050")
        with self.assertRaises(ValueError):
            interpretar_destino("servidor:70000", "5050")

    def test_endereco_com_espaco_ou_barra(self) -> None:
        for ruim in ("meu servidor", "http://servidor/jogo", "servidor/x"):
            with self.assertRaises(ValueError, msg=ruim):
                interpretar_destino(ruim, "5050")


class TestTraduzirErro(unittest.TestCase):
    def test_mensagens_por_tipo_de_falha(self) -> None:
        casos = [
            (socket.gaierror(11001, "x"), "encontrar o servidor"),
            (TimeoutError("t"), "Tempo esgotado"),
            (ConnectionRefusedError(10061, "recusada"), "recusada"),
            (UnicodeError("label too long"), "não é um nome"),
            (OSError(101, "Rede inacessível"), "Rede inacessível"),
        ]
        for erro, trecho in casos:
            texto = traduzir_erro_conexao(erro, "h.exemplo", 5050)
            self.assertIn(trecho, texto)
        self.assertIn("h.exemplo:5050", traduzir_erro_conexao(TimeoutError(), "h.exemplo", 5050))


class TestModeloPartida(unittest.TestCase):
    def modelo_em_jogo(self, inicio: dict = INICIO_J1) -> ModeloPartida:
        modelo = ModeloPartida()
        modelo.aplicar(inicio)
        return modelo

    def test_estado_inicial_antes_de_qualquer_mensagem(self) -> None:
        modelo = ModeloPartida()
        self.assertEqual(modelo.fase, CONECTADO)
        self.assertFalse(modelo.pode_disparar("B3"))

    def test_aguarde(self) -> None:
        modelo = ModeloPartida()
        modelo.aplicar({"tipo": "AGUARDE", "mensagem": "Conectado. Aguardando o segundo jogador..."})
        self.assertEqual(modelo.fase, AGUARDANDO)
        self.assertEqual(modelo.situacao, ("Aguardando outro jogador...", "espera"))
        self.assertFalse(modelo.pode_disparar("B3"))  # sem partida não há cliques

    def test_inicio_do_jogador_1_e_do_jogador_2(self) -> None:
        j1 = self.modelo_em_jogo(INICIO_J1)
        self.assertEqual((j1.fase, j1.jogador, j1.sua_vez), (EM_ANDAMENTO, 1, True))
        self.assertEqual(j1.situacao[1], "minha")
        j2 = self.modelo_em_jogo(INICIO_J2)
        self.assertEqual((j2.jogador, j2.sua_vez), (2, False))
        self.assertEqual(j2.situacao[1], "adversario")

    def test_so_pode_clicar_na_minha_vez(self) -> None:
        self.assertTrue(self.modelo_em_jogo(INICIO_J1).pode_disparar("B3"))
        self.assertFalse(self.modelo_em_jogo(INICIO_J2).pode_disparar("B3"))

    def test_tipos_de_casa_no_inicio(self) -> None:
        modelo = self.modelo_em_jogo()
        self.assertEqual(modelo.tipo_casa(True, 0, 0), NAVIO)        # A1 é meu navio
        self.assertEqual(modelo.tipo_casa(True, 0, 2), SEM_DISPARO)  # C1 é água
        self.assertEqual(modelo.tipo_casa(False, 2, 1), DESCONHECIDA)

    def test_disparo_bloqueia_ate_a_resposta(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.registrar_disparo("B3")
        self.assertEqual(modelo.pendente, "B3")
        self.assertFalse(modelo.pode_disparar("C3"))  # outro clique é ignorado
        self.assertEqual(modelo.situacao[1], "espera")
        with self.assertRaises(ValueError):
            modelo.registrar_disparo("C3")
        modelo.aplicar(ATUALIZACAO_J1)
        self.assertIsNone(modelo.pendente)
        self.assertFalse(modelo.sua_vez)  # a vez passou ao adversário

    def test_atualizacao_do_atirador(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.registrar_disparo("B3")
        modelo.aplicar(ATUALIZACAO_J1)
        self.assertEqual(modelo.tipo_casa(False, 2, 1), ACERTO)
        self.assertIn("Você atirou em B3 e acertou um navio", modelo.ultima_jogada)
        self.assertEqual((modelo.navios_proprio, modelo.navios_adversario), (2, 2))
        self.assertFalse(modelo.pode_disparar("C3"))  # não é mais a minha vez
        self.assertFalse(modelo.pode_disparar("B3"))  # nem em casa já atacada

    def test_atualizacao_do_alvo(self) -> None:
        modelo = self.modelo_em_jogo(INICIO_J2)
        modelo.aplicar(ATUALIZACAO_J2)
        self.assertEqual(modelo.tipo_casa(True, 2, 1), ACERTO)  # B3 no meu tabuleiro
        self.assertEqual(modelo.tipo_casa(True, 3, 1), NAVIO)   # B4 ainda intacto
        self.assertIn("O adversário atirou em B3 e acertou um dos seus navios", modelo.ultima_jogada)
        self.assertTrue(modelo.sua_vez)
        self.assertTrue(modelo.pode_disparar("A1"))

    def test_casa_ja_atacada_nao_e_clicavel(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.registrar_disparo("B3")
        modelo.aplicar(ATUALIZACAO_J1)
        modelo.aplicar({**ATUALIZACAO_J1, "atirador": 2, "coordenada": "C1", "sua_vez": True,
                        "tabuleiro_proprio": ["NNO~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
                        "resultado": "AGUA"})
        self.assertTrue(modelo.sua_vez)
        self.assertFalse(modelo.pode_disparar("B3"))  # X no tabuleiro do adversário
        self.assertTrue(modelo.pode_disparar("B4"))

    def test_afundou_marca_a_casa_do_golpe(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({**ATUALIZACAO_J1, "coordenada": "B4", "resultado": "AFUNDOU",
                        "tabuleiro_adversario": ["~~~~~", "~~~~~", "~X~~~", "~X~~~", "~~~~~"],
                        "navios_restantes": {"proprio": 2, "adversario": 1}})
        self.assertEqual(modelo.tipo_casa(False, 3, 1), AFUNDOU)   # B4: golpe final
        self.assertEqual(modelo.tipo_casa(False, 2, 1), ACERTO)    # B3: o protocolo não diz que é do mesmo navio
        self.assertEqual(modelo.navios_adversario, 1)
        self.assertIn("afundou um navio", modelo.ultima_jogada)

    def test_afundou_um_navio_meu(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({**ATUALIZACAO_J1, "atirador": 2, "coordenada": "B1", "resultado": "AFUNDOU",
                        "sua_vez": True,
                        "tabuleiro_proprio": ["XX~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
                        "navios_restantes": {"proprio": 1, "adversario": 2}})
        self.assertEqual(modelo.tipo_casa(True, 0, 1), AFUNDOU)
        self.assertEqual(modelo.tipo_casa(True, 0, 0), ACERTO)
        self.assertEqual(modelo.navios_proprio, 1)
        self.assertIn("afundou um dos seus navios", modelo.ultima_jogada)

    def test_agua(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({**ATUALIZACAO_J1, "coordenada": "C3", "resultado": "AGUA",
                        "tabuleiro_adversario": ["~~~~~", "~~~~~", "~~O~~", "~~~~~", "~~~~~"]})
        self.assertEqual(modelo.tipo_casa(False, 2, 2), AGUA)
        self.assertIn("água", modelo.ultima_jogada)

    def test_erro_libera_o_bloqueio_e_mantem_a_vez(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.registrar_disparo("B3")
        modelo.aplicar({"tipo": "ERRO", "codigo": "DISPARO_REPETIDO", "mensagem": "Você já atirou em B3."})
        self.assertIsNone(modelo.pendente)
        self.assertTrue(modelo.sua_vez)
        self.assertEqual(modelo.fase, EM_ANDAMENTO)
        self.assertEqual((modelo.aviso, modelo.aviso_e_erro), ("Você já atirou em B3.", True))
        self.assertTrue(modelo.pode_disparar("C3"))  # a interface continua utilizável

    def test_erro_sem_campos_nao_derruba(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({"tipo": "ERRO"})
        self.assertTrue(modelo.aviso)
        self.assertEqual(modelo.fase, EM_ANDAMENTO)

    def test_servidor_cheio_encerra(self) -> None:
        modelo = ModeloPartida()
        modelo.aplicar({"tipo": "ERRO", "codigo": "SERVIDOR_CHEIO", "mensagem": "Já existe uma partida em andamento."})
        self.assertEqual(modelo.fase, ENCERRADA)
        self.assertEqual(modelo.desfecho.tipo, "erro")
        self.assertIn("partida em andamento", modelo.desfecho.texto)

    def test_fim_vitoria_e_derrota(self) -> None:
        for vencedor, esperado in ((1, "vitoria"), (2, "derrota")):
            modelo = self.modelo_em_jogo()
            modelo.aplicar({"tipo": "FIM", "motivo": "VITORIA", "vencedor": vencedor, "mensagem": "x"})
            self.assertEqual((modelo.fase, modelo.desfecho.tipo, modelo.sua_vez), (ENCERRADA, esperado, False))
            self.assertEqual(modelo.situacao[1], esperado)
            self.assertFalse(modelo.pode_disparar("B3"))

    def test_fim_por_saida_e_desconexao_do_adversario(self) -> None:
        saiu = self.modelo_em_jogo()
        saiu.aplicar({"tipo": "FIM", "motivo": "ADVERSARIO_SAIU", "vencedor": None, "mensagem": "O adversário saiu da partida."})
        self.assertEqual((saiu.desfecho.tipo, saiu.desfecho.titulo), ("interrompida", "O adversário saiu"))
        caiu = self.modelo_em_jogo()
        caiu.aplicar({"tipo": "FIM", "motivo": "ADVERSARIO_DESCONECTOU", "vencedor": None, "mensagem": "A conexão do adversário caiu."})
        self.assertEqual(caiu.desfecho.titulo, "O adversário desconectou")

    def test_fim_com_motivo_desconhecido_usa_o_texto_do_servidor(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({"tipo": "FIM", "motivo": "NOVO", "vencedor": None, "mensagem": "Texto do servidor."})
        self.assertEqual(modelo.desfecho.texto, "Texto do servidor.")

    def test_mensagens_depois_do_fim_sao_ignoradas(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({"tipo": "FIM", "motivo": "ADVERSARIO_SAIU", "vencedor": None, "mensagem": ""})
        modelo.aplicar(ATUALIZACAO_J2)
        modelo.aplicar(INICIO_J2)
        self.assertEqual((modelo.fase, modelo.jogador), (ENCERRADA, 1))

    def test_conexao_perdida(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.registrar_disparo("B3")
        modelo.conexao_perdida()
        self.assertEqual((modelo.fase, modelo.desfecho.titulo), (ENCERRADA, "Conexão perdida"))
        self.assertIsNone(modelo.pendente)
        antes_do_fim = ModeloPartida()
        antes_do_fim.conexao_perdida()
        self.assertIn("sem iniciar a partida", antes_do_fim.desfecho.texto)

    def test_conexao_perdida_depois_do_fim_nao_muda_o_desfecho(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({"tipo": "FIM", "motivo": "VITORIA", "vencedor": 1, "mensagem": ""})
        modelo.conexao_perdida()
        self.assertEqual(modelo.desfecho.tipo, "vitoria")

    def test_mensagens_malformadas_levantam_valueerror(self) -> None:
        ruins = [
            {"tipo": "INICIO", "jogador": 3, "sua_vez": True, "tabuleiro_proprio": VAZIO, "tabuleiro_adversario": VAZIO},
            {**INICIO_J1, "sua_vez": "sim"},
            {**INICIO_J1, "tabuleiro_proprio": ["~~~~"] * 5},
            {**INICIO_J1, "tabuleiro_adversario": ["~~~~Z"] * 5},
            {**INICIO_J1, "tabuleiro_proprio": "NN~~~"},
        ]
        for mensagem in ruins:
            with self.assertRaises(ValueError, msg=mensagem):
                ModeloPartida().aplicar(mensagem)
        modelo = self.modelo_em_jogo()
        for mensagem in ({**ATUALIZACAO_J1, "coordenada": "Z9"}, {**ATUALIZACAO_J1, "resultado": "?"},
                         {**ATUALIZACAO_J1, "navios_restantes": []}):
            with self.assertRaises(ValueError, msg=mensagem):
                modelo.aplicar(mensagem)
        with self.assertRaises(KeyError):  # campo ausente: a interface trata como falha de protocolo
            modelo.aplicar({"tipo": "ATUALIZACAO"})
        with self.assertRaises(ValueError):  # ATUALIZACAO sem INICIO
            ModeloPartida().aplicar(ATUALIZACAO_J1)

    def test_tipo_desconhecido_e_ignorado(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({"tipo": "OUTRA_COISA", "x": 1})
        self.assertEqual(modelo.fase, EM_ANDAMENTO)

    def test_novo_inicio_zera_marcas_da_partida_anterior(self) -> None:
        modelo = self.modelo_em_jogo()
        modelo.aplicar({**ATUALIZACAO_J1, "resultado": "AFUNDOU"})
        self.assertTrue(modelo.afundou_adversario)
        modelo.aplicar(INICIO_J1)
        self.assertEqual((modelo.afundou_adversario, modelo.afundou_proprio, modelo.pendente), (set(), set(), None))


class SessaoBase(unittest.TestCase):
    """Servidor real em 127.0.0.1 e uma fila de eventos compartilhada."""

    def setUp(self) -> None:
        navios = itertools.cycle([[("A1", "B1"), ("D4", "D5")], [("D1", "E1"), ("B3", "B4")]])
        self.servidor = Servidor("127.0.0.1", 0, criar_tabuleiro=lambda: Tabuleiro(next(navios)))
        _, self.porta = self.servidor.iniciar()
        self.thread = threading.Thread(target=self.servidor.servir_para_sempre, daemon=True)
        self.thread.start()
        self.eventos: "queue.Queue" = queue.Queue()
        self.sessoes: list[Sessao] = []
        self.sockets: list[socket.socket] = []
        self.addCleanup(self._limpar)

    def _limpar(self) -> None:
        for sessao in self.sessoes:
            sessao.encerrar()
        for sock in self.sockets:
            sock.close()
        self.servidor.encerrar()
        self.thread.join(PRAZO)

    def nova_sessao(self, geracao: int = 1, porta: int | None = None) -> Sessao:
        sessao = Sessao("127.0.0.1", porta or self.porta, self.eventos, geracao)
        self.sessoes.append(sessao)
        sessao.iniciar()
        return sessao

    def proximo(self, tipo: str | None = None, geracao: int | None = None):
        """Próximo evento (opcionalmente de um tipo); falha se passar o prazo."""
        limite = time.monotonic() + PRAZO
        while True:
            try:
                g, t, dados = self.eventos.get(timeout=max(0.01, limite - time.monotonic()))
            except queue.Empty:
                self.fail(f"nenhum evento {tipo!r} em {PRAZO}s")
            if (tipo is None or t == tipo) and (geracao is None or g == geracao):
                return g, t, dados
            if time.monotonic() > limite:
                self.fail(f"nenhum evento {tipo!r} em {PRAZO}s")

    def esperar_estado(self, esperado: str) -> None:
        limite = time.monotonic() + PRAZO
        while time.monotonic() < limite:
            if self.servidor.estado == esperado:
                return
            time.sleep(0.01)
        self.fail(f"estado {self.servidor.estado!r}, esperado {esperado!r}")

    def jogador_raw(self) -> tuple[socket.socket, object]:
        sock = socket.create_connection(("127.0.0.1", self.porta), timeout=PRAZO)
        self.sockets.append(sock)
        return sock, sock.makefile("rb")


class TestSessao(SessaoBase):
    def test_conecta_e_recebe_aguarde_com_a_geracao_certa(self) -> None:
        self.nova_sessao(geracao=7)
        self.assertEqual(self.proximo("conectado")[0], 7)
        g, _, mensagem = self.proximo("mensagem")
        self.assertEqual((g, mensagem["tipo"]), (7, "AGUARDE"))

    def test_enviar_antes_de_conectar_devolve_false(self) -> None:
        sessao = Sessao("127.0.0.1", self.porta, self.eventos, 1)
        self.assertFalse(sessao.enviar({"tipo": "SAIR"}))

    def test_porta_sem_servidor_vira_falha_de_conexao_compreensivel(self) -> None:
        livre = socket.socket()
        livre.bind(("127.0.0.1", 0))
        porta_fechada = livre.getsockname()[1]
        livre.close()
        self.nova_sessao(porta=porta_fechada)
        _, _, texto = self.proximo("falha_conexao")
        self.assertIn("127.0.0.1", texto)
        self.assertTrue("recusada" in texto or "Tempo esgotado" in texto or "Não foi possível" in texto, texto)

    def test_dominio_inexistente_vira_falha_de_conexao(self) -> None:
        with patch("nucleo_gui.socket.create_connection", side_effect=socket.gaierror(11001, "nome desconhecido")):
            sessao = Sessao("nao-existe.exemplo", 5050, self.eventos, 3)
            sessao.iniciar()
            g, _, texto = self.proximo("falha_conexao")
        self.assertEqual(g, 3)
        self.assertIn("nao-existe.exemplo", texto)

    def test_cancelar_enquanto_conecta_nao_gera_eventos_de_jogo_e_fecha_o_socket(self) -> None:
        a, b = socket.socketpair()  # b faz o papel do servidor
        self.addCleanup(b.close)
        liberar = threading.Event()

        def conexao_lenta(*_args, **_kwargs):
            liberar.wait(PRAZO)
            return a

        with patch("nucleo_gui.socket.create_connection", side_effect=conexao_lenta):
            sessao = Sessao("lento.exemplo", 5050, self.eventos, 1)
            sessao.iniciar()
            sessao.encerrar()  # cancelado antes de terminar de conectar
            liberar.set()
            sessao._thread.join(PRAZO)
        self.assertFalse(sessao._thread.is_alive())
        self.assertTrue(self.eventos.empty(), "cancelada: não pode haver evento 'conectado'")
        b.settimeout(PRAZO)
        self.assertEqual(b.recv(10), b"")  # o socket foi fechado pela sessão cancelada

    def test_sair_avisa_o_adversario_com_fim_adversario_saiu(self) -> None:
        sessao = self.nova_sessao()
        self.proximo("mensagem")  # AGUARDE
        sock, leitor = self.jogador_raw()
        self.assertEqual(receber_mensagem(leitor)["tipo"], "INICIO")
        self.proximo("mensagem")  # INICIO do jogador 1
        sessao.sair()
        fim = receber_mensagem(leitor)
        self.assertEqual((fim["tipo"], fim["motivo"], fim["vencedor"]), ("FIM", "ADVERSARIO_SAIU", None))
        self.esperar_estado(SEM_JOGADORES)
        self.assertTrue(sessao.encerrada)

    def test_sair_enquanto_aguarda_libera_a_vaga(self) -> None:
        sessao = self.nova_sessao()
        self.proximo("mensagem")  # AGUARDE
        sessao.sair()
        self.esperar_estado(SEM_JOGADORES)

    def test_queda_do_servidor_vira_encerrada(self) -> None:
        self.nova_sessao()
        self.proximo("mensagem")
        self.servidor.encerrar()
        self.assertEqual(self.proximo("encerrada")[1], "encerrada")

    def test_linha_invalida_do_servidor_vira_protocolo(self) -> None:
        escuta = socket.socket()
        escuta.bind(("127.0.0.1", 0))
        escuta.listen()
        self.addCleanup(escuta.close)
        self.nova_sessao(porta=escuta.getsockname()[1])
        conexao, _ = escuta.accept()
        self.addCleanup(conexao.close)
        conexao.sendall(b"isto nao e json\n")
        self.assertEqual(self.proximo("protocolo")[1], "protocolo")

    def test_envio_apos_encerrar_devolve_false(self) -> None:
        sessao = self.nova_sessao()
        self.proximo("mensagem")
        sessao.encerrar()
        self.assertFalse(sessao.enviar({"tipo": "SAIR"}))

    def test_disparar_chega_ao_servidor(self) -> None:
        sessao = self.nova_sessao()
        self.proximo("mensagem")  # AGUARDE
        sock, leitor = self.jogador_raw()
        receber_mensagem(leitor)  # INICIO do jogador 2
        self.proximo("mensagem")  # INICIO do jogador 1
        self.assertTrue(sessao.enviar({"tipo": "DISPARAR", "coordenada": "B3"}))
        atualizacao = receber_mensagem(leitor)
        self.assertEqual((atualizacao["tipo"], atualizacao["resultado"]), ("ATUALIZACAO", "ACERTO"))
        _, _, mensagem = self.proximo("mensagem")
        self.assertEqual(mensagem["tipo"], "ATUALIZACAO")


if __name__ == "__main__":
    unittest.main()
