"""Testes da fase 3: o servidor recebe os dois jogadores e inicia a partida.

Cada teste sobe um Servidor real numa porta livre de 127.0.0.1 e conecta
clientes TCP de verdade, usando o protocolo.py. Cobrem as linhas 1 a 5 e 10 a 13
da tabela 9.2 do readme.md.

Executar a partir da raiz do projeto:
    python -m unittest discover -s tests -p 'test_servidor.py' -v
"""

import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from protocolo import ConexaoEncerrada, enviar_mensagem, receber_mensagem  # noqa: E402
from servidor import (  # noqa: E402
    AGUARDANDO_SEGUNDO,
    SEM_JOGADORES,
    VEZ_J1,
    Servidor,
)
from tabuleiro import Tabuleiro  # noqa: E402

NAVIOS_J1 = [("A1", "B1"), ("D4", "D5")]
NAVIOS_J2 = [("D1", "E1"), ("B3", "B4")]
VAZIO = ["~~~~~"] * 5


class Cliente:
    """Cliente mínimo de teste: um socket, um leitor e timeout de segurança."""

    def __init__(self, porta: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", porta), timeout=5)
        # Timeout só como rede de segurança: um erro de lógica vira falha, não trava.
        self.leitor = self.sock.makefile("rb")

    def enviar(self, mensagem):
        enviar_mensagem(self.sock, mensagem)

    def receber(self):
        return receber_mensagem(self.leitor)

    def fechar(self):
        self.leitor.close()
        self.sock.close()

    def esperar_fechamento(self, caso: unittest.TestCase):
        """Confere que o servidor fechou esta conexão."""
        with caso.assertRaises(ConexaoEncerrada):
            self.receber()


class BaseServidor(unittest.TestCase):
    def setUp(self):
        # Navios fixos e alternados: o 1º tabuleiro de cada partida é o do jogador 1.
        navios = iter([NAVIOS_J1, NAVIOS_J2] * 10)
        self.servidor = Servidor("127.0.0.1", 0, criar_tabuleiro=lambda: Tabuleiro(next(navios)))
        _, self.porta = self.servidor.iniciar()
        self.thread = threading.Thread(target=self.servidor.servir_para_sempre, daemon=True)
        self.thread.start()
        self.clientes = []
        self.addCleanup(self._encerrar)

    def _encerrar(self):
        for cliente in self.clientes:
            cliente.fechar()
        self.servidor.encerrar()
        self.thread.join(5)

    def conectar(self) -> Cliente:
        cliente = Cliente(self.porta)
        self.clientes.append(cliente)
        return cliente

    def esperar_estado(self, esperado: str):
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            if self.servidor.estado == esperado:
                return
            time.sleep(0.01)
        self.fail(f"estado {self.servidor.estado}, esperado {esperado}")

    def iniciar_partida(self):
        j1 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "AGUARDE")
        j2 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "INICIO")
        self.assertEqual(j2.receber()["tipo"], "INICIO")
        return j1, j2


class TestEntradaDosJogadores(BaseServidor):
    def test_primeiro_jogador_recebe_aguarde(self):
        j1 = self.conectar()
        self.assertEqual(
            j1.receber(),
            {"tipo": "AGUARDE", "mensagem": "Conectado. Aguardando o segundo jogador..."},
        )
        self.esperar_estado(AGUARDANDO_SEGUNDO)

    def test_segundo_jogador_inicia_a_partida_com_inicio_para_cada_um(self):
        j1 = self.conectar()
        j1.receber()
        j2 = self.conectar()
        self.assertEqual(
            j1.receber(),
            {
                "tipo": "INICIO",
                "jogador": 1,
                "sua_vez": True,
                "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
                "tabuleiro_adversario": VAZIO,
            },
        )
        self.assertEqual(
            j2.receber(),
            {
                "tipo": "INICIO",
                "jogador": 2,
                "sua_vez": False,
                "tabuleiro_proprio": ["~~~NN", "~~~~~", "~N~~~", "~N~~~", "~~~~~"],
                "tabuleiro_adversario": VAZIO,
            },
        )
        self.esperar_estado(VEZ_J1)

    def test_tabuleiros_sorteados_por_padrao(self):
        self.servidor.encerrar()
        self.thread.join(5)
        self.servidor = Servidor("127.0.0.1", 0)
        _, self.porta = self.servidor.iniciar()
        self.thread = threading.Thread(target=self.servidor.servir_para_sempre, daemon=True)
        self.thread.start()
        j1 = self.conectar()
        j1.receber()
        j2 = self.conectar()
        for inicio in (j1.receber(), j2.receber()):
            self.assertEqual("".join(inicio["tabuleiro_proprio"]).count("N"), 4)
            self.assertEqual(inicio["tabuleiro_adversario"], VAZIO)


class TestEnquantoAguarda(BaseServidor):
    def test_disparar_antes_do_inicio_gera_partida_nao_iniciada(self):
        j1 = self.conectar()
        j1.receber()
        j1.enviar({"tipo": "DISPARAR", "coordenada": "B3"})
        resposta = j1.receber()
        self.assertEqual(resposta["tipo"], "ERRO")
        self.assertEqual(resposta["codigo"], "PARTIDA_NAO_INICIADA")
        self.esperar_estado(AGUARDANDO_SEGUNDO)

    def test_sair_enquanto_aguarda_libera_a_vaga_de_jogador_1(self):
        j1 = self.conectar()
        j1.receber()
        j1.enviar({"tipo": "SAIR"})
        j1.esperar_fechamento(self)  # SAIR não tem resposta; o servidor só fecha
        self.esperar_estado(SEM_JOGADORES)
        novo = self.conectar()
        self.assertEqual(novo.receber()["tipo"], "AGUARDE")  # é o novo jogador 1
        j2 = self.conectar()
        self.assertEqual(novo.receber()["jogador"], 1)
        self.assertEqual(j2.receber()["jogador"], 2)

    def test_queda_enquanto_aguarda_libera_a_vaga_de_jogador_1(self):
        j1 = self.conectar()
        j1.receber()
        j1.fechar()
        self.esperar_estado(SEM_JOGADORES)
        novo = self.conectar()
        self.assertEqual(novo.receber()["tipo"], "AGUARDE")

    def test_mensagem_invalida_e_tipo_desconhecido_nao_mudam_o_estado(self):
        j1 = self.conectar()
        j1.receber()
        j1.sock.sendall(b"isto nao e json\n")
        self.assertEqual(j1.receber()["codigo"], "MENSAGEM_INVALIDA")
        j1.enviar({"tipo": "INICIO"})
        self.assertEqual(j1.receber()["codigo"], "TIPO_DESCONHECIDO")
        self.esperar_estado(AGUARDANDO_SEGUNDO)
        j2 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "INICIO")
        self.assertEqual(j2.receber()["tipo"], "INICIO")


class TestDuranteAPartida(BaseServidor):
    def test_terceiro_cliente_recebe_servidor_cheio_e_e_desconectado(self):
        j1, j2 = self.iniciar_partida()
        terceiro = self.conectar()
        resposta = terceiro.receber()
        self.assertEqual(resposta["tipo"], "ERRO")
        self.assertEqual(resposta["codigo"], "SERVIDOR_CHEIO")
        terceiro.esperar_fechamento(self)
        self.esperar_estado(VEZ_J1)
        # A partida segue: os dois jogadores continuam sendo atendidos.
        j2.enviar({"tipo": "PING"})
        self.assertEqual(j2.receber()["codigo"], "TIPO_DESCONHECIDO")

    def test_sair_durante_a_partida_avisa_o_adversario(self):
        for quem_sai in (0, 1):
            with self.subTest(jogador=quem_sai + 1):
                jogadores = self.iniciar_partida()
                saiu, ficou = jogadores[quem_sai], jogadores[1 - quem_sai]
                saiu.enviar({"tipo": "SAIR"})
                self.assertEqual(
                    ficou.receber(),
                    {
                        "tipo": "FIM",
                        "motivo": "ADVERSARIO_SAIU",
                        "vencedor": None,
                        "mensagem": "O adversário saiu da partida.",
                    },
                )
                ficou.esperar_fechamento(self)
                saiu.esperar_fechamento(self)
                self.esperar_estado(SEM_JOGADORES)

    def test_queda_durante_a_partida_avisa_o_adversario(self):
        j1, j2 = self.iniciar_partida()
        j2.fechar()
        fim = j1.receber()
        self.assertEqual(fim["tipo"], "FIM")
        self.assertEqual(fim["motivo"], "ADVERSARIO_DESCONECTOU")
        self.assertIsNone(fim["vencedor"])
        j1.esperar_fechamento(self)
        self.esperar_estado(SEM_JOGADORES)

    def test_mensagem_incompleta_e_tratada_como_queda(self):
        j1, j2 = self.iniciar_partida()
        j1.sock.sendall(b'{"tipo":"DISP')  # sem a LF final
        j1.fechar()
        self.assertEqual(j2.receber()["motivo"], "ADVERSARIO_DESCONECTOU")

    def test_apos_o_fim_o_servidor_aceita_nova_partida(self):
        j1, j2 = self.iniciar_partida()
        j1.enviar({"tipo": "SAIR"})
        j2.receber()
        self.esperar_estado(SEM_JOGADORES)
        novo1, novo2 = self.iniciar_partida()
        self.esperar_estado(VEZ_J1)

    def test_erros_vao_so_para_quem_errou(self):
        j1, j2 = self.iniciar_partida()
        j2.sock.sendall(b"[1,2]\n")
        self.assertEqual(j2.receber()["codigo"], "MENSAGEM_INVALIDA")
        # j1 não recebeu nada: a próxima mensagem dele é a resposta ao próprio pedido.
        j1.enviar({"tipo": "OLA"})
        self.assertEqual(j1.receber()["codigo"], "TIPO_DESCONHECIDO")


if __name__ == "__main__":
    unittest.main()
